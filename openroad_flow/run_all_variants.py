#!/usr/bin/env python3
"""串行跑 5 个 asap7 设计的新增 variant（扩增到总数 10 组）。

每个 run 完整保留所有阶段输出（3_place.odb / 4_cts.odb / 5_route.odb 等
中间文件是跨阶段指标预测的特征输入，必须保留，不删除任何东西）。
结果汇总到 variant_run_summary.tsv。
"""
import json, os, re, subprocess, time

ROOT = "/home/lyh/OpenROAD-flow-scripts/openroad_flow"
VARDIR = os.path.join(ROOT, "scr/designs/asap7/generated_variants")
SUMMARY = os.path.join(ROOT, "variant_run_summary.tsv")

# (design, variant) 按规模从小到大排序，串行跑。
# 注意：riscv32i 的 area_compact（die 65.2×65.2）太小放不下 4 个 SRAM 宏
#（DPL-0036），已用 dens_xhigh（lb_addon 0.5）替代。
RUNS = [
    ("gcd",      "area_compact"),
    ("gcd",      "dens_low"),
    ("gcd",      "ar_wide"),
    ("gcd",      "area_loose"),
    ("gcd",      "dens_high"),
    ("gcd",      "ar_tall"),
    ("riscv32i", "dens_low"),
    ("riscv32i", "ar_wide"),
    ("riscv32i", "area_loose"),
    ("riscv32i", "dens_high"),
    ("riscv32i", "ar_tall"),
    ("riscv32i", "area_xloose"),
    ("riscv32i", "dens_xhigh"),
    ("aes",      "area_compact"),
    ("aes",      "dens_low"),
    ("aes",      "ar_wide"),
    ("aes",      "area_loose"),
    ("aes",      "dens_high"),
    ("aes",      "ar_tall"),
    ("ibex",     "area_compact"),
    ("ibex",     "dens_low"),
    ("ibex",     "ar_wide"),
    ("ibex",     "area_loose"),
    ("ibex",     "dens_high"),
    ("ibex",     "ar_tall"),
    ("ibex",     "area_xloose"),
    ("jpeg",     "area_compact"),
    ("jpeg",     "dens_low"),
    ("jpeg",     "ar_wide"),
    ("jpeg",     "area_loose"),
    ("jpeg",     "dens_high"),
    ("jpeg",     "ar_tall"),
    ("jpeg",     "area_xloose"),
    ("jpeg",     "dens_xhigh"),
]

HEADER = ["design", "variant", "status", "setup_wns", "setup_tns", "hold_wns",
          "setup_violations", "clock_period", "params"]


def variant_params(design, variant):
    """从 variant .tcl 提取被覆盖的参数，作为数据集输入特征。"""
    path = os.path.join(VARDIR, "%s_%s.tcl" % (design, variant))
    overrides = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                m = re.match(r"set\s+vars\((\w+)\)\s+(.+)", line.strip())
                if m and m.group(1) != "flow_variant":
                    overrides[m.group(1)] = m.group(2).strip()
    return json.dumps(overrides, ensure_ascii=False)


def extract_metrics(run_dir):
    js = os.path.join(run_dir, "report", "6_final_timing_summary.json")
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
    with open(SUMMARY, "w") as f:
        f.write("\t".join(HEADER) + "\n")

    total = len(RUNS)
    for i, (design, variant) in enumerate(RUNS, 1):
        cfg = os.path.join(VARDIR, "%s_%s.tcl" % (design, variant))
        run_dir = os.path.join(ROOT, "runs", "asap7", design, variant)
        t0 = time.time()
        print("[%02d/%02d] %s/%s ..." % (i, total, design, variant), flush=True)
        proc = subprocess.run(
            ["bash", "scr/run_flow.sh", "--design", design,
             "--variant", variant, "--config", cfg],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        status = "OK" if proc.returncode == 0 else "FAIL(%d)" % proc.returncode
        metrics = extract_metrics(run_dir) if proc.returncode == 0 else {}
        elapsed = time.time() - t0

        row = [
            design, variant, status,
            metrics.get("setup_wns", ""),
            metrics.get("setup_tns", ""),
            metrics.get("hold_wns", ""),
            metrics.get("setup_violations", ""),
            metrics.get("clock_period", ""),
            variant_params(design, variant),
        ]
        with open(SUMMARY, "a") as f:
            f.write("\t".join(str(x) for x in row) + "\n")
        print("        -> %s  (%.0fs)  WNS=%s TNS=%s" % (
            status, elapsed, metrics.get("setup_wns", "?"), metrics.get("setup_tns", "?")),
            flush=True)

    print("ALL DONE. Summary: %s" % SUMMARY, flush=True)


if __name__ == "__main__":
    main()
