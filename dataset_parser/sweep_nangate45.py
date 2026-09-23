#!/usr/bin/env python3
"""Nangate45 ML dataset sweep: flow -> ml reports -> vectors -> checks.

7 designs x 10 hyperparameter-only strategies (FLOW_VARIANT). Per design:
  1. ORFS flow to finish (POST_CTS_TCL exports 4_cts.def/4_cts.v;
     detail_place.tcl exports 3_place.def/3_place.v)
  2. generate_ml_reports.py --stage all  (place/cts on stage ODBs with
     placement-based parasitics only, route on 6_final.odb + SPEF)
  3. ml_vector_parser.py --stage all     (strict per-stage files, no leakage)
  4. check_consistency.py per stage
  5. verify_features.py on route vectors (source recomputation)

All outputs stay local (no shared-folder sync). Resume-safe.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

FLOW_ROOT = Path("/home/lyh/OpenROAD-flow-scripts")
FLOW_DIR = FLOW_ROOT / "flow"
PARSER_DIR = FLOW_ROOT / "dataset_parser"
LOGS = PARSER_DIR / "logs"
POST_CTS_TCL = FLOW_DIR / "scripts" / "post_cts_export.tcl"
OUT_ROOT = FLOW_ROOT / "flow" / "datasets" / "nangate45"

DESIGNS = ["aes", "blabla", "gcd", "picorv32", "PPU", "s35932", "salsa20"]
NICK = {d: f"bench_{d}" for d in DESIGNS}

VARIANTS = [
    "base",
    "util60_den60",
    "util70_den70",
    "util75_den72",
    "low_density_den50",
    "high_density_den76",
    "route_adjust25",
    "route_adjust60",
    "route_adjust75",
    "cts_small_cluster",
]

# Effective hyperparameters per variant (values resolved by config.mk).
VARIANT_PARAMS = {
    "base": {"strategy": "base", "note": "design default configuration"},
    "util60_den60": {"strategy": "util60_den60", "core_utilization": 60, "place_density": 0.60},
    "util70_den70": {"strategy": "util70_den70", "core_utilization": 70, "place_density": 0.70},
    "util75_den72": {"strategy": "util75_den72", "core_utilization": 75, "place_density": 0.72},
    "low_density_den50": {"strategy": "low_density_den50", "place_density": 0.50},
    "high_density_den76": {"strategy": "high_density_den76", "place_density": 0.76},
    "route_adjust25": {"strategy": "route_adjust25", "routing_layer_adjustment": 0.25},
    "route_adjust60": {"strategy": "route_adjust60", "routing_layer_adjustment": 0.60},
    "route_adjust75": {"strategy": "route_adjust75", "routing_layer_adjustment": 0.75},
    "cts_small_cluster": {"strategy": "cts_small_cluster", "cts_cluster_size": 30},
}

FLOW_REQUIRED = [
    "3_place.odb", "3_place.sdc", "3_place.def", "3_place.v",
    "4_cts.odb", "4_cts.sdc", "4_cts.def", "4_cts.v",
    "6_final.odb", "6_final.def", "6_final.v", "6_final.spef",
]
REPORT_REQUIRED = [
    "place_grt_path_report.json", "place_net_timing.csv", "place_pin_timing.csv",
    "place_net_cap.csv", "place_design_info.json",
    "grt_path_report.json", "cts_net_timing.csv", "cts_pin_timing.csv",
    "cts_net_cap.csv", "cts_design_info.json",
    "path_report.json", "route_net_timing.csv", "route_pin_timing.csv",
    "route_net_cap.csv", "route_design_info.json",
]


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "sweep_n45.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], log_file: Path, env_extra: dict[str, str] | None = None) -> bool:
    import os

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, cwd=FLOW_DIR, env=env, stdout=f, stderr=subprocess.STDOUT)
    return proc.returncode == 0


def result_dir(design: str, variant: str) -> Path:
    return FLOW_DIR / "results" / "nangate45" / NICK[design] / variant


def report_dir(design: str, variant: str) -> Path:
    return FLOW_DIR / "reports" / "nangate45" / NICK[design] / variant / "ml_reports"


def vectors_dir(design: str, variant: str, stage: str) -> Path:
    return OUT_ROOT / design / stage / "vectors" / f"{design}_{stage}_vectors" / "vectors" / variant


def resolve_stages(stages_arg: str) -> list[str]:
    """Stage switch shared with ml_vector_parser.py.

    Empty string -> whatever dataset_parser/stage_config.json says (all three
    stages by default). The resolved list drives the parse, check and verify
    phases below, so a stage that is switched off is never parsed nor expected
    to exist.
    """
    import ml_vector_parser as mv

    class _Args:
        stage = None
        stages = stages_arg
        stage_config = ""
        place = cts = route = None

    return mv.resolve_stages(_Args())


def all_files_present(d: Path, names: list[str]) -> bool:
    return all((d / name).exists() for name in names)


def run_flow(design: str, variant: str) -> bool:
    rd = result_dir(design, variant)
    if all_files_present(rd, FLOW_REQUIRED):
        log(f"[flow][skip] {design}/{variant}: result files complete")
        return True
    log(f"[flow] {design}/{variant}: make finish ...")
    ok = run(
        ["make", f"DESIGN_CONFIG=./designs/nangate45_benchmarks/{design}/config.mk",
         f"FLOW_VARIANT={variant}", "finish"],
        LOGS / "flow" / f"n45_flow_{design}_{variant}.log",
        env_extra={"POST_CTS_TCL": str(POST_CTS_TCL)},
    )
    if not ok:
        log(f"[flow][fail] {design}/{variant}")
        return False
    if not all_files_present(rd, FLOW_REQUIRED):
        missing = [n for n in FLOW_REQUIRED if not (rd / n).exists()]
        log(f"[flow][fail] {design}/{variant}: missing {missing}")
        return False
    log(f"[flow][ok] {design}/{variant}")
    return True


def run_reports(design: str, variant: str) -> bool:
    rd = report_dir(design, variant)
    # Version-aware skip: the net_cap.csv dump must carry the wire_cap_f column
    # (current generate_ml_reports.py format), not just exist.
    cap_csv = rd / "cts_net_cap.csv"
    fresh = cap_csv.exists() and "wire_cap_f" in (cap_csv.read_text(errors="ignore").splitlines() or [""])[0]
    if all_files_present(rd, REPORT_REQUIRED) and fresh:
        log(f"[reports][skip] {design}/{variant}: complete")
        return True
    log(f"[reports] {design}/{variant} ...")
    ok = run(
        ["python3", str(PARSER_DIR / "generate_ml_reports.py"),
         "--platform", "nangate45", "--design", NICK[design], "--variant", variant, "--stage", "all"],
        LOGS / "reports" / f"n45_reports_{design}_{variant}.log",
    )
    if not ok or not all_files_present(rd, REPORT_REQUIRED):
        missing = [n for n in REPORT_REQUIRED if not (rd / n).exists()]
        log(f"[reports][fail] {design}/{variant}: missing {missing}")
        return False
    log(f"[reports][ok] {design}/{variant}")
    return True


def run_parse(design: str, variant: str, stages: list[str]) -> bool:
    if all(vectors_dir(design, variant, s).joinpath("design_info.json").exists() for s in stages):
        log(f"[parse][skip] {design}/{variant}: vectors exist")
        return True
    log(f"[parse] {design}/{variant} ...")
    params = dict(VARIANT_PARAMS[variant])
    params.update({"platform": "nangate45", "design": design})
    ok = run(
        ["python3", str(PARSER_DIR / "ml_vector_parser.py"),
         "--flow-root", str(FLOW_ROOT), "--platform", "nangate45",
         "--design", NICK[design], "--variant", variant,
         "--dataset-design", design,
         "--stages", ",".join(stages),
         "--scenario", variant,
         "--scenario-params", json.dumps(params),
         # parser appends <dataset-design>/<stage>/vectors/... below --out
         "--out", str(OUT_ROOT)],
        LOGS / "parse" / f"n45_parse_{design}_{variant}.log",
    )
    if not ok:
        log(f"[parse][fail] {design}/{variant}")
        return False
    log(f"[parse][ok] {design}/{variant}")
    return True


def run_checks(design: str, variant: str, stages: list[str]) -> bool:
    for stage in stages:
        vd = vectors_dir(design, variant, stage)
        proc = subprocess.run(
            ["python3", str(PARSER_DIR / "check_consistency.py"), str(vd)],
            cwd=FLOW_DIR, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            log(f"[check][fail] {design}/{variant}/{stage}: {proc.stdout[-500:]}")
            return False
    log(f"[check][ok] {design}/{variant}")
    return True


def run_verify(design: str, variant: str, stages: list[str]) -> bool:
    if "route" not in stages:
        log(f"[verify][skip] {design}/{variant}: route stage not enabled")
        return True
    lib_dir = FLOW_DIR / "objects" / "nangate45" / NICK[design] / variant / "lib"
    lib = lib_dir / "merged.lib"
    if not lib.exists():
        # no macros in this design -> the flow links the liberty directly
        lib = lib_dir / "NangateOpenCellLibrary_typical.lib"
    vd = vectors_dir(design, variant, "route")
    if not lib.exists():
        log(f"[verify][skip] {design}/{variant}: liberty missing in {lib_dir}")
        return True
    proc = subprocess.run(
        ["python3", str(PARSER_DIR / "verify_features.py"),
         "--results-dir", str(result_dir(design, variant)),
         "--reports-dir", str(report_dir(design, variant)),
         "--out", str(vd),
         "--lib", str(lib),
         "--platform-dir", str(FLOW_DIR / "platforms" / "nangate45"),
         "--dbu", "2000"],
        cwd=FLOW_DIR, text=True, capture_output=True,
    )
    tail = proc.stdout[-1500:]
    if proc.returncode != 0:
        log(f"[verify][fail] {design}/{variant}: {tail}")
        return False
    log(f"[verify][ok] {design}/{variant}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--designs", default=",".join(DESIGNS))
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--phases", default="flow,reports,parse,check,verify")
    parser.add_argument("--trial", action="store_true", help="Only gcd x base (one design x one variant).")
    parser.add_argument(
        "--stages",
        default="",
        help="Comma list of stages to parse (place,cts,route). Default: dataset_parser/stage_config.json.",
    )
    args = parser.parse_args()
    designs = args.designs.split(",")
    variants = args.variants.split(",")
    phases = set(args.phases.split(","))
    stages = resolve_stages(args.stages)
    if args.trial:
        designs, variants = ["gcd"], ["base"]
    total = len(designs) * len(variants)
    log(f"=== sweep start: {total} runs, phases={sorted(phases)}, stages={stages} ===")
    import shutil as _shutil

    free_gb = _shutil.disk_usage(FLOW_DIR).free / 1e9
    if free_gb < 10:
        log(f"[abort] free space {free_gb:.1f}G < 10G")
        sys.exit(1)
    log(f"free space: {free_gb:.1f}G")
    fails = 0
    for design in designs:
        for variant in variants:
            ok = True
            if "flow" in phases:
                ok &= run_flow(design, variant)
            if "reports" in phases and ok:
                ok &= run_reports(design, variant)
            if "parse" in phases and ok:
                ok &= run_parse(design, variant, stages)
            if "check" in phases and ok:
                ok &= run_checks(design, variant, stages)
            if "verify" in phases and ok:
                ok &= run_verify(design, variant, stages)
            if not ok:
                fails += 1
                log(f"[x] {design}/{variant} failed at some phase")
    log(f"=== sweep done: fails={fails}/{total} ===")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
