#!/usr/bin/env python
"""Train tabular baselines on a fixed-dimensional view of the CTS GNN inputs."""

from __future__ import annotations

import argparse
import csv
import inspect
import json
import math
import sys
from pathlib import Path
from typing import Any

import dgl
import numpy as np
import torch
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[4]
M0_CODE = ROOT / "experiments" / "cts_to_route" / "M0_pool" / "code"
sys.path.insert(0, str(M0_CODE))

from cts_route_common import (  # noqa: E402
    CTS_CELL_TYPE_VOCAB,
    CTS_DESIGN_FEATURES,
    CTS_EDGE_FEAT_KEY,
    CTS_GEOMETRY_FEATURES,
    CTS_NODE_FEAT_KEY,
    CTS_SCHEMA_MODE,
    compute_cts_normalizer,
    cts_path_geometry,
    discover_cts_graphs,
    read_cts_design_scalars,
    validate_cts_graph,
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graph-root", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--models", default="ridge,xgboost,mlp")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--smoke", action="store_true", help="extract a few paths and validate alignment only")
    ap.add_argument("--smoke-paths", type=int, default=8)
    ap.add_argument("--huber-beta-ns", type=float, default=0.02)
    ap.add_argument("--ridge-alphas", default="0.001,0.01,0.1,1.0")
    ap.add_argument("--within-design-val-ratio", type=float, default=0.2)
    ap.add_argument("--mlp-hidden", type=int, default=512)
    ap.add_argument("--mlp-epochs", type=int, default=150)
    ap.add_argument("--mlp-patience", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=2048)
    ap.add_argument("--xgb-estimators", type=int, default=500)
    ap.add_argument("--xgb-max-depth", type=int, default=8)
    ap.add_argument("--xgb-learning-rate", type=float, default=0.04)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return ap.parse_args()


def build_edge_lookup(
    g: dgl.DGLGraph,
) -> dict[tuple[int, int], list[int]]:
    src, dst = g.edges(order="eid")
    src = src.cpu().numpy()
    dst = dst.cpu().numpy()
    lookup: dict[tuple[int, int], list[int]] = {}
    for eid, pair in enumerate(zip(src.tolist(), dst.tolist())):
        lookup.setdefault(pair, []).append(eid)
    return lookup


def path_edge_features(
    edge_lookup: dict[tuple[int, int], list[int]],
    edge_features: np.ndarray,
    ids: np.ndarray,
) -> tuple[np.ndarray, int]:
    rows: list[np.ndarray] = []
    unmatched = 0
    for u, v in zip(ids[:-1], ids[1:]):
        if u < 0 or v < 0:
            continue
        edge_ids = edge_lookup.get((int(u), int(v)))
        if not edge_ids:
            unmatched += 1
            continue
        rows.extend(edge_features[edge_ids])
    if not rows:
        return np.zeros(30, dtype=np.float32), unmatched
    arr = np.asarray(rows, dtype=np.float32)
    return np.concatenate([arr.mean(axis=0), arr.max(axis=0)]), unmatched


def encode_record(rec: Any, node_mean: np.ndarray, node_std: np.ndarray,
                  edge_mean: np.ndarray, edge_std: np.ndarray,
                  geom_mean: np.ndarray, geom_std: np.ndarray,
                  design_mean: np.ndarray, design_std: np.ndarray,
                  max_paths: int = 0) -> dict[str, Any]:
    g = dgl.load_graphs(rec.graph_path)[0][0]
    validate_cts_graph(g, rec.graph_path)
    tp = torch.load(rec.timing_paths_path, map_location="cpu", weights_only=False)
    if tp.get("schema") != CTS_SCHEMA_MODE:
        raise RuntimeError(f"{rec.key}: timing path schema mismatch: {tp.get('schema')}")
    y_all = tp["target_route"].float().numpy()[:, 0]
    mask = tp["label_mask"].float().numpy().reshape(-1) > 0.5
    valid_idx = np.flatnonzero(mask)
    if max_paths > 0:
        valid_idx = valid_idx[:max_paths]
    ptr = tp["node_ptr"].cpu().numpy().astype(np.int64)
    flat = tp["node_ids_flat"].cpu().numpy().astype(np.int64)
    raw_node = g.ndata[CTS_NODE_FEAT_KEY].float().cpu().numpy()
    raw_edge = g.edata[CTS_EDGE_FEAT_KEY].float().cpu().numpy()

    geom_all = cts_path_geometry(
        torch.from_numpy(raw_node[:, :2]),
        tp["node_ptr"],
        tp["node_ids_flat"],
        len(y_all),
    ).astype(np.float32)
    design = read_cts_design_scalars(Path(rec.graph_path).parent / "graph_meta.json").astype(np.float32)

    # Keep cell_type_id categorical: aggregate its train-vocabulary histogram
    # separately instead of treating numeric IDs as an ordered scalar.
    cat_idx = raw_node[:, 2].astype(np.int64)
    cont_dims = [i for i in range(raw_node.shape[1]) if i != 2]
    node_cont = raw_node[:, cont_dims]
    node_mean_cont = node_mean[cont_dims]
    node_std_cont = node_std[cont_dims]
    node_cont = (node_cont - node_mean_cont) / np.maximum(node_std_cont, 1e-6)
    edge_norm = (raw_edge - edge_mean) / np.maximum(edge_std, 1e-6)
    edge_lookup = build_edge_lookup(g)

    features: list[np.ndarray] = []
    targets: list[float] = []
    unmapped_nodes = 0
    unmatched_edges = 0
    total_path_edges = 0
    for p in valid_idx:
        start, end = int(ptr[p]), int(ptr[p + 1])
        path_ids = flat[start:end]
        valid_ids = path_ids[path_ids >= 0]
        unmapped_nodes += int((path_ids < 0).sum())
        if valid_ids.size:
            vals = node_cont[valid_ids]
            max_pool = vals.max(axis=0)
            last_idx = max(int(valid_ids.size) - 1, 0)
            last_node_id = max(int(path_ids[last_idx]), 0)
            last_pool = node_cont[last_node_id]
            node_pool = np.concatenate([vals.mean(axis=0), max_pool, last_pool])
            cats = np.clip(cat_idx[valid_ids], 0, CTS_CELL_TYPE_VOCAB - 1)
            cat_hist = np.bincount(cats, minlength=CTS_CELL_TYPE_VOCAB).astype(np.float32)
            cat_hist /= max(int(cats.size), 1)
        else:
            node_pool = np.zeros(node_cont.shape[1] * 3, dtype=np.float32)
            cat_hist = np.zeros(CTS_CELL_TYPE_VOCAB, dtype=np.float32)
        edge_pool, misses = path_edge_features(edge_lookup, edge_norm, path_ids)
        unmatched_edges += misses
        total_path_edges += max(path_ids.size - 1, 0)
        features.append(np.concatenate([
            node_pool,
            cat_hist,
            edge_pool,
            (geom_all[p] - geom_mean) / np.maximum(geom_std, 1e-6),
            (design - design_mean) / np.maximum(design_std, 1e-6),
        ]))
        targets.append(float(y_all[p]))

    return {
        "key": rec.key,
        "design": rec.design,
        "strategy": rec.strategy,
        "split": rec.split,
        "x": np.asarray(features, dtype=np.float32),
        "y": np.asarray(targets, dtype=np.float32),
        "slack": tp["target_route"].float().numpy()[valid_idx, 1].astype(np.float32),
        "slack_mask": tp["slack_mask"].float().numpy().reshape(-1)[valid_idx] > 0.5,
        "path_indices": valid_idx,
        "clock_period_ns": float(design[0]),
        "unmapped_nodes": unmapped_nodes,
        "unmatched_edges": unmatched_edges,
        "path_edges": total_path_edges,
        "path_count": len(valid_idx),
    }


def split_path_indices(indices: np.ndarray, val_ratio: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    if val_ratio <= 0.0 or indices.size < 2:
        return indices, np.zeros((0,), dtype=np.int64)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(indices.size)
    n_val = max(1, min(int(round(indices.size * float(val_ratio))), indices.size - 1))
    val_idx = np.sort(indices[perm[:n_val]])
    train_idx = np.sort(indices[perm[n_val:]])
    return train_idx, val_idx


def select_record_paths(record: dict[str, Any], global_indices: np.ndarray) -> dict[str, Any]:
    positions = np.searchsorted(record["path_indices"], global_indices)
    if positions.size and (
        positions.max() >= record["path_indices"].size
        or not np.array_equal(record["path_indices"][positions], global_indices)
    ):
        raise RuntimeError(f"{record['key']}: selected path indices are misaligned")
    out = dict(record)
    for key in ("x", "y", "slack", "slack_mask", "path_indices"):
        out[key] = record[key][positions]
    return out


def macro_metrics(records: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {
        k: [] for k in (
            "r2", "mae", "rmse", "MAPE_percent", "normalized_MAE",
            "top5_delay_recall", "slack_critical_top5_recall",
            "slack_critical_delay_mae",
        )
    }
    for rec in records:
        y, pred = rec["y"], rec["pred"]
        if not len(y):
            continue
        error = pred - y
        mae = float(np.abs(error).mean())
        values["mae"].append(mae)
        values["rmse"].append(float(np.sqrt(np.square(error).mean())))
        values["MAPE_percent"].append(
            float((np.abs(error) / np.maximum(np.abs(y), 1e-6)).mean() * 100.0)
        )
        values["normalized_MAE"].append(
            mae / max(float(rec.get("clock_period_ns", 0.0)), 1e-9)
        )
        ss_tot = float(np.square(y - y.mean()).sum())
        if len(y) > 1 and ss_tot > 1e-15:
            values["r2"].append(1.0 - float(np.square(error).sum()) / ss_tot)
        k = max(1, int(math.ceil(0.05 * len(y))))
        true_top = set(np.argpartition(y, -k)[-k:].tolist())
        pred_top = set(np.argpartition(pred, -k)[-k:].tolist())
        values["top5_delay_recall"].append(len(true_top & pred_top) / k)
        slack = rec.get("slack")
        slack_mask = rec.get("slack_mask")
        if slack is not None and slack_mask is not None:
            critical_maes = []
            critical_recalls = []
            for path_kind in (0, 1):
                local = (np.asarray(rec["path_indices"]) % 2 == path_kind) & np.asarray(slack_mask)
                local_idx = np.flatnonzero(local)
                if not local_idx.size:
                    continue
                n_critical = max(1, int(math.ceil(0.05 * local_idx.size)))
                critical_local = local_idx[np.argpartition(np.asarray(slack)[local_idx], n_critical - 1)[:n_critical]]
                n_predicted = max(1, int(math.ceil(0.05 * local_idx.size)))
                predicted_local = local_idx[
                    np.argpartition(pred[local_idx], -n_predicted)[-n_predicted:]
                ]
                critical_recalls.append(
                    len(set(critical_local.tolist()) & set(predicted_local.tolist()))
                    / len(critical_local)
                )
                critical_maes.append(
                    float(np.abs(pred[critical_local] - y[critical_local]).mean())
                )
            if critical_maes:
                values["slack_critical_delay_mae"].append(float(np.mean(critical_maes)))
            if critical_recalls:
                values["slack_critical_top5_recall"].append(float(np.mean(critical_recalls)))
    return {name: float(np.mean(v)) if v else float("nan") for name, v in values.items()}


def record_balanced_weights(records: list[dict[str, Any]]) -> np.ndarray:
    total = sum(len(rec["y"]) for rec in records)
    n_records = max(len(records), 1)
    return np.concatenate([
        np.full(
            len(rec["y"]),
            total / (n_records * max(len(rec["y"]), 1)),
            dtype=np.float64,
        )
        for rec in records
    ]).astype(np.float32)


def predict_linear_huber(
    x_train: np.ndarray,
    y_train: np.ndarray,
    train_weights: np.ndarray,
    x_val: np.ndarray,
    val_records: list[dict[str, Any]],
    x_test: np.ndarray,
    args: argparse.Namespace,
    out_dir: Path,
) -> np.ndarray:
    device = torch.device(args.device)
    scaler = StandardScaler().fit(x_train, sample_weight=train_weights)
    xtr = torch.from_numpy(scaler.transform(x_train).astype(np.float32)).to(device)
    xva = torch.from_numpy(scaler.transform(x_val).astype(np.float32)).to(device)
    ytr = torch.from_numpy(y_train.astype(np.float32)).to(device)
    wtr = torch.from_numpy(train_weights).to(device)
    beta = float(args.huber_beta_ns)
    best_score = float("inf")
    best_params = None
    alphas = [float(v) for v in args.ridge_alphas.split(",") if v.strip()]
    with (out_dir / "logs" / "ridge_smoothl1_selection.jsonl").open("w", encoding="utf-8") as log:
        for alpha in alphas:
            weight = torch.zeros(xtr.shape[1], device=device, requires_grad=True)
            bias = torch.zeros((), device=device, requires_grad=True)
            optimizer = torch.optim.LBFGS(
                [weight, bias], lr=0.5, max_iter=120, line_search_fn="strong_wolfe"
            )

            def closure():
                optimizer.zero_grad(set_to_none=True)
                error = xtr @ weight + bias - ytr
                absolute = error.abs()
                element_loss = torch.where(
                    absolute < beta, 0.5 * error.square() / beta, absolute - 0.5 * beta
                )
                loss = (element_loss * wtr).sum() / wtr.sum().clamp_min(1e-12)
                loss = loss + 0.5 * alpha * weight.square().sum()
                loss.backward()
                return loss

            optimizer.step(closure)
            with torch.no_grad():
                val_pred = (xva @ weight + bias).cpu().numpy()
            cursor, record_mae = 0, []
            for rec in val_records:
                stop = cursor + len(rec["y"])
                record_mae.append(float(np.abs(val_pred[cursor:stop] - rec["y"]).mean()))
                cursor = stop
            score = float(np.mean(record_mae))
            log.write(json.dumps({"alpha": alpha, "val_macro_record_mae_ns": score}) + "\n")
            log.flush()
            if score < best_score:
                best_score = score
                best_params = (weight.detach().cpu().clone(), bias.detach().cpu().clone(), alpha)
    if best_params is None:
        raise RuntimeError("SmoothL1 linear model did not fit")
    weight, bias, alpha = best_params
    xte = torch.from_numpy(scaler.transform(x_test).astype(np.float32)).to(device)
    torch.save({
        "weight": weight, "bias": bias, "alpha": alpha, "huber_beta_ns": beta,
        "val_macro_record_mae_ns": best_score,
        "scaler_mean": scaler.mean_, "scaler_scale": scaler.scale_,
    }, out_dir / "weights" / "ridge_smoothl1.pt")
    with torch.no_grad():
        return (xte @ weight.to(device) + bias.to(device)).cpu().numpy()


def predict_xgboost(
    x_train: np.ndarray,
    y_train: np.ndarray,
    train_weights: np.ndarray,
    x_val: np.ndarray,
    val_records: list[dict[str, Any]],
    x_test: np.ndarray,
    args: argparse.Namespace,
    out_dir: Path,
) -> np.ndarray:
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise RuntimeError(
            "XGBoost was requested but is not installed in the selected Python environment."
        ) from exc

    dtrain = xgb.DMatrix(x_train, label=y_train, weight=train_weights)
    dval = xgb.DMatrix(
        x_val, label=np.concatenate([rec["y"] for rec in val_records])
    )
    dtest = xgb.DMatrix(x_test)
    beta = float(args.huber_beta_ns)
    val_lengths = [len(rec["y"]) for rec in val_records]

    def smooth_l1_obj(pred: np.ndarray, data: Any) -> tuple[np.ndarray, np.ndarray]:
        error = pred - data.get_label()
        abs_error = np.abs(error)
        grad = np.clip(error, -beta, beta) / beta
        # SmoothL1 has zero curvature outside beta. A tiny Hessian there makes
        # Newton leaf steps explode on this large, record-weighted dataset.
        # Use a positive curvature majorizer for stable tree construction;
        # gradients still match the exact SmoothL1 objective.
        hess = np.full_like(error, 1.0 / beta)
        weight = data.get_weight()
        if weight.size:
            grad = grad * weight
            hess = hess * weight
        return grad, hess

    def val_macro_mae(pred: np.ndarray, _data: Any) -> tuple[str, float]:
        cursor = 0
        per_record = []
        for rec, length in zip(val_records, val_lengths):
            stop = cursor + length
            per_record.append(float(np.abs(pred[cursor:stop] - rec["y"]).mean()))
            cursor = stop
        return "val_macro_record_mae_ns", float(np.mean(per_record))

    params = {
        "tree_method": "hist",
        # With a custom objective XGBoost does not infer an intercept from the
        # labels; initialize at the record-balanced training target mean.
        "base_score": float(np.average(y_train, weights=train_weights)),
        "max_depth": int(args.xgb_max_depth),
        "eta": float(args.xgb_learning_rate),
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "lambda": 1.0,
        "seed": int(args.seed),
        "nthread": max(1, torch.get_num_threads()),
        "disable_default_eval_metric": 1,
    }
    evals_result: dict[str, Any] = {}
    train_kwargs = {
        "params": params,
        "dtrain": dtrain,
        "num_boost_round": int(args.xgb_estimators),
        "evals": [(dval, "val")],
        "obj": smooth_l1_obj,
        "maximize": False,
        "early_stopping_rounds": 40,
        "evals_result": evals_result,
        "verbose_eval": False,
    }
    train_signature = inspect.signature(xgb.train).parameters
    metric_arg = "custom_metric" if "custom_metric" in train_signature else "feval"
    try:
        model = xgb.train(**train_kwargs, **{metric_arg: val_macro_mae})
    except TypeError as exc:
        raise RuntimeError(
            f"Installed XGBoost ({xgb.__version__}) does not support custom objectives "
            "with validation metrics required by this matched-loss experiment."
        ) from exc
    model.save_model(str(out_dir / "weights" / "xgboost_smoothl1.json"))
    (out_dir / "logs" / "xgboost_training_metrics.json").write_text(
        json.dumps(evals_result, indent=2), encoding="utf-8"
    )
    best_iteration = int(getattr(model, "best_iteration", args.xgb_estimators - 1))
    try:
        prediction = model.predict(dtest, iteration_range=(0, best_iteration + 1))
    except TypeError:
        prediction = model.predict(dtest, ntree_limit=best_iteration + 1)
    return prediction.astype(np.float32)


def predict_mlp(x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray,
                y_val: np.ndarray, x_test: np.ndarray, args: argparse.Namespace,
                out_dir: Path, train_weights: np.ndarray,
                val_records: list[dict[str, Any]]) -> np.ndarray:
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    x_scaler = StandardScaler().fit(x_train, sample_weight=train_weights)
    x_train_t = torch.from_numpy(x_scaler.transform(x_train).astype(np.float32))
    x_val_t = torch.from_numpy(x_scaler.transform(x_val).astype(np.float32))
    x_test_t = torch.from_numpy(x_scaler.transform(x_test).astype(np.float32))
    y_train_t = torch.from_numpy(y_train.astype(np.float32))
    train_weight_t = torch.from_numpy(train_weights.astype(np.float32))
    model = torch.nn.Sequential(
        torch.nn.Linear(x_train.shape[1], args.mlp_hidden),
        torch.nn.GELU(),
        torch.nn.Dropout(0.2),
        torch.nn.Linear(args.mlp_hidden, args.mlp_hidden // 2),
        torch.nn.GELU(),
        torch.nn.Dropout(0.1),
        torch.nn.Linear(args.mlp_hidden // 2, 1),
    ).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    best = float("inf")
    best_epoch = 0
    best_state = None
    wait = 0
    log_path = out_dir / "logs" / "mlp_training.jsonl"
    with log_path.open("w", encoding="utf-8") as log:
        for epoch in range(1, args.mlp_epochs + 1):
            model.train()
            order = torch.randperm(x_train_t.shape[0])
            losses = []
            for indices in order.split(args.batch_size):
                xb = x_train_t[indices].to(device)
                yb = y_train_t[indices].to(device)
                wb = train_weight_t[indices].to(device)
                pred = model(xb).squeeze(1)
                element_loss = torch.nn.functional.smooth_l1_loss(
                    pred, yb, beta=float(args.huber_beta_ns), reduction="none"
                )
                loss = (element_loss * wb).sum() / wb.sum().clamp_min(1e-12)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                opt.step()
                losses.append(float(loss.detach().cpu()))
            model.eval()
            with torch.no_grad():
                val_pred = model(x_val_t.to(device)).squeeze(1).cpu().numpy()
            cursor, record_mae = 0, []
            for rec in val_records:
                stop = cursor + len(rec["y"])
                record_mae.append(float(np.abs(val_pred[cursor:stop] - rec["y"]).mean()))
                cursor = stop
            val_mae = float(np.mean(record_mae))
            row = {"epoch": epoch, "train_loss": float(np.mean(losses)),
                   "val_macro_record_mae_ns": val_mae}
            log.write(json.dumps(row) + "\n")
            log.flush()
            if val_mae < best:
                best, best_epoch, wait = val_mae, epoch, 0
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            else:
                wait += 1
                if wait >= args.mlp_patience:
                    break
    if best_state is None:
        raise RuntimeError("MLP did not produce a validation checkpoint")
    model.load_state_dict(best_state)
    torch.save({"model_state_dict": best_state, "epoch": best_epoch,
                "huber_beta_ns": float(args.huber_beta_ns),
                "x_mean": x_scaler.mean_, "x_scale": x_scaler.scale_},
               out_dir / "weights" / "mlp_best.pt")
    model.eval()
    with torch.no_grad():
        return model(x_test_t.to(device)).squeeze(1).cpu().numpy()


def main() -> int:
    args = parse_args()
    np.random.seed(args.seed)
    out_dir = args.output_dir
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"Refusing to reuse non-empty experiment directory: {out_dir}")
    for name in ("logs", "results", "weights", "configs"):
        (out_dir / name).mkdir(parents=True, exist_ok=True)
    records = discover_cts_graphs(args.graph_root)
    if not records:
        raise RuntimeError(f"No CTS graph records found in {args.graph_root}")
    train_records = [r for r in records if r.split == "train"]
    if not train_records:
        raise RuntimeError("No training records found")
    train_indices_by_key: dict[str, np.ndarray] = {}
    effective_train_records = []
    for rec in train_records:
        paths = torch.load(rec.timing_paths_path, map_location="cpu", weights_only=False)
        valid_idx = np.flatnonzero(
            paths["label_mask"].float().reshape(-1).numpy() > 0.5
        )
        train_idx, _ = split_path_indices(
            valid_idx, args.within_design_val_ratio, args.seed
        )
        if train_idx.size:
            effective_train_records.append(rec)
            train_indices_by_key[rec.key] = train_idx
    if not effective_train_records:
        raise RuntimeError("No valid training paths remain after the within-record split")
    norm = compute_cts_normalizer([r.graph_path for r in effective_train_records])
    node_mean, node_std = norm.node_mean.numpy(), norm.node_std.numpy()
    edge_mean, edge_std = norm.edge_mean.numpy(), norm.edge_std.numpy()

    train_geom_rows = []
    train_design_rows = []
    for rec in effective_train_records:
        graph = dgl.load_graphs(rec.graph_path)[0][0]
        paths = torch.load(rec.timing_paths_path, map_location="cpu", weights_only=False)
        valid_idx = np.flatnonzero(
            paths["label_mask"].float().reshape(-1).numpy() > 0.5
        )
        train_idx = train_indices_by_key[rec.key]
        if not train_idx.size:
            continue
        raw_xy = graph.ndata[CTS_NODE_FEAT_KEY][:, :2].float()
        total_paths = int(paths["label_mask"].shape[0])
        geom = cts_path_geometry(
            raw_xy, paths["node_ptr"], paths["node_ids_flat"], total_paths
        )
        train_geom_rows.append(geom[train_idx])
        train_design_rows.append(
            read_cts_design_scalars(Path(rec.graph_path).parent / "graph_meta.json")
        )
    if not train_geom_rows or not train_design_rows:
        raise RuntimeError("Could not estimate training-only geometry/design normalization")
    geom_stack = np.concatenate(train_geom_rows, axis=0)
    design_stack = np.stack(train_design_rows, axis=0)
    geom_mean, geom_std = geom_stack.mean(axis=0), np.maximum(geom_stack.std(axis=0), 1e-6)
    design_mean = design_stack.mean(axis=0)
    design_std = np.maximum(design_stack.std(axis=0), 1e-6)

    if args.smoke:
        selected_records = []
        for split in ("train", "val", "test"):
            selected_records.extend([r for r in records if r.split == split][:1])
        selected = [
            encode_record(
                r, node_mean, node_std, edge_mean, edge_std,
                geom_mean, geom_std, design_mean, design_std, args.smoke_paths,
            )
            for r in selected_records
        ]
        if not selected or any(not np.isfinite(row["x"]).all() for row in selected):
            raise RuntimeError("Feature smoke check failed")
        map_stats = {
            row["key"]: {
                "path_count_checked": int(row["x"].shape[0]),
                "feature_dim": int(row["x"].shape[1]),
                "unmapped_nodes": row["unmapped_nodes"],
                "unmatched_edges": row["unmatched_edges"],
                "path_edges": row["path_edges"],
            }
            for row in selected
        }
        print(json.dumps(map_stats, indent=2))
        return 0

    encoded = [
        encode_record(
            r, node_mean, node_std, edge_mean, edge_std,
            geom_mean, geom_std, design_mean, design_std,
        )
        for r in records
    ]
    splits: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}
    for row in encoded:
        if row["split"] == "train":
            train_idx, val_idx = split_path_indices(
                row["path_indices"], args.within_design_val_ratio, args.seed
            )
            if train_idx.size:
                splits["train"].append(select_record_paths(row, train_idx))
            if val_idx.size:
                splits["val"].append(select_record_paths(row, val_idx))
        elif row["split"] == "val":
            splits["val"].append(row)
        elif row["split"] == "test":
            splits["test"].append(row)
    if not all(splits[s] for s in ("train", "val", "test")):
        raise RuntimeError("Expected non-empty M0-matched train/validation/test partitions")
    x_train = np.concatenate([r["x"] for r in splits["train"]])
    y_train = np.concatenate([r["y"] for r in splits["train"]])
    x_val = np.concatenate([r["x"] for r in splits["val"]])
    y_val = np.concatenate([r["y"] for r in splits["val"]])
    x_test = np.concatenate([r["x"] for r in splits["test"]])
    y_test = np.concatenate([r["y"] for r in splits["test"]])
    offsets: dict[str, tuple[int, int]] = {}
    cursor = 0
    for rec in splits["test"]:
        offsets[rec["key"]] = (cursor, cursor + len(rec["y"]))
        cursor += len(rec["y"])
    if cursor != len(y_test):
        raise RuntimeError("Test record offsets do not align")

    config = {
        "schema": "same_cts_inputs_tabular_v2",
        "graph_root": str(args.graph_root.resolve()),
        "split_policy": "physical train/val/test directories, identical to graph dataset",
        "within_design_val_ratio": float(args.within_design_val_ratio),
        "within_design_val_seed": int(args.seed),
        "target": "target_route[:, 0], used only as label",
        "mask": "label_mask",
        "loss": {
            "name": "SmoothL1",
            "beta_ns": float(args.huber_beta_ns),
            "training_records_equal_weight": True,
        },
        "node_sources": ["g.ndata.feat:19 fields; cell_type_id as 906-bin path histogram; other fields mean/max/last"],
        "edge_sources": ["g.edata.feat:15 fields; matched consecutive path edges mean/max"],
        "path_geometry": list(CTS_GEOMETRY_FEATURES),
        "design_scalars": list(CTS_DESIGN_FEATURES),
        "feature_dim": int(x_train.shape[1]),
        "train_records": [r["key"] for r in splits["train"]],
        "val_records": [r["key"] for r in splits["val"]],
        "test_records": [r["key"] for r in splits["test"]],
        "normalization_fit": "node/edge: training graphs; geometry: labeled training paths; design scalars: training records",
        "normalization": {
            "geometry_mean": geom_mean.tolist(),
            "geometry_std": geom_std.tolist(),
            "design_mean": design_mean.tolist(),
            "design_std": design_std.tolist(),
        },
        "args": vars(args) | {"graph_root": str(args.graph_root), "output_dir": str(out_dir)},
    }
    (out_dir / "configs" / "experiment.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    np.savez_compressed(out_dir / "configs" / "feature_normalizer.npz",
                        node_mean=node_mean, node_std=node_std, edge_mean=edge_mean, edge_std=edge_std,
                        geom_mean=geom_mean, geom_std=geom_std,
                        design_mean=design_mean, design_std=design_std)

    model_names = [x.strip().lower() for x in args.models.split(",") if x.strip()]
    pred_by_model: dict[str, np.ndarray] = {}
    train_weights = record_balanced_weights(splits["train"])
    if "ridge" in model_names:
        pred_by_model["ridge_smoothl1"] = predict_linear_huber(
            x_train, y_train, train_weights, x_val, splits["val"], x_test, args, out_dir
        ).astype(np.float32)
    if "xgboost" in model_names:
        pred_by_model["xgboost_smoothl1"] = predict_xgboost(
            x_train, y_train, train_weights, x_val, splits["val"], x_test, args, out_dir
        )
    if "mlp" in model_names:
        pred_by_model["mlp"] = predict_mlp(
            x_train, y_train, x_val, y_val, x_test, args, out_dir,
            train_weights, splits["val"],
        ).astype(np.float32)

    summary: dict[str, Any] = {
        "feature_dim": int(x_train.shape[1]),
        "input_contract": "same CTS node/edge/path-geometry/design feature sources as M0/M1",
        "loss": {"name": "SmoothL1", "beta_ns": float(args.huber_beta_ns)},
        "train_record_weighting": "equal total weight per (design,strategy)",
        "model_selection": "validation macro-average per-record MAE",
        "metrics_protocol": "macro-average per-(design,strategy) R2/MAE/RMSE/MAPE/normalized_MAE",
        "critical_path_metrics": {
            "top5_delay_recall": "top 5% route-delay ranking overlap; delay-based proxy only",
            "slack_critical_delay_mae": "route-delay MAE on bottom-5%-slack samples, computed separately for max/min paths; slack is evaluation-only",
        },
        "models": {},
    }
    rows: list[dict[str, Any]] = []
    for name, prediction in pred_by_model.items():
        per_records = []
        for rec in splits["test"]:
            lo, hi = offsets[rec["key"]]
            per_records.append({
                "key": rec["key"],
                "design": rec["design"],
                "strategy": rec["strategy"],
                "y": rec["y"],
                "pred": prediction[lo:hi],
                "slack": rec["slack"],
                "slack_mask": rec["slack_mask"],
                "path_indices": rec["path_indices"],
                "clock_period_ns": rec["clock_period_ns"],
            })
        metrics = macro_metrics(per_records)
        summary["models"][name] = {
            **metrics,
            "test_paths": int(len(y_test)),
            "per_record": {
                row["key"]: {
                    **macro_metrics([row]),
                    "n": int(len(row["y"])),
                } for row in per_records
            },
        }
        for record in summary["models"][name]["per_record"].items():
            key, result = record
            rows.append({"model": name, "record": key, **result})
    (out_dir / "results" / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (out_dir / "results" / "per_record_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "model", "record", "n", "r2", "mae", "rmse", "MAPE_percent",
                "normalized_MAE", "top5_delay_recall", "slack_critical_top5_recall",
                "slack_critical_delay_mae",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary["models"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
