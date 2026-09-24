#!/usr/bin/env python
"""Train CTS-to-route GNN variants without discovering or loading test data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import dgl
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "experiments" / "cts_to_route" / "M0_pool" / "code"))

from cts_route_common import (  # noqa: E402
    CTS_CELL_TYPE_VOCAB,
    CTS_DESIGN_FEATURES,
    CTS_EDGE_DIM,
    CTS_EDGE_FEAT_KEY,
    CTS_GEOMETRY_FEATURES,
    CTS_NODE_DIM,
    CTS_NODE_FEAT_KEY,
    CTS_SCHEMA_MODE,
    CtsGraphEncoder,
    discover_cts_graphs,
    read_cts_design_scalars,
    validate_cts_graph,
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--graph-root", type=Path, required=True)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--variant", choices=("pool", "path_edge", "pathseq"), default="path_edge")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--within-design-val-ratio", type=float, default=0.2)
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--patience", type=int, default=15)
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--hidden-dim", type=int, default=128)
    ap.add_argument("--cell-emb-dim", type=int, default=16)
    ap.add_argument("--gnn-layers", type=int, default=2)
    ap.add_argument("--dropout", type=float, default=0.15)
    ap.add_argument("--fusion-dim", type=int, default=256)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--huber-beta-ns", type=float, default=0.02)
    ap.add_argument("--target-scale", choices=("ns", "clock"), default="ns")
    ap.add_argument("--relative-loss-weight", type=float, default=0.0,
                    help="train-only relative SmoothL1 weighting; 0 disables it")
    ap.add_argument("--relative-floor-ns", type=float, default=0.02,
                    help="denominator floor for relative weighting in ns")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return ap.parse_args()


def split_indices(indices: np.ndarray, ratio: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    if ratio <= 0 or indices.size < 2:
        return indices, np.zeros(0, dtype=np.int64)
    order = np.random.default_rng(seed).permutation(indices.size)
    n_val = max(1, min(int(round(indices.size * ratio)), indices.size - 1))
    return np.sort(indices[order[n_val:]]), np.sort(indices[order[:n_val]])


def metrics_by_record(rows: list[dict[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    values: dict[str, list[float]] = {
        "mae_ns": [], "mape_percent": [], "normalized_mae": [], "r2": []
    }
    detail = []
    for row in rows:
        y, pred = row["y"], row["pred"]
        err = pred - y
        mae = float(np.abs(err).mean())
        var = float(np.square(y - y.mean()).sum())
        r2 = 1.0 - float(np.square(err).sum()) / var if var > 1e-15 else float("nan")
        item = {
            "record": row["key"], "design": row["design"], "n": int(len(y)),
            "mae_ns": mae,
            "mape_percent": float((np.abs(err) / np.maximum(np.abs(y), 1e-6)).mean() * 100),
            "normalized_mae": mae / max(row["period"], 1e-9),
            "r2": r2,
        }
        detail.append(item)
        for key in ("mae_ns", "mape_percent", "normalized_mae"):
            values[key].append(item[key])
        if math.isfinite(r2):
            values["r2"].append(r2)
    return (
        {key: float(np.mean(v)) if v else float("nan") for key, v in values.items()},
        detail,
    )


def edge_lookup(g: dgl.DGLGraph) -> dict[tuple[int, int], list[int]]:
    src, dst = g.edges(order="eid")
    result: dict[tuple[int, int], list[int]] = {}
    for eid, (u, v) in enumerate(zip(src.cpu().tolist(), dst.cpu().tolist())):
        result.setdefault((u, v), []).append(eid)
    return result


def load_record(rec: Any) -> dict[str, Any]:
    if rec.split == "test":
        raise RuntimeError("Leakage guard: refusing to load test record")
    g = dgl.load_graphs(rec.graph_path)[0][0]
    validate_cts_graph(g, rec.graph_path)
    tp = torch.load(rec.timing_paths_path, map_location="cpu", weights_only=False)
    if tp.get("schema") != CTS_SCHEMA_MODE:
        raise RuntimeError(f"{rec.key}: schema mismatch: {tp.get('schema')}")
    target = tp["target_route"][:, 0].float().numpy()
    valid = np.flatnonzero(tp["label_mask"].float().reshape(-1).numpy() > 0.5)
    design = read_cts_design_scalars(Path(rec.graph_path).parent / "graph_meta.json").astype(np.float32)
    node_raw = g.ndata[CTS_NODE_FEAT_KEY].float().numpy()
    edge_raw = g.edata[CTS_EDGE_FEAT_KEY].float().numpy()
    path_ids = tp["node_ids_flat"].long().numpy()
    ptr = tp["node_ptr"].long().numpy()
    raw_geometry = np.zeros((len(target), 3), dtype=np.float32)
    for p in range(len(target)):
        ids = path_ids[ptr[p]:ptr[p + 1]]
        ids = ids[(ids >= 0) & (ids < node_raw.shape[0])]
        if ids.size:
            xy = node_raw[ids, :2]
            raw_geometry[p, 1] = ids.size
            raw_geometry[p, 0] = np.abs(np.diff(xy, axis=0)).sum()
            raw_geometry[p, 2] = raw_geometry[p, 0] / ids.size
    return {
        "record_obj": rec, "key": rec.key, "design": rec.design, "split": rec.split,
        "graph": g, "node_raw": node_raw, "edge_raw": edge_raw,
        "path_ids": path_ids, "ptr": ptr, "y_all": target, "valid": valid,
        "geometry_raw": raw_geometry, "design_raw": design,
        "period": float(design[0]), "edge_lookup": edge_lookup(g),
    }


def fit_stats(
    train: list[dict[str, Any]],
    train_indices: dict[str, np.ndarray],
    design_records: list[dict[str, Any]],
) -> dict[str, np.ndarray]:
    node = np.concatenate([r["node_raw"] for r in train], axis=0)
    edge = np.concatenate([r["edge_raw"] for r in train], axis=0)
    nm, ns = node.mean(0), node.std(0)
    em, es = edge.mean(0), edge.std(0)
    for i in (2, 4, 5, 6, 8, 9, 10, 11, 16, 17, 18):
        nm[i], ns[i] = 0.0, 1.0
    for i in (0, 1):
        em[i], es[i] = 0.0, 1.0
    geom = np.concatenate([
        r["geometry_raw"][train_indices[r["key"]]] for r in train
    ])
    designs = np.stack([r["design_raw"] for r in design_records])
    return {
        "node_mean": nm.astype(np.float32), "node_std": np.maximum(ns, 1e-6).astype(np.float32),
        "edge_mean": em.astype(np.float32), "edge_std": np.maximum(es, 1e-6).astype(np.float32),
        "geom_mean": geom.mean(0).astype(np.float32),
        "geom_std": np.maximum(geom.std(0), 1e-6).astype(np.float32),
        "design_mean": designs.mean(0).astype(np.float32),
        "design_std": np.maximum(designs.std(0), 1e-6).astype(np.float32),
    }


def prepare_features(rows: list[dict[str, Any]], stats: dict[str, np.ndarray]) -> None:
    for row in rows:
        n = (row["node_raw"] - stats["node_mean"]) / stats["node_std"]
        e = (row["edge_raw"] - stats["edge_mean"]) / stats["edge_std"]
        row["node_norm"] = torch.from_numpy(n.astype(np.float32))
        node_cont = np.delete(n, 2, axis=1)
        row["edge_norm"] = torch.from_numpy(e.astype(np.float32))
        row["targets"] = row["y_all"]
        row["geometry"] = (row["geometry_raw"] - stats["geom_mean"]) / stats["geom_std"]
        row["design_features"] = (
            row["design_raw"] - stats["design_mean"]
        ) / stats["design_std"]
        row["node_sequences"] = []
        row["edge_sequences"] = []
        row["histograms"] = []
        row["node_pools"] = []
        row["edge_pools"] = []
        cats = row["node_raw"][:, 2].astype(np.int64)
        for p in row["valid"]:
            ids = row["path_ids"][row["ptr"][p]:row["ptr"][p + 1]]
            ids = ids[(ids >= 0) & (ids < row["node_raw"].shape[0])]
            eids: list[int] = []
            for u, v in zip(ids[:-1], ids[1:]):
                eids.extend(row["edge_lookup"].get((int(u), int(v)), []))
            row["node_sequences"].append(ids)
            row["edge_sequences"].append(np.asarray(eids, dtype=np.int64))
            if ids.size:
                cont = node_cont[ids]
                row["node_pools"].append(np.concatenate([cont.mean(0), cont.max(0), cont[-1]]))
                hist = np.bincount(np.clip(cats[ids], 0, CTS_CELL_TYPE_VOCAB - 1),
                                   minlength=CTS_CELL_TYPE_VOCAB).astype(np.float32)
                row["histograms"].append(hist / ids.size)
            else:
                row["node_pools"].append(np.zeros((CTS_NODE_DIM * 3,), np.float32))
                row["histograms"].append(np.zeros((CTS_CELL_TYPE_VOCAB,), np.float32))
            if eids:
                ev = e[eids]
                row["edge_pools"].append(np.concatenate([ev.mean(0), ev.max(0)]))
            else:
                row["edge_pools"].append(np.zeros((CTS_EDGE_DIM * 2,), np.float32))
        row["path_to_local"] = {int(p): i for i, p in enumerate(row["valid"])}


class PathDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], indices: dict[str, np.ndarray]):
        self.rows = rows
        self.entries = [
            (ri, int(p))
            for ri, row in enumerate(rows)
            for p in indices.get(row["key"], np.zeros(0, dtype=np.int64))
        ]

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, i: int) -> dict[str, Any]:
        ri, p = self.entries[i]
        row = self.rows[ri]
        local = row["path_to_local"][p]
        return {
            "record": ri, "path": p,
            "node_ids": row["node_sequences"][local],
            "edge_ids": row["edge_sequences"][local],
            "x_node": row["node_pools"][local],
            "x_hist": row["histograms"][local],
            "x_edge": row["edge_pools"][local],
            "level": np.concatenate([row["geometry"][p], row["design_features"]]),
            "y": row["targets"][p],
        }


def collate_paths(items: list[dict[str, Any]]) -> dict[str, Any]:
    def pad_seq(key: str) -> tuple[torch.Tensor, torch.Tensor]:
        seqs = [v[key] for v in items]
        width = max(1, max(map(len, seqs)))
        out = torch.zeros((len(seqs), width), dtype=torch.long)
        mask = torch.zeros((len(seqs), width), dtype=torch.bool)
        for i, seq in enumerate(seqs):
            if len(seq):
                out[i, :len(seq)] = torch.from_numpy(seq.astype(np.int64))
                mask[i, :len(seq)] = True
        return out, mask
    nodes, node_mask = pad_seq("node_ids")
    edges, edge_mask = pad_seq("edge_ids")
    return {
        "record": torch.tensor([v["record"] for v in items]),
        "path": torch.tensor([v["path"] for v in items]),
        "node_ids": nodes, "node_mask": node_mask, "edge_ids": edges, "edge_mask": edge_mask,
        "x_node": torch.from_numpy(np.stack([v["x_node"] for v in items]).astype(np.float32)),
        "x_hist": torch.from_numpy(np.stack([v["x_hist"] for v in items]).astype(np.float32)),
        "x_edge": torch.from_numpy(np.stack([v["x_edge"] for v in items]).astype(np.float32)),
        "level": torch.from_numpy(np.stack([v["level"] for v in items]).astype(np.float32)),
        "y": torch.tensor([v["y"] for v in items], dtype=torch.float32),
    }


class GNNModel(nn.Module):
    def __init__(self, variant: str, stats: dict[str, np.ndarray], hidden: int,
                 cell_emb: int, layers: int, dropout: float, fusion: int):
        super().__init__()
        self.variant = variant
        self.encoder = CtsGraphEncoder(
            node_dim=CTS_NODE_DIM,
            edge_dim=CTS_EDGE_DIM,
            cell_emb_dim=cell_emb,
            hidden_dim=hidden,
            num_layers=layers,
            dropout=dropout,
        )
        self.node_proj = nn.Sequential(nn.Linear(hidden, hidden), nn.GELU(), nn.LayerNorm(hidden))
        self.edge_proj = nn.Sequential(nn.Linear(CTS_EDGE_DIM, hidden), nn.GELU())
        self.path_gru = nn.GRU(hidden, hidden // 2, batch_first=True, bidirectional=True)
        self.register_buffer("node_mean", torch.tensor(stats["node_mean"]))
        self.register_buffer("node_std", torch.tensor(stats["node_std"]))
        self.register_buffer("edge_mean", torch.tensor(stats["edge_mean"]))
        self.register_buffer("edge_std", torch.tensor(stats["edge_std"]))
        graph_width = hidden * 3
        feature_width = (
            (CTS_NODE_DIM - 1) * 3
            + CTS_CELL_TYPE_VOCAB
            + CTS_EDGE_DIM * 2
            + 3
            + len(CTS_DESIGN_FEATURES)
        )
        self.fusion = nn.Sequential(
            nn.Linear(graph_width + feature_width, fusion),
            nn.LayerNorm(fusion), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(fusion, fusion // 2), nn.GELU(), nn.Dropout(dropout / 2),
            nn.Linear(fusion // 2, 1),
        )
        self.linear_skip = nn.Linear(3 + len(CTS_DESIGN_FEATURES), 1)
        nn.init.zeros_(self.linear_skip.weight)
        nn.init.zeros_(self.linear_skip.bias)
        self.edge_hidden = hidden

    def forward(
        self,
        row: dict[str, Any],
        batch: dict[str, torch.Tensor],
        encoded_nodes: torch.Tensor | None = None,
        graph: dgl.DGLGraph | None = None,
    ) -> torch.Tensor:
        device = batch["y"].device
        g = graph or row.get("graph_device")
        if g is None or g.device != device:
            g = row["graph"].to(device)
            g.ndata[CTS_NODE_FEAT_KEY] = row["node_norm"].to(device)
            g.edata[CTS_EDGE_FEAT_KEY] = row["edge_norm"].to(device)
            row["graph_device"] = g
        node_h = encoded_nodes
        if node_h is None:
            node_h = self.encoder(g)
            if node_h.shape[-1] != self.edge_hidden:
                raise RuntimeError(
                    f"encoder hidden dimension {node_h.shape[-1]} != configured {self.edge_hidden}"
                )
            node_h = self.node_proj(node_h)
        node_seq = node_h[batch["node_ids"].clamp_min(0)]
        node_mask = batch["node_mask"].unsqueeze(-1)
        node_seq = node_seq * node_mask
        denom = node_mask.sum(1).clamp_min(1)
        mean = node_seq.sum(1) / denom
        maxv = node_seq.masked_fill(~node_mask, -1e4).max(1).values
        last_i = (batch["node_mask"].sum(1) - 1).clamp_min(0)
        last = node_seq[torch.arange(node_seq.shape[0], device=device), last_i]
        node_readout = torch.cat([mean, maxv, last], dim=1)

        if self.variant == "pathseq":
            seq, _ = self.path_gru(node_seq)
            seq_mean = seq.sum(1) / denom
            seq_max = seq.masked_fill(~node_mask, -1e4).max(1).values
            seq_last = seq[torch.arange(seq.shape[0], device=device), last_i]
            graph_repr = torch.cat([seq_mean, seq_max, seq_last], dim=1)
        elif self.variant == "path_edge":
            edge_h = self.edge_proj(g.edata[CTS_EDGE_FEAT_KEY])[
                batch["edge_ids"].clamp_min(0)
            ]
            edge_mask = batch["edge_mask"].unsqueeze(-1)
            edge_h = edge_h * edge_mask
            edge_denom = edge_mask.sum(1).clamp_min(1)
            edge_mean = edge_h.sum(1) / edge_denom
            edge_max = edge_h.masked_fill(~edge_mask, -1e4).max(1).values
            edge_max = torch.where(edge_mask.any(1), edge_max, torch.zeros_like(edge_max))
            # Keep graph-derived node states and add explicit ordered path-edge context.
            graph_repr = node_readout + torch.cat(
                [edge_mean, edge_max, torch.zeros_like(edge_mean)], dim=1
            )
        else:
            graph_repr = node_readout
        features = torch.cat(
            [graph_repr, batch["x_node"], batch["x_hist"], batch["x_edge"], batch["level"]],
            dim=1,
        )
        if features.shape[1] != self.fusion[0].in_features:
            raise RuntimeError(
                f"{self.variant} fusion width {features.shape[1]} != "
                f"configured {self.fusion[0].in_features}"
            )
        return self.fusion(features).squeeze(1) + self.linear_skip(batch["level"]).squeeze(1)


def run_epoch(
    model: GNNModel,
    loader: DataLoader,
    rows: list[dict[str, Any]],
    optimizer: torch.optim.Optimizer | None,
    args: argparse.Namespace,
    device: torch.device,
) -> tuple[float, list[dict[str, Any]]]:
    is_train = optimizer is not None
    model.train(is_train)
    preds: dict[str, list[tuple[int, float]]] = {r["key"]: [] for r in rows}
    losses = []
    # Do one graph-encoder pass per record and accumulate all its path batches
    # before stepping; this keeps the expensive graph pass out of the inner loop.
    batches_by_record: dict[int, list[dict[str, Any]]] = {}
    for batch_cpu in loader:
        record_idx = int(batch_cpu["record"][0])
        if not torch.all(batch_cpu["record"] == record_idx):
            raise RuntimeError("Internal batch crossed graph records")
        batches_by_record.setdefault(record_idx, []).append(batch_cpu)
    for record_idx, record_batches in batches_by_record.items():
        row = rows[record_idx]
        graph = row["graph"].to(device)
        graph.ndata[CTS_NODE_FEAT_KEY] = row["node_norm"].to(device)
        graph.edata[CTS_EDGE_FEAT_KEY] = row["edge_norm"].to(device)
        encoded_nodes = model.node_proj(model.encoder(graph))
        if encoded_nodes.shape[-1] != model.edge_hidden:
            raise RuntimeError("Unexpected encoder hidden width")
        if is_train:
            optimizer.zero_grad(set_to_none=True)
        batch_losses = []
        for batch_cpu in record_batches:
            batch = {
                k: v.to(device) if torch.is_tensor(v) else v
                for k, v in batch_cpu.items()
            }
            y = batch["y"]
            scale = row["period"] if args.target_scale == "clock" else 1.0
            target = y / max(scale, 1e-9)
            with torch.set_grad_enabled(is_train):
                pred = model(
                    row, batch, encoded_nodes=encoded_nodes, graph=graph
                )
                beta = args.huber_beta_ns / max(scale, 1e-9)
                element = F.smooth_l1_loss(pred, target, beta=beta, reduction="none")
                if args.relative_loss_weight > 0:
                    denom = torch.clamp(batch["y"].abs(), min=args.relative_floor_ns)
                    weights = 1.0 + args.relative_loss_weight / denom
                    weights = weights / weights.mean().clamp_min(1e-6)
                    element = element * weights
                loss = element.mean()
                if is_train:
                    (loss / len(record_batches)).backward(
                        retain_graph=batch_cpu is not record_batches[-1]
                    )
            batch_losses.append(float(loss.detach().cpu()))
            out_ns = pred.detach().cpu().numpy() * scale
            for p, yp in zip(batch["path"].cpu().tolist(), out_ns.tolist()):
                preds[row["key"]].append((p, yp))
        losses.append(float(np.mean(batch_losses)))
        if is_train:
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
    scored = []
    for row in rows:
        pairs = preds[row["key"]]
        if not pairs:
            continue
        pred_map = dict(pairs)
        indices = np.asarray(sorted(pred_map), dtype=np.int64)
        scored.append({
            "key": row["key"], "design": row["design"],
            "period": row["period"], "y": row["y_all"][indices],
            "pred": np.asarray([pred_map[int(i)] for i in indices], dtype=np.float32),
        })
    return float(np.mean(losses)) if losses else float("nan"), scored


class RecordBatchSampler:
    def __init__(self, dataset: PathDataset, batch_size: int, shuffle: bool):
        self.dataset, self.batch_size, self.shuffle = dataset, batch_size, shuffle
        self.groups: dict[int, list[int]] = {}
        for i, (ri, _) in enumerate(dataset.entries):
            self.groups.setdefault(ri, []).append(i)

    def __iter__(self):
        groups = list(self.groups.values())
        if self.shuffle:
            order = torch.randperm(len(groups)).tolist()
            groups = [groups[i] for i in order]
        for group in groups:
            if self.shuffle:
                order = torch.randperm(len(group)).tolist()
                group = [group[i] for i in order]
            for start in range(0, len(group), self.batch_size):
                yield group[start:start + self.batch_size]

    def __len__(self) -> int:
        return sum(math.ceil(len(g) / self.batch_size) for g in self.groups.values())


def main() -> int:
    args = parse_args()
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    out = args.output_dir
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"Refusing non-empty run directory: {out}")
    for sub in ("configs", "logs", "metrics", "weights"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # Test is not even enumerated in the optimizer process.
    records = discover_cts_graphs(args.graph_root, ["train", "val"])
    if any(r.split == "test" for r in records):
        raise RuntimeError("Leakage guard: test record discovered")
    train_rows_all = [load_record(r) for r in records if r.split == "train"]
    val_rows = [load_record(r) for r in records if r.split == "val"]
    train_indices: dict[str, np.ndarray] = {}
    val_indices: dict[str, np.ndarray] = {}
    train_rows = []
    for row in train_rows_all:
        tr, va = split_indices(row["valid"], args.within_design_val_ratio, args.seed)
        row["valid"] = tr
        train_indices[row["key"]] = tr
        if tr.size:
            train_rows.append(row)
        if va.size:
            held = dict(row)
            held["valid"] = va
            val_rows.append(held)
    for row in val_rows:
        val_indices.setdefault(row["key"], row["valid"])
    if not train_rows or not val_rows:
        raise RuntimeError("Train or validation partition is empty")

    stats = fit_stats(train_rows, train_indices, train_rows_all)
    prepare_features(train_rows + val_rows, stats)
    np.savez_compressed(out / "configs" / "train_only_normalizer.npz", **stats)
    config = {
        "variant": args.variant, "graph_root": str(args.graph_root),
        "loaded_splits": ["train", "val"], "test_enumerated": False,
        "test_labels_loaded": False,
        "train_records": [r["key"] for r in train_rows],
        "validation_records": [r["key"] for r in val_rows],
        "normalizer_fit_records": [r["key"] for r in train_rows],
        "target": "Route delay target_route[:,0], label_mask only",
        "inputs": ["CTS node features", "CTS directed edge features",
                   "CTS node_ids_flat/node_ptr", "CTS path geometry", "CTS design scalars"],
        "forbidden": ["Route features", "slack", "AT/RAT", "baseline_cts_grt", "test split"],
        "loss": {"name": "SmoothL1", "beta_ns": args.huber_beta_ns},
        "relative_loss_weight": args.relative_loss_weight,
        "relative_floor_ns": args.relative_floor_ns,
        "selection": "validation record-macro MAE",
        "args": {**vars(args), "output_dir": str(out)},
    }
    (out / "configs" / "experiment.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    train_ds = PathDataset(train_rows, train_indices)
    val_ds = PathDataset(val_rows, val_indices)
    train_loader = DataLoader(
        train_ds, batch_sampler=RecordBatchSampler(train_ds, args.batch_size, True),
        collate_fn=collate_paths,
    )
    val_loader = DataLoader(
        val_ds, batch_sampler=RecordBatchSampler(val_ds, args.batch_size, False),
        collate_fn=collate_paths,
    )
    device = torch.device(args.device)
    model = GNNModel(args.variant, stats, args.hidden_dim, args.cell_emb_dim,
                     args.gnn_layers, args.dropout, args.fusion_dim).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    best_mae, best_epoch, wait = float("inf"), 0, 0
    started = time.time()
    with (out / "logs" / "training.jsonl").open("w", encoding="utf-8") as log:
        for epoch in range(1, args.epochs + 1):
            train_loss, _ = run_epoch(model, train_loader, train_rows, optimizer, args, device)
            _, val_scored = run_epoch(model, val_loader, val_rows, None, args, device)
            val_metrics, val_detail = metrics_by_record(val_scored)
            entry = {
                "epoch": epoch, "train_loss": train_loss, "validation": val_metrics,
                "elapsed_seconds": time.time() - started,
            }
            line = json.dumps(entry)
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()
            if val_metrics["mae_ns"] < best_mae:
                best_mae, best_epoch, wait = val_metrics["mae_ns"], epoch, 0
                torch.save({
                    "epoch": epoch, "model_state_dict": model.state_dict(),
                    "validation_metrics": val_metrics, "config": config,
                }, out / "weights" / "best.pt")
                with (out / "metrics" / "validation_per_record.csv").open(
                    "w", newline="", encoding="utf-8"
                ) as f:
                    writer = csv.DictWriter(f, fieldnames=list(val_detail[0]))
                    writer.writeheader()
                    writer.writerows(val_detail)
            else:
                wait += 1
                if wait >= args.patience:
                    break
    summary = {
        "best_epoch": best_epoch, "best_validation_macro_mae_ns": best_mae,
        "test_accessed": False, "test_metrics": None,
    }
    (out / "metrics" / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
