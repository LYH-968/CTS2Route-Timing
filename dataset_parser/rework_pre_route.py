#!/usr/bin/env python3
"""对已完成全量解析的 design 重跑 place/cts 阶段（无 Routing 泄漏的新流程）。

背景：早期版本的 place/cts 阶段使用 global_route + estimate_parasitics
-global_routing（routing information leakage）。本脚本用新流程（placement
寄生估计）重新生成 place/cts 数据并替换宿主机对应子目录，route 阶段不动。

用法:
  python3 dataset_parser/rework_pre_route.py [--design gcd aes ...]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

RUNS = Path("/mnt/hgfs/host_files/dataset/asap7/runs_asap7")
OUT = Path("/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_asap7_v2")
HOST = Path("/mnt/hgfs/host_files/dataset/asap7/parsed_ml_dataset_asap7")
FLOW_ROOT = Path("/home/lyh/OpenROAD-flow-scripts")
LOG = FLOW_ROOT / "dataset_parser" / "logs" / "rework_pre_route.log"

DEFAULT_DESIGNS = ["gcd", "aes", "blabla", "ethmac"]


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str]) -> int:
    log("[run] " + " ".join(cmd))
    return subprocess.run(cmd, cwd=FLOW_ROOT).returncode


def sync_stage(design: str, stage: str) -> bool:
    local = OUT / design / stage
    if not local.is_dir():
        log(f"[fail] {design}/{stage}: 本地无输出")
        return False
    host_new = HOST / design / f"{stage}.new"
    if run(["rsync", "-a", "--delete", str(local) + "/", str(host_new) + "/"]) != 0:
        log(f"[fail] {design}/{stage}: rsync 失败")
        return False
    n_local = sum(1 for p in local.rglob("*") if p.is_file())
    n_host = sum(1 for p in host_new.rglob("*") if p.is_file())
    if n_local != n_host:
        log(f"[fail] {design}/{stage}: 文件数不一致 {n_local} vs {n_host}")
        return False
    shutil.rmtree(HOST / design / stage, ignore_errors=True)
    shutil.move(str(host_new), str(HOST / design / stage))
    log(f"[ok] {design}/{stage} 已替换宿主机（{n_host} 文件）")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", action="append")
    args = ap.parse_args()
    designs = args.design or DEFAULT_DESIGNS
    fails = []
    for design in designs:
        log(f"=== rework {design}: place + cts ===")
        for stage in ("place", "cts"):
            rc = run(
                [
                    sys.executable,
                    "dataset_parser/parse_openroad_flow_asap7_runs.py",
                    "--runs-root",
                    str(RUNS),
                    "--out",
                    str(OUT),
                    "--design",
                    design,
                    "--stage",
                    stage,
                    "--resume",
                    "--min-free-gb",
                    "5",
                ]
            )
            if rc != 0:
                log(f"[fail] {design}/{stage} 解析失败")
                fails.append(f"{design}/{stage}")
                continue
            bad = 0
            vbase = OUT / design / stage / "vectors" / f"{design}_{stage}_vectors" / "vectors"
            for vdir in sorted(p for p in vbase.iterdir() if p.is_dir()) if vbase.is_dir() else []:
                if run([sys.executable, "dataset_parser/check_consistency.py", str(vdir)]) != 0:
                    bad += 1
            if bad:
                log(f"[fail] {design}/{stage} 一致性 {bad} 个目录失败")
                fails.append(f"{design}/{stage}")
                continue
            if not sync_stage(design, stage):
                fails.append(f"{design}/{stage}")
        shutil.rmtree(OUT / design, ignore_errors=True)
        log(f"=== rework {design} 完成 ===")
    if fails:
        log(f"[done] 失败: {fails}")
        sys.exit(1)
    log("[done] 全部完成")


if __name__ == "__main__":
    main()
