#!/usr/bin/env python3
"""Evaluate an existing CTS-to-route checkpoint without training or writing to its run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

CODE_DIR = Path(__file__).resolve().parent
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

import train_cts_route_v2 as trainer  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--normalizer", type=Path, required=True)
    parser.add_argument("--graph-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"Refusing to reuse non-empty output directory: {args.output_dir}")
    if not args.checkpoint.is_file():
        raise FileNotFoundError(args.checkpoint)
    if not args.normalizer.is_file():
        raise FileNotFoundError(args.normalizer)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("configs", "logs", "results", "weights"):
        (args.output_dir / name).mkdir()

    ckpt = torch.load(args.checkpoint, map_location=args.device, weights_only=False)
    config = ckpt.get("training_config", {})
    saved_args: dict[str, Any] = config.get("args", {})
    saved_args.update({
        "graph_root": str(args.graph_root),
        "output_dir": str(args.output_dir),
        "normalizer_path": str(args.normalizer),
        "recompute_normalizer": False,
        "max_normalizer_graphs": 0,
        "epochs": int(ckpt.get("epoch", 0)),
        "early_stop_patience": 0,
        "resume_checkpoint": "",
        "resume_optimizer": False,
        "device": args.device,
    })
    run_args = argparse.Namespace(**saved_args)
    trainer.set_seed(int(run_args.seed))
    device = torch.device(args.device)

    records = trainer.discover_cts_graphs(args.graph_root, ["test"])
    if not records:
        raise RuntimeError(f"No test records found under {args.graph_root}")
    normalizer = trainer.load_normalizer(args.normalizer)
    if normalizer.schema_mode != trainer.CTS_SCHEMA_MODE:
        raise RuntimeError(
            f"Normalizer schema {normalizer.schema_mode!r} != {trainer.CTS_SCHEMA_MODE!r}"
        )

    assets: dict[str, dict[str, Any]] = {}
    test_indices: dict[str, np.ndarray] = {}
    for rec in records:
        payload = trainer.load_cts_assets(rec, run_args, normalizer)
        if payload is None:
            continue
        idx = trainer.valid_path_indices(payload["timing_paths"])
        if run_args.max_paths_per_record and idx.size > run_args.max_paths_per_record:
            raise RuntimeError("Refusing sampled evaluation; checkpoint config has max-path sampling")
        assets[rec.key] = payload
        test_indices[rec.key] = idx

    records = [rec for rec in records if rec.key in assets and test_indices[rec.key].size]
    if not records:
        raise RuntimeError("No valid labeled test paths were found")

    geom_mean = np.asarray(config["geometry_stats"]["mean"], dtype=np.float64)
    geom_std = np.maximum(np.asarray(config["geometry_stats"]["std"], dtype=np.float64), 1e-6)
    design_mean = np.asarray(config["design_stats"]["mean"], dtype=np.float64)
    design_std = np.maximum(np.asarray(config["design_stats"]["std"], dtype=np.float64), 1e-6)
    model = trainer.CtsTimingPathModel(
        run_args, geom_mean, geom_std, design_mean, design_std
    ).to(device)
    model.load_state_dict(ckpt["model_state_dict"], strict=True)
    delay_loss = trainer.MaskedDelayLoss(
        str(run_args.delay_loss), beta=float(run_args.delay_huber_beta)
    )
    slack_loss = trainer.MaskedDelayLoss(
        "smooth_l1", beta=float(run_args.delay_huber_beta)
    )

    y_train_by_design: dict[str, list[np.ndarray]] = {}
    geometry_train_by_design: dict[str, list[np.ndarray]] = {}
    train_records = trainer.discover_cts_graphs(args.graph_root, ["train"])
    for rec in train_records:
        paths = torch.load(rec.timing_paths_path, map_location="cpu", weights_only=False)
        idx = trainer.valid_path_indices(paths)
        train_idx, _ = trainer.split_path_indices(
            idx, float(run_args.within_design_val_ratio), int(run_args.seed)
        )
        if not train_idx.size:
            continue
        graph = trainer.dgl.load_graphs(str(rec.graph_path))[0][0]
        geometry = trainer.cts_path_geometry(
            graph.ndata["feat"][:, :2],
            paths["node_ptr"],
            paths["node_ids_flat"],
            int(paths["label_mask"].shape[0]),
        )
        y_train_by_design.setdefault(rec.design, []).append(
            paths["target_route"].float().numpy()[train_idx, 0]
        )
        geometry_train_by_design.setdefault(rec.design, []).append(geometry[train_idx])
    trivial = trainer.fit_trivial_baselines(
        {d: np.concatenate(chunks) for d, chunks in y_train_by_design.items()},
        {d: np.concatenate(chunks, axis=0) for d, chunks in geometry_train_by_design.items()},
    )
    eval_keys = [rec.key for rec in records]
    per_design, per_record = trainer.evaluate_records(
        model,
        eval_keys,
        test_indices,
        assets,
        run_args,
        device,
        delay_loss,
        slack_loss,
        trivial,
    )
    summary = {
        "task": "cts_to_route_checkpoint_only_evaluation",
        "checkpoint": str(args.checkpoint.resolve()),
        "checkpoint_epoch": int(ckpt.get("epoch", -1)),
        "graph_root": str(args.graph_root.resolve()),
        "split": "test",
        "test_records": [rec.key for rec in records],
        "model_variant": str(run_args.model_variant),
        "loss_protocol": {
            "name": str(run_args.delay_loss),
            "smooth_l1_beta_ns": float(run_args.delay_huber_beta),
            "delay_mask": "label_mask",
            "slack_weight": float(run_args.slack_weight),
        },
        "test_per_design": per_design,
        "test_summary": trainer.summarise(per_design, "model"),
        "test_by_record": trainer.summarise_by_record(per_record),
        "test_per_record": per_record,
    }
    (args.output_dir / "results" / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (args.output_dir / "configs" / "evaluation.json").write_text(
        json.dumps({
            "checkpoint": str(args.checkpoint.resolve()),
            "normalizer": str(args.normalizer.resolve()),
            "graph_root": str(args.graph_root.resolve()),
            "device": args.device,
            "split": "test",
            "training_config": config,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary["test_by_record"], ensure_ascii=False))
    print(trainer.format_per_record_table(
        f"M0 checkpoint-only test evaluation (epoch {ckpt.get('epoch', '?')})",
        per_record,
        per_design=per_design,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
