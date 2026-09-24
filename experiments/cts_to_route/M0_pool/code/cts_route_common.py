#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTS-to-Route shared schema, graph encoder, normalization, and evaluation utilities."""

from __future__ import annotations
import json
import math
import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import dgl
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from dgl.nn.functional import edge_softmax
SCHEMA_MODE = 'unified_29_7'
NODE_FEAT_KEY = 'feat_unified'
EDGE_FEAT_KEY = 'feat_unified'
NORMALIZER_EPS = 1e-06
CTS_SCHEMA_MODE = 'cts_net_v3'
CTS_NODE_DIM = 19
CTS_EDGE_DIM = 15
CTS_NODE_FEAT_KEY = 'feat'
CTS_EDGE_FEAT_KEY = 'feat'
CTS_CELL_TYPE_INDEX = 2
CTS_CELL_TYPE_VOCAB = 906
CTS_NORM_SKIP_NODE_DIMS = (CTS_CELL_TYPE_INDEX, 4, 5, 6, 8, 9, 10, 11, 16, 17, 18)
CTS_NORM_SKIP_EDGE_DIMS = (0, 1)
CTS_ALL_DESIGNS = ['aes', 'blabla', 'ethmac', 'gcd', 'ibex', 'jpeg', 'ppu', 'picorv32', 'riscv32i', 's35932']
CTS_TRAIN_DESIGNS = list(CTS_ALL_DESIGNS)
CTS_VAL_DESIGNS = list(CTS_ALL_DESIGNS)
CTS_TEST_DESIGNS = list(CTS_ALL_DESIGNS)
CTS_STRATEGIES_PER_DESIGN = {'train': 5, 'val': 1, 'test': 4}
CTS_SPLIT_POLICY = 'per_design_strategy_base_in_train'
CTS_LOW_SIGNAL_DESIGNS = ('gcd', 'ethmac')
CTS_GEOMETRY_FEATURES = ['wl_manh_um', 'n_pins', 'wl_per_pin_um']
CTS_DESIGN_FEATURES = ['clock_period_ns', 'utilization', 'log1p_die_area_um2', 'log1p_num_nodes']
CTS_GRAPH_SUFFIX = '__cts.dgl'

def print_safe_mode(*output_roots: str) -> None:
    print('[SAFE MODE] old DGL/checkpoint files will not be overwritten.')
    if output_roots:
        print('[OUTPUT ROOT] ' + ' '.join(output_roots))
WRITE_GUARD_MARKERS = ('unified_29_7', 'cts_route', 'cts_net_v2', 'cts_net_v3')

def ensure_unified_output_path(path: Path, overwrite: bool=False) -> None:
    resolved = path.resolve()
    text = str(resolved).replace('\\', '/')
    if not any((marker in text for marker in WRITE_GUARD_MARKERS)):
        raise RuntimeError(f'Refusing to write outside a known output tree {WRITE_GUARD_MARKERS}: {resolved}')
    if path.exists() and (not overwrite):
        raise FileExistsError(f'Output exists and --overwrite was not set: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)

def write_json(path: Path, payload: Dict[str, Any], overwrite: bool=True) -> None:
    if not overwrite and path.exists():
        return
    ensure_unified_output_path(path, overwrite=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

class GraphNormalizer:
    """逐 dim z-score。

    schema_mode / node_dim / edge_dim / feat key 都作为实例属性保存，所以同一个类
    既能服务旧的 `unified_29_7`(29/7)，也能服务 `cts_net_v2`(8/8)。默认值仍是旧
    schema，保证 `GraphNormalizer.from_dict` 读旧 normalizer json 时行为不变。
    """

    def __init__(self, node_mean: torch.Tensor, node_std: torch.Tensor, edge_mean: torch.Tensor, edge_std: torch.Tensor, *, schema_mode: str=SCHEMA_MODE, node_feat_key: str=NODE_FEAT_KEY, edge_feat_key: str=EDGE_FEAT_KEY, computed_on: str='train_split_place_plus_route'):
        self.node_mean = node_mean.float()
        self.node_std = torch.clamp(node_std.float(), min=NORMALIZER_EPS)
        self.edge_mean = edge_mean.float()
        self.edge_std = torch.clamp(edge_std.float(), min=NORMALIZER_EPS)
        self.schema_mode = str(schema_mode)
        self.node_feat_key = str(node_feat_key)
        self.edge_feat_key = str(edge_feat_key)
        self.computed_on = str(computed_on)
        self.node_dim = int(self.node_mean.numel())
        self.edge_dim = int(self.edge_mean.numel())

    def apply(self, g: dgl.DGLGraph) -> dgl.DGLGraph:
        (nk, ek) = (self.node_feat_key, self.edge_feat_key)
        g.ndata[nk] = (g.ndata[nk].float() - self.node_mean.to(g.ndata[nk].device)) / self.node_std.to(g.ndata[nk].device)
        g.edata[ek] = (g.edata[ek].float() - self.edge_mean.to(g.edata[ek].device)) / self.edge_std.to(g.edata[ek].device)
        return g

    def to_dict(self, extra: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        payload = {'schema_mode': self.schema_mode, 'normalization': 'z_score', 'computed_on': self.computed_on, 'node_dim': self.node_dim, 'edge_dim': self.edge_dim, 'node_feat_key': self.node_feat_key, 'edge_feat_key': self.edge_feat_key, 'eps': NORMALIZER_EPS, 'node_feat_mean': self.node_mean.cpu().tolist(), 'node_feat_std': self.node_std.cpu().tolist(), 'edge_feat_mean': self.edge_mean.cpu().tolist(), 'edge_feat_std': self.edge_std.cpu().tolist()}
        if extra:
            payload.update(extra)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GraphNormalizer':
        schema_mode = data.get('schema_mode')
        if schema_mode not in (SCHEMA_MODE, CTS_SCHEMA_MODE):
            raise RuntimeError(f'normalizer schema mismatch: {schema_mode}')
        node_dim = int(data.get('node_dim', -1))
        edge_dim = int(data.get('edge_dim', -1))
        if len(data['node_feat_mean']) != node_dim or len(data['edge_feat_mean']) != edge_dim:
            raise RuntimeError('normalizer dimension mismatch')
        return cls(torch.tensor(data['node_feat_mean'], dtype=torch.float32), torch.tensor(data['node_feat_std'], dtype=torch.float32), torch.tensor(data['edge_feat_mean'], dtype=torch.float32), torch.tensor(data['edge_feat_std'], dtype=torch.float32), schema_mode=str(schema_mode), node_feat_key=str(data.get('node_feat_key', NODE_FEAT_KEY)), edge_feat_key=str(data.get('edge_feat_key', EDGE_FEAT_KEY)), computed_on=str(data.get('computed_on', 'train_split_place_plus_route')))

def load_normalizer(path: Path) -> GraphNormalizer:
    return GraphNormalizer.from_dict(json.loads(path.read_text(encoding='utf-8')))

def save_normalizer(path: Path, normalizer: GraphNormalizer, extra: Optional[Dict[str, Any]]=None) -> None:
    write_json(path, normalizer.to_dict(extra), overwrite=True)

class EdgeGATLayer(nn.Module):

    def __init__(self, in_dim_node: int, in_dim_edge: int, out_dim_node: int, out_dim_edge: int, lambda_param: float=0.5, dropout: float=0.2):
        super().__init__()
        self.in_dim_node = in_dim_node
        self.in_dim_edge = in_dim_edge
        self.out_dim_node = out_dim_node
        self.out_dim_edge = out_dim_edge
        self.lambda_param = lambda_param
        self.W_node = nn.Linear(in_dim_node, out_dim_node, bias=False)
        self.W_edge = nn.Linear(in_dim_edge, out_dim_edge, bias=False)
        self.attn_node = nn.Linear(2 * out_dim_node + out_dim_edge, 1, bias=False)
        self.edge_update = nn.Sequential(nn.Linear(out_dim_edge + 2 * out_dim_node, out_dim_edge), nn.ReLU(), nn.Dropout(dropout))
        self.dropout = nn.Dropout(dropout)
        self.leaky_relu = nn.LeakyReLU(0.2)

    def forward(self, g: dgl.DGLGraph, h_node: torch.Tensor, h_edge: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        with g.local_scope():
            if self.in_dim_node == self.out_dim_node:
                n_trans = (1 - self.lambda_param) * h_node + self.lambda_param * self.W_node(h_node)
            else:
                n_trans = self.W_node(h_node)
            if self.in_dim_edge == self.out_dim_edge:
                e_trans = (1 - self.lambda_param) * h_edge + self.lambda_param * self.W_edge(h_edge)
            else:
                e_trans = self.W_edge(h_edge)
            g.ndata['h'] = n_trans
            g.edata['e'] = e_trans
            g.apply_edges(self._node_attention_score)
            g.edata['alpha'] = edge_softmax(g, g.edata['alpha'])
            g.update_all(self._node_message_func, self._node_reduce_func)
            h_node_agg = g.ndata['h_agg']
            g.apply_edges(self._edge_update_func)
            h_edge_new = g.edata['e_new']
            h_node_new = F.relu(h_node_agg + h_node) if self.in_dim_node == self.out_dim_node else F.relu(h_node_agg)
            h_edge_new = F.relu(h_edge_new + h_edge) if self.in_dim_edge == self.out_dim_edge else F.relu(h_edge_new)
            return (h_node_new, h_edge_new)

    def _node_attention_score(self, edges):
        z = torch.cat([edges.src['h'], edges.dst['h'], edges.data['e']], dim=1)
        return {'alpha': self.leaky_relu(self.attn_node(z))}

    def _node_message_func(self, edges):
        return {'m': edges.data['alpha'] * edges.src['h']}

    def _node_reduce_func(self, nodes):
        return {'h_agg': torch.sum(nodes.mailbox['m'], dim=1)}

    def _edge_update_func(self, edges):
        z = torch.cat([edges.data['e'], edges.src['h'], edges.dst['h']], dim=1)
        return {'e_new': self.edge_update(z)}

def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

def memory_stats(device: torch.device) -> Dict[str, int]:
    if device.type != 'cuda':
        return {'memory_allocated': 0, 'memory_reserved': 0, 'max_memory_allocated': 0}
    return {'memory_allocated': int(torch.cuda.memory_allocated(device)), 'memory_reserved': int(torch.cuda.memory_reserved(device)), 'max_memory_allocated': int(torch.cuda.max_memory_allocated(device))}

@dataclass
class CtsGraphRecord:
    """一个 (design, strategy) 的 CTS 图 + 配套的路径标签文件。"""
    split: str
    design: str
    strategy: str
    graph_path: str
    timing_paths_path: str

    @property
    def key(self) -> str:
        return f'{self.design}__{self.strategy}'

def cts_split_designs() -> Dict[str, Any]:
    """写进 training_config.json 的划分说明。新协议下 train/val/test 的 design 列表相同，
    真正的划分在策略维度，所以这里把策略数一并写上，否则这个字段会误导读者。"""
    return {'policy': CTS_SPLIT_POLICY, 'train': list(CTS_TRAIN_DESIGNS), 'val': list(CTS_VAL_DESIGNS), 'test': list(CTS_TEST_DESIGNS), 'strategies_per_design': dict(CTS_STRATEGIES_PER_DESIGN), 'note': 'split 是 per-design 的策略级划分：每个 design 的 base + 4 个变体参与梯度，1 个变体作 val，4 个变体作 test（见 code/resplit_cts_strategies.py）'}

def discover_cts_graphs(root: Path, splits: Optional[Sequence[str]]=None) -> List[CtsGraphRecord]:
    """扫描 `<root>/<split>/<design>/<strategy>/<design>__<strategy>__cts.dgl`。

    与旧的 `discover_unified_graphs` 不同，这里的物理目录就是逻辑 split（不再用
    `split_for_design` 覆盖），因为 builder 的 `DEFAULT_SPLITS` 已经按新的 10-design
    划分落盘（handoff §6）。缺 `.timing_paths.pt` 的记录直接跳过。
    """
    root = Path(root)
    requested = set(splits) if splits else {'train', 'val', 'test'}
    records: List[CtsGraphRecord] = []
    for split in ('train', 'val', 'test'):
        if split not in requested:
            continue
        split_dir = root / split
        if not split_dir.is_dir():
            continue
        for graph_path in sorted(split_dir.glob(f'*/*/*{CTS_GRAPH_SUFFIX}')):
            strategy_dir = graph_path.parent
            design = strategy_dir.parent.name
            strategy = strategy_dir.name
            stem = graph_path.name[:-len('.dgl')]
            tp_path = strategy_dir / f'{stem}.timing_paths.pt'
            if not tp_path.is_file():
                continue
            records.append(CtsGraphRecord(split, design, strategy, str(graph_path), str(tp_path)))
    return records

def validate_cts_graph(g: dgl.DGLGraph, path: str='') -> None:
    if CTS_NODE_FEAT_KEY not in g.ndata:
        raise KeyError(f"{path}: missing g.ndata['{CTS_NODE_FEAT_KEY}']")
    if CTS_EDGE_FEAT_KEY not in g.edata:
        raise KeyError(f"{path}: missing g.edata['{CTS_EDGE_FEAT_KEY}']")
    node_dim = int(g.ndata[CTS_NODE_FEAT_KEY].shape[1])
    edge_dim = int(g.edata[CTS_EDGE_FEAT_KEY].shape[1])
    if node_dim != CTS_NODE_DIM:
        raise RuntimeError(f'{path}: node feat dim={node_dim}, expected {CTS_NODE_DIM} (cts_net_v3)')
    if edge_dim != CTS_EDGE_DIM:
        raise RuntimeError(f'{path}: edge feat dim={edge_dim}, expected {CTS_EDGE_DIM} (cts_net_v3)')
    if not torch.isfinite(g.ndata[CTS_NODE_FEAT_KEY]).all():
        raise RuntimeError(f'{path}: node feat contains NaN/Inf')
    if not torch.isfinite(g.edata[CTS_EDGE_FEAT_KEY]).all():
        raise RuntimeError(f'{path}: edge feat contains NaN/Inf')

def compute_cts_normalizer(graph_paths: Sequence[str], max_graphs: int=0) -> GraphNormalizer:
    """在给定图集合（= train split）上逐 dim 重算 mean/std（handoff §7.2）。

    用「和 / 平方和」在线累加，不需要把所有节点堆进内存（aes 单图 5.2 万节点，
    全量 train 60 张图会到千万量级）。
    """
    node_sum = node_sq = edge_sum = edge_sq = None
    node_count = edge_count = used = 0
    for path in graph_paths:
        g = dgl.load_graphs(str(path))[0][0]
        validate_cts_graph(g, str(path))
        n = g.ndata[CTS_NODE_FEAT_KEY].float()
        e = g.edata[CTS_EDGE_FEAT_KEY].float()
        if node_sum is None:
            node_sum = n.sum(dim=0)
            node_sq = (n * n).sum(dim=0)
            edge_sum = e.sum(dim=0)
            edge_sq = (e * e).sum(dim=0)
        else:
            node_sum = node_sum + n.sum(dim=0)
            node_sq = node_sq + (n * n).sum(dim=0)
            edge_sum = edge_sum + e.sum(dim=0)
            edge_sq = edge_sq + (e * e).sum(dim=0)
        node_count += max(int(n.shape[0]), 1)
        edge_count += max(int(e.shape[0]), 1)
        used += 1
        if max_graphs and used >= max_graphs:
            break
    if node_sum is None:
        raise RuntimeError('compute_cts_normalizer: no graphs available')
    node_mean = node_sum / max(node_count, 1)
    edge_mean = edge_sum / max(edge_count, 1)
    node_var = torch.clamp(node_sq / max(node_count, 1) - node_mean * node_mean, min=0.0)
    edge_var = torch.clamp(edge_sq / max(edge_count, 1) - edge_mean * edge_mean, min=0.0)
    node_std = torch.sqrt(node_var)
    edge_std = torch.sqrt(edge_var)
    for dim in CTS_NORM_SKIP_NODE_DIMS:
        node_mean[dim] = 0.0
        node_std[dim] = 1.0
    for dim in CTS_NORM_SKIP_EDGE_DIMS:
        edge_mean[dim] = 0.0
        edge_std[dim] = 1.0
    return GraphNormalizer(node_mean, node_std, edge_mean, edge_std, schema_mode=CTS_SCHEMA_MODE, node_feat_key=CTS_NODE_FEAT_KEY, edge_feat_key=CTS_EDGE_FEAT_KEY, computed_on='train_split_cts_only')

def cts_path_geometry(raw_xy: torch.Tensor, node_ptr: torch.Tensor, node_ids_flat: torch.Tensor, num_paths: Optional[int]=None) -> np.ndarray:
    """由 pin 坐标算每条路径的纯几何量（handoff §5.18），**不碰任何时序/布线量**。

    Args:
        raw_xy: [N, 2] 节点坐标（um），必须是 normalize **之前**的原始值。
        node_ptr: [P+1] 路径在 `node_ids_flat` 里的偏移。
        node_ids_flat: [M] `node_ptr` 所指的**节点 id**（不是位置）。必须显式传入：
            `node_ptr` 给出的是 flat 数组里的位置，位置与节点 id 在多路径记录里并不相同
            （`M > num_nodes` 时按位置索引会越界；不越界时则是静默算错）。

    Returns:
        [P, 3] float64，列见 `CTS_GEOMETRY_FEATURES` = 曼哈顿总长 / pin 数 / 每 pin 长度。
    """
    xy = raw_xy.detach().float().cpu().numpy() if torch.is_tensor(raw_xy) else np.asarray(raw_xy, dtype=np.float32)
    ptr = node_ptr.detach().cpu().numpy() if torch.is_tensor(node_ptr) else np.asarray(node_ptr)
    ptr = ptr.astype(np.int64)
    ids = node_ids_flat.detach().cpu().numpy() if torch.is_tensor(node_ids_flat) else np.asarray(node_ids_flat)
    ids = ids.astype(np.int64)
    total = int(ptr.size - 1)
    if num_paths is not None:
        total = min(total, int(num_paths))
    out = np.zeros((max(total, 0), len(CTS_GEOMETRY_FEATURES)), dtype=np.float64)
    for i in range(total):
        (s, e) = (int(ptr[i]), int(ptr[i + 1]))
        s = max(s, 0)
        e = min(e, ids.size)
        n_pins = 0
        wl = 0.0
        prev = -1
        for j in range(s, e):
            node = int(ids[j])
            if node < 0:
                continue
            if node >= xy.shape[0]:
                raise IndexError(f'cts_path_geometry: path {i} node id {node} 越界（num_nodes={xy.shape[0]}）；graph 与 timing_paths 不是同一条记录')
            n_pins += 1
            if prev >= 0:
                wl += abs(float(xy[node, 0]) - float(xy[prev, 0])) + abs(float(xy[node, 1]) - float(xy[prev, 1]))
            prev = node
        out[i, 0] = wl
        out[i, 1] = float(n_pins)
        out[i, 2] = wl / n_pins if n_pins else 0.0
    return out

def r2_score_np(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64).reshape(-1)
    y_pred = np.asarray(y_pred, dtype=np.float64).reshape(-1)
    if y_true.size == 0 or y_true.size != y_pred.size:
        return float('nan')
    ss_tot = float(((y_true - y_true.mean()) ** 2).sum())
    if ss_tot <= 0.0:
        return float('nan')
    ss_res = float(((y_true - y_pred) ** 2).sum())
    return 1.0 - ss_res / ss_tot

def read_cts_design_scalars(meta_path: Path) -> np.ndarray:
    """从 `graph_meta.json` 读 design 级电平标量，顺序同 `CTS_DESIGN_FEATURES`。

    `graph_meta.json` 是 builder 在 **CTS 阶段**写出的，只依赖 `design_info.json`
    与 DEF/网表，**不含任何布线派生量**，所以可以安全进推理路径（handoff §5.20）。
    缺字段一律回退到中性值而不抛异常——分层目录里个别老图可能没有新字段。
    """
    meta: Dict[str, Any] = {}
    path = Path(meta_path)
    if path.is_file():
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            meta = {}
    periods = meta.get('clock_periods_s') or {}
    values = []
    for v in periods.values():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            values.append(f)
    clock_period_ns = min(values) * 1000000000.0 if values else 0.0
    try:
        utilization = float(meta.get('utilization') or 0.0)
    except (TypeError, ValueError):
        utilization = 0.0
    try:
        die_area = float(meta.get('die_area_um2') or 0.0)
    except (TypeError, ValueError):
        die_area = 0.0
    try:
        num_nodes = float(meta.get('num_nodes') or 0.0)
    except (TypeError, ValueError):
        num_nodes = 0.0
    return np.asarray([clock_period_ns, utilization, float(np.log1p(max(die_area, 0.0))), float(np.log1p(max(num_nodes, 0.0)))], dtype=np.float64)

class CtsGraphEncoder(nn.Module):
    """cts_net_v2 图编码器：cell_type_id 过 Embedding（handoff §7.4）+ EdgeGAT 堆叠。

    复用旧 `EdgeGATLayer` 不动。与 `UnifiedGraphEncoder` 的唯一结构差别是入口：
    节点特征 dim 2 是 categorical id，必须走 Embedding 而不是当连续量线性投影。
    """

    def __init__(self, node_dim: int=CTS_NODE_DIM, edge_dim: int=CTS_EDGE_DIM, cell_vocab: int=CTS_CELL_TYPE_VOCAB, cell_emb_dim: int=16, hidden_dim: int=128, num_layers: int=3, dropout: float=0.2, cat_index: int=CTS_CELL_TYPE_INDEX):
        super().__init__()
        self.hidden_dim = int(hidden_dim)
        self.num_layers = int(num_layers)
        self.cat_index = int(cat_index)
        self.cell_emb = nn.Embedding(int(cell_vocab), int(cell_emb_dim))
        nn.init.normal_(self.cell_emb.weight, mean=0.0, std=0.02)
        in_node = int(node_dim) - 1 + int(cell_emb_dim)
        self.node_in_proj = nn.Linear(in_node, self.hidden_dim)
        self.edge_in_proj = nn.Linear(int(edge_dim), self.hidden_dim)
        self.layers = nn.ModuleList()
        for i in range(self.num_layers):
            self.layers.append(EdgeGATLayer(self.hidden_dim, self.hidden_dim, self.hidden_dim, self.hidden_dim, max(0.1, 1.0 / (i + 2)), dropout))
        self.dropout = nn.Dropout(dropout)

    def node_input(self, g: dgl.DGLGraph) -> torch.Tensor:
        h = g.ndata[CTS_NODE_FEAT_KEY].float()
        idx = h[:, self.cat_index].long().clamp_(0, self.cell_emb.num_embeddings - 1)
        emb = self.cell_emb(idx)
        return torch.cat([h[:, :self.cat_index], emb, h[:, self.cat_index + 1:]], dim=1)

    def forward(self, g: dgl.DGLGraph) -> torch.Tensor:
        h_node = F.leaky_relu(self.node_in_proj(self.node_input(g)), negative_slope=0.1)
        h_edge = F.relu(self.edge_in_proj(g.edata[CTS_EDGE_FEAT_KEY].float()))
        for (i, layer) in enumerate(self.layers):
            (h_node, h_edge) = layer(g, h_node, h_edge)
            if i < self.num_layers - 1:
                h_node = self.dropout(h_node)
                h_edge = self.dropout(h_edge)
        return h_node
