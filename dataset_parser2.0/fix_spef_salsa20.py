#!/usr/bin/env python3
"""修复 salsa20 损坏的 6_final.spef：从 6_1_fill.odb 重新 extract_parasitics + write_spef。

背景：宿主机 runs 中 8 个 variant 的 6_final.spef 存在语法错误（当初写出中断），
导致 read_spef 失败、route 标签缺失。修复方式与 ORFS final_report.tcl 相同：
6_1_fill.odb + RCX 规则重新提取，校验通过后替换原文件（原文件备份为 .broken）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RUNS = Path("/mnt/hgfs/host_files/dataset/asap7/runs_asap7/salsa20")
PLATFORM = Path("/home/lyh/OpenROAD-flow-scripts/flow/platforms/asap7")
OPENROAD = "/home/lyh/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad"
LOG = Path("/home/lyh/OpenROAD-flow-scripts/dataset_parser/logs/fix_spef.log")

BROKEN = ["area_loose", "area_xxloose", "ar_tall", "ar_wide", "base", "dens_high", "dens_low", "dens_xhigh"]


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def tcl_quote(p: Path) -> str:
    return "{" + str(p).replace("}", "\\}") + "}"


def fix_variant(variant: str) -> bool:
    result = RUNS / variant / "result"
    spef = result / "6_final.spef"
    lib = RUNS / variant / "objects" / "lib" / "merged.lib"
    if not (result / "6_1_fill.odb").exists():
        log(f"[skip] {variant}: 无 6_1_fill.odb")
        return False
    # 先测：读当前 spef 是否真的失败（双重确认）
    tcl = f"""
read_liberty {tcl_quote(lib)}
read_db {tcl_quote(result / '6_1_fill.odb')}
read_sdc {tcl_quote(result / '6_1_fill.sdc')}
set_propagated_clock [all_clocks]
global_connect
define_process_corner -ext_model_index 0 X
extract_parasitics -ext_model_file {tcl_quote(PLATFORM / 'rcx_patterns.rules')}
write_spef {tcl_quote(result / '6_final.spef.fixed')}
# 校验：新 spef 能读回来
read_spef {tcl_quote(result / '6_final.spef.fixed')}
report_wns > {tcl_quote(result / 'spef_fix_check.rpt')}
exit
"""
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as f:
        f.write(tcl)
        tcl_path = Path(f.name)
    try:
        proc = subprocess.run(
            [OPENROAD, "-no_init", "-no_splash", str(tcl_path)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=3600,
        )
    finally:
        tcl_path.unlink(missing_ok=True)
    if proc.returncode != 0 or not (result / "6_final.spef.fixed").exists():
        log(f"[fail] {variant}: 提取失败 rc={proc.returncode}")
        tail = "\n".join(proc.stdout.splitlines()[-8:])
        log(tail)
        return False
    if "STA-0164" in proc.stdout or "Error" in proc.stdout:
        log(f"[fail] {variant}: 新 spef 校验失败")
        tail = "\n".join(proc.stdout.splitlines()[-8:])
        log(tail)
        return False
    # 备份损坏原文件并替换
    broken_backup = result / "6_final.spef.broken"
    if not broken_backup.exists():
        shutil.move(str(spef), str(broken_backup))
    shutil.move(str(result / "6_final.spef.fixed"), str(spef))
    log(f"[ok] {variant}: 6_final.spef 已修复（原文件备份为 6_final.spef.broken）")
    return True


def main() -> None:
    fails = []
    for variant in BROKEN:
        if not fix_variant(variant):
            fails.append(variant)
    if fails:
        log(f"[done] 失败: {fails}")
        sys.exit(1)
    log("[done] 全部修复完成")


if __name__ == "__main__":
    main()
