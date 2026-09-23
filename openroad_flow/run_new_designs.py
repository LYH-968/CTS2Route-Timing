#!/usr/bin/env python3
"""串行跑 6 个新 asap7 design（blabla/picorv32/ppu/salsa20/s35932/ethmac）各 10 组。

每个 run 完整保留所有阶段输出（3_place.odb / 4_cts.odb / 5_route.odb 等中间文件是
跨阶段指标预测的特征输入，必须保留，不删除任何东西）。结果汇总到 new_designs_run_summary.tsv。

先跑 6 个 base（尽早暴露 setup 错误），再按 design 逐个跑 9 个 variant。
已完成（存在 6_final.spef + 6_final.sdf）的 run 会自动跳过，可断点续跑。
"""
import json, os, shutil, subprocess, time

ROOT = "/home/lyh/OpenROAD-flow-scripts/openroad_flow"
MIN_FREE_GB = 15.0  # 磁盘低于此值即停止，等解析+备份+清理后续跑
DESIGNS_DIR = os.path.join(ROOT, "scr/designs/asap7")
VARDIR = os.path.join(DESIGNS_DIR, "generated_variants")
SUMMARY = os.path.join(ROOT, "new_designs_run_summary.tsv")
LOGDIR = os.path.join(ROOT, "runlogs")

# 按规模从小到大
DESIGNS = ["blabla", "ppu", "picorv32", "salsa20", "s35932", "ethmac"]
VARIANTS = ["area_compact", "area_loose", "area_xloose", "area_xxloose",
            "dens_low", "dens_high", "dens_xhigh", "ar_wide", "ar_tall"]

# 先 6 个 base，再 54 个 variant（按 design 分组）
RUNS = [(d, "base") for d in DESIGNS]
RUNS += [(d, v) for d in DESIGNS for v in VARIANTS]

HEADER = ["design", "variant", "status", "setup_wns", "setup_tns", "hold_wns",
          "setup_violations", "clock_period", "elapsed_s"]


def cfg_path(design, variant):
    if variant == "base":
        return os.path.join(DESIGNS_DIR, f"{design}.tcl")
    return os.path.join(VARDIR, f"{design}_{variant}.tcl")


def done(design, variant):
    r = os.path.join(ROOT, "runs", "asap7", design, variant, "result")
    return os.path.exists(os.path.join(r, "6_final.spef")) and \
           os.path.exists(os.path.join(r, "6_final.sdf"))


def extract_metrics(design, variant):
    js = os.path.join(ROOT, "runs", "asap7", design, variant,
                      "report", "6_final_timing_summary.json")
    if not os.path.exists(js):
        return {}
    with open(js) as f:
        d = json.load(f)
    return {
        "setup_wns": d.get("setup_wns"),
        "setup_tns": d.get("setup_tns"),
        "hold_wns": d.get("hold_wns"),
        "setup_violations": d.get("setup_violation_count"),
        "clock_period": d.get("clock_periods"),
    }


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    with open(SUMMARY, "w") as f:
        f.write("\t".join(HEADER) + "\n")

    total = len(RUNS)
    ok = fail = skip = 0
    for i, (design, variant) in enumerate(RUNS, 1):
        free = shutil.disk_usage(ROOT).free / (1024 ** 3)
        if free < MIN_FREE_GB:
            print("[stop] free space %.1fG < %.1fG, stopping (completed %d/%d, %d failed)" % (
                free, MIN_FREE_GB, i - 1, total, fail), flush=True)
            break
        if done(design, variant):
            print("[%02d/%02d] %s/%s -> SKIP (already finished)" % (i, total, design, variant), flush=True)
            skip += 1
            continue
        cfg = cfg_path(design, variant)
        run_log = os.path.join(LOGDIR, f"{design}_{variant}.log")
        t0 = time.time()
        print("[%02d/%02d] %s/%s ..." % (i, total, design, variant), flush=True)
        with open(run_log, "w") as lf:
            proc = subprocess.run(
                ["bash", "scr/run_flow.sh", "--design", design,
                 "--variant", variant, "--config", cfg],
                cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT)
        elapsed = time.time() - t0
        status = "OK" if proc.returncode == 0 else "FAIL(%d)" % proc.returncode
        metrics = extract_metrics(design, variant) if proc.returncode == 0 else {}
        if proc.returncode == 0:
            ok += 1
        else:
            fail += 1

        row = [design, variant, status,
               metrics.get("setup_wns", ""), metrics.get("setup_tns", ""),
               metrics.get("hold_wns", ""), metrics.get("setup_violations", ""),
               metrics.get("clock_period", ""), "%.0f" % elapsed]
        with open(SUMMARY, "a") as f:
            f.write("\t".join(str(x) for x in row) + "\n")
        print("        -> %s  (%.0fs)  WNS=%s  [log %s]" % (
            status, elapsed, metrics.get("setup_wns", "?"), run_log), flush=True)

    print("ALL DONE. ok=%d fail=%d skip=%d  Summary: %s" % (ok, fail, skip, SUMMARY), flush=True)


if __name__ == "__main__":
    main()
