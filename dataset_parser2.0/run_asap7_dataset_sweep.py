#!/usr/bin/env python3
"""Run an ASAP7 design/strategy sweep and parse gcd-style ML datasets.

The default matrix is 10 designs x 20 strategies. Each job is run
sequentially:

  1. OpenROAD-flow-scripts make finish
  2. dataset_parser/ml_dataset_parser.py --stage all

Use --dry-run before launching a long sweep.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_FLOW_ROOT = Path("/home/lyh/OpenROAD-flow-scripts")
DEFAULT_OUT = DEFAULT_FLOW_ROOT / "parsed_ml_dataset_asap7"
DEFAULT_OPENROAD = DEFAULT_FLOW_ROOT / "tools/install/OpenROAD/bin/openroad"

DEFAULT_DESIGNS = [
    "aes",
    "aes-block",
    "aes-mbff",
    "gcd",
    "ibex",
    "jpeg",
    "ethmac",
    "riscv32i",
    "swerv_wrapper",
    "uart",
]


@dataclass(frozen=True)
class Strategy:
    name: str
    vars: dict[str, str] = field(default_factory=dict)


STRATEGIES = [
    Strategy("util50_den55", {"CORE_UTILIZATION": "50", "PLACE_DENSITY": "0.55"}),
    Strategy("util60_den60", {"CORE_UTILIZATION": "60", "PLACE_DENSITY": "0.60"}),
    Strategy("util65_den65", {"CORE_UTILIZATION": "65", "PLACE_DENSITY": "0.65"}),
    Strategy("util70_den70", {"CORE_UTILIZATION": "70", "PLACE_DENSITY": "0.70"}),
    Strategy("util75_den72", {"CORE_UTILIZATION": "75", "PLACE_DENSITY": "0.72"}),
    Strategy("low_density_den50", {"CORE_UTILIZATION": "55", "PLACE_DENSITY": "0.50"}),
    Strategy("high_density_den76", {"CORE_UTILIZATION": "78", "PLACE_DENSITY": "0.76"}),
    Strategy(
        "place_pad_g1_d1",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT": "1",
            "CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "1",
        },
    ),
    Strategy(
        "place_pad_g2_d1",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT": "2",
            "CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "1",
        },
    ),
    Strategy(
        "place_pad_g2_d2",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT": "2",
            "CELL_PAD_IN_SITES_DETAIL_PLACEMENT": "2",
        },
    ),
    Strategy("route_adjust25", {"CORE_UTILIZATION": "65", "PLACE_DENSITY": "0.65", "ROUTING_LAYER_ADJUSTMENT": "0.25"}),
    Strategy("route_adjust40", {"CORE_UTILIZATION": "65", "PLACE_DENSITY": "0.65", "ROUTING_LAYER_ADJUSTMENT": "0.40"}),
    Strategy("route_adjust60", {"CORE_UTILIZATION": "65", "PLACE_DENSITY": "0.65", "ROUTING_LAYER_ADJUSTMENT": "0.60"}),
    Strategy("route_adjust75", {"CORE_UTILIZATION": "65", "PLACE_DENSITY": "0.65", "ROUTING_LAYER_ADJUSTMENT": "0.75"}),
    Strategy(
        "gpl_routability",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "GPL_ROUTABILITY_DRIVEN": "1",
            "ROUTING_LAYER_ADJUSTMENT": "0.50",
        },
    ),
    Strategy(
        "gpl_timing_off",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "GPL_TIMING_DRIVEN": "0",
            "ROUTING_LAYER_ADJUSTMENT": "0.50",
        },
    ),
    Strategy(
        "cts_small_cluster",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CTS_CLUSTER_SIZE": "10",
            "CTS_CLUSTER_DIAMETER": "20",
        },
    ),
    Strategy(
        "cts_large_cluster",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CTS_CLUSTER_SIZE": "50",
            "CTS_CLUSTER_DIAMETER": "80",
        },
    ),
    Strategy(
        "cts_buf_short_tns50",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CTS_BUF_DISTANCE": "20",
            "TNS_END_PERCENT": "50",
        },
    ),
    Strategy(
        "aspect_wide_margin2",
        {
            "CORE_UTILIZATION": "65",
            "PLACE_DENSITY": "0.65",
            "CORE_ASPECT_RATIO": "1.6",
            "CORE_MARGIN": "2",
        },
    ),
]


def shell_join(cmd: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def strategy_by_name(name: str) -> Strategy:
    for strategy in STRATEGIES:
        if strategy.name == name:
            return strategy
    raise SystemExit(f"unknown strategy: {name}")


def final_odb(flow_root: Path, design: str, variant: str) -> Path:
    return flow_root / "flow/results/asap7" / design / variant / "6_final.odb"


def parsed_graph(out_root: Path, design: str, variant: str) -> Path:
    return out_root / design / "route/vectors" / f"{design}_route_vectors/vectors" / variant / "wire_graph/timing_wire_graph.json"


def command_log_path(log_root: Path, design: str, strategy: str, step: str) -> Path:
    return log_root / design / strategy / f"{step}.log"


def run_logged(cmd: list[str], cwd: Path, log_path: Path, dry_run: bool) -> int:
    print(shell_join(cmd))
    if dry_run:
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"# start: {start}\n")
        log.write(f"# cwd: {cwd}\n")
        log.write(f"# cmd: {shell_join(cmd)}\n\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, text=True)
        log.write(f"\n# end: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"# returncode: {proc.returncode}\n")
        return proc.returncode


def append_status(status_csv: Path, row: dict[str, str], dry_run: bool) -> None:
    if dry_run:
        return
    status_csv.parent.mkdir(parents=True, exist_ok=True)
    exists = status_csv.exists()
    with status_csv.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "time",
                "design",
                "strategy",
                "step",
                "status",
                "returncode",
                "log",
            ],
        )
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequential ASAP7 flow + ML parser sweep.")
    parser.add_argument("--flow-root", default=str(DEFAULT_FLOW_ROOT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--openroad", default=str(DEFAULT_OPENROAD))
    parser.add_argument("--design", action="append", help="Design to run. Repeatable. Default: built-in 10-design set.")
    parser.add_argument("--strategy", action="append", help="Strategy name to run. Repeatable. Default: all 20 strategies.")
    parser.add_argument("--start-index", type=int, default=0, help="Skip jobs before this zero-based matrix index.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of jobs to attempt after --start-index.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument("--resume", action="store_true", help="Skip jobs whose final ODB and parsed route graph already exist.")
    parser.add_argument("--flow-only", action="store_true", help="Only run OpenROAD flow, skip report/vector parsing.")
    parser.add_argument("--parse-only", action="store_true", help="Only run report/vector parsing, skip OpenROAD flow.")
    parser.add_argument("--reports-only", action="store_true", help="Generate ml_reports but do not write vector JSON.")
    parser.add_argument("--skip-reports", action="store_true", help="Parse existing ml_reports instead of regenerating them.")
    parser.add_argument("--stage", choices=["place", "cts", "route", "both", "all"], default="all")
    parser.add_argument("--patch-size-dbu", type=int, default=3330)
    parser.add_argument("--group-path-count", type=int, default=1_000_000)
    parser.add_argument("--endpoint-path-count", type=int, default=1)
    parser.add_argument("--stop-on-error", action="store_true", help="Stop the sweep on the first failed command.")
    args = parser.parse_args()

    flow_root = Path(args.flow_root).resolve()
    out_root = Path(args.out).resolve()
    openroad = Path(args.openroad).resolve()
    script_dir = Path(__file__).resolve().parent
    flow_dir = flow_root / "flow"
    log_root = script_dir / "logs/asap7_sweep"
    status_csv = log_root / "status.csv"
    designs = args.design or DEFAULT_DESIGNS
    strategies = [strategy_by_name(name) for name in args.strategy] if args.strategy else STRATEGIES

    matrix = [(design, strategy) for design in designs for strategy in strategies]
    if args.start_index:
        matrix = matrix[args.start_index :]
    if args.limit:
        matrix = matrix[: args.limit]

    print(f"flow_root: {flow_root}")
    print(f"out:       {out_root}")
    print(f"log_root:  {log_root}")
    print(f"jobs:      {len(matrix)}")
    if args.dry_run:
        print("mode:      dry-run")

    failures = 0
    for job_offset, (design, strategy) in enumerate(matrix, start=args.start_index):
        variant = strategy.name
        config = Path(f"./designs/asap7/{design}/config.mk")
        config_abs = flow_dir / "designs/asap7" / design / "config.mk"
        if not config_abs.exists():
            print(f"[skip] missing config: {config_abs}")
            failures += 1
            continue

        print(f"\n[{job_offset}] design={design} strategy={variant}")
        print(json.dumps(strategy.vars, sort_keys=True))

        if args.resume and final_odb(flow_root, design, variant).exists() and parsed_graph(out_root, design, variant).exists():
            print("[skip] resume: flow and parsed graph already exist")
            continue

        if not args.parse_only:
            make_cmd = [
                "make",
                f"DESIGN_CONFIG={config}",
                f"FLOW_VARIANT={variant}",
                *[f"{key}={value}" for key, value in strategy.vars.items()],
                "finish",
            ]
            log_path = command_log_path(log_root, design, variant, "flow_finish")
            rc = run_logged(make_cmd, flow_dir, log_path, args.dry_run)
            append_status(
                status_csv,
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "design": design,
                    "strategy": variant,
                    "step": "flow_finish",
                    "status": "ok" if rc == 0 else "failed",
                    "returncode": str(rc),
                    "log": str(log_path),
                },
                args.dry_run,
            )
            if rc != 0:
                failures += 1
                if args.stop_on_error:
                    raise SystemExit(rc)
                continue

        if args.flow_only:
            continue

        parse_cmd = [
            sys.executable,
            str(script_dir / "ml_dataset_parser.py"),
            "--flow-root",
            str(flow_root),
            "--platform",
            "asap7",
            "--design",
            design,
            "--variant",
            variant,
            "--scenario",
            variant,
            "--out",
            str(out_root),
            "--stage",
            args.stage,
            "--patch-size-dbu",
            str(args.patch_size_dbu),
            "--group-path-count",
            str(args.group_path_count),
            "--endpoint-path-count",
            str(args.endpoint_path_count),
            "--openroad",
            str(openroad),
        ]
        if args.skip_reports:
            parse_cmd.append("--skip-reports")
        if args.reports_only:
            parse_cmd.append("--reports-only")
        log_path = command_log_path(log_root, design, variant, "parse_dataset")
        rc = run_logged(parse_cmd, flow_root, log_path, args.dry_run)
        append_status(
            status_csv,
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "design": design,
                "strategy": variant,
                "step": "parse_dataset",
                "status": "ok" if rc == 0 else "failed",
                "returncode": str(rc),
                "log": str(log_path),
            },
            args.dry_run,
        )
        if rc != 0:
            failures += 1
            if args.stop_on_error:
                raise SystemExit(rc)

    if failures:
        raise SystemExit(f"{failures} job steps failed; see {status_csv}")


if __name__ == "__main__":
    main()
