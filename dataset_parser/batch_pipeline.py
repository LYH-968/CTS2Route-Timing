#!/usr/bin/env python3
"""流水线批量：解析 design(N+1) 与同步 design(N) 并行，节省整体墙钟时间。

模式:
  parse+verify(jpeg) → [sync(jpeg) ∥ parse(picorv32)] → [sync(picorv32) ∥ parse(ppu)] → ...

本地盘同时最多驻留 2 个 design（峰值空间有充足余量），每个 design 解析完成后
立即启动后台同步线程（rsync → 校验 → 替换宿主机 → 删本地），主循环继续解析
下一个 design。解析工具自带 --min-free-gb 5 保险丝与 --resume 断点续跑。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

RUNS = Path("/mnt/hgfs/host_files/dataset/asap7/runs_asap7")
OUT = Path("/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_asap7_v2")
HOST = Path("/mnt/hgfs/host_files/dataset/asap7/parsed_ml_dataset_asap7")
FLOW_ROOT = Path("/home/lyh/OpenROAD-flow-scripts")
LOG = FLOW_ROOT / "dataset_parser" / "logs" / "batch_pipeline.log"

# ibex 之前已由独立脚本同步中；本流水线负责其余 design
DESIGNS = ["jpeg", "picorv32", "ppu", "riscv32i", "s35932", "salsa20"]


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str]) -> int:
    log("[run] " + " ".join(cmd))
    return subprocess.run(cmd, cwd=FLOW_ROOT).returncode


REQUIRED_RESULT_FILES = ("3_place.odb", "4_cts.odb", "4_cts.sdc", "6_final.odb", "6_final.def", "6_final.v")


def variants_of(design: str) -> list[str]:
    """只统计 result 目录含必需文件的 variant（与解析工具的 discover_runs 口径一致）。"""
    out = []
    for p in sorted((RUNS / design).iterdir()):
        result = p / "result"
        if result.is_dir() and all((result / name).exists() for name in REQUIRED_RESULT_FILES):
            out.append(p.name)
    return out


def host_is_new_format(design: str) -> bool:
    want = len(variants_of(design)) * 3
    got = len(list((HOST / design).glob("*/vectors/*/vectors/*/design_info.json")))
    return got >= want


def sync_design(design: str) -> None:
    """同步线程：rsync 到宿主机 → 文件数校验 → 替换 → 删本地。"""
    local = OUT / design
    host_new = HOST / (design + ".new")
    n_local = sum(1 for p in local.rglob("*") if p.is_file())
    for attempt in range(3):
        if run(["rsync", "-a", "--delete", str(local) + "/", str(host_new) + "/"]) != 0:
            log(f"[sync-fail] {design} rsync 失败（第 {attempt + 1} 次）")
            time.sleep(60)
            continue
        n_host = sum(1 for p in host_new.rglob("*") if p.is_file())
        if n_host != n_local:
            log(f"[sync-fail] {design} 文件数不一致 {n_host} vs {n_local}，重试")
            time.sleep(60)
            continue
        shutil.rmtree(HOST / design, ignore_errors=True)
        shutil.move(str(host_new), str(HOST / design))
        log(f"[sync-ok] {design} 已替换宿主机（{n_host} 文件）")
        shutil.rmtree(local, ignore_errors=True)
        log(f"[sync-clean] {design} 本地已删，VM 剩余 {shutil.disk_usage(OUT).free / (1024**3):.0f}G")
        return
    log(f"[sync-fail] {design} 三次尝试失败，保留本地数据待人工处理")


def parse_design(design: str) -> bool:
    variants = variants_of(design)
    log(f"=== parse {design}: {len(variants)} variants ===")
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
            "--resume",
            "--min-free-gb",
            "5",
        ]
    )
    if rc != 0:
        log(f"[fail] {design} 解析失败 rc={rc}")
        return False
    # 完整性检查
    missing = []
    for v in variants:
        for stage in ("place", "cts", "route"):
            marker = OUT / design / stage / "vectors" / f"{design}_{stage}_vectors" / "vectors" / v / "design_info.json"
            if not marker.exists():
                missing.append(f"{v}/{stage}")
    if missing:
        log(f"[partial] {design} 不完整（缺 {len(missing)}），保留本地不同步")
        return False
    # 一致性检查 + 特征抽查
    bad = 0
    for stage in ("place", "cts", "route"):
        vbase = OUT / design / stage / "vectors" / f"{design}_{stage}_vectors" / "vectors"
        for vdir in sorted(p for p in vbase.iterdir() if p.is_dir()):
            if run([sys.executable, "dataset_parser/check_consistency.py", str(vdir)]) != 0:
                bad += 1
    if bad:
        log(f"[fail] {design} 一致性检查 {bad} 个目录失败")
        return False
    vdir = OUT / design / "route" / "vectors" / f"{design}_route_vectors" / "vectors" / "base"
    rc = run(
        [
            sys.executable,
            "dataset_parser/verify_features.py",
            "--results-dir",
            str(RUNS / design / "base" / "result"),
            "--reports-dir",
            str(RUNS / design / "base" / "report" / "ml_reports"),
            "--out",
            str(vdir),
            "--lib",
            str(RUNS / design / "base" / "objects" / "lib" / "merged.lib"),
            "--platform-dir",
            str(FLOW_ROOT / "flow" / "platforms" / "asap7"),
            "--dbu",
            "1000",
        ]
    )
    if rc != 0:
        log(f"[fail] {design} verify_features 失败")
        return False
    return True


def main() -> None:
    fails = []
    sync_thread: threading.Thread | None = None
    for design in DESIGNS:
        if host_is_new_format(design):
            log(f"[skip] {design} 宿主机已为新格式")
            continue
        if not parse_design(design):
            fails.append(design)
            continue
        # 等上一个 design 的同步完成（控制本地盘最多 2 个 design）
        if sync_thread is not None:
            sync_thread.join()
            sync_thread = None
        sync_thread = threading.Thread(target=sync_design, args=(design,), daemon=True)
        sync_thread.start()
        log(f"[pipeline] {design} 同步已启动，主循环继续下一个 design")
    if sync_thread is not None:
        sync_thread.join()
    if fails:
        log(f"[done] 失败: {fails}")
        sys.exit(1)
    log("[done] 流水线全部完成")


if __name__ == "__main__":
    main()
