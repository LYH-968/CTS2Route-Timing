#!/usr/bin/env python3
"""One-command parser for gcd-style ML datasets from ORFS results."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_FLOW_ROOT = "/home/lyh/OpenROAD-flow-scripts"
DEFAULT_OPENROAD = "/home/lyh/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad"


def discover_designs(flow_root: Path, platform: str, variant: str) -> list[str]:
    results_root = flow_root / "flow" / "results" / platform
    if not results_root.exists():
        raise SystemExit(f"results root does not exist: {results_root}")
    return sorted(child.name for child in results_root.iterdir() if child.is_dir() and (child / variant).is_dir())


def run(cmd: list[str]) -> None:
    print("[run] " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ml_reports and parse ORFS result files into gcd-style ML vector datasets."
    )
    parser.add_argument("--flow-root", default=DEFAULT_FLOW_ROOT)
    parser.add_argument("--platform", default="nangate45")
    parser.add_argument("--variant", default="base")
    parser.add_argument("--design", action="append", help="Design name. Can be used multiple times. Defaults to all designs.")
    parser.add_argument("--dataset-design", default="", help="Output dataset design name. Only valid with one --design.")
    parser.add_argument("--out", default="/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_nangate45")
    parser.add_argument(
        "--cache-root",
        default="",
        help="Directory for ODB-derived intermediate DEF/Verilog files. Defaults outside --out.",
    )
    parser.add_argument("--stage", choices=["place", "cts", "route", "both", "all"], default="both")
    parser.add_argument("--scenario", default="base_default")
    parser.add_argument("--patch-size-dbu", type=int, default=3330)
    parser.add_argument(
        "--group-path-count",
        type=int,
        default=1_000_000,
        help="Maximum paths per path group. Default is high to cover timing endpoints.",
    )
    parser.add_argument(
        "--endpoint-path-count",
        type=int,
        default=1,
        help="Paths per endpoint for OpenSTA report_checks.",
    )
    parser.add_argument("--openroad", default=DEFAULT_OPENROAD)
    parser.add_argument("--results-dir", default="", help="External result directory for one design/variant.")
    parser.add_argument("--reports-dir", default="", help="External ml_reports directory for one design/variant.")
    parser.add_argument("--reports-parent-dir", default="", help="External report directory containing 5_route_drc.rpt.")
    parser.add_argument("--objects-dir", default="", help="External objects directory containing lib/merged.lib.")
    parser.add_argument("--skip-reports", action="store_true", help="Skip OpenSTA/OpenROAD report generation and parse existing reports.")
    parser.add_argument("--reports-only", action="store_true", help="Only generate ml_reports; do not write vector JSON files.")
    parser.add_argument("--continue-on-error", action="store_true", help="Forwarded to report generation.")
    args = parser.parse_args()

    flow_root = Path(args.flow_root).resolve()
    script_dir = Path(__file__).resolve().parent
    designs = args.design or discover_designs(flow_root, args.platform, args.variant)
    if not designs:
        raise SystemExit("no designs found")
    if args.dataset_design and len(designs) != 1:
        raise SystemExit("--dataset-design requires exactly one design")

    report_script = script_dir / "generate_ml_reports.py"
    vector_script = script_dir / "ml_vector_parser.py"

    if not args.skip_reports:
        cmd = [
            sys.executable,
            str(report_script),
            "--flow-root",
            str(flow_root),
            "--platform",
            args.platform,
            "--variant",
            args.variant,
            "--stage",
            "congestion" if args.stage == "cts" else "both",
            "--group-path-count",
            str(args.group_path_count),
            "--endpoint-path-count",
            str(args.endpoint_path_count),
            "--openroad",
            args.openroad,
        ]
        for design in designs:
            cmd.extend(["--design", design])
        if args.continue_on_error:
            cmd.append("--continue-on-error")
        if args.results_dir:
            cmd.extend(["--results-dir", str(Path(args.results_dir).resolve())])
        if args.reports_dir:
            cmd.extend(["--reports-dir", str(Path(args.reports_dir).resolve())])
        if args.reports_parent_dir:
            cmd.extend(["--reports-parent-dir", str(Path(args.reports_parent_dir).resolve())])
        if args.objects_dir:
            cmd.extend(["--objects-dir", str(Path(args.objects_dir).resolve())])
        run(cmd)

    if args.reports_only:
        return

    for design in designs:
        cmd = [
            sys.executable,
            str(vector_script),
            "--flow-root",
            str(flow_root),
            "--platform",
            args.platform,
            "--design",
            design,
            "--variant",
            args.variant,
            "--out",
            str(Path(args.out).resolve()),
            "--stage",
            args.stage,
            "--scenario",
            args.scenario,
            "--patch-size-dbu",
            str(args.patch_size_dbu),
            "--openroad",
            args.openroad,
        ]
        if args.cache_root:
            cmd.extend(["--cache-root", str(Path(args.cache_root).resolve())])
        if args.dataset_design:
            cmd.extend(["--dataset-design", args.dataset_design])
        if args.results_dir:
            cmd.extend(["--results-dir", str(Path(args.results_dir).resolve())])
        if args.reports_dir:
            cmd.extend(["--reports-dir", str(Path(args.reports_dir).resolve())])
        if args.objects_dir:
            cmd.extend(["--objects-dir", str(Path(args.objects_dir).resolve())])
        run(cmd)


if __name__ == "__main__":
    main()
