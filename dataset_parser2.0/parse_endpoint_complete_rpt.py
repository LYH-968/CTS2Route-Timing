#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenSTA "Endpoint-complete" 时序报告解析器：把 *.rpt 转成逐 endpoint 时序标签。

背景
----
旧 iDATA 流水线（build_dataset_tm_pow_v3_dbu_topk_debug_abs_modified.py）里的
``parse_ista_rpt_summary_table`` 只能解析 iSTA 的 markdown 8 列汇总表。新数据集
（/mnt/hgfs/host_files/dataset/asap7）的 *.rpt 由 generate_ml_reports.py 生成，
是 OpenSTA 的 "Endpoint-complete" 格式：每个 endpoint 一个 max/min 小节，含完整
路径明细和 arrival / required / slack 汇总块，旧解析器读不出任何行。

本模块负责把该格式解析为结构化 endpoint 时序标签，作为 cts→route 时序预测的
route 端监督来源。

输入格式（每小节）::

    ==========================================================================
    endpoint 0 max _25090_/D
    --------------------------------------------------------------------------
    Startpoint: mem_ready (input port clocked by clk)
    Endpoint: _25090_ (edge-triggered flip-flop clocked by clk)
    Path Group: clk
    Path Type: max

        Cap    Slew   Delay    Time   Description
    -----------------------------------------------------------------------
                         446.50   data arrival time      <- 数据路径表末尾
                        1071.41   data required time     <- 时钟路径表末尾
    -----------------------------------------------------------------------
                        1071.41   data required time      <- 汇总块（required）
                        -446.50   data arrival time      <- 汇总块（arrival，带负号）
    -----------------------------------------------------------------------
                         624.91   slack (MET)            <- slack = required - arrival

    无路径的小节为::

        ==========================================================================
        endpoint 1900 max trace_data[8]
        --------------------------------------------------------------------------
        No paths found.

注意：min 小节（hold）的 slack = arrival - required，与 max 相反。

输出
----
每条记录（records[i]，JSON 可直接序列化）::

    {
      "endpoint_idx": 0,
      "path_type": "max",            # max | min
      "endpoint_pin": "_25090_/D",   # 与 wire_paths 的 Point 对齐用（去 " (cell)" 后）
      "startpoint": "mem_ready",
      "endpoint_inst": "_25090_",
      "endpoint_desc": "edge-triggered flip-flop clocked by clk",
      "path_group": "clk",
      "status": "ok",                # ok | no_paths | unconstrained
      "data_arrival_time_ns": 446.5,
      "data_required_time_ns": 1071.41,
      "slack_ns": 624.91,
      "slack_met": true
    }

设计级汇总（summary，口径与 dataset_parser/verify_features.py 一致）::

    {
      "num_sections": 3808,          # endpoint max/min 小节总数
      "num_with_paths": 2954,        # status == ok
      "num_no_paths": 854,
      "endpoint_count_max": 1904,    # 有路径的 max 小节数
      "endpoint_count_min": 1904,
      "wns_max": ...,                # min(slack) over max
      "tns_max": ...,                # sum(min(slack, 0)) over max
      "wns_min": ..., "tns_min": ...
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

SECTION_START_RE = re.compile(r"^endpoint\s+(\d+)\s+(max|min)\s+(\S+)\s*$")
META_RE = re.compile(r"^(Startpoint|Endpoint|Path Group|Path Type):\s*(.*)$")
NUM_LINE_RE = re.compile(
    r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+(data arrival time|data required time)\s*$"
)
SLACK_LINE_RE = re.compile(
    r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+slack \((MET|VIOLATED)\)\s*$"
)
NO_PATHS_RE = re.compile(r"^\s*No paths found\.?\s*$")
UNCONSTRAINED_RE = re.compile(r"unconstrained", re.IGNORECASE)

# "Endpoint: xxx (edge-triggered flip-flop clocked by clk)" -> (inst, desc)
ENDPOINT_META_RE = re.compile(r"^(\S+)\s*(\(.*\))?\s*$")


@dataclass
class EndpointRecord:
    """单个 endpoint 的 max/min 时序标签。"""

    endpoint_idx: int
    path_type: str  # "max" | "min"
    endpoint_pin: str  # 形如 "_25090_/D"，与 wire_paths Point 对齐
    startpoint: str = ""
    endpoint_inst: str = ""
    endpoint_desc: str = ""
    path_group: str = ""
    status: str = "ok"  # ok | no_paths | unconstrained
    data_arrival_time_ns: Optional[float] = None
    data_required_time_ns: Optional[float] = None
    slack_ns: Optional[float] = None
    slack_met: Optional[bool] = None
    line_no: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RptParseResult:
    """解析结果：逐 endpoint 记录 + 设计级汇总。"""

    source: str
    format: str = "opensta_endpoint_complete_v1"
    records: List[EndpointRecord] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": self.format,
            "source": self.source,
            "summary": self.summary,
            "records": [r.to_dict() for r in self.records],
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for rec in self.records:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")


def _parse_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_endpoint_complete_rpt(
    rpt_path: Path | str,
    limit: int = 0,
) -> RptParseResult:
    """流式解析 Endpoint-complete 报告。

    Args:
        rpt_path: *.rpt 路径（generate_ml_reports.py 生成的 path_report.rpt）。
        limit: >0 时最多解析前 limit 个小节（调试用）。

    Returns:
        RptParseResult，含 records 与 summary。
    """
    records: List[EndpointRecord] = []

    cur: Optional[EndpointRecord] = None
    arrival: Optional[float] = None
    required: Optional[float] = None

    def finalize() -> None:
        nonlocal cur, arrival, required
        if cur is None:
            return
        cur.data_arrival_time_ns = arrival
        cur.data_required_time_ns = required
        records.append(cur)
        cur = None
        arrival = required = None

    with open(rpt_path, "r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            m = SECTION_START_RE.match(line)
            if m:
                finalize()
                if limit and len(records) >= int(limit):
                    break
                cur = EndpointRecord(
                    endpoint_idx=int(m.group(1)),
                    path_type=m.group(2),
                    endpoint_pin=m.group(3),
                    line_no=line_no,
                )
                continue

            if cur is None:
                continue

            mm = META_RE.match(line)
            if mm:
                key, value = mm.group(1), mm.group(2).strip()
                if key == "Startpoint":
                    cur.startpoint = value
                elif key == "Endpoint":
                    em = ENDPOINT_META_RE.match(value)
                    if em:
                        cur.endpoint_inst = em.group(1)
                        cur.endpoint_desc = (em.group(2) or "").strip()
                    else:
                        cur.endpoint_inst = value
                elif key == "Path Group":
                    cur.path_group = value
                continue

            if NO_PATHS_RE.match(line):
                cur.status = "no_paths"
                continue
            if UNCONSTRAINED_RE.search(line) and cur.status == "ok":
                # 部分报告在 "No paths found." 之外还会打印 unconstrained 提示
                cur.status = "unconstrained"
                continue

            nm = NUM_LINE_RE.match(line)
            if nm:
                value = _parse_float(nm.group(1))
                if nm.group(2) == "data arrival time" and arrival is None:
                    arrival = value
                elif nm.group(2) == "data required time" and required is None:
                    required = value
                continue

            sm = SLACK_LINE_RE.match(line)
            if sm:
                cur.slack_ns = _parse_float(sm.group(1))
                cur.slack_met = sm.group(2) == "MET"
                continue

    finalize()

    result = RptParseResult(source=str(rpt_path), records=records)
    result.summary = compute_summary(records)
    return result


def compute_summary(records: List[EndpointRecord]) -> Dict[str, Any]:
    """设计级汇总。WNS/TNS 口径与 verify_features.py 一致：
    wns = min(slack)，tns = sum(min(slack, 0))，分 max/min 分别统计。
    """
    ok = [r for r in records if r.status == "ok"]
    stats: Dict[str, Any] = {
        "num_sections": len(records),
        "num_with_paths": len(ok),
        "num_no_paths": sum(1 for r in records if r.status == "no_paths"),
        "num_unconstrained": sum(1 for r in records if r.status == "unconstrained"),
        "unique_endpoint_pins": len({r.endpoint_pin for r in records}),
    }
    for ptype in ("max", "min"):
        slacks = [r.slack_ns for r in ok if r.path_type == ptype and r.slack_ns is not None]
        stats[f"endpoint_count_{ptype}"] = len(slacks)
        stats[f"wns_{ptype}"] = min(slacks) if slacks else None
        stats[f"tns_{ptype}"] = sum(s for s in slacks if s < 0) if slacks else None
        stats[f"violated_{ptype}"] = sum(1 for r in ok if r.path_type == ptype and r.slack_met is False)
    return stats


# ==================== 与 wire_paths 的对齐辅助 ====================


def normalize_point_name(point: str) -> str:
    """wire_paths 的 Point 形如 "inst/pin (CELL)"，rpt 的 endpoint_pin 为 "inst/pin"。

    去掉尾部 " (...)"（cell 名）后返回；与旧 builder 的 _normalize_point_name 一致。
    """
    s = str(point).strip()
    s = re.sub(r"\s*\(.*\)\s*$", "", s)
    return s.strip()


def build_endpoint_lookup(
    records: List[EndpointRecord],
) -> Tuple[Dict[Tuple[str, str], EndpointRecord], Dict[str, Dict[str, EndpointRecord]]]:
    """建索引。

    Returns:
        (by_key, by_pin)
        by_key: {(endpoint_pin, path_type) -> record}（仅 status == ok 的记录）
        by_pin: {endpoint_pin -> {"max": rec, "min": rec}}
    """
    by_key: Dict[Tuple[str, str], EndpointRecord] = {}
    by_pin: Dict[str, Dict[str, EndpointRecord]] = {}
    for rec in records:
        if rec.status != "ok":
            continue
        by_key[(rec.endpoint_pin, rec.path_type)] = rec
        by_pin.setdefault(rec.endpoint_pin, {})[rec.path_type] = rec
    return by_key, by_pin


def label_for_endpoint(
    by_key: Dict[Tuple[str, str], EndpointRecord],
    endpoint_point: str,
    path_type: str = "max",
) -> Optional[EndpointRecord]:
    """给一条 wire_path 的 endpoint Point 名找 rpt 标签。

    Args:
        endpoint_point: wire_paths 的最后一个 Point（如 "_25090_/D (DFFHQNx3_ASAP7_75t_R)"）。
        path_type: "max"（setup，训练默认）或 "min"。

    Returns:
        匹配的 EndpointRecord；无匹配返回 None（调用方决定 fallback，如用 wire_paths Incr）。
    """
    pin = normalize_point_name(endpoint_point)
    return by_key.get((pin, path_type))


def selfcheck(records: List[EndpointRecord], tol_ns: float = 0.02) -> Dict[str, Any]:
    """校验 slack 与 arrival/required 的自洽性：
    max: slack ≈ required - arrival；min(hold): slack ≈ arrival - required。

    rpt 数值打印精度为 0.01ns，因此默认容差 0.02ns（OpenSTA 内部四舍五入
    导致 slack 与 required-arrival 差 ±0.01ns 属正常）。
    """
    checked = mism = 0
    for r in records:
        if r.status != "ok" or r.slack_ns is None:
            continue
        if r.data_arrival_time_ns is None or r.data_required_time_ns is None:
            continue
        checked += 1
        expected = (
            r.data_required_time_ns - r.data_arrival_time_ns
            if r.path_type == "max"
            else r.data_arrival_time_ns - r.data_required_time_ns
        )
        if abs(expected - r.slack_ns) > tol_ns:
            mism += 1
    return {"checked": checked, "mismatched": mism}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rpt", required=True, help="Endpoint-complete 报告路径（*.rpt）")
    ap.add_argument("--out-json", default="", help="可选：完整结果（含 records）输出 JSON")
    ap.add_argument("--out-jsonl", default="", help="可选：逐 endpoint 记录输出 JSONL")
    ap.add_argument("--limit", type=int, default=0, help="调试：最多解析前 N 个小节")
    ap.add_argument("--selfcheck", action="store_true", help="校验 slack 与 arrival/required 自洽")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    result = parse_endpoint_complete_rpt(args.rpt, limit=args.limit)
    print("========== Endpoint-complete RPT Parse ==========")
    print(f"source: {args.rpt}")
    for key, value in result.summary.items():
        print(f"  {key}: {value}")
    if args.selfcheck:
        sc = selfcheck(result.records)
        print(f"  selfcheck: checked={sc['checked']} mismatched={sc['mismatched']}")
        if sc["mismatched"]:
            print("[WARN] slack 与 arrival/required 不自洽的小节数 > 0", file=sys.stderr)
    if args.out_json:
        result.write_json(Path(args.out_json))
        print(f"wrote json: {args.out_json}")
    if args.out_jsonl:
        result.write_jsonl(Path(args.out_jsonl))
        print(f"wrote jsonl: {args.out_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
