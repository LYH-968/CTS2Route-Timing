#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTS → route 路径延迟预测训练器（`cts_net_v3` schema，方案 B）。

对应 `CTS_TO_ROUTE_HANDOFF.md` §7 的 4 条改造要点：

1. **图发现**：`graphs_cts_v3/<split>/<design>/<strategy>/<design>__<strategy>__cts.dgl`，
   schema 检查改为 `cts_net_v3`（node 8D / edge 8D）。
2. **Normalizer**：在 train split 上重算，逐 dim z-score；categorical/binary 维度
   （cell_type_id / is_port / is_input / is_endpoint / is_net_edge / net_use）不归一化。
3. **直接回归**：`y = timing_paths["target_route"][:, 0]`（route 路径延迟 ns），
   `mask = label_mask`。`baseline_cts_grt` **只在 `--ablation-grt` 下被读**，作为
   额外输入特征做对照。slack 用独立的 `slack_mask`，默认关（`--slack-weight 0`）。
3b. **平凡基线对照**：每轮评估都并列报「常数基线」和「路径几何线性回归」的 R²/MAE；
   `gcd`/`ethmac` 单列（§5.11/§5.16/§5.18，它们无信号，混进汇总会失真）。
4. **cell_type_id** 走 `nn.Embedding`（词表 906）再拼连续特征。

为什么不直接改 `train_timing_path_unified_29_7.py`：那个文件是旧 ASAP7 cross-stage
任务的产物，`inference_timing_path_unified_29_7.py` 仍在 import 它的
`UnifiedTimingPathModel` / `PathSemanticStore`，且它的整条数据路径（place/route 图对、
patch CNN、LLM 语义分支、对 place baseline 的残差回归）在新 schema 下全部作废。
就地改会同时打断旧任务、又留下大量死代码。新任务开新文件，复用
`unified_29_7_common.CtsGraphEncoder` / `EdgeGATLayer` / `GraphNormalizer`。

用法（用 pytorch conda env）::

    PY="D:/Anaconda3/envs/pytorch/python.exe"

    # 冒烟：gcd + aes，两两都在 train split，用 20% 路径做 within-design 验证
    $PY train_cts_route_v2.py --graph-root E:/CTS/smoke_v2 --epochs 20 \
        --within-design-val-ratio 0.2 --output-dir E:/CTS/outputs/smoke_cts_route_v2

    # 全量
    $PY train_cts_route_v2.py --graph-root E:/CTS/dataset/asap7/graphs_cts_v3 --epochs 100

    # GRT 消融对照（默认训练路径不读 baseline_cts_grt）
    $PY train_cts_route_v2.py --graph-root ... --ablation-grt --output-dir .../ablation_grt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import dgl
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cts_route_common import (  # noqa: E402
    CTS_DESIGN_FEATURES,
    CTS_EDGE_DIM,
    CTS_GEOMETRY_FEATURES,
    CTS_LOW_SIGNAL_DESIGNS,
    CTS_NODE_DIM,
    CTS_NORM_SKIP_EDGE_DIMS,
    CTS_NORM_SKIP_NODE_DIMS,
    CTS_SCHEMA_MODE,
    CtsGraphEncoder,
    compute_cts_normalizer,
    cts_path_geometry,
    cts_split_designs,
    discover_cts_graphs,
    load_normalizer,
    memory_stats,
    print_safe_mode,
    r2_score_np,
    read_cts_design_scalars,
    save_normalizer,
    set_seed,
    validate_cts_graph,
    write_json,
)

OUTPUT_DIRNAME = "outputs/train_cts_route_v2"


# ==================== Dataset ====================


class CtsPathDataset(Dataset):
    """一个 (design, strategy) 图上的若干条路径；每条路径 = 图上一串节点 + 一个标签。

    节点 id 里允许出现 -1（builder 没能把 wire_path 的点映射回图节点），这些位置
    `path_node_mask=0`，不参与池化。
    """

    def __init__(
        self,
        timing_paths: Dict[str, Any],
        geometry: np.ndarray,
        path_indices: Sequence[int],
        include_grt_baseline: bool = False,
        design_scalars: Optional[np.ndarray] = None,
    ):
        self.node_ids_flat = timing_paths["node_ids_flat"].long()
        self.node_ptr = timing_paths["node_ptr"].long()
        self.target_route = timing_paths["target_route"].float()
        self.label_mask = timing_paths["label_mask"].float().reshape(-1)
        self.slack_mask = timing_paths["slack_mask"].float().reshape(-1)
        # §7.3：baseline_cts_grt 只在 --ablation-grt 打开时才被读到。
        self.baseline_grt = (
            timing_paths["baseline_cts_grt"].float().reshape(-1) if include_grt_baseline else None
        )
        self.geometry = torch.as_tensor(geometry, dtype=torch.float32)
        self.design_scalars = (
            torch.as_tensor(design_scalars, dtype=torch.float32)
            if design_scalars is not None
            else torch.zeros(len(CTS_DESIGN_FEATURES))
        )
        self.path_indices = [int(i) for i in path_indices]
        self.include_grt_baseline = bool(include_grt_baseline)

    def __len__(self) -> int:
        return len(self.path_indices)

    def __getitem__(self, i: int) -> Dict[str, Any]:
        p = self.path_indices[i]
        s, e = int(self.node_ptr[p]), int(self.node_ptr[p + 1])
        ids = self.node_ids_flat[s:e]
        mask = (ids >= 0).float()
        item = {
            "path_node_ids": ids.clamp_min(0),
            "path_node_mask": mask,
            "path_index": p,
            "target_delay": self.target_route[p, 0],
            "target_slack": self.target_route[p, 1],
            "label_mask": self.label_mask[p],
            "slack_mask": self.slack_mask[p],
            "geometry": self.geometry[p],
            "design_scalars": self.design_scalars,
        }
        if self.include_grt_baseline:
            item["baseline_grt"] = self.baseline_grt[p]
        return item


def collate_cts_paths(batch: List[Dict[str, Any]]) -> Dict[str, torch.Tensor]:
    max_len = max(int(b["path_node_ids"].numel()) for b in batch)
    n = len(batch)
    ids = torch.zeros((n, max_len), dtype=torch.long)
    mask = torch.zeros((n, max_len), dtype=torch.float32)
    for i, b in enumerate(batch):
        m = int(b["path_node_ids"].numel())
        if m:
            ids[i, :m] = b["path_node_ids"]
            mask[i, :m] = b["path_node_mask"]
    out = {
        "path_node_ids": ids,
        "path_node_mask": mask,
        "path_index": torch.tensor([b["path_index"] for b in batch], dtype=torch.long),
        "target_delay": torch.stack([b["target_delay"] for b in batch]),
        "target_slack": torch.stack([b["target_slack"] for b in batch]),
        "label_mask": torch.stack([b["label_mask"] for b in batch]),
        "slack_mask": torch.stack([b["slack_mask"] for b in batch]),
        "geometry": torch.stack([b["geometry"] for b in batch]),
        "design_scalars": torch.stack([b["design_scalars"] for b in batch]),
    }
    if "baseline_grt" in batch[0]:
        out["baseline_grt"] = torch.stack([b["baseline_grt"] for b in batch])
    return out


# ==================== 模型 ====================


class CtsTimingPathModel(nn.Module):
    """图编码 → 路径池化（mean/max/尾节点）+ 几何 → 直接回归 route 路径延迟。

    注意这里 **没有** 「baseline + delta」的残差结构：`baseline_cts_grt` 在方案 B 下
    推理时不存在，只能作为 `--ablation-grt` 的额外输入特征出现。
    """

    def __init__(self, args, geom_mean: np.ndarray, geom_std: np.ndarray,
                 design_mean: Optional[np.ndarray] = None, design_std: Optional[np.ndarray] = None):
        super().__init__()
        self.use_geometry = not bool(args.disable_geometry)
        # §5.20：design 级电平输入。默认开；--disable-design-features 用于消融复现「没有
        # 电平标度时会怎样」。
        self.use_design_features = not bool(args.disable_design_features)
        self.use_grt_baseline = bool(args.ablation_grt)
        self.encoder = CtsGraphEncoder(
            node_dim=CTS_NODE_DIM,
            edge_dim=CTS_EDGE_DIM,
            cell_emb_dim=int(args.cell_emb_dim),
            hidden_dim=int(args.hidden_dim),
            num_layers=int(args.gnn_layers),
            dropout=float(args.dropout),
        )
        h = int(args.hidden_dim)
        self.model_variant = str(getattr(args, "model_variant", "pool"))
        if self.model_variant not in ("pool", "pathseq"):
            raise ValueError(f"unknown model_variant={self.model_variant}")
        if self.model_variant == "pathseq":
            self.path_gru = nn.GRU(h, int(args.path_gru_hidden), batch_first=True, bidirectional=True)
            self.path_attn = nn.Linear(int(args.path_gru_hidden) * 2, 1)
            path_dim = int(args.path_gru_hidden) * 2 * 3
        else:
            path_dim = h * 3
        parts = [path_dim]
        # 池化分支（mean/max/尾节点，3h 维）整体做 LayerNorm **再** 拼几何/电平标量。
        # 不加这一步时，384 个 GNN 维度按默认 init 各自贡献 ~1/√391 的权重，噪声合成的
        # std 约是 7 个有效维度（几何 3 + 电平 4）的 20 倍 —— 实测后果是模型预测方差被压平：
        # val MAE 与 4 系数几何线性基线持平，R² 却差 0.1~0.9（held-out 上甚至为负）。
        # LayerNorm 把池化分支的逐维尺度压到 1/√(3h)，与几何/电平维度可比。
        self.pool_norm = nn.LayerNorm(path_dim)
        if self.use_geometry:
            parts.append(len(CTS_GEOMETRY_FEATURES))
            self.register_buffer("geom_mean", torch.as_tensor(geom_mean, dtype=torch.float32))
            self.register_buffer("geom_std", torch.as_tensor(np.maximum(geom_std, 1e-6), dtype=torch.float32))
        if self.use_design_features:
            parts.append(len(CTS_DESIGN_FEATURES))
            d_mean = design_mean if design_mean is not None else np.zeros(len(CTS_DESIGN_FEATURES))
            d_std = design_std if design_std is not None else np.ones(len(CTS_DESIGN_FEATURES))
            self.register_buffer("design_mean", torch.as_tensor(d_mean, dtype=torch.float32))
            self.register_buffer("design_std", torch.as_tensor(np.maximum(d_std, 1e-6), dtype=torch.float32))
        else:
            # 保持 buffer 存在（checkpoint 兼容），但 forward 里不用。
            self.register_buffer("design_mean", torch.zeros(len(CTS_DESIGN_FEATURES)))
            self.register_buffer("design_std", torch.ones(len(CTS_DESIGN_FEATURES)))
        if self.use_grt_baseline:
            parts.append(1)
        in_dim = sum(parts)
        self.in_dim = in_dim
        # 可部署特征块的维度（= 几何 + 电平标量 + 可选 GRT），供 --linear-skip 用
        self.level_dim = (
            (len(CTS_GEOMETRY_FEATURES) if self.use_geometry else 0)
            + (len(CTS_DESIGN_FEATURES) if self.use_design_features else 0)
            + (1 if self.use_grt_baseline else 0)
        )

        if args.head_type == "linear":
            self.head = nn.Linear(in_dim, 1)
        else:
            self.head = nn.Sequential(
                nn.Linear(in_dim, int(args.fusion_dim)),
                nn.GELU(),
                nn.Dropout(float(args.dropout)),
                nn.Linear(int(args.fusion_dim), max(int(args.fusion_dim) // 2, 1)),
                nn.GELU(),
                nn.Linear(max(int(args.fusion_dim) // 2, 1), 1),
            )
        # 可部署特征块（几何 ⊕ 电平标量，可选 GRT）上的**直接线性通路**（--linear-skip）。
        # 动机是实测的：纯 MLP 在这批数据上打不过「同特征集的 4 系数线性回归」——
        # val MAE 与之持平但 R² 差 0.1~0.9。原因是 3h 维池化分支的合成噪声按默认 init
        # 比 7 个有效维度大 ~20 倍（见 self.pool_norm 处注释），MLP 分不出该听谁。
        # 零初始化 → 起跑点与纯 MLP 完全一致，但这条通路的梯度是干净的 7 维最小二乘，
        # 收敛很快。⚠ 这一路会让「模型 vs 平凡基线」的对照变得平凡（模型天然 ≥ 该基线），
        # 所以**必须同时报 pure-MLP 那一路**，不能只报这一路（handoff §5.17）。
        self.level_head: Optional[nn.Linear] = None
        if bool(getattr(args, "linear_skip", False)) and self.level_dim > 0:
            self.level_head = nn.Linear(self.level_dim, 1)
            nn.init.zeros_(self.level_head.weight)
            nn.init.zeros_(self.level_head.bias)
        # 辅助 slack 头：只有 --slack-weight > 0 时才会被用到（其掩码 slack_mask 与
        # label_mask 不同源，绝不可互换）。
        self.head_slack = nn.Linear(in_dim, 1) if float(args.slack_weight) > 0 else None

    def build_path_features(
        self,
        node_emb: torch.Tensor,
        batch: Dict[str, torch.Tensor],
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """返回 (head 输入, 可部署特征块)。后者给 `--linear-skip` 用。"""
        ids = batch["path_node_ids"]
        mask = batch["path_node_mask"]
        h = node_emb[ids]                                   # [B, L, H]
        m = mask.unsqueeze(-1)
        if self.model_variant == "pathseq":
            seq, _ = self.path_gru(h)
            seq_mask = mask.unsqueeze(-1).bool()
            attn_logits = self.path_attn(seq).masked_fill(~seq_mask, -1e9)
            attn = torch.softmax(attn_logits, dim=1)
            attn_pool = (seq * attn).sum(dim=1)
            first_pool = seq[:, 0, :]
            last_idx = (mask.sum(dim=1).long() - 1).clamp_min(0)
            last_pool = seq[torch.arange(seq.shape[0], device=seq.device), last_idx]
            parts = [self.pool_norm(torch.cat([attn_pool, first_pool, last_pool], dim=1))]
        else:
            denom = m.sum(dim=1).clamp_min(1.0)
            mean_pool = (h * m).sum(dim=1) / denom
            neg_inf = torch.finfo(h.dtype).min
            max_pool = h.masked_fill(m == 0, neg_inf).max(dim=1).values
            max_pool = torch.where(torch.isfinite(max_pool), max_pool, torch.zeros_like(max_pool))
            last_idx = (mask.sum(dim=1).long() - 1).clamp_min(0)
            last_pool = h[torch.arange(h.shape[0], device=h.device), last_idx]
            parts = [self.pool_norm(torch.cat([mean_pool, max_pool, last_pool], dim=1))]
        level: List[torch.Tensor] = []
        if self.use_geometry:
            geom = (batch["geometry"].to(h.device) - self.geom_mean) / self.geom_std
            parts.append(geom)
            level.append(geom)
        if self.use_design_features:
            des = (batch["design_scalars"].to(h.device) - self.design_mean) / self.design_std
            parts.append(des)
            level.append(des)
        if self.use_grt_baseline:
            if "baseline_grt" not in batch:
                raise RuntimeError(
                    "model was built with --ablation-grt but the batch has no baseline_grt; "
                    "the dataset must be constructed with include_grt_baseline=True."
                )
            grt = batch["baseline_grt"].to(h.device).unsqueeze(-1)
            parts.append(grt)
            level.append(grt)
        return torch.cat(parts, dim=1), (torch.cat(level, dim=1) if level else parts[0][:, :0])

    def forward(self, node_emb: torch.Tensor, batch: Dict[str, torch.Tensor]) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        feats, level = self.build_path_features(node_emb, batch)
        pred = self.head(feats).squeeze(1)
        if self.level_head is not None:
            pred = pred + self.level_head(level).squeeze(1)
        pred_slack = self.head_slack(feats).squeeze(1) if self.head_slack is not None else None
        return pred, pred_slack


class MaskedDelayLoss(nn.Module):
    """掩码回归损失。`mask` 恒为 `label_mask`（主监督）或 `slack_mask`（辅助），两者不同源。"""

    def __init__(self, kind: str = "smooth_l1", beta: float = 0.02):
        super().__init__()
        self.kind = str(kind)
        self.beta = float(beta)

    def forward(self, pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        if self.kind == "mse":
            elem = (pred - target).pow(2)
        elif self.kind == "mae":
            elem = (pred - target).abs()
        else:
            elem = F.smooth_l1_loss(pred, target, beta=self.beta, reduction="none")
        mask = mask.reshape(-1).to(elem.dtype)
        return (elem * mask).sum() / mask.sum().clamp_min(1.0)


# ==================== 数据资产加载 ====================


def load_cts_assets(rec, args, normalizer) -> Optional[Dict[str, Any]]:
    """读图 + 标签 + 路径几何量。**GEOMETRY 用归一化前的原始坐标算。**"""
    graphs, _ = dgl.load_graphs(str(rec.graph_path))
    g = graphs[0]
    validate_cts_graph(g, rec.graph_path)
    raw_xy = g.ndata["feat"][:, :2].detach().clone()
    if normalizer is not None:
        g = normalizer.apply(g)
    timing_paths = torch.load(rec.timing_paths_path, map_location="cpu", weights_only=False)
    if str(timing_paths.get("schema", "")) != CTS_SCHEMA_MODE:
        raise RuntimeError(
            f"{rec.timing_paths_path}: schema={timing_paths.get('schema')!r}, expected {CTS_SCHEMA_MODE!r}"
        )
    num_paths = int(timing_paths["label_mask"].shape[0])
    if num_paths == 0:
        return None
    if not args.ablation_grt:
        # §7.3：默认训练路径**不得引用** baseline_cts_grt。这里直接从内存里丢掉，
        # 让任何误用都在第一时间炸出来，而不是悄悄把 GRT 泄漏进推理路径。
        timing_paths.pop("baseline_cts_grt", None)
    geometry = cts_path_geometry(
        raw_xy, timing_paths["node_ptr"], timing_paths["node_ids_flat"], num_paths
    )
    # design 级电平标量（§5.20）：从 graph_meta.json 读，不是从标签文件读。
    design_scalars = read_cts_design_scalars(Path(rec.graph_path).parent / "graph_meta.json")
    return {
        "record": rec,
        "graph": g,
        "timing_paths": timing_paths,
        "geometry": geometry,
        "design_scalars": design_scalars,
        "clock_period_ns": float(design_scalars[0]) if design_scalars.size else None,
    }


def valid_path_indices(timing_paths: Dict[str, Any]) -> np.ndarray:
    """只保留 label_mask==1 的路径（label_mask==0 是 builder 的 fallback 占位）。"""
    lm = timing_paths["label_mask"].float().reshape(-1).numpy()
    return np.flatnonzero(lm > 0.5)


def split_path_indices(idx: np.ndarray, val_ratio: float, seed: int) -> Tuple[np.ndarray, np.ndarray]:
    if val_ratio <= 0.0 or idx.size < 2:
        return idx, np.zeros((0,), dtype=np.int64)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(idx.size)
    n_val = max(1, min(int(round(idx.size * float(val_ratio))), idx.size - 1))
    val_idx = np.sort(idx[perm[:n_val]])
    train_idx = np.sort(idx[perm[n_val:]])
    return train_idx, val_idx


def collect_geometry_stats(assets: Sequence[Dict[str, Any]], indices: Sequence[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    chunks = [a["geometry"][i] for a, i in zip(assets, indices) if i.size]
    if not chunks:
        return np.zeros(len(CTS_GEOMETRY_FEATURES)), np.ones(len(CTS_GEOMETRY_FEATURES))
    all_geom = np.concatenate(chunks, axis=0)
    return all_geom.mean(axis=0), np.maximum(all_geom.std(axis=0), 1e-6)


# ==================== 平凡基线（§7.3b / §5.17） ====================


def _lstsq_fit(geom: np.ndarray, y: np.ndarray) -> Optional[List[float]]:
    """3 特征 + 截距的闭式最小二乘（不引 sklearn 依赖）。"""
    if geom.shape[0] < 4:
        return None
    A = np.concatenate([geom, np.ones((geom.shape[0], 1))], axis=1)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    return [float(c) for c in coef]


def _lstsq_apply(geom: np.ndarray, coef: Sequence[float]) -> np.ndarray:
    A = np.concatenate([geom, np.ones((geom.shape[0], 1))], axis=1)
    return A @ np.asarray(coef, dtype=np.float64)


def fit_trivial_baselines(
    y_by_design: Dict[str, np.ndarray],
    geom_by_design: Dict[str, np.ndarray],
) -> Dict[str, Any]:
    """拟合平凡参照，**只用 train split**。

    主参照是 **per-design** 的（与 handoff §5.18 的口径一致）。这一点很关键：aes 的
    路径延迟量级 ~0.13 ns、gcd ~0.043 ns，两者差 3 倍。**单个 global 线性模型会被样本
    多的 design 主导**（aes 612 条 train 路径 vs gcd 70 条），在 gcd 上系统性高估 2.4 倍，
    而 gcd 的真实方差只有 0.007 ns —— R² 会被打到 -75，看起来像「几何特征无用」，
    实际只是跨 design 的绝对电平差没被建模。

    所以 global 版本也一并算、一并报，用来**显式暴露这个电平差**，而不是拿它当
    模型的主参照。
    """
    out: Dict[str, Any] = {"per_design": {}, "global": None}
    ys, gs = [], []
    for design, y in y_by_design.items():
        geom = geom_by_design.get(design)
        if y.size == 0 or geom is None or geom.shape[0] != y.shape[0]:
            continue
        out["per_design"][design] = {
            "const_value": float(y.mean()),
            "geom_coef": _lstsq_fit(geom, y),
        }
        ys.append(y)
        gs.append(geom)
    if ys:
        y_all = np.concatenate(ys)
        g_all = np.concatenate(gs, axis=0)
        out["global"] = {
            "const_value": float(y_all.mean()),
            "geom_coef": _lstsq_fit(g_all, y_all),
        }
    return out


def apply_trivial_baselines(fitted: Dict[str, Any], design: str, geom: np.ndarray) -> Dict[str, np.ndarray]:
    """给定 design 与路径几何，产出全部平凡基线预测。

    两套并列，用途不同：
    - `const` / `geom_linreg`：**per-design** 拟合（handoff §5.18 口径）。只有在 fit 集合里
      出现过的 design 才有 —— 也即 train design 自身。held-out design（val/test）拿不到，
      故意**不填 NaN 占位**，让下游看到「这条基线在这个 design 上不存在」。
    - `const_global` / `geom_linreg_global`：train 全体一次拟合（不做任何 design 适配）。
      这是 held-out design 上**唯一可部署的**同特征集平凡基线，同时它就是 §5.20 用来暴露
      跨 design 电平差的那把尺子：它在 val/test 上越差，越说明「电平标度」是主要矛盾。
    """
    preds: Dict[str, np.ndarray] = {}
    n = geom.shape[0]
    if n == 0:
        return preds
    per = (fitted.get("per_design") or {}).get(design)
    if per:
        preds["const"] = np.full((n,), per["const_value"], dtype=np.float64)
        if per.get("geom_coef"):
            preds["geom_linreg"] = _lstsq_apply(geom, per["geom_coef"])
    glob = fitted.get("global")
    if glob:
        preds["const_global"] = np.full((n,), glob["const_value"], dtype=np.float64)
        if glob.get("geom_coef"):
            preds["geom_linreg_global"] = _lstsq_apply(geom, glob["geom_coef"])
    return preds


def metric_bundle(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    clock_period_ns: Any = 1.0,
) -> Dict[str, float]:
    y_true = np.asarray(y_true, dtype=np.float64).reshape(-1)
    y_pred = np.asarray(y_pred, dtype=np.float64).reshape(-1)
    if y_true.size == 0:
        return {"n": 0, "mae": float("nan"), "rmse": float("nan"), "r2": float("nan"),
                "y_mean": float("nan"), "y_std": float("nan"), "var": 0.0,
                "rel_mae": float("nan"), "MAPE_percent": float("nan"),
                "normalized_MAE": float("nan")}
    err = y_pred - y_true
    mae = float(np.abs(err).mean())
    y_mean = float(y_true.mean())
    var = float(y_true.var())
    period = np.asarray(clock_period_ns, dtype=np.float64)
    if period.ndim == 0:
        period = np.full(y_true.shape, float(period), dtype=np.float64)
    else:
        period = np.broadcast_to(period.reshape(-1), y_true.shape)
    period = np.maximum(np.abs(period), 1e-9)
    return {
        "n": int(y_true.size),
        "mae": mae,
        "rmse": float(np.sqrt((err * err).mean())),
        "r2": float(r2_score_np(y_true, y_pred)),
        "MAPE_percent": float(
            (np.abs(err) / np.maximum(np.abs(y_true), 1e-6)).mean() * 100.0
        ),
        "normalized_MAE": float((np.abs(err) / period).mean()),
        # y 的统计量留下来给 summarise() 做**按方差加权**的聚合（见那里的注释）：
        # gcd 的 y_std 只有 ~0.008 ns，宏平均 R² 会被它绑架。
        "y_mean": y_mean,
        "y_std": float(np.sqrt(max(var, 0.0))),
        "var": var,
        # 相对 MAE：按该 design 自身电平归一，否则 riscv32i（y_mean 0.54 ns）一个人就能
        # 左右宏平均 MAE，而它在标度上跟 gcd（0.043 ns）差 12 倍。
        "rel_mae": mae / max(abs(y_mean), 1e-9),
    }


# ==================== 训练 / 评估 ====================


def encode_graph(model: CtsTimingPathModel, g, device: torch.device) -> torch.Tensor:
    return model.encoder(g.to(device))


def run_path_batches(
    model: CtsTimingPathModel,
    node_emb: torch.Tensor,
    timing_paths: Dict[str, Any],
    geometry: np.ndarray,
    path_indices: np.ndarray,
    args,
    device: torch.device,
    optimizer: Optional[torch.optim.Optimizer],
    delay_criterion: MaskedDelayLoss,
    slack_criterion: MaskedDelayLoss,
    design_scalars: Optional[np.ndarray] = None,
) -> Tuple[float, np.ndarray, np.ndarray]:
    """在一张图的路径子集上前向（+反传）。返回 (mean loss, y_true, y_pred)。"""
    if path_indices.size == 0:
        return 0.0, np.zeros((0,)), np.zeros((0,))
    dataset = CtsPathDataset(
        timing_paths, geometry, path_indices,
        include_grt_baseline=bool(args.ablation_grt),
        design_scalars=design_scalars,
    )
    loader = DataLoader(
        dataset,
        batch_size=int(args.path_batch_size),
        shuffle=optimizer is not None,
        collate_fn=collate_cts_paths,
    )
    is_train = optimizer is not None
    model.train(is_train)
    n_batches = len(loader)
    total_loss = 0.0
    ys: List[np.ndarray] = []
    ps: List[np.ndarray] = []
    grad_ctx = torch.enable_grad() if is_train else torch.no_grad()
    with grad_ctx:
        for batch_idx, batch in enumerate(loader):
            batch = {k: v.to(device) if torch.is_tensor(v) else v for k, v in batch.items()}
            pred, pred_slack = model(node_emb, batch)
            # 主监督：route 路径延迟 ns，掩码 = label_mask
            loss = delay_criterion(pred, batch["target_delay"], batch["label_mask"])
            if model.head_slack is not None and float(args.slack_weight) > 0:
                # 辅助监督：rpt slack ns，掩码 = slack_mask（**与 label_mask 不同源**）
                loss = loss + float(args.slack_weight) * slack_criterion(
                    pred_slack, batch["target_slack"], batch["slack_mask"]
                )
            if is_train:
                # node_emb 是每张图只算一次、被所有 batch 共用的子图，必须 retain_graph，
                # 否则第一个 batch 反传完就把编码器的计算图释放了。
                (loss / max(n_batches, 1)).backward(retain_graph=batch_idx < n_batches - 1)
            total_loss += float(loss.detach().cpu().item())
            keep = batch["label_mask"].reshape(-1) > 0.5
            ys.append(batch["target_delay"].detach().cpu().numpy()[keep.cpu().numpy()])
            ps.append(pred.detach().cpu().numpy()[keep.cpu().numpy()])
    if is_train:
        if float(args.grad_clip) > 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(args.grad_clip))
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
    y_true = np.concatenate(ys) if ys else np.zeros((0,))
    y_pred = np.concatenate(ps) if ps else np.zeros((0,))
    return total_loss / max(n_batches, 1), y_true, y_pred


def _finalize_buckets(buckets: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """把「累加的 y/p」变成 metric bundle。per-design 与 per-(design,strategy) 共用。"""
    out: Dict[str, Dict[str, Any]] = {}
    for name, bucket in sorted(buckets.items()):
        y = np.concatenate(bucket["model"]["ys"]) if bucket["model"]["ys"] else np.zeros((0,))
        p = np.concatenate(bucket["model"]["ps"]) if bucket["model"]["ps"] else np.zeros((0,))
        period = (
            np.concatenate(bucket["model"]["clock_periods"])
            if bucket["model"]["clock_periods"] else np.ones(y.shape)
        )
        entry: Dict[str, Any] = {
            "loss": float(np.mean(bucket["model"]["loss"])) if bucket["model"]["loss"] else float("nan"),
            "model": metric_bundle(y, p, period),
            "baselines": {},
        }
        for bname, b in bucket["baseline"].items():
            entry["baselines"][bname] = metric_bundle(
                np.concatenate(b["ys"]), np.concatenate(b["ps"]), period
            )
        out[name] = entry
    return out


@torch.no_grad()
def evaluate_records(
    model: CtsTimingPathModel,
    keys: Sequence[str],
    idx_map: Dict[str, np.ndarray],
    assets: Dict[str, Dict[str, Any]],
    args,
    device: torch.device,
    delay_criterion: MaskedDelayLoss,
    slack_criterion: MaskedDelayLoss,
    trivial: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """评估模型 + 同特征集的平凡基线并列（§7.3b），**同时产出两个粒度**。

    返回 `(per_design, per_record)`：
    - `per_design`：把一个 design 的**全部 test 策略路径合起来**算一个 R²（10 个值）。
      用于 §5.11 的低信号分档、以及 per-design 口径的诊断。
    - `per_record`：**每个 (design, strategy) 各算一个自己的 R²**（40 个值）。
      这是**主口径** —— 全局 R² = 这 40 个 R² 的**宏平均**（见 `summarise_by_record`）。

    为什么两个都要：`per_design` 会把一个 design 的 4 个策略混在一起，策略间若有系统性
    偏差就会被自己的策略方差掩盖；`per_record` 让每个策略平等地投一票，与 §6「策略级划分」
    的粒度一致。两者并列报出，差异本身就是信息（差异大 = 策略间难度不均）。
    """
    # ⚠ 必须在 `encode_graph` **之前**切 eval：`run_path_batches` 里有 `model.train(is_train)`
    # （optimizer=None ⇒ eval），但它是**在 encode 之后**才执行的。所以只靠那一行，循环里
    # **第一条**记录的 encoder 前向仍跑在 train 模式下（dropout 0.2 生效），其余记录才干净 ——
    # 实测 aes（第一个被评估的 design）的 val R² 因此带着噪声（0.9533 vs 0.9484/0.9587 波动），
    # 而 blabla…s35932 九条与 eval 模式**逐位相同**。放在这里，整轮评估的模式就是确定的。
    model.eval()
    per_design: Dict[str, Dict[str, Any]] = {}
    per_record: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        payload = assets.get(key)
        if payload is None:
            continue
        idx = idx_map.get(key, np.zeros((0,), dtype=np.int64))
        if idx.size == 0:
            continue
        node_emb = encode_graph(model, payload["graph"], device)
        loss, y, p = run_path_batches(
            model, node_emb, payload["timing_paths"], payload["geometry"],
            idx, args, device, None, delay_criterion, slack_criterion,
            design_scalars=payload["design_scalars"],
        )
        design = payload["record"].design
        targets = [(design, per_design), (payload["record"].key, per_record)]
        for bucket_name, store in targets:
            bucket = store.setdefault(
                bucket_name,
                {"model": {"ys": [], "ps": [], "loss": [], "clock_periods": []}, "baseline": {}},
            )
            bucket["model"]["ys"].append(y)
            bucket["model"]["ps"].append(p)
            bucket["model"]["loss"].append(loss)
            bucket["model"]["clock_periods"].append(
                np.full(y.shape, float(payload.get("clock_period_ns") or 0.0))
            )
        if y.size:
            geom_val = payload["geometry"][idx]
            # 基线**按 design 拟合**，两个粒度用同一套预测。per-record 只是换个分桶方式，
            # 不是换基线 —— 按 (design,strategy) 去拟合会把该策略自己的标签喂进去，是泄漏。
            preds = apply_trivial_baselines(trivial, design, geom_val)
            for bucket_name, store in targets:
                for name, pred in preds.items():
                    slot = store[bucket_name]["baseline"].setdefault(
                        name, {"ys": [], "ps": []}
                    )
                    slot["ys"].append(y)
                    slot["ps"].append(pred)
    return _finalize_buckets(per_design), _finalize_buckets(per_record)



def _pick_baseline(baselines: Dict[str, Dict[str, float]], name: str) -> Dict[str, float]:
    """优先 per-design 版本，缺了就退回 train-global 版本（held-out design 只有后者）。"""
    if name in baselines:
        return baselines[name]
    return baselines.get(name + "_global", {})


def format_metrics_table(title: str, per_design: Dict[str, Dict[str, Any]]) -> str:
    lines = [f"=== {title} ===",
             f"{'design':<10} {'n':>6} {'model_R2':>9} {'model_MAE':>10} "
             f"{'MAPE%':>9} {'nMAE':>8} | {'const_R2':>9} {'geom_R2':>9} "
             f"{'geom_MAE':>10} {'geom_MAPE%':>11} {'geom_nMAE':>10}"]
    per_design_fit = 0
    for design, v in sorted(per_design.items()):
        m = v["model"]
        c = _pick_baseline(v["baselines"], "const")
        g = _pick_baseline(v["baselines"], "geom_linreg")
        if "const" in v["baselines"]:
            per_design_fit += 1
        lines.append(
            f"{design:<10} {m['n']:>6} {m['r2']:>+9.4f} {m['mae']:>10.5f} "
            f"{m.get('MAPE_percent', float('nan')):>9.2f} "
            f"{m.get('normalized_MAE', float('nan')):>8.4f} | "
            f"{c.get('r2', float('nan')):>+9.4f} {g.get('r2', float('nan')):>+9.4f} "
            f"{g.get('mae', float('nan')):>10.5f} "
            f"{g.get('MAPE_percent', float('nan')):>11.2f} "
            f"{g.get('normalized_MAE', float('nan')):>10.4f}"
        )
    s = summarise(per_design, "model")
    for tag, label in (("all", "全体"), ("excluding_low_signal", "排除低信号"), ("low_signal", "低信号单列")):
        b = s[tag]
        base = summarise(
            {d: {"m": _pick_baseline(v["baselines"], "geom_linreg")} for d, v in per_design.items()
             if _pick_baseline(v["baselines"], "geom_linreg")}, "m",
        )[tag] if per_design else {}
        lines.append(
            f"  [{label}] designs={b['designs']} n={b['n']} model R2={b['r2']:+.4f} "
            f"R2_varw={b['r2_varw']:+.4f} MAE={b['mae']:.5f} "
            f"MAPE={b['MAPE_percent']:.2f}% nMAE={b['normalized_MAE']:.4f} "
            f"relMAE={b['rel_mae']:.4f}"
            + (f" | geom_base R2={base.get('r2', float('nan')):+.4f} "
               f"R2_varw={base.get('r2_varw', float('nan')):+.4f} "
               f"MAE={base.get('mae', float('nan')):.5f} "
               f"MAPE={base.get('MAPE_percent', float('nan')):.2f}% "
               f"nMAE={base.get('normalized_MAE', float('nan')):.4f} "
               f"relMAE={base.get('rel_mae', float('nan')):.4f}" if base else "")
        )
    lines.append(
        "  注：R2 = per-design R² 的宏平均，会被 gcd 这种「几乎没有方差」的 design 绑架"
        "（y_std ~0.008 ns，0.09 ns 的 MAE 就给 −115）。**R2_varw = 按方差加权的 R²"
        "（1 − Σ MSE_d / Σ var_d），才是有界可比的汇总**；relMAE = MAE / |mean(y)| 的宏平均。"
    )
    if per_design and per_design_fit < len(per_design):
        lines.append(
            f"  注：{per_design_fit}/{len(per_design)} 个 design 有 per-design 拟合的平凡基线；"
            "其余是 held-out design，只能用 train-global 拟合的那两列（§5.20）。"
        )
    return "\n".join(lines)


def summarise(per_design: Dict[str, Dict[str, Any]], key: str) -> Dict[str, Any]:
    """把 per-design 指标聚合成「全体 / 低信号单列 / 其余」三档（§5.11 要求 gcd 单列）。"""
    low = {d: v for d, v in per_design.items() if d in CTS_LOW_SIGNAL_DESIGNS}
    rest = {d: v for d, v in per_design.items() if d not in CTS_LOW_SIGNAL_DESIGNS}

    def agg(ds: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        if not ds:
            return {"designs": 0, "n": 0, "mae": float("nan"), "rmse": float("nan"),
                    "r2": float("nan"), "r2_varw": float("nan"), "rel_mae": float("nan"),
                    "MAPE_percent": float("nan"), "normalized_MAE": float("nan")}
        n = sum(int(v[key]["n"]) for v in ds.values())
        mae = float(np.mean([v[key]["mae"] for v in ds.values()]))
        rmse = float(np.mean([v[key]["rmse"] for v in ds.values()]))
        r2 = float(np.mean([v[key]["r2"] for v in ds.values()]))
        # **按方差加权**的 R² = 1 − Σ_d MSE_d / Σ_d var_d（per-design 各贡献一项，不按 n 加权）。
        # 为什么需要它：R² 是「MSE ÷ 该 design 自己的方差」，gcd 的 y_std 只有 ~0.008 ns，
        # 一个 0.089 ns 的 MAE 就能给出 R² = −115（实测 epoch 2）。宏平均 R² 因此在 gcd 进
        # val 之后完全不可读 —— 一个 design 就能把它从 +0.18 拉到 −12。方差加权版让
        # 小方差 design 自然降权，是有界的、可比的。⚠ 报数字时以它和
        # `excluding_low_signal` 为主，`all.r2` 只作对照。
        var_sum = sum(float(v[key].get("var", 0.0)) for v in ds.values())
        mse_sum = sum(float(v[key].get("rmse", 0.0)) ** 2 for v in ds.values())
        rel_mae = float(np.mean([v[key].get("rel_mae", float("nan")) for v in ds.values()]))
        mape = float(np.mean([v[key].get("MAPE_percent", float("nan")) for v in ds.values()]))
        normalized_mae = float(np.mean([v[key].get("normalized_MAE", float("nan")) for v in ds.values()]))
        return {
            "designs": len(ds), "n": n, "mae": mae, "rmse": rmse, "r2": r2,
            "r2_varw": float(1.0 - mse_sum / var_sum) if var_sum > 0 else float("nan"),
            "rel_mae": rel_mae,
            "MAPE_percent": mape,
            "normalized_MAE": normalized_mae,
            "var_sum_ns2": var_sum,
            "design_names": sorted(ds),
        }

    return {"all": agg(per_design), "low_signal": agg(low), "excluding_low_signal": agg(rest)}


def summarise_by_record(
    per_record: Dict[str, Dict[str, Any]],
    designs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """**主口径**：每个 (design, strategy) 各自一个 R²，全局 R² = 这些 R² 的**宏平均**。

    与 `summarise()` 的区别是粒度：那里把一个 design 的 4 个 test 策略的路径**合起来**算
    一个 R²（策略之间的差异被并进同一个方差里，一个策略跑偏会被它自己的策略方差掩盖）；
    这里有 40 个 R²，**每个策略平等投一票**，与 §6「策略级划分」的粒度一致。

    `designs` 用于筛子集（如排除 `CTS_LOW_SIGNAL_DESIGNS`）。
    基线与模型同粒度：模型用 `per_record[k]["model"]`，基线用 `per_record[k]["baselines"][name]`。
    两边都只在**同时存在**的 record 上聚合，避免分母不一致。
    """
    picked = []
    for rkey, v in sorted(per_record.items()):
        design = rkey.split("__", 1)[0]
        if designs is not None and design not in designs:
            continue
        picked.append((rkey, design, v))

    def agg(series: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
        metric_vals = [(d, series(v)) for _, d, v in picked]
        metric_vals = [(d, m) for d, m in metric_vals if m]
        r2_vals = [
            (d, m) for d, m in metric_vals
            if np.isfinite(float(m.get("r2", np.nan)))
        ]
        if not metric_vals:
            return {"pairs": 0, "n": 0, "r2": float("nan"), "r2_varw": float("nan"),
                    "mae": float("nan"), "rel_mae": float("nan"), "designs": 0,
                    "MAPE_percent": float("nan"), "normalized_MAE": float("nan")}
        var_sum = sum(float(m.get("var", 0.0)) for _, m in r2_vals)
        mse_sum = sum(float(m.get("rmse", 0.0)) ** 2 for _, m in r2_vals)

        def finite_mean(field: str) -> float:
            field_values = [
                float(m.get(field, np.nan)) for _, m in metric_vals
                if np.isfinite(float(m.get(field, np.nan)))
            ]
            return float(np.mean(field_values)) if field_values else float("nan")

        return {
            "pairs": len(r2_vals),
            "metric_pairs": len(metric_vals),
            "designs": len({d for d, _ in metric_vals}),
            "n": sum(int(m.get("n", 0)) for _, m in metric_vals),
            "r2": float(np.mean([m["r2"] for _, m in r2_vals])) if r2_vals else float("nan"),
            "r2_varw": float(1.0 - mse_sum / var_sum) if var_sum > 0 else float("nan"),
            "mae": finite_mean("mae"),
            "rel_mae": finite_mean("rel_mae"),
            "MAPE_percent": finite_mean("MAPE_percent"),
            "normalized_MAE": finite_mean("normalized_MAE"),
        }

    bnames = sorted({n for _, _, v in picked for n in v.get("baselines", {})})
    out: Dict[str, Any] = {
        "model": agg(lambda v: v.get("model")),
        "baselines": {n: agg(lambda v, n=n: v.get("baselines", {}).get(n)) for n in bnames},
        "records": [k for k, _, _ in picked],
    }
    return out


def format_per_record_table(
    title: str,
    per_record: Dict[str, Dict[str, Any]],
    baseline: str = "geom_linreg",
    per_design: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """每个 (design, strategy) 一行：该策略**自己的** R² + 该行的基线。

    `design_R2` 列故意与 `R2` 列并列 —— 同一 design 的 4 行有相同的 design_R2 值（那是把
    4 个策略的路径合起来算的），两者之差就是「这个策略相对该 design 平均水平的难易」。
    `R2_varw` 不在这里单列：对单个 record，`r2_varw ≡ r2`（1 − MSE/var 就是 R² 的定义），
    它只在**跨 record 聚合**时才有别于宏平均 R²（见 `summarise_by_record`）。
    """
    pd_ = summarise_by_record(per_record)
    design_r2 = {d: v["model"]["r2"] for d, v in (per_design or {}).items()}
    lines = [f"=== {title} ===",
             f"{'design':<10}{'strategy':<32}{'n':>7}{'R2':>10}{'MAE':>10}"
             f"{'MAPE%':>10}{'nMAE':>9}{'relMAE':>9}{'design_R2':>11}"
             f"{f'base_{baseline}_R2':>18}"]
    for rkey, v in sorted(per_record.items()):
        m = v["model"]
        if m["n"] == 0:
            continue
        design, strategy = rkey.split("__", 1)
        b = v["baselines"].get(baseline, {})
        lines.append(
            f"{design:<10}{strategy:<32}{m['n']:>7}{m['r2']:>+10.4f}{m['mae']:>10.5f}"
            f"{m.get('MAPE_percent', float('nan')):>10.2f}"
            f"{m.get('normalized_MAE', float('nan')):>9.4f}"
            f"{m.get('rel_mae', float('nan')):>9.4f}{design_r2.get(design, float('nan')):>+11.4f}"
            f"{b.get('r2', float('nan')):>+18.4f}")
    mm, gb = pd_["model"], pd_["baselines"].get(baseline, {})
    lines.append(
        f"\n  [全局宏平均] {mm['pairs']} 个 (design, strategy) 对（{mm['designs']} 个 design，n={mm['n']}）"
        f"\n    模型      R2={mm['r2']:+.4f}  R2_varw={mm['r2_varw']:+.4f}  MAE={mm['mae']:.5f}  relMAE={mm['rel_mae']:.4f}"
        f"\n    {baseline:<10}R2={gb.get('r2', float('nan')):+.4f}  "
        f"R2_varw={gb.get('r2_varw', float('nan')):+.4f}  "
        f"MAE={gb.get('mae', float('nan')):.5f}  relMAE={gb.get('rel_mae', float('nan')):.4f}"
    )
    lines.append(
        f"    模型 MAPE={mm['MAPE_percent']:.2f}% nMAE={mm['normalized_MAE']:.4f} | "
        f"{baseline} MAPE={gb.get('MAPE_percent', float('nan')):.2f}% "
        f"nMAE={gb.get('normalized_MAE', float('nan')):.4f}"
    )
    excl = summarise_by_record(per_record, [d for d in {k.split('__', 1)[0] for k in per_record}
                                            if d not in CTS_LOW_SIGNAL_DESIGNS])
    me, ge = excl["model"], excl["baselines"].get(baseline, {})
    lines.append(
        f"\n  [排除低信号 {sorted(CTS_LOW_SIGNAL_DESIGNS)}] {me['pairs']} 对"
        f"\n    模型      R2={me['r2']:+.4f}  R2_varw={me['r2_varw']:+.4f}  MAE={me['mae']:.5f}  relMAE={me['rel_mae']:.4f}"
        f"\n    {baseline:<10}R2={ge.get('r2', float('nan')):+.4f}  "
        f"R2_varw={ge.get('r2_varw', float('nan')):+.4f}  "
        f"MAE={ge.get('mae', float('nan')):.5f}  relMAE={ge.get('rel_mae', float('nan')):.4f}"
    )
    lines.append(
        f"    模型 MAPE={me['MAPE_percent']:.2f}% nMAE={me['normalized_MAE']:.4f} | "
        f"{baseline} MAPE={ge.get('MAPE_percent', float('nan')):.2f}% "
        f"nMAE={ge.get('normalized_MAE', float('nan')):.4f}"
    )
    return "\n".join(lines)


# ==================== CLI ====================


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="CTS→route path-delay trainer on the cts_net_v3 schema (方案 B, no global routing at inference)."
    )
    ap.add_argument("--graph-root", default=str(PROJECT_ROOT.parent / "dataset" / "asap7" / "graphs_cts_v3"))
    ap.add_argument("--output-dir", default=str(PROJECT_ROOT / OUTPUT_DIRNAME))
    ap.add_argument("--normalizer-path", default="", help="读已有 normalizer；缺省 = <output-dir>/normalizer_cts_net_v3.json")
    ap.add_argument("--recompute-normalizer", action="store_true", help="无视已有文件，在 train split 上重算")
    ap.add_argument("--max-normalizer-graphs", type=int, default=0)

    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument(
        "--early-stop-patience", type=int, default=25,
        help="验证 MAE 连续 N 轮无改善就停（0=禁用）。best_model.pt 一直是历史最优，"
             "所以早停只省时间、不降低最终指标。",
    )
    ap.add_argument("--path-batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lr-schedule", choices=["none", "cosine"], default="cosine",
                    help="每个 epoch 后调 LR。常量 LR 在 6 分钟/epoch 的量级上很难收敛干净。")
    ap.add_argument("--min-lr-ratio", type=float, default=0.05,
                    help="余弦退火的下界（相对 --lr）")
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--grad-clip", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=42)

    ap.add_argument("--hidden-dim", type=int, default=128)
    ap.add_argument("--gnn-layers", type=int, default=3)
    ap.add_argument("--dropout", type=float, default=0.2)
    ap.add_argument("--cell-emb-dim", type=int, default=16)
    ap.add_argument("--model-variant", choices=["pool", "pathseq"], default="pool",
                    help="pool=M0 mean/max/last; pathseq=M1 ordered BiGRU path encoder")
    ap.add_argument("--path-gru-hidden", type=int, default=64)
    ap.add_argument("--fusion-dim", type=int, default=128)
    ap.add_argument("--head-type", choices=["linear", "mlp"], default="mlp")
    ap.add_argument(
        "--linear-skip", action="store_true",
        help="在 head 输出上并一条「几何⊕电平标量」的线性通路（零初始化）。"
             "纯 MLP 实测打不过同特征集的线性基线，这一路是为此加的解。"
             "⚠ 开了之后「模型 vs 平凡基线」的对照变平凡，报结果必须同时给纯 MLP 那一路。",
    )

    ap.add_argument("--delay-loss", choices=["smooth_l1", "mse", "mae"], default="smooth_l1")
    ap.add_argument("--delay-huber-beta", type=float, default=0.02, help="单位 ns；默认约为典型路径延迟的 1%%")
    ap.add_argument("--slack-weight", type=float, default=0.0, help=">0 时启用 slack 辅助头（掩码 slack_mask）")

    ap.add_argument("--disable-geometry", action="store_true", help="关掉模型侧的 3 维路径几何输入（做消融）")
    ap.add_argument(
        "--disable-design-features",
        action="store_true",
        help="关掉 design 级电平标量（clock_period_ns 等，§5.20）。用于复现「没有电平标度」的消融。",
    )
    ap.add_argument(
        "--ablation-grt",
        action="store_true",
        help="把 baseline_cts_grt 当**额外输入特征**读进来。默认训练路径绝不引用它（§7.3）。",
    )

    ap.add_argument(
        "--within-design-val-ratio",
        type=float,
        default=0.0,
        help="从 train split 的每个 design 内部再切一份路径级验证集（冒烟时 gcd/aes 都在 train，必须靠它才有 val）。",
    )
    ap.add_argument("--designs", default="", help="逗号分隔，只跑指定 design（冒烟用）")
    ap.add_argument("--max-paths-per-record", type=int, default=0, help="每条 (design,strategy) 采样的上限，0=不限")
    ap.add_argument("--device", default="", help="cuda / cpu；缺省自动")
    ap.add_argument("--resume-checkpoint", default="", help="从已有 checkpoint 续跑；--epochs 表示最终总 epoch")
    ap.add_argument("--resume-optimizer", action="store_true", help="续跑时同时恢复 optimizer 状态")
    return ap.parse_args()


def _read_valid_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and "epoch" in row:
                rows.append(row)
    return rows


def _restore_best_from_rows(rows: Sequence[Dict[str, Any]]) -> Tuple[float, int, Dict[str, Any]]:
    best_val_mae = float("inf")
    best_epoch = 0
    best_row: Dict[str, Any] = {}
    for row in rows:
        try:
            rb = row.get("val_by_record", {})
            metric = float(rb.get("model", {}).get("mae", float("inf")))
            epoch = int(row.get("epoch", 0))
        except (TypeError, ValueError):
            continue
        if np.isfinite(metric) and metric < best_val_mae:
            best_val_mae = metric
            best_epoch = epoch
            best_row = row
    return best_val_mae, best_epoch, best_row


def main() -> None:
    # Windows 控制台默认 GBK，日志里有 `R²`、`Δ` 等字符，不重设会 UnicodeEncodeError。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = parse_args()
    set_seed(args.seed)
    out_dir = Path(args.output_dir)
    print_safe_mode(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.ablation_grt:
        print(
            "[WARN] --ablation-grt 已开启：baseline_cts_grt（GRT 抽寄派生）会作为**输入特征**参与。\n"
            "       这一路结果是消融对照，**不能**当作方案 B 的可部署指标（交接文档 §4.3 / §7.3）。"
        )

    device = torch.device(args.device) if args.device else torch.device(
        "cuda" if torch.cuda.is_available() else "cpu"
    )

    # ---- 图发现（§7.1）----
    records = discover_cts_graphs(Path(args.graph_root), ["train", "val", "test"])
    if args.designs:
        wanted = {d.strip() for d in args.designs.split(",") if d.strip()}
        records = [r for r in records if r.design in wanted]
    train_records = [r for r in records if r.split == "train"]
    val_records = [r for r in records if r.split == "val"]
    test_records = [r for r in records if r.split == "test"]
    if not train_records:
        raise SystemExit(f"[ERROR] {args.graph_root} 下没有 train split 的 cts 图。")
    hard_val_records = list(val_records)
    if args.within_design_val_ratio <= 0.0 and not hard_val_records:
        raise SystemExit(
            "[ERROR] 没有验证集：val split 为空，且 --within-design-val-ratio <= 0。\n"
            "        冒烟时（gcd/aes 都在 train）用 --within-design-val-ratio 0.2。"
        )
    print(
        f"[INFO] records: train={len(train_records)} val={len(val_records)} test={len(test_records)} "
        f"| designs={sorted({r.design for r in records})}"
    )

    # ---- 资产 + 路径划分 ----
    assets: Dict[str, Dict[str, Any]] = {}
    train_idx: Dict[str, np.ndarray] = {}
    val_idx: Dict[str, np.ndarray] = {}

    def load_all(recs: Sequence[Any], norm) -> None:
        for rec in recs:
            if rec.key in assets:
                continue
            payload = load_cts_assets(rec, args, normalizer=norm)
            if payload is None:
                continue
            assets[rec.key] = payload
            idx = valid_path_indices(payload["timing_paths"])
            if args.max_paths_per_record and idx.size > args.max_paths_per_record:
                idx = np.sort(np.random.default_rng(args.seed).choice(idx, args.max_paths_per_record, replace=False))
            tr, va = split_path_indices(idx, float(args.within_design_val_ratio), args.seed)
            if rec.split == "val":
                # val design 从不参与训练，全部路径都是验证路径
                train_idx[rec.key], val_idx[rec.key] = np.zeros((0,), dtype=np.int64), idx
            elif rec.split == "test":
                train_idx[rec.key], val_idx[rec.key] = np.zeros((0,), dtype=np.int64), np.zeros((0,), dtype=np.int64)
            else:
                train_idx[rec.key], val_idx[rec.key] = tr, va

    load_all(train_records + hard_val_records, norm=None)
    train_records = [r for r in train_records if r.key in assets and train_idx[r.key].size > 0]
    hard_val_records = [r for r in hard_val_records if r.key in assets]
    eval_keys = [r.key for r in hard_val_records] + [
        r.key for r in train_records if val_idx[r.key].size > 0
    ]
    if not eval_keys:
        raise SystemExit("[ERROR] 没有任何验证路径。检查 --within-design-val-ratio。")

    # ---- normalizer（§7.2：在 train split 上重算，逐 dim z-score）----
    norm_path = Path(args.normalizer_path) if args.normalizer_path else out_dir / "normalizer_cts_net_v3.json"
    normalizer = None
    if norm_path.is_file() and not args.recompute_normalizer:
        normalizer = load_normalizer(norm_path)
        if normalizer.schema_mode != CTS_SCHEMA_MODE:
            raise SystemExit(f"[ERROR] {norm_path} 是 {normalizer.schema_mode} schema，不是 {CTS_SCHEMA_MODE}")
        print(f"[INFO] loaded normalizer: {norm_path} (computed_on={normalizer.computed_on})")
    if normalizer is None:
        t0 = time.time()
        normalizer = compute_cts_normalizer(
            [r.graph_path for r in train_records], max_graphs=int(args.max_normalizer_graphs)
        )
        save_normalizer(
            norm_path,
            normalizer,
            extra={
                "train_designs": sorted({r.design for r in train_records}),
                "num_train_graphs": len(train_records),
                "skipped_node_dims": list(CTS_NORM_SKIP_NODE_DIMS),
                "skipped_edge_dims": list(CTS_NORM_SKIP_EDGE_DIMS),
            },
        )
        print(f"[INFO] computed normalizer on {len(train_records)} train graphs in {time.time() - t0:.1f}s -> {norm_path}")
    print(
        "[INFO] node mean=" + np.array2string(np.asarray(normalizer.node_mean), precision=4)
        + " std=" + np.array2string(np.asarray(normalizer.node_std), precision=4)
    )
    print(
        "[INFO] edge mean=" + np.array2string(np.asarray(normalizer.edge_mean), precision=4)
        + " std=" + np.array2string(np.asarray(normalizer.edge_std), precision=4)
    )

    # 重新装载（normalizer 就位后）
    assets.clear()
    load_all(train_records + hard_val_records, norm=normalizer)

    # ---- 几何统计（喂模型用，只用 train 路径）----
    geom_mean, geom_std = collect_geometry_stats(
        [assets[k] for k in train_idx if k in assets and train_idx[k].size],
        [train_idx[k] for k in train_idx if k in assets and train_idx[k].size],
    )

    # ---- design 级标量统计（§5.20）：按 train record 统计（每条 (design,strategy) 等权）----
    design_rows = np.stack([
        assets[k]["design_scalars"] for k in train_idx if k in assets and train_idx[k].size
    ]) if train_idx else np.zeros((0, len(CTS_DESIGN_FEATURES)))
    if design_rows.shape[0] >= 2:
        design_mean = design_rows.mean(axis=0)
        design_std = np.maximum(design_rows.std(axis=0), 1e-6)
    else:
        design_mean = np.zeros(len(CTS_DESIGN_FEATURES))
        design_std = np.ones(len(CTS_DESIGN_FEATURES))
    print("[INFO] design scalars " + ", ".join(CTS_DESIGN_FEATURES)
          + " | mean=" + np.array2string(design_mean, precision=4)
          + " std=" + np.array2string(design_std, precision=4))

    # ---- 平凡基线（§7.3b）：只用 train 路径拟合，per-design（§5.18 口径）----
    y_chunks: Dict[str, List[np.ndarray]] = {}
    geom_chunks: Dict[str, List[np.ndarray]] = {}
    for key, idx in train_idx.items():
        if idx.size == 0 or key not in assets:
            continue
        design = assets[key]["record"].design
        y_chunks.setdefault(design, []).append(
            assets[key]["timing_paths"]["target_route"].float().numpy()[idx, 0]
        )
        geom_chunks.setdefault(design, []).append(assets[key]["geometry"][idx])
    trivial = fit_trivial_baselines(
        {d: np.concatenate(v) for d, v in y_chunks.items()},
        {d: np.concatenate(v, axis=0) for d, v in geom_chunks.items()},
    )

    model = CtsTimingPathModel(args, geom_mean, geom_std, design_mean, design_std).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.lr), weight_decay=float(args.weight_decay))
    scheduler = None
    if args.lr_schedule == "cosine":
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer,
            T_max=max(int(args.epochs), 1),
            eta_min=float(args.lr) * float(args.min_lr_ratio),
        )
    delay_criterion = MaskedDelayLoss(args.delay_loss, beta=float(args.delay_huber_beta))
    slack_criterion = MaskedDelayLoss("smooth_l1", beta=float(args.delay_huber_beta))

    resume_ckpt: Optional[Dict[str, Any]] = None
    resume_epoch = 0
    if args.resume_checkpoint:
        resume_path = Path(args.resume_checkpoint)
        if not resume_path.is_file():
            raise SystemExit(f"[ERROR] resume checkpoint 不存在: {resume_path}")
        resume_ckpt = torch.load(str(resume_path), map_location=device, weights_only=False)
        model.load_state_dict(resume_ckpt["model_state_dict"], strict=True)
        resume_epoch = int(resume_ckpt.get("epoch", 0))
        if args.resume_optimizer and resume_ckpt.get("optimizer_state_dict"):
            optimizer.load_state_dict(resume_ckpt["optimizer_state_dict"])
        if scheduler is not None:
            if resume_ckpt.get("scheduler_state_dict"):
                scheduler.load_state_dict(resume_ckpt["scheduler_state_dict"])
            else:
                for _ in range(resume_epoch):
                    scheduler.step()
        print(f"[INFO] resumed checkpoint: {resume_path} (completed_epoch={resume_epoch})")

    config = {
        "schema_mode": CTS_SCHEMA_MODE,
        "task": "cts_to_route_path_delay_direct_regression",
        "plan": "B (no global routing at inference)",
        "target": "timing_paths['target_route'][:, 0] (route path delay, ns)",
        "loss_mask": "label_mask",
        "slack_loss_mask": "slack_mask (separate, not interchangeable)",
        "ablation_grt": bool(args.ablation_grt),
        "graph_root": str(args.graph_root),
        "split_policy": "physical_split_dir (handoff §6)",
        "splits": cts_split_designs(),
        "node_dim": int(CTS_NODE_DIM),
        "edge_dim": int(CTS_EDGE_DIM),
        "geometry_features": list(CTS_GEOMETRY_FEATURES),
        "geometry_stats": {"mean": geom_mean.tolist(), "std": geom_std.tolist()},
        "design_features": list(CTS_DESIGN_FEATURES),
        "design_stats": {"mean": design_mean.tolist(), "std": design_std.tolist()},
        "use_design_features": not bool(args.disable_design_features),
        "trivial_baselines_fitted": trivial,
        "train_records": [r.key for r in train_records],
        "eval_records": eval_keys,
        "args": vars(args),
    }
    write_json(out_dir / "training_config.json", config)

    log_path = out_dir / "training_log.jsonl"
    best_val_mae = float("inf")
    best_epoch = 0
    best_path = out_dir / "best_model.pt"
    best_row: Dict[str, Any] = {}
    epoch_rows: List[Dict[str, Any]] = []
    start = time.time()

    if args.resume_checkpoint:
        epoch_rows = _read_valid_jsonl_rows(log_path)
        epoch_rows = [r for r in epoch_rows if int(r.get("epoch", 0)) <= resume_epoch]
        best_val_mae, best_epoch, best_row = _restore_best_from_rows(epoch_rows)
        if epoch_rows:
            start -= float(epoch_rows[-1].get("elapsed_time", 0.0))
        with log_path.open("w", encoding="utf-8") as logf:
            for row in epoch_rows:
                logf.write(json.dumps(row, ensure_ascii=False) + "\n")

    log_mode = "a" if args.resume_checkpoint else "w"
    with log_path.open(log_mode, encoding="utf-8") as logf:
        for epoch in range(resume_epoch + 1, int(args.epochs) + 1):
            t_epoch = time.time()
            # ---- train ----
            model.train()
            tr_losses, tr_true, tr_pred = [], [], []
            for rec in train_records:
                payload = assets[rec.key]
                node_emb = encode_graph(model, payload["graph"], device)
                loss, y, p = run_path_batches(
                    model, node_emb, payload["timing_paths"], payload["geometry"],
                    train_idx[rec.key], args, device, optimizer, delay_criterion, slack_criterion,
                    design_scalars=payload["design_scalars"],
                )
                tr_losses.append(loss)
                tr_true.append(y)
                tr_pred.append(p)

            # ---- eval（每个 design 单列，§7.3b）----
            # 模式切换在 `evaluate_records` 里做（必须在 encode 之前，原因见那里的注释）。
            per_design_metrics, per_record_metrics = evaluate_records(
                model, eval_keys, val_idx, assets, args, device,
                delay_criterion, slack_criterion, trivial,
            )
            val_loss = float(np.mean([v["loss"] for v in per_design_metrics.values()])) if per_design_metrics else float("nan")
            summary = {
                "model": summarise(per_design_metrics, "model"),
                "baselines": {
                    name: summarise(
                        {d: {"m": v["baselines"][name]} for d, v in per_design_metrics.items() if name in v["baselines"]},
                        "m",
                    )
                    for name in sorted({n for v in per_design_metrics.values() for n in v["baselines"]})
                },
            }

            tr_y = np.concatenate(tr_true) if tr_true else np.zeros((0,))
            tr_p = np.concatenate(tr_pred) if tr_pred else np.zeros((0,))
            row = {
                "epoch": epoch,
                "schema_mode": CTS_SCHEMA_MODE,
                "ablation_grt": bool(args.ablation_grt),
                "elapsed_time": time.time() - start,
                **memory_stats(device),
                "train_loss": float(np.mean(tr_losses)) if tr_losses else float("nan"),
                "train_mae": metric_bundle(tr_y, tr_p)["mae"],
                "train_r2": metric_bundle(tr_y, tr_p)["r2"],
                "val_loss": val_loss,
                "val_model": summary["model"],
                "val_baselines": summary["baselines"],
                # **主口径**（每个 (design,strategy) 一个 R²）：val 上就是 10 个策略各一个 R²，
                # 全局 = 宏平均。⚠ 它跟 per-design 口径**不是同一把尺子**，epoch 之间只看它自己。
                "val_by_record": summarise_by_record(per_record_metrics),
                "val_per_record": per_record_metrics,
                "per_design": per_design_metrics,
            }
            epoch_rows.append(row)
            logf.write(json.dumps(row, ensure_ascii=False) + "\n")
            logf.flush()

            m = summary["model"]
            rb = row["val_by_record"]

            def base_metric(name: str, field: str, bucket: str = "all") -> float:
                """per-design 版本优先；held-out design 上用 train-global 版本（§5.20）。"""
                for key in (name, name + "_global"):
                    v = summary["baselines"].get(key, {}).get(bucket, {}).get(field)
                    if v is not None and np.isfinite(float(v)):
                        return float(v)
                return float("nan")

            print(json.dumps({
                "epoch": epoch,
                "epoch_sec": round(time.time() - t_epoch, 1),
                "train_mae_ns": round(row["train_mae"], 5),
                "val_mae_ns": round(m["all"]["mae"], 5),
                "val_MAPE_percent": round(m["all"]["MAPE_percent"], 3),
                "val_normalized_MAE": round(m["all"]["normalized_MAE"], 5),
                "val_r2": round(m["all"]["r2"], 4),
                # 低信号 design 单列（§5.11）
                "val_r2_excl_low_signal": round(m["excluding_low_signal"]["r2"], 4),
                "val_r2_low_signal": round(m["low_signal"]["r2"], 4),
                # 按方差加权的汇总（§6 新协议后 gcd 也进了 val，宏平均 R² 被它绑架，见 summarise）
                "val_r2_varw": round(m["all"]["r2_varw"], 4),
                "val_r2_varw_excl_low_signal": round(m["excluding_low_signal"]["r2_varw"], 4),
                # 平凡基线（§5.17）：模型的 R² 只有跟这几行并排读才有意义。
                "base_const_mae_ns": round(base_metric("const", "mae"), 5),
                "base_geom_r2": round(base_metric("geom_linreg", "r2"), 4),
                "base_geom_r2_varw": round(base_metric("geom_linreg", "r2_varw"), 4),
                "base_geom_mae_ns": round(base_metric("geom_linreg", "mae"), 5),
                # **主口径**（per-(design,strategy)，§6）：val 上 = 10 个策略 R² 的宏平均。
                "val_r2_by_record": round(rb["model"]["r2"], 4),
                "val_r2_varw_by_record": round(rb["model"]["r2_varw"], 4),
                "base_geom_r2_by_record": round(rb["baselines"].get("geom_linreg", {}).get("r2", float("nan")), 4),
            }, ensure_ascii=False))

            ckpt = {
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "scheduler_state_dict": scheduler.state_dict() if scheduler is not None else None,
                "metrics": row,
                "training_config": config,
                "feature_schema_info": {
                    "schema_mode": CTS_SCHEMA_MODE,
                    "node_dim": int(CTS_NODE_DIM),
                    "edge_dim": int(CTS_EDGE_DIM),
                },
            }
            torch.save(ckpt, out_dir / "last_model.pt")
            record_summary = summarise_by_record(per_record_metrics)
            metric = record_summary["model"]["mae"] if np.isfinite(record_summary["model"]["mae"]) else float("inf")
            if metric < best_val_mae:
                best_val_mae = metric
                best_epoch = epoch
                torch.save(ckpt, best_path)
                best_row = row
            if scheduler is not None:
                scheduler.step()
            patience = int(args.early_stop_patience)
            if patience > 0 and epoch - best_epoch >= patience:
                print(json.dumps({
                    "early_stop": True,
                    "epoch": epoch,
                    "best_epoch": best_epoch,
                    "best_val_mae_ns": round(best_val_mae, 5),
                    "patience": patience,
                }, ensure_ascii=False))
                break

    # ---- held-out test split（picorv32 / s35932），用 best checkpoint ----
    test_metrics: Optional[Dict[str, Dict[str, Any]]] = None
    if test_records and best_path.is_file():
        print(f"[INFO] evaluating held-out test split: {sorted({r.design for r in test_records})}")
        load_all(test_records, norm=normalizer)
        test_idx = {r.key: valid_path_indices(assets[r.key]["timing_paths"]) for r in test_records if r.key in assets}
        test_keys = [r.key for r in test_records if r.key in assets and test_idx[r.key].size > 0]
        ckpt = torch.load(str(best_path), map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model_state_dict"], strict=True)
        # 模式由 `evaluate_records` 负责（它必须在 encode 之前切 eval）
        test_metrics, test_per_record = evaluate_records(
            model, test_keys, test_idx, assets, args, device,
            delay_criterion, slack_criterion, trivial,
        )
        print(format_per_record_table(
            f"HELD-OUT TEST · 主口径：每个 (design, strategy) 一个 R²，全局 = 这 "
            f"{len(test_per_record)} 个 R² 的宏平均 (best epoch {ckpt.get('epoch', '?')}) — 单位 ns",
            test_per_record,
            per_design=test_metrics,
        ))
        print()
        print(format_metrics_table(
            f"HELD-OUT TEST · 辅助口径：per-design 汇总 (best epoch {ckpt.get('epoch', '?')}) — 单位 ns",
            test_metrics,
        ))

    summary_payload = {
        "task": "cts_to_route_path_delay_direct_regression",
        "schema_mode": CTS_SCHEMA_MODE,
        "plan": "B",
        "model_variant": str(args.model_variant),
        "path_gru_hidden": int(args.path_gru_hidden),
        "ablation_grt": bool(args.ablation_grt),
        "use_design_features": not bool(args.disable_design_features),
        "best_val_mae_ns": best_val_mae,
        "best_epoch": int(best_row.get("epoch", -1)) if best_path.is_file() else -1,
        "best_val_summary": best_row.get("val_model") if best_path.is_file() else None,
        "best_val_baselines": best_row.get("val_baselines") if best_path.is_file() else None,
        "best_val_per_design": best_row.get("per_design") if best_path.is_file() else None,
        "test_per_design": test_metrics,
        "test_summary": summarise(test_metrics, "model") if test_metrics else None,
        # **主口径**：per-(design,strategy) 粒度，全局 R² = 40 个 R² 的宏平均
        "test_by_record": summarise_by_record(test_per_record) if test_per_record else None,
        "test_per_record": test_per_record,
        "loss_protocol": {
            "name": str(args.delay_loss),
            "smooth_l1_beta_ns": float(args.delay_huber_beta),
            "delay_mask": "label_mask",
            "slack_weight": float(args.slack_weight),
        },
        "metric_protocol": {
            "mape": "per-record mean(abs(error) / max(abs(target_ns), 1e-6 ns)) * 100; macro-average records",
            "normalized_mae": "per-record MAE_ns / clock_period_ns; macro-average records",
        },
        "trivial_baselines_fitted": trivial,
        "config": config,
    }
    write_json(out_dir / "training_summary.json", summary_payload)
    _mm = (summary_payload["test_by_record"] or {}).get("model", {})
    _gb = (summary_payload["test_by_record"] or {}).get("baselines", {}).get("geom_linreg", {})
    print(json.dumps({
        "best_val_mae_ns": best_val_mae,
        "best_model_path": str(best_path),
        "test_designs": sorted(test_metrics) if test_metrics else [],
        "test_GLOBAL_R2_by_record": round(_mm.get("r2", float("nan")), 4),
        "test_GLOBAL_R2_varw_by_record": round(_mm.get("r2_varw", float("nan")), 4),
        "test_GLOBAL_MAE_ns": round(_mm.get("mae", float("nan")), 5),
        "test_GLOBAL_MAPE_percent_by_record": round(_mm.get("MAPE_percent", float("nan")), 3),
        "test_GLOBAL_normalized_MAE_by_record": round(
            _mm.get("normalized_MAE", float("nan")), 5
        ),
        "test_GLOBAL_R2_geom_baseline": round(_gb.get("r2", float("nan")), 4),
        "test_GLOBAL_MAPE_percent_geom_baseline": round(
            _gb.get("MAPE_percent", float("nan")), 3
        ),
        "test_GLOBAL_normalized_MAE_geom_baseline": round(
            _gb.get("normalized_MAE", float("nan")), 5
        ),
        "test_pairs": _mm.get("pairs"),
        "note": "全局 R² = 每个 (design,strategy) 各自 R² 的宏平均（主口径，test_by_record）；"
                "test_summary 是 per-design 粒度的辅助口径。两者都要与基线并列读（§5.17）。",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
