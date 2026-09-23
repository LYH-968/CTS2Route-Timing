#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTS → route 时序预测：新图编码器 + 标签构建（cts_net_v1 schema）。

与旧 build_dataset_tm_pow_v3_dbu_topk_debug_abs_modified.py 的区别：
  - 特征阶段从 place 换成 cts（wire_graph / nets / design_info 全部取 cts 阶段）
  - 节点特征不再是 27D/29D，而是新方案 8D（见 NODE_SCHEMA）
  - sink pin cap 从 liberty（merged.lib）全量查表，driver/net 侧挂 net 总 C
  - 标签侧：route .rpt 用 parse_endpoint_complete_rpt 解析（新格式），
    cts baseline 用 cts wire_paths 的 Incr 累加，无需解析 rpt 作 baseline

输入（每个 design×strategy，cts 阶段）:
  parsed_ml_dataset_asap7/<design>/cts/vectors/<design>_cts_vectors/vectors/<strategy>/
    wire_graph/timing_wire_graph.json   # pin 节点 + net 边 + cell arc 边
    nets/net_*.json                     # net 级 C / R / use / driver 映射
    wire_paths/wire_path_*.json         # path 序列 + cts 阶段 Incr（baseline）
    design_info.json                    # routing layer 约束 / clock / 面积
    tech/cells.json                     # cell id 词表
  route 阶段:
    <design>/route/vectors/<design>_route_vectors/vectors/<strategy>/<design>.rpt
  liberty:
    design_info.json 的 liberty_file（merged.lib，全库共用，md5 相同）

输出（out_root/<split>/<design>/<strategy>/）:
  <design>__<strategy>__cts.dgl        # DGL 图（ndata/edata feat，cts_net_v1 schema）
  <design>__<strategy>__cts.timing_paths.pt  # 路径标签（baseline/target/delta/mask）
  graph_meta.json                      # 图规模 + 设计级属性（layer 约束/clock/面积）
out_root/schema.json                   # 特征 schema 文档
out_root/labels_summary.json           # 全库标签覆盖汇总
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch

try:
    import dgl
    HAS_DGL = True
except Exception:
    HAS_DGL = False

try:
    from parse_endpoint_complete_rpt import (
        build_endpoint_lookup,
        label_for_endpoint,
        normalize_point_name,
        parse_endpoint_complete_rpt,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from parse_endpoint_complete_rpt import (
        build_endpoint_lookup,
        label_for_endpoint,
        normalize_point_name,
        parse_endpoint_complete_rpt,
    )

SCHEMA_NAME = "cts_net_v1"

# 节点特征 [N, 8]（float32，id 类特征由模型转 long）
NODE_SCHEMA = [
    ("coord_x_um", "float"),
    ("coord_y_um", "float"),
    ("cell_type_id", "categorical"),   # tech/cells.json id；端口/NA = num_cells
    ("pin_cap_pf", "float"),           # liberty 输入 pin 电容，仅 sink pin
    ("is_port", "binary"),
    ("is_input", "binary"),            # 1=sink pin / 输入端口；0=driver pin
    ("is_endpoint", "binary"),
    ("fanout_num", "count"),
]

# 边特征 [E, 8]
EDGE_SCHEMA = [
    ("is_net_edge", "binary"),
    ("edge_delay_rise_max", "float ns"),
    ("edge_delay_fall_max", "float ns"),
    ("edge_delay_rise_min", "float ns"),
    ("edge_delay_fall_min", "float ns"),
    ("net_cap_pf", "float"),           # net 总 C（net 边；cell arc 边为 0）
    ("net_resistance", "float"),       # cts 阶段无布线，恒 0（保留位）
    ("net_use", "binary"),             # 1=CLOCK, 0=SIGNAL（net 边；cell arc 边为 0）
]

# design 级固定划分（salsa20 已剔除，ethmac_lvt 已并入 ethmac）
DEFAULT_SPLITS = {
    "train": ["aes", "blabla", "ethmac", "gcd", "jpeg", "riscv32i"],
    "val": ["ibex", "ppu"],
    "test": ["picorv32", "s35932"],
}

LIB_CACHE: Dict[str, Dict[str, Dict[str, float]]] = {}


# ==================== liberty pin cap（独立轻量解析） ====================


def parse_liberty_pin_caps(lib_path: Path) -> Dict[str, Dict[str, float]]:
    """解析 merged.lib 的输入 pin 电容（pF）。所有 asap7 design 的 lib 相同，解析一次复用。"""
    text = lib_path.read_text(encoding="utf-8", errors="ignore")
    cap_unit = re.search(r"\bcapacitive_load_unit\s*\(\s*([-+eE.\d]+)\s*,\s*(\w+)\s*\)", text)
    scale = 1.0
    if cap_unit:
        unit_scale = {"pf": 1.0, "ff": 1e-3, "nf": 1e3}.get(cap_unit.group(2).lower(), 1.0)
        scale = float(cap_unit.group(1)) * unit_scale
    pin_caps: Dict[str, Dict[str, float]] = {}
    pos = 0
    while True:
        m = re.search(r"\bcell\s*\(\s*([^)]+)\s*\)\s*\{", text[pos:])
        if not m:
            break
        cell = m.group(1).strip()
        start = pos + m.end()
        # 找 cell body 的匹配右括号
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start : i - 1]
        pos = i
        caps: Dict[str, float] = {}
        for pm in re.finditer(r"\bpin\s*\(\s*([^)]+)\s*\)\s*\{", body):
            pin = pm.group(1).strip()
            pin_body = body[pm.end():]
            end = pin_body.find("}")
            if end < 0:
                continue
            pin_body = pin_body[:end]
            cm = re.search(r"\bcapacitance\s*:\s*([-+eE.\d]+)\s*;", pin_body)
            if cm:
                caps[pin] = float(cm.group(1)) * scale
        if caps:
            pin_caps[cell] = caps
    return pin_caps


def load_pin_caps_for_lib(lib_path: Path) -> Dict[str, Dict[str, float]]:
    digest = hashlib.md5(lib_path.read_bytes()).hexdigest()
    if digest not in LIB_CACHE:
        t0 = time.time()
        LIB_CACHE[digest] = parse_liberty_pin_caps(lib_path)
        print(f"[INFO] liberty pin caps parsed: {lib_path} ({len(LIB_CACHE[digest])} cells, {time.time()-t0:.1f}s)")
    return LIB_CACHE[digest]


# ==================== 数据加载 ====================


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return json.load(f)


def load_cells_index(tech_dir: Path) -> Tuple[Dict[str, int], int]:
    cells = load_json(tech_dir / "cells.json")["cells"]
    name_to_id = {c["name"]: int(c["id"]) for c in cells}
    return name_to_id, len(cells)  # unknown id = len(cells)


def load_nets(nets_dir: Path) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, int]]:
    """读全部 net_*.json → (net_id → {C,R,use}, driver_pin_key → net_id)。"""
    net_meta: Dict[int, Dict[str, Any]] = {}
    driver_to_net: Dict[str, int] = {}
    for path in glob.glob(str(nets_dir / "net_*.json")):
        j = load_json(Path(path))
        feat = j.get("feature", {}) or {}
        net_meta[int(j["id"])] = {
            "C": feat.get("C"),
            "R": feat.get("R"),
            "use": feat.get("use"),
        }
        for pin in j.get("pins", []):
            if pin.get("driver"):
                if pin.get("i") == "PIN":
                    key = str(pin.get("p"))
                else:
                    key = f"{pin.get('i')}:{pin.get('p')}"
                driver_to_net.setdefault(key, int(j["id"]))
    return net_meta, driver_to_net


def load_wire_paths(wp_dir: Path) -> List[Dict[str, Any]]:
    """读全部 wire_path_*.json → 每条 path: points / sum_incr_ns / endpoint(原始)。"""
    out: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(str(wp_dir / "wire_path_*.json")), key=lambda p: int(re.search(r"wire_path_(\d+)", p).group(1))):
        arr = load_json(Path(path))
        points: List[str] = []
        incr = 0.0
        for item in arr if isinstance(arr, list) else []:
            if not isinstance(item, dict):
                continue
            for v in item.values():
                if isinstance(v, dict) and "Point" in v:
                    points.append(str(v["Point"]))
                if isinstance(v, dict) and "Incr" in v and v["Incr"] is not None:
                    incr += float(v["Incr"])
        if not points:
            continue
        out.append({"points": points, "sum_incr_ns": incr, "endpoint_raw": points[-1]})
    return out


# ==================== 图构建 ====================


def build_graph_for_strategy(
    design: str,
    strategy: str,
    cts_dir: Path,
    route_rpt_path: Path,
    pin_caps: Dict[str, Dict[str, float]],
    cells_name_to_id: Dict[str, int],
    unknown_cell_id: int,
    normalizer_stats: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Dict[str, Any], Dict[str, Any]]:
    """构建一个 (design, strategy) 的 cts 图 + path 标签。

    Returns: (dgl_graph, graph_meta, label_stats)
    """
    t0 = time.time()
    wg = load_json(cts_dir / "wire_graph" / "timing_wire_graph.json")
    nodes = wg["nodes"]
    edges = wg["edges"]
    design_info = load_json(cts_dir / "design_info.json")
    net_meta, driver_to_net = load_nets(cts_dir / "nets")
    wire_paths = load_wire_paths(cts_dir / "wire_paths")
    rpt_result = parse_endpoint_complete_rpt(route_rpt_path)
    rpt_by_key, _ = build_endpoint_lookup(rpt_result.records)

    N = len(nodes)
    name_to_nid: Dict[str, int] = {}
    node_feat = np.zeros((N, len(NODE_SCHEMA)), dtype=np.float32)
    for i, n in enumerate(nodes):
        name = str(n["name"])
        name_to_nid[name] = i
        name_to_nid[name.replace(":", "/")] = i  # 兼容 "inst/pin" 形式
        feat = n.get("node_feature", {}) or {}
        coord = feat.get("node_coord") or [0.0, 0.0]
        cell = str(feat.get("cell_name", "NA"))
        is_input = bool(feat.get("is_input", False))
        node_feat[i, 0] = float(coord[0])
        node_feat[i, 1] = float(coord[1])
        node_feat[i, 2] = float(cells_name_to_id.get(cell, unknown_cell_id))
        if is_input and ":" in name:
            inst, pin = name.rsplit(":", 1)
            node_feat[i, 3] = float(pin_caps.get(cell, {}).get(pin, 0.0))
        node_feat[i, 4] = 1.0 if n.get("is_port") else 0.0
        node_feat[i, 5] = 1.0 if is_input else 0.0
        node_feat[i, 6] = 1.0 if feat.get("is_endpoint") else 0.0
        node_feat[i, 7] = float(feat.get("fanout_num") or 0)

    src = np.array([int(e["from_node"]) for e in edges], dtype=np.int64)
    dst = np.array([int(e["to_node"]) for e in edges], dtype=np.int64)
    node_names = [str(n["name"]) for n in nodes]
    E = len(edges)
    edge_feat = np.zeros((E, len(EDGE_SCHEMA)), dtype=np.float32)
    net_id_per_edge = np.full((E,), -1, dtype=np.int64)
    for i, e in enumerate(edges):
        ef = e.get("edge_feature", {}) or {}
        is_net = bool(e.get("is_net_edge", False))
        edge_feat[i, 0] = 1.0 if is_net else 0.0
        d4 = ef.get("edge_delay") or [0.0, 0.0, 0.0, 0.0]
        for k in range(4):
            v = d4[k] if k < len(d4) else None
            edge_feat[i, 1 + k] = float(v) if v is not None else 0.0
        if is_net:
            u_name = node_names[int(e["from_node"])]
            net_id = driver_to_net.get(u_name)
            if net_id is not None:
                net_id_per_edge[i] = net_id
                meta = net_meta.get(net_id, {})
                edge_feat[i, 5] = float(meta.get("C")) if meta.get("C") is not None else 0.0
                edge_feat[i, 6] = float(meta.get("R")) if meta.get("R") is not None else 0.0
                edge_feat[i, 7] = 1.0 if str(meta.get("use")) == "CLOCK" else 0.0

    if HAS_DGL:
        g = dgl.graph((torch.from_numpy(src), torch.from_numpy(dst)), num_nodes=N)
        g.ndata["feat"] = torch.from_numpy(node_feat)
        g.edata["feat"] = torch.from_numpy(edge_feat)
    else:
        g = None

    # ---- path 标签 ----
    endpoints: List[str] = []
    node_ids_flat: List[int] = []
    node_ptr: List[int] = [0]
    baseline: List[float] = []
    target_delay: List[float] = []
    target_slack: List[float] = []
    label_mask: List[float] = []
    for wp in wire_paths:
        ep_raw = wp["endpoint_raw"]
        ep_norm = normalize_point_name(ep_raw)
        endpoints.append(ep_norm)
        ids = []
        for pt in wp["points"]:
            key = normalize_point_name(pt)
            nid = name_to_nid.get(key, name_to_nid.get(key.rsplit("/", 1)[0] + ":" + key.rsplit("/", 1)[-1], -1))
            ids.append(nid if nid is not None else -1)
        node_ids_flat.extend(ids)
        node_ptr.append(len(node_ids_flat))
        base_delay = wp["sum_incr_ns"]
        baseline.append(base_delay)
        rec = label_for_endpoint(rpt_by_key, ep_raw, "max")
        if rec is not None and rec.data_arrival_time_ns is not None:
            target_delay.append(rec.data_arrival_time_ns)
            target_slack.append(rec.slack_ns if rec.slack_ns is not None else 0.0)
            label_mask.append(1.0)
        else:
            target_delay.append(base_delay)  # fallback：route delay 未知时用 cts delay
            target_slack.append(0.0)
            label_mask.append(0.0)

    clock_periods = design_info.get("clock_periods_s") or {}
    clock_period_ns = (list(clock_periods.values())[0] * 1e9) if clock_periods else None

    n_paths = len(endpoints)
    timing_paths = {
        "schema": SCHEMA_NAME,
        "endpoints": endpoints,
        "node_ids_flat": torch.tensor(node_ids_flat, dtype=torch.int64),
        "node_ptr": torch.tensor(node_ptr, dtype=torch.int64),
        "baseline_cts": torch.tensor(baseline, dtype=torch.float32).reshape(-1, 1),   # cts 路径延迟（Incr 累加）
        "target_route": torch.stack(
            [torch.tensor(target_delay, dtype=torch.float32),
             torch.tensor(target_slack, dtype=torch.float32)], dim=1
        ),  # route 标签 [delay, slack]，未匹配处为 fallback
        "label_mask": torch.tensor(label_mask, dtype=torch.float32).reshape(-1, 1),
        "clock_period_ns": clock_period_ns,
    }

    graph_meta = {
        "schema": SCHEMA_NAME,
        "design": design,
        "strategy": strategy,
        "stage": "cts",
        "design_name": design_info.get("design_name", design),
        "num_nodes": N,
        "num_edges": E,
        "net_edge_count": int((edge_feat[:, 0] > 0.5).sum()),
        "cell_arc_count": int((edge_feat[:, 0] <= 0.5).sum()),
        "node_feat_dim": len(NODE_SCHEMA),
        "edge_feat_dim": len(EDGE_SCHEMA),
        "min_routing_layer": design_info.get("min_routing_layer"),
        "max_routing_layer": design_info.get("max_routing_layer"),
        "min_clk_routing_layer": design_info.get("min_clk_routing_layer"),
        "max_clk_routing_layer": design_info.get("max_clk_routing_layer"),
        "routing_layer_adjustment": design_info.get("routing_layer_adjustment"),
        "clock_periods_s": clock_periods,
        "die_area_um2": design_info.get("die_area_um2"),
        "core_area_um2": design_info.get("core_area_um2"),
        "utilization": design_info.get("utilization"),
        "rpt_summary": rpt_result.summary,
        "build_sec": round(time.time() - t0, 2),
    }

    label_stats = {
        "design": design,
        "strategy": strategy,
        "num_paths": n_paths,
        "num_labeled": int(sum(label_mask)),
        "num_fallback": int(n_paths - sum(label_mask)),
        "clock_period_ns": clock_period_ns,
    }
    return (g, node_feat, edge_feat, src, dst), graph_meta, timing_paths, label_stats


# ==================== 主流程 ====================


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default="/mnt/hgfs/host_files/dataset/asap7/parsed_ml_dataset_asap7")
    ap.add_argument("--out-root", default="/mnt/hgfs/host_files/dataset/asap7/graphs_cts_v1")
    ap.add_argument("--design", action="append", default=[], help="只处理指定 design（可重复）")
    ap.add_argument("--strategy", action="append", default=[], help="只处理指定 strategy（可重复）")
    ap.add_argument("--split-json", default="", help="可选：自定义 split JSON {train:[],val:[],test:[]}")
    ap.add_argument("--liberty-path", default="", help="merged.lib 路径（默认取第一个 design 的 design_info.liberty_file）")
    ap.add_argument("--skip-existing", action="store_true", help="已存在输出时跳过")
    ap.add_argument("--limit", type=int, default=0, help="调试：最多处理 N 个 (design,strategy)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    if not HAS_DGL:
        print("[WARN] dgl 未安装：仍会生成 timing_paths.pt 与 meta，但跳过 .dgl 保存。")
    data_root = Path(args.data_root)
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    splits = DEFAULT_SPLITS
    if args.split_json:
        splits = json.loads(Path(args.split_json).read_text(encoding="utf-8"))
    design_to_split = {d: s for s, ds in splits.items() for d in ds}

    designs = sorted(
        d for d in os.listdir(data_root)
        if os.path.isdir(data_root / d) and d in design_to_split
    )
    if args.design:
        designs = [d for d in designs if d in set(args.design)]

    # liberty：解析一次全库共用（实测所有 design 的 merged.lib md5 相同）
    liberty_path = Path(args.liberty_path) if args.liberty_path else None
    pin_caps: Dict[str, Dict[str, float]] = {}
    if liberty_path is None:
        first_di = data_root / designs[0] / "cts" / "vectors" / f"{designs[0]}_cts_vectors" / "vectors"
        first_di = next(iter(glob.glob(str(first_di / "*" / "design_info.json"))), None)
        if first_di:
            liberty_path = Path(load_json(Path(first_di)).get("liberty_file", ""))
    if liberty_path and liberty_path.is_file():
        pin_caps = load_pin_caps_for_lib(liberty_path)
    else:
        print(f"[WARN] liberty 文件不存在: {liberty_path}；sink pin cap 将全为 0")

    write_json_path = out_root / "schema.json"
    write_json_path.write_text(
        json.dumps(
            {
                "schema": SCHEMA_NAME,
                "node_schema": [{"index": i, "name": n, "type": t} for i, (n, t) in enumerate(NODE_SCHEMA)],
                "edge_schema": [{"index": i, "name": n, "type": t} for i, (n, t) in enumerate(EDGE_SCHEMA)],
                "splits": splits,
                "note": "stage=cts 特征 → route 标签；sink pin cap 来自 liberty，net C 挂 net 边",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_rows: List[Dict[str, Any]] = []
    processed = 0
    t_start = time.time()
    for design in designs:
        split = design_to_split[design]
        cts_base = data_root / design / "cts" / "vectors" / f"{design}_cts_vectors" / "vectors"
        route_base = data_root / design / "route" / "vectors" / f"{design}_route_vectors" / "vectors"
        strategies = sorted(os.listdir(cts_base)) if cts_base.is_dir() else []
        if args.strategy:
            strategies = [s for s in strategies if s in set(args.strategy)]
        for strategy in strategies:
            cts_dir = cts_base / strategy
            route_dir = route_base / strategy
            out_dir = out_root / split / design / strategy
            dgl_out = out_dir / f"{design}__{strategy}__cts.dgl"
            tp_out = out_dir / f"{design}__{strategy}__cts.timing_paths.pt"
            if args.skip_existing and tp_out.is_file() and (dgl_out.is_file() or not HAS_DGL):
                print(f"[SKIP] {design}/{strategy} 已存在")
                continue
            rpts = sorted(glob.glob(str(route_dir / "*.rpt")))
            if not rpts or os.path.getsize(rpts[0]) == 0:
                print(f"[SKIP] {design}/{strategy}: route rpt 缺失")
                continue
            cells_name_to_id, unknown_cell_id = load_cells_index(cts_dir / "tech")
            try:
                (g, node_feat, edge_feat, src, dst), meta, timing_paths, label_stats = build_graph_for_strategy(
                    design, strategy, cts_dir, Path(rpts[0]), pin_caps, cells_name_to_id, unknown_cell_id
                )
            except Exception as exc:
                print(f"[FAIL] {design}/{strategy}: {exc}")
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            if g is not None:
                dgl.save_graphs(str(dgl_out), [g])
            torch.save(timing_paths, str(tp_out))
            (out_dir / "graph_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary_rows.append({**label_stats, "split": split})
            print(
                f"[OK] {split}/{design}/{strategy}: nodes={meta['num_nodes']} edges={meta['num_edges']} "
                f"paths={label_stats['num_paths']} labeled={label_stats['num_labeled']} "
                f"fallback={label_stats['num_fallback']} ({meta['build_sec']}s)"
            )
            processed += 1
            if args.limit and processed >= args.limit:
                break
        if args.limit and processed >= args.limit:
            break

    (out_root / "labels_summary.json").write_text(
        json.dumps({"schema": SCHEMA_NAME, "rows": summary_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[DONE] {processed} graphs in {time.time()-t_start:.0f}s → {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
