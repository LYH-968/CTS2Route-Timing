#!/usr/bin/env python3
"""逐 design 滚动处理：解析 → 验证 → 拷回宿主机替换旧目录 → 清理本地。

用法:
  python3 dataset_parser/batch_parse_and_sync.py

行为:
  - 跳过宿主机上已为新格式的 design（design_info.json.zst 数 == variant 数 × 3）
  - 每个 design: 从宿主机 runs 读输入，解析到 VM 本地盘
  - check_consistency 全部阶段-variant + verify_features 抽查 base
  - rsync 到宿主机 <d>.new，校验文件数后替换旧目录（旧目录删除，见 --keep-old）
  - 删除 VM 本地副本，继续下一个 design
  - 宿主机空间不足时优先删除该 design 的旧目录再拷（新数据已通过验证）
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
LOG = FLOW_ROOT / "dataset_parser" / "logs" / "batch_sync.log"

# runs_asap7 里存在的 design（不含 gcd；ethmac_lvt 无 runs 源，跳过）
DESIGNS = ["aes", "blabla", "ethmac", "ibex", "jpeg", "picorv32", "ppu", "riscv32i", "s35932", "salsa20"]


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], **kw) -> int:
    log("[run] " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=FLOW_ROOT, **kw)
        return proc.returncode
    except Exception as exc:  # noqa: BLE001
        log(f"[error] {exc}")
        return 1


def variants_of(design: str) -> list[str]:
    return sorted(p.name for p in (RUNS / design).iterdir() if (p / "result").is_dir())


def host_is_new_format(design: str) -> bool:
    """宿主机上该 design 是否已为新格式（每 variant × 3 阶段都有 design_info.json.zst）。"""
    want = len(variants_of(design)) * 3
    got = len(list((HOST / design).glob("*/vectors/*/vectors/*/design_info.json.zst")))
    return got >= want


def stage_dirs(design: str) -> list[Path]:
    dirs = []
    for stage in ("place", "cts", "route"):
        base = OUT / design / stage / "vectors" / f"{design}_{stage}_vectors" / "vectors"
        if base.is_dir():
            dirs.extend(sorted(p for p in base.iterdir() if p.is_dir()))
    return dirs


def host_free_gb() -> float:
    return shutil.disk_usage(HOST).free / (1024**3)


def process_design(design: str, keep_old: bool) -> bool:
    log(f"=== design {design}: {len(variants_of(design))} variants ===")
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

    # 完整性检查：解析必须覆盖全部 variant × 3 阶段，防止部分数据被同步替换宿主机完整数据
    variants = variants_of(design)
    missing = []
    for v in variants:
        for stage in ("place", "cts", "route"):
            marker = OUT / design / stage / "vectors" / f"{design}_{stage}_vectors" / "vectors" / v / "design_info.json.zst"
            if not marker.exists():
                missing.append(f"{v}/{stage}")
    if missing:
        log(f"[partial] {design} 不完整（缺 {len(missing)} 个阶段-variant），保留本地数据不同步: {missing[:6]}...")
        return False

    # 1) 一致性检查（所有 stage-variant）
    bad = 0
    for vdir in stage_dirs(design):
        if run([sys.executable, "dataset_parser/check_consistency.py", str(vdir)]) != 0:
            bad += 1
    if bad:
        log(f"[fail] {design} 一致性检查 {bad} 个目录失败")
        return False

    # 2) 特征数值抽查（base variant, route 阶段）
    vdir = OUT / design / "route" / "vectors" / f"{design}_route_vectors" / "vectors" / "base"
    if vdir.is_dir():
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

    # 3) 拷回宿主机并替换旧目录
    local = OUT / design
    n_local = sum(1 for p in local.rglob("*") if p.is_file())
    host_old = HOST / design
    host_new = HOST / (design + ".new")
    old_size = sum(p.stat().st_size for p in host_old.rglob("*") if p.is_file()) if host_old.is_dir() else 0
    new_size = sum(p.stat().st_size for p in local.rglob("*") if p.is_file())
    # 宿主机空间不足（old+new 放不下）时先删旧目录（新数据已验证，且用户已批准滚动替换）
    if host_old.is_dir() and (old_size + new_size) / (1024**3) > host_free_gb() - 3:
        log(f"[space] {design}: 宿主机先删旧目录（{old_size / (1024**3):.1f}G）")
        shutil.rmtree(host_old, ignore_errors=True)
    if run(["rsync", "-a", "--delete", str(local) + "/", str(host_new) + "/"]) != 0:
        log(f"[fail] {design} rsync 到宿主机失败")
        return False
    n_host = sum(1 for p in host_new.rglob("*") if p.is_file())
    if n_host != n_local:
        log(f"[fail] {design} 宿主机文件数 {n_host} != 本地 {n_local}")
        return False
    if host_old.is_dir():
        if keep_old:
            shutil.move(str(host_old), str(HOST / (design + ".old")))
        else:
            shutil.rmtree(host_old, ignore_errors=True)
    shutil.move(str(host_new), str(host_old))
    log(f"[ok] {design} 已替换宿主机 {host_old}（{n_host} 文件, {new_size / (1024**3):.1f}G）")

    # 4) 清理本地副本
    shutil.rmtree(local, ignore_errors=True)
    log(f"[clean] {design} 本地副本已删，VM 剩余 {shutil.disk_usage(OUT).free / (1024**3):.0f}G")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", action="append", help="只处理指定 design（可重复）")
    ap.add_argument("--keep-old", action="store_true", help="替换后保留旧目录为 <d>.old（默认删除）")
    ap.add_argument("--start-index", type=int, default=0)
    args = ap.parse_args()
    designs = args.design or DESIGNS
    designs = designs[args.start_index :]

    fails = []
    for design in designs:
        if design not in {p.name for p in RUNS.iterdir()}:
            log(f"[skip] {design}: runs_asap7 无此 design")
            continue
        if host_is_new_format(design):
            log(f"[skip] {design}: 宿主机已为新格式")
            continue
        t0 = time.time()
        ok = process_design(design, args.keep_old)
        log(f"=== {design} {'成功' if ok else '失败'}，耗时 {(time.time() - t0) / 3600:.1f}h ===")
        if not ok:
            fails.append(design)
    if fails:
        log(f"[done] 失败 design: {fails}")
        sys.exit(1)
    log("[done] 全部完成")


if __name__ == "__main__":
    main()
