#!/usr/bin/env python3
"""Build statistics and figures for the ASAP7 CTS-to-Route dataset."""

from __future__ import annotations

import json
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
CODE = Path(r"E:\CTS\code")
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))

from parse_endpoint_complete_rpt import parse_endpoint_complete_rpt  # noqa: E402


DESIGNS = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and p.name != "dataset_card")
COLORS = plt.cm.tab20(np.linspace(0, 1, max(len(DESIGNS), 1)))
COLOR = dict(zip(DESIGNS, COLORS))


def json_load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def strategy_dirs(design: str, stage: str) -> list[Path]:
    base = ROOT / design / stage / "vectors"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("design_info.json") if p.parent.is_dir())


def report_path(info_path: Path) -> Path | None:
    info = json_load(info_path)
    design_name = str(info.get("design_name", info.get("design", info_path.parents[0].name)))
    candidates = [info_path.parent / f"{design_name}.rpt", *info_path.parent.glob("*.rpt")]
    return next((p for p in candidates if p.is_file()), None)


def stage_hash(info_path: Path) -> str | None:
    rpt = report_path(info_path)
    if rpt is None:
        return None
    h = hashlib.sha256()
    with rpt.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def net_records(info_path: Path) -> dict[str, dict[str, Any]]:
    net_dir = info_path.parent / "nets"
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(net_dir.glob("net_*.json")):
        try:
            obj = json_load(path)
        except Exception:
            continue
        name = str(obj.get("name", ""))
        pins = obj.get("pins") or []
        pin_sig = tuple(sorted((str(p.get("i", "")), str(p.get("p", "")), int(p.get("driver", -1))) for p in pins))
        if name:
            records[name] = {"file": path.name, "obj": obj, "pin_sig": pin_sig}
    return records


def net_matching_stats() -> tuple[pd.DataFrame, pd.DataFrame]:
    summary: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    for design in DESIGNS:
        cts_map = {p.parent.name: p for p in strategy_dirs(design, "cts")}
        route_map = {p.parent.name: p for p in strategy_dirs(design, "route")}
        for strategy in sorted(set(cts_map) & set(route_map)):
            cts, route = net_records(cts_map[strategy]), net_records(route_map[strategy])
            cts_names, route_names = set(cts), set(route)
            exact = cts_names & route_names
            cts_only, route_only = cts_names - route_names, route_names - cts_names
            cts_by_sig = defaultdict(list)
            route_by_sig = defaultdict(list)
            for name in cts_only:
                cts_by_sig[cts[name]["pin_sig"]].append(name)
            for name in route_only:
                route_by_sig[route[name]["pin_sig"]].append(name)
            signature_matches = sum(min(len(cts_by_sig[s]), len(route_by_sig[s])) for s in set(cts_by_sig) & set(route_by_sig))
            union = cts_names | route_names
            row = {
                "design": design, "strategy": strategy,
                "cts_nets": len(cts_names), "route_nets": len(route_names),
                "exact_name_matches": len(exact), "cts_only": len(cts_only), "route_only": len(route_only),
                "signature_matches_among_name_mismatch": signature_matches,
                "matched_after_signature": len(exact) + signature_matches,
                "match_rate_over_cts": (len(exact) + signature_matches) / len(cts_names) if cts_names else np.nan,
                "match_rate_over_route": (len(exact) + signature_matches) / len(route_names) if route_names else np.nan,
                "jaccard_name_match": len(exact) / len(union) if union else np.nan,
            }
            summary.append(row)
            for name in sorted(cts_only):
                detail.append({"design": design, "strategy": strategy, "status": "cts_only", "cts_name": name, "route_name": "", "pin_signature_match": False})
            for name in sorted(route_only):
                candidates = route_by_sig.get(cts.get(name, {}).get("pin_sig", ()), [])
                detail.append({"design": design, "strategy": strategy, "status": "route_only", "cts_name": "", "route_name": name, "pin_signature_match": False})
            for sig in sorted(set(cts_by_sig) & set(route_by_sig), key=str):
                for c, r in zip(sorted(cts_by_sig[sig]), sorted(route_by_sig[sig])):
                    detail.append({"design": design, "strategy": strategy, "status": "renamed_signature_match", "cts_name": c, "route_name": r, "pin_signature_match": True})
    return pd.DataFrame(summary), pd.DataFrame(detail)


def load_design_stats() -> tuple[pd.DataFrame, pd.DataFrame]:
    design_rows: list[dict[str, Any]] = []
    path_rows: list[dict[str, Any]] = []
    for design in DESIGNS:
        cts_map = {p.parent.name: p for p in strategy_dirs(design, "cts")}
        route_map = {p.parent.name: p for p in strategy_dirs(design, "route")}
        for strategy in sorted(set(cts_map) | set(route_map)):
            row: dict[str, Any] = {"design": design, "strategy": strategy}
            stage_data: dict[str, dict[str, Any]] = {}
            for stage, mapping in (("cts", cts_map), ("route", route_map)):
                p = mapping.get(strategy)
                if p is None:
                    continue
                info = json_load(p)
                stage_data[stage] = info
                row[f"{stage}_period_ns"] = min(
                    [float(x) * 1e9 for x in (info.get("clock_periods_s") or {}).values() if float(x) > 0],
                    default=np.nan,
                )
                for key in ("utilization", "die_area_um2", "core_area_um2", "instance_count", "net_count", "port_count", "total_inst_area_um2"):
                    row[f"{stage}_{key}"] = info.get(key, np.nan)
                rpt = report_path(p)
                row[f"{stage}_report_sha256"] = stage_hash(p)
                if rpt:
                    parsed = parse_endpoint_complete_rpt(rpt, time_unit_to_ns=1e-3)
                    records = [r for r in parsed.records if r.status == "ok"]
                    max_records = [r for r in records if r.path_type == "max"]
                    min_records = [r for r in records if r.path_type == "min"]
                    row[f"{stage}_report_paths"] = len(records)
                    row[f"{stage}_max_paths"] = len(max_records)
                    row[f"{stage}_min_paths"] = len(min_records)
                    row[f"{stage}_arrival_mean_ns"] = float(np.nanmean([r.data_arrival_time_ns for r in records])) if records else np.nan
                    row[f"{stage}_slack_mean_ns"] = float(np.nanmean([r.slack_ns for r in records])) if records else np.nan
                    row[f"{stage}_slack_min_ns"] = float(np.nanmin([r.slack_ns for r in records])) if records else np.nan
                    for r in records:
                        path_rows.append({
                            "design": design,
                            "strategy": strategy,
                            "stage": stage,
                            "path_type": r.path_type,
                            "arrival_ns": r.data_arrival_time_ns,
                            "required_ns": r.data_required_time_ns,
                            "slack_ns": r.slack_ns,
                            "slack_met": r.slack_met,
                        })
            for key in ("utilization", "die_area_um2", "core_area_um2", "instance_count", "net_count", "total_inst_area_um2"):
                c, r = row.get(f"cts_{key}"), row.get(f"route_{key}")
                if pd.notna(c) and pd.notna(r):
                    row[f"delta_{key}"] = float(r) - float(c)
                    row[f"ratio_{key}"] = float(r) / float(c) if float(c) else np.nan
            design_rows.append(row)
    return pd.DataFrame(design_rows), pd.DataFrame(path_rows)


def savefig(name: str) -> None:
    plt.tight_layout()
    plt.savefig(OUT / "figures" / name, dpi=180, bbox_inches="tight")
    plt.close()


def plot_path_scatter(paths: pd.DataFrame) -> None:
    for stage, title, y in (("route", "Route data arrival time", "arrival_ns"), ("route", "Route slack", "slack_ns")):
        plt.figure(figsize=(12, 6))
        for design in DESIGNS:
            d = paths[(paths.stage == stage) & (paths.design == design)].dropna(subset=[y])
            if d.empty:
                continue
            # Deterministic downsampling keeps the GitHub figure lightweight.
            if len(d) > 12000:
                d = d.iloc[np.linspace(0, len(d) - 1, 12000).astype(int)]
            x = np.arange(len(d))
            plt.scatter(x, d[y], s=2, alpha=0.25, color=COLOR[design], label=design)
        plt.xlabel("Path record index within plotted design")
        plt.ylabel(f"{y.replace('_', ' ')} (ns)")
        plt.title(f"{title} distribution by design")
        plt.grid(alpha=0.2)
        plt.legend(ncol=4, fontsize=8, frameon=False)
        savefig("route_arrival_scatter.png" if y == "arrival_ns" else "route_slack_scatter.png")


def plot_distributions(paths: pd.DataFrame) -> None:
    for column, filename, title in (("arrival_ns", "arrival_boxplot.png", "Data arrival time by design"), ("slack_ns", "slack_boxplot.png", "Slack by design")):
        plt.figure(figsize=(12, 6))
        values, labels = [], []
        for design in DESIGNS:
            d = paths[(paths.stage == "route") & (paths.design == design)][column].dropna()
            if not d.empty:
                values.append(d.to_numpy())
                labels.append(design)
        plt.boxplot(values, labels=labels, showfliers=False)
        plt.ylabel(f"{column.replace('_', ' ')} (ns)")
        plt.title(title)
        plt.grid(axis="y", alpha=0.2)
        savefig(filename)


def plot_stage_change(designs: pd.DataFrame) -> None:
    cols = [("arrival_mean_ns", "Mean data arrival time"), ("slack_mean_ns", "Mean slack")]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, (col, title) in zip(axes, cols):
        for design in DESIGNS:
            d = designs[designs.design == design].dropna(subset=[f"cts_{col}", f"route_{col}"])
            if d.empty:
                continue
            ax.scatter(d[f"cts_{col}"], d[f"route_{col}"], s=28, alpha=0.75, color=COLOR[design], label=design)
        lim = ax.get_xlim() + ax.get_ylim()
        lo, hi = min(lim), max(lim)
        ax.plot([lo, hi], [lo, hi], "k--", lw=0.8)
        ax.set_xlabel(f"CTS {title.lower()} (ns)")
        ax.set_ylabel(f"Route {title.lower()} (ns)")
        ax.set_title(f"CTS to Route: {title}")
        ax.grid(alpha=0.2)
    axes[1].legend(ncol=2, fontsize=8, frameon=False)
    savefig("cts_to_route_stage_change.png")


def plot_net_matching(nets: pd.DataFrame) -> None:
    if nets.empty:
        return
    agg = nets.groupby("design", as_index=False).agg(
        exact_name_rate=("jaccard_name_match", "mean"),
        cts_anchored_rate=("match_rate_over_cts", "mean"),
        route_anchored_rate=("match_rate_over_route", "mean"),
        cts_only=("cts_only", "sum"),
        route_only=("route_only", "sum"),
    )
    x = np.arange(len(agg))
    width = 0.25
    plt.figure(figsize=(13, 6))
    plt.bar(x - width, agg.exact_name_rate, width, label="Exact net-name Jaccard")
    plt.bar(x, agg.cts_anchored_rate, width, label="Matched / CTS nets")
    plt.bar(x + width, agg.route_anchored_rate, width, label="Matched / Route nets")
    plt.xticks(x, agg.design, rotation=35, ha="right")
    plt.ylim(0, 1.05)
    plt.ylabel("Match rate")
    plt.title("CTS-to-Route net matching by design")
    plt.grid(axis="y", alpha=0.2)
    plt.legend(frameon=False)
    savefig("cts_to_route_net_match_rate.png")

    plt.figure(figsize=(13, 6))
    plt.bar(x - width / 2, agg.cts_only, width, label="CTS-only net names")
    plt.bar(x + width / 2, agg.route_only, width, label="Route-only net names")
    plt.xticks(x, agg.design, rotation=35, ha="right")
    plt.ylabel("Number of net names")
    plt.title("CTS-only and Route-only net names")
    plt.grid(axis="y", alpha=0.2)
    plt.legend(frameon=False)
    savefig("cts_to_route_net_mismatch_counts.png")


def main() -> None:
    (OUT / "figures").mkdir(parents=True, exist_ok=True)
    designs, paths = load_design_stats()
    nets, net_detail = net_matching_stats()
    designs.to_csv(OUT / "design_strategy_summary.csv", index=False)
    paths.to_csv(OUT / "path_level_summary.csv", index=False)
    nets.to_csv(OUT / "net_matching_summary.csv", index=False)
    net_detail.to_csv(OUT / "net_matching_detail.csv", index=False)
    plot_path_scatter(paths)
    plot_distributions(paths)
    plot_stage_change(designs)
    plot_net_matching(nets)
    summary = {
        "dataset_root": str(ROOT),
        "designs": DESIGNS,
        "num_designs": len(DESIGNS),
        "num_design_strategy_records": int(len(designs)),
        "path_rows": int(len(paths)),
        "path_rows_by_stage": paths.groupby("stage").size().to_dict() if not paths.empty else {},
        "path_rows_by_design_route": paths[paths.stage == "route"].groupby("design").size().to_dict() if not paths.empty else {},
        "net_matching": {
            "records": int(len(nets)),
            "mean_exact_name_match_rate": float(nets["jaccard_name_match"].mean()) if not nets.empty else None,
            "mean_cts_anchored_match_rate": float(nets["match_rate_over_cts"].mean()) if not nets.empty else None,
            "mean_route_anchored_match_rate": float(nets["match_rate_over_route"].mean()) if not nets.empty else None,
        },
        "notes": [
            "arrival_ns is the report data arrival time, not a clock-period-normalized delay.",
            "slack_ns is parsed from endpoint-complete reports and is in ns.",
            "The route report is used for the primary supervised-target distribution.",
        ],
    }
    (OUT / "dataset_statistics.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
