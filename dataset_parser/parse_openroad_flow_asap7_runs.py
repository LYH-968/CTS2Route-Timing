#!/usr/bin/env python3
"""Parse existing openroad_flow/runs/asap7 runs into parsed_ml_dataset_asap7."""

from __future__ import annotations

import argparse
import csv
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_FLOW_ROOT = Path("/home/lyh/OpenROAD-flow-scripts")
DEFAULT_RUNS_ROOT = DEFAULT_FLOW_ROOT / "openroad_flow/runs/asap7"
DEFAULT_OUT = DEFAULT_FLOW_ROOT / "parsed_ml_dataset_asap7"
DEFAULT_OPENROAD = DEFAULT_FLOW_ROOT / "tools/install/OpenROAD/bin/openroad"


@dataclass(frozen=True)
class Run:
    design: str
    variant: str
    run_dir: Path

    @property
    def result_dir(self) -> Path:
        return self.run_dir / "result"

    @property
    def report_dir(self) -> Path:
        return self.run_dir / "report"

    @property
    def ml_reports_dir(self) -> Path:
        return self.report_dir / "ml_reports"

    @property
    def objects_dir(self) -> Path:
        return self.run_dir / "objects"


def shell_join(cmd: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def discover_runs(runs_root: Path) -> list[Run]:
    runs: list[Run] = []
    if not runs_root.exists():
        raise SystemExit(f"runs root does not exist: {runs_root}")
    for design_dir in sorted(child for child in runs_root.iterdir() if child.is_dir()):
        for variant_dir in sorted(child for child in design_dir.iterdir() if child.is_dir()):
            result_dir = variant_dir / "result"
            if not result_dir.exists():
                continue
            required = [
                result_dir / "3_place.odb",
                result_dir / "4_cts.odb",
                result_dir / "4_cts.sdc",
                result_dir / "6_final.odb",
                result_dir / "6_final.def",
                result_dir / "6_final.v",
            ]
            if all(path.exists() for path in required):
                runs.append(Run(design=design_dir.name, variant=variant_dir.name, run_dir=variant_dir))
    return runs


def parsed_done(out_root: Path, run: Run, stage: str) -> bool:
    graph = (
        out_root
        / run.design
        / stage
        / "vectors"
        / f"{run.design}_{stage}_vectors"
        / "vectors"
        / run.variant
        / "wire_graph/timing_wire_graph.json"
    )
    return graph.exists()


def run_logged(cmd: list[str], cwd: Path, log_path: Path, dry_run: bool) -> int:
    print(shell_join(cmd))
    if dry_run:
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"# start: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"# cwd: {cwd}\n")
        log.write(f"# cmd: {shell_join(cmd)}\n\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, text=True)
        log.write(f"\n# end: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write(f"# returncode: {proc.returncode}\n")
        return proc.returncode


def append_status(status_csv: Path, run: Run, rc: int, log_path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    status_csv.parent.mkdir(parents=True, exist_ok=True)
    exists = status_csv.exists()
    with status_csv.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["time", "design", "variant", "status", "returncode", "log"])
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "design": run.design,
                "variant": run.variant,
                "status": "ok" if rc == 0 else "failed",
                "returncode": str(rc),
                "log": str(log_path),
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse openroad_flow/runs/asap7 into gcd-style ML vectors.")
    parser.add_argument("--flow-root", default=str(DEFAULT_FLOW_ROOT))
    parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--openroad", default=str(DEFAULT_OPENROAD))
    parser.add_argument("--design", action="append", help="Only parse this top-level run design. Repeatable.")
    parser.add_argument("--variant", action="append", help="Only parse this variant. Repeatable.")
    parser.add_argument("--stage", choices=["place", "cts", "route", "both", "all"], default="all")
    parser.add_argument("--patch-size-dbu", type=int, default=3330)
    parser.add_argument("--group-path-count", type=int, default=1_000_000)
    parser.add_argument("--endpoint-path-count", type=int, default=1)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip runs whose requested stage graphs already exist.")
    parser.add_argument("--skip-reports", action="store_true", help="Use existing ml_reports instead of regenerating them.")
    parser.add_argument("--reports-only", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--min-free-gb", type=float, default=8.0, help="Stop before a job if output filesystem free space is below this value.")
    args = parser.parse_args()

    flow_root = Path(args.flow_root).resolve()
    runs_root = Path(args.runs_root).resolve()
    out_root = Path(args.out).resolve()
    script_dir = Path(__file__).resolve().parent
    logs_root = script_dir / "logs/openroad_flow_asap7_parse"
    status_csv = logs_root / "status.csv"

    runs = discover_runs(runs_root)
    if args.design:
        allowed = set(args.design)
        runs = [run for run in runs if run.design in allowed]
    if args.variant:
        allowed = set(args.variant)
        runs = [run for run in runs if run.variant in allowed]
    if args.start_index:
        runs = runs[args.start_index :]
    if args.limit:
        runs = runs[: args.limit]

    requested_stages = {"both": ["place", "route"], "all": ["place", "cts", "route"]}.get(args.stage, [args.stage])
    print(f"flow_root: {flow_root}")
    print(f"runs_root: {runs_root}")
    print(f"out:       {out_root}")
    print(f"logs:      {logs_root}")
    print(f"runs:      {len(runs)}")
    print(f"min_free:  {args.min_free_gb:.1f}G")
    if args.dry_run:
        print("mode:      dry-run")

    failures = 0
    for idx, run in enumerate(runs, start=args.start_index):
        print(f"\n[{idx}] design={run.design} variant={run.variant}")
        free_gb = shutil.disk_usage(out_root.parent).free / (1024**3)
        if free_gb < args.min_free_gb:
            print(f"[stop] free space {free_gb:.1f}G is below --min-free-gb {args.min_free_gb:.1f}G")
            break
        if args.resume and all(parsed_done(out_root, run, stage) for stage in requested_stages) and not args.reports_only:
            print("[skip] resume: requested parsed stage graphs already exist")
            continue
        cmd = [
            sys.executable,
            str(script_dir / "ml_dataset_parser.py"),
            "--flow-root",
            str(flow_root),
            "--platform",
            "asap7",
            "--design",
            run.design,
            "--dataset-design",
            run.design,
            "--variant",
            run.variant,
            "--scenario",
            run.variant,
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
            str(Path(args.openroad).resolve()),
            "--results-dir",
            str(run.result_dir),
            "--reports-dir",
            str(run.ml_reports_dir),
            "--reports-parent-dir",
            str(run.report_dir),
            "--objects-dir",
            str(run.objects_dir),
        ]
        if args.skip_reports:
            cmd.append("--skip-reports")
        if args.reports_only:
            cmd.append("--reports-only")
        log_path = logs_root / run.design / run.variant / "parse.log"
        rc = run_logged(cmd, flow_root, log_path, args.dry_run)
        append_status(status_csv, run, rc, log_path, args.dry_run)
        if rc != 0:
            failures += 1
            if args.stop_on_error:
                raise SystemExit(rc)

    if failures:
        raise SystemExit(f"{failures} parse jobs failed; see {status_csv}")


if __name__ == "__main__":
    main()
