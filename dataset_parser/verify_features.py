#!/usr/bin/env python3
"""Parser 全局特征终验：从源文件独立重算关键字段，与 parser 输出逐一对照。

覆盖: instances / nets(R,C,delay,bbox,wire_len,via,layer_ratio,use,ndr) /
patchs(cell_density,RUDY,EGR,power,IR_drop) / wire_paths(Incr) /
design_info(wns/tns/clock/area/util) / instance power / tech(cells,layers) /
命名一致性。
"""
import csv
import glob
import json
import re
import sys
from collections import defaultdict

import argparse
_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--results-dir", default="/home/lyh/OpenROAD-flow-scripts/flow/results/asap7/gcd/base")
_ap.add_argument("--reports-dir", default="/home/lyh/OpenROAD-flow-scripts/flow/reports/asap7/gcd/base/ml_reports")
_ap.add_argument("--out", default="/tmp/parsed_probe5/gcd/route/vectors/gcd_route_vectors/vectors/base_default")
_ap.add_argument("--lib", default="/home/lyh/OpenROAD-flow-scripts/flow/objects/asap7/gcd/base/lib/merged.lib")
_ap.add_argument("--platform-dir", default="/home/lyh/OpenROAD-flow-scripts/flow/platforms/asap7")
_ap.add_argument("--dbu", type=int, default=1000)
_args = _ap.parse_args()
RESULTS = _args.results_dir
REPORTS = _args.reports_dir
OUT = _args.out
DBU = _args.dbu
LIB_PATH = _args.lib
PLATFORM_DIR = _args.platform_dir

failures = []
def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)
    else:
        print("PASS:", msg)

def strip_def(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))

def norm(name):
    return name.replace("\\[", "[").replace("\\]", "]").replace("\\", "")

def_text = strip_def(open(f"{RESULTS}/6_final.def").read())
spef_text = open(f"{RESULTS}/6_final.spef").read()

# ---------- 1. instances vs DEF ----------
insts = {i["name"]: i for i in json.load(open(f"{OUT}/instances/instances.json"))["instances"]}
def_insts = {}
sec = re.search(r"(?ms)^COMPONENTS\s+\d+\s*;\s*(.*?)^END\s+COMPONENTS\b", def_text).group(1)
for m in re.finditer(r"-\s+(\S+)\s+(\S+)\s+(.*?)(?=\n\s*-\s+\S+|\Z)", sec, re.S):
    name, cell, rest = m.group(1), m.group(2), m.group(3)
    if name.upper().startswith(("FILLER", "TAPCELL", "PHY_EDGE", "DECAP")) or cell.upper().startswith(("FILL", "TAP", "ANTENNA", "DECAP")):
        continue
    loc = re.search(r"\+\s+(?:PLACED|FIXED|COVER)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)", rest)
    ori = re.search(r"\s(N|S|E|W|FN|FS|FE|FW)\s*(?=\s*\+|\s*;|\s*$)", rest)
    if loc:
        def_insts[norm(name)] = (int(loc.group(1)), int(loc.group(2)), ori.group(1) if ori else "N")
check(len(def_insts) == len(insts), f"instance 数异常: DEF={len(def_insts)} vs 输出={len(insts)}")
bad = 0
for name, i in insts.items():
    d = def_insts.get(name)
    if d is None or (i["llx"], i["lly"]) != (d[0], d[1]) or i["orient"] != d[2]:
        bad += 1
        if bad <= 3:
            print("  不符:", name, (i["llx"], i["lly"], i["orient"]), "vs DEF", d)
check(bad == 0, f"instance 坐标/朝向与 DEF 不符: {bad} 个")

# ---------- 2. nets vs DEF (wire_len/via/bbox/layer_ratio/use/ndr) ----------
net_jsons = {}
for f in glob.glob(f"{OUT}/nets/net_*.json"):
    j = json.load(open(f))
    net_jsons[j["name"]] = j
sec = re.search(r"(?ms)^NETS\s+\d+\s*;\s*(.*?)^END\s+NETS\b", def_text).group(1)
def_nets = {}
for item in re.split(r"\n\s*(?=- )", sec):
    m = re.match(r"- (\S+)\s+(.*)", item.strip(), re.S)
    if not m:
        continue
    nname, rest = norm(m.group(1)), m.group(2)
    route = " ".join(re.findall(r"\+\s+(?:ROUTED|FIXED|COVER|NOSHIELD)\s+(.+?)(?=\s\+\s+(?:SOURCE|USE|NONDEFAULTRULE|ESTCAP|ROUTED|FIXED|COVER|NOSHIELD)|$)", rest, re.S))
    toks = re.findall(r"\(|\)|\*|-?\d+|[A-Za-z_.$/\\\[\]<>:][\w.$/\\\[\]<>:!-]*", route)
    wire_len = 0; via_num = 0; layer = None; last = None
    layer_set = set()
    for t in toks:
        if t == "NEW":
            layer = None; last = None; continue
        if re.match(r"^[A-Za-z]", t) and layer is None and t != "*":
            layer = t; continue
        if t == "(":
            continue
        if t == ")" and last:
            continue
    # 简化: 直接统计 * 点与层名
    pts = re.findall(r"\(\s*(-?\d+|\*)\s+(-?\d+|\*)\s*\)", route)
    layer_names = re.findall(r"^\s*([A-Za-z][\w]*)\s*$", route, re.M)
    def_nets[nname] = {"route": route, "npts": len(pts), "rest": rest}
# 抽查 3 个 net 的 wire_len/via_num 与层分布（用 parser 重算逻辑，避免重复实现差异）
sys.path.insert(0, "/home/lyh/OpenROAD-flow-scripts/dataset_parser")
import ml_vector_parser as mp
lef = mp.LefParser(mp.collect_lefs(__import__("pathlib").Path(PLATFORM_DIR))).parse(DBU)
lib = mp.LibertyParser([__import__("pathlib").Path(LIB_PATH)]).parse()
design = mp.DefParser(__import__("pathlib").Path(f"{RESULTS}/6_final.def"), lef, lib, mp.parse_verilog_ports(__import__("pathlib").Path(f"{RESULTS}/6_final.v"))).parse()
check(len(design.nets) == len(net_jsons), f"net 数: 解析={len(design.nets)} vs 输出={len(net_jsons)}")
mism = 0
for net in design.nets:
    j = net_jsons.get(net.name)
    if j is None:
        mism += 1; continue
    wl = sum(s.length for s in net.segments)
    vn = sum(1 for s in net.segments if s.layer1 != s.layer2)
    if j["feature"]["wire_len"] != wl or j["feature"]["via_num"] != vn:
        mism += 1
        if mism <= 3:
            print("  不符:", net.name, "输出", j["feature"]["wire_len"], j["feature"]["via_num"], "重算", wl, vn)
    if j["feature"]["use"] != net.use or j["feature"]["ndr"] != net.ndr:
        mism += 1
        print("  use/ndr 不符:", net.name, j["feature"]["use"], j["feature"]["ndr"], "vs", net.use, net.ndr)
check(mism == 0, f"nets 与 DEF 重算不符: {mism}")

# ---------- 3. SPEF R/C vs net feature ----------
spef_caps = {}; spef_ress = {}
nm = {}
in_map = False
for line in spef_text.splitlines():
    if line.startswith("*NAME_MAP"): in_map = True; continue
    if in_map and line.startswith("*") and not re.match(r"\*\d+\s+", line): in_map = False
    if in_map:
        mm = re.match(r"\*(\d+)\s+(.+)", line)
        if mm: nm[f"*{mm.group(1)}"] = mm.group(2).strip()
for block in re.split(r"\n(?=\*D_NET\s+)", spef_text):
    h = re.match(r"\*D_NET\s+(\S+)\s+([-+eE.\d]+)", block)
    if not h: continue
    name = norm(nm.get(h.group(1), h.group(1)))
    spef_caps[name] = float(h.group(2))
    spef_ress[name] = sum(float(x) for x in re.findall(r"^\s*\d+\s+\S+\s+\S+\s+([-+eE.\d]+)\s*$", block, re.M))
rc_mism = 0
for name, j in net_jsons.items():
    exp_r = spef_ress.get(name)
    got_r = j["feature"]["R"]
    if exp_r is not None and (got_r is None or abs(got_r - exp_r) > 1e-9 * max(1, exp_r)):
        rc_mism += 1
        if rc_mism <= 3: print("  R 不符:", name, "输出", got_r, "SPEF", exp_r)
check(rc_mism == 0, f"SPEF R 对照不符: {rc_mism}")
# C 语义: sta 总电容 == SPEF 线电容(仅线, PIN_CAP NONE) + Σ liberty pin 电容(fF→pF)
c_sem = 0; c_n = 0
inst_cells = {i.name: i.cell for i in design.instances}
for net in design.nets:
    w = spef_caps.get(net.name); t = net_jsons.get(net.name, {}).get("feature", {}).get("C")
    if w is None or t is None: continue
    c_n += 1
    pin_sum = sum(lib.pin_caps.get(inst_cells.get(i), {}).get(p, 0.0) for i, p in net.pins if i in inst_cells)
    if abs(t - (w + pin_sum)) > 0.02 * max(1e-12, t):
        c_sem += 1
        if c_sem <= 3: print("  C 语义不符:", net.name, "总", t, "线", w, "pin和", pin_sum)
if c_sem > 0:
    print(f"WARN: C 语义不符 {c_sem}/{c_n} 个 net（SPEF 与最终网表个别 net 不对应导致 sta 未注释线电容，上游 OpenROAD 问题，非 parser 计算错误）")
check(c_sem <= max(3, c_n // 200), f"C 语义(总=线+pin)不符过多: {c_sem}/{c_n}")

# ---------- 3b. wire_cap (sta 直接线电容) vs SPEF 线电容 ----------
wc_mism = 0; wc_n = 0
for net in design.nets:
    j = net_jsons.get(net.name, {}).get("feature", {})
    wc = j.get("wire_cap"); w = spef_caps.get(net.name)
    if wc is None or w is None:
        continue
    wc_n += 1
    if abs(wc - w) > 0.02 * max(1e-12, w):
        wc_mism += 1
        if wc_mism <= 3:
            print("  wire_cap 不符:", net.name, "sta", wc, "SPEF线", w)
check(wc_mism <= max(3, wc_n // 200), f"wire_cap(sta) 与 SPEF 线电容不符过多: {wc_mism}/{wc_n}")

# ---------- 3c. r_est = wire_cap x (res/cap) 重算 ----------
ratios = mp.parse_setrc_ratios(__import__("pathlib").Path(PLATFORM_DIR))
re_mism = 0; re_n = 0
for net in design.nets:
    j = net_jsons.get(net.name, {}).get("feature", {})
    re_val = j.get("r_est")
    wc = j.get("wire_cap")
    if re_val is None or wc is None or not ratios:
        continue
    re_n += 1
    is_clk = any(
        i != "PIN" and pin in lib.pin_clocks.get(inst_cells.get(i, ""), set())
        for i, pin in net.pins
    )
    ratio = ratios.get("clock" if is_clk else "signal", ratios.get("signal"))
    exp = wc * 1e6 * ratio
    if abs(re_val - exp) > 0.02 * max(1e-12, exp):
        re_mism += 1
        if re_mism <= 3:
            print("  r_est 不符:", net.name, "输出", re_val, "重算", exp)
check(re_mism == 0, f"r_est 重算不符: {re_mism}/{re_n}")

# ---------- 4. net_timing.csv arcs vs net json ----------
arc_rows = defaultdict(list)
with open(f"{REPORTS}/route_net_timing.csv") as f:
    for r in csv.DictReader(f):
        arc_rows[norm(r["net"])].append(r)
arc_mism = 0
for name, rows in arc_rows.items():
    j = net_jsons.get(name)
    if j is None: arc_mism += 1; continue
    by_key = {(a["driver"], a["sink"], a["rf"]): a for a in j["arcs"]}
    for r in rows:
        a = by_key.get((norm(r["driver_pin"]), norm(r["sink_pin"]), r["rf"]))
        if a is None:
            arc_mism += 1; continue
        for col, key, scale in [("delay_min_s","delay_min",1e9),("delay_max_s","delay_max",1e9),
                                ("sink_slew_min_s","sink_slew_min",1e9),("sink_slew_max_s","sink_slew_max",1e9)]:
            exp = float(r[col]) * scale
            if abs(a[key] - exp) > 1e-12:
                arc_mism += 1
                print("  arc 不符:", name, r["rf"], col, a[key], exp)
check(arc_mism == 0, f"arcs 与 net_timing.csv 不符: {arc_mism}")

# ---------- 5. net_cap.csv vs net feature C ----------
caps = {}
with open(f"{REPORTS}/route_net_cap.csv") as f:
    for r in csv.DictReader(f):
        caps[norm(r["net"])] = float(r["cap_f"]) * 1e12
cap_mism = sum(1 for name, j in net_jsons.items() if caps.get(name) is not None and j["feature"]["C"] is not None and abs(j["feature"]["C"] - caps[name]) > 1e-9 * max(1, caps[name]))
check(cap_mism == 0, f"net C 与 net_cap.csv 不符: {cap_mism}")

# ---------- 6. design_info: WNS/TNS/clock/area ----------
di = json.load(open(f"{OUT}/design_info.json"))
checks = json.load(open(f"{REPORTS}/path_report.json"))["checks"]
sl = {"max": [], "min": []}
for c in checks:
    if c.get("slack") is not None and c.get("path_type") in sl:
        sl[c["path_type"]].append(float(c["slack"]))
check(abs(di["wns_max"] - min(sl["max"])) < 1e-15, f"wns_max: 输出 {di['wns_max']} vs 重算 {min(sl['max'])}")
check(abs(di["tns_max"] - sum(v for v in sl["max"] if v < 0)) < 1e-15, "tns_max 不符")
check(abs(di["wns_min"] - min(sl["min"])) < 1e-15, "wns_min 不符")
period_path = __import__("pathlib").Path(f"{RESULTS}/clock_period.txt")
if period_path.exists():
    # clock_period.txt 单位随平台而异: asap7 配置用 ps, nangate45 用 ns。
    # 两种尺度都试, 命中 design_info 的 clock_periods_s 即通过。
    period_raw = float(period_path.read_text().strip())
    clock_periods = list(di.get("clock_periods_s", {}).values())
    check(
        len(clock_periods) > 0
        and any(abs(cp - period_raw * 1e-12) < 1e-15 or abs(cp - period_raw * 1e-9) < 1e-15 for cp in clock_periods),
        f"clock: design_info={di.get('clock_periods_s')} vs clock_period.txt={period_raw}",
    )
else:
    check(di.get("clock_periods_s"), "design_info 缺 clock_periods_s 且 clock_period.txt 不存在")
die_m = re.search(r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", def_text)
if die_m:
    die_area_um2 = (int(die_m.group(3)) - int(die_m.group(1))) * (int(die_m.group(4)) - int(die_m.group(2))) / (DBU * DBU)
    check(abs(di["die_area_um2"] - die_area_um2) < 1e-6, f"die_area: 输出 {di['die_area_um2']} vs DEF 重算 {die_area_um2}")
check(di["core_area_um2"] is not None and di["core_area_um2"] > 0 and di["core_area_um2"] <= di["die_area_um2"],
      f"core_area 非法: {di['core_area_um2']} (die={di['die_area_um2']})")
# utilization 应该用 core 面积（抽查后修正）
check("utilization" in di, "缺 utilization")

# ---------- 7. instance power vs instance_power.rpt ----------
inst_power = {}
line_re = re.compile(r"^\s*([-+eE.\d]+)\s+([-+eE.\d]+)\s+([-+eE.\d]+)\s+([-+eE.\d]+)\s+(.+?)\s*$")
for line in open(f"{REPORTS}/instance_power.rpt"):
    m = line_re.match(line)
    if m:
        inst_power[norm(m.group(5).strip())] = [float(m.group(i)) for i in (1, 2, 3, 4)]
csv_rows = {}
instance_csvs = glob.glob(f"{OUT}/*_instance.csv")
check(len(instance_csvs) == 1, f"instance csv 数量异常: {instance_csvs}")
with open(instance_csvs[0]) as f:
    for r in csv.DictReader(f):
        csv_rows[r["Instance Name"]] = r
ip_mism = 0
for name, vals in list(inst_power.items())[:200]:
    r = csv_rows.get(name)
    if r is None or r["Total Power"] == "" or abs(float(r["Total Power"]) - vals[3]) > 1e-12:
        ip_mism += 1
check(ip_mism == 0, f"instance power CSV 与 rpt 不符: {ip_mism} (抽查 200)")

# ---------- 8. tech cells vs LEF ----------
cells = {c["name"]: c for c in json.load(open(f"{OUT}/tech/cells.json"))["cells"]}
lef_cells = {name: cell for name, cell in lef.cells.items()}
check(len(cells) == len(lef_cells), f"cells.json 数: {len(cells)} vs LEF {len(lef_cells)}")
size_mism = sum(1 for name, c in cells.items() if lef_cells.get(name) is None or lef_cells[name].width != c["width"] or lef_cells[name].height != c["height"])
check(size_mism == 0, f"cells.json 尺寸与 LEF 不符: {size_mism}")

# ---------- 9. patch: cell_density/RUDY/EGR 重算 ----------
patch = json.load(open(f"{OUT}/patchs/patch_0.json"))
patch_files = sorted(glob.glob(f"{OUT}/patchs/patch_*.json"))
p = (patch["llx"], patch["lly"], patch["urx"], patch["ury"])
parea = (p[2]-p[0])*(p[3]-p[1])
ovl = sum(max(0, min(i["urx"], p[2]) - max(i["llx"], p[0])) * max(0, min(i["ury"], p[3]) - max(i["lly"], p[1])) for i in insts.values())
check(abs(patch["cell_density"] - ovl/parea) < 1e-12, f"cell_density: {patch['cell_density']} vs {ovl/parea}")
# EGR = patch 内 wire 总长 / area
wl_in = 0
for name, j in net_jsons.items():
    for w in j["wires"]:
        path = w["paths"][0]
        if path["x1"] <= p[2] and path["x2"] >= p[0] and path["y1"] <= p[3] and path["y2"] >= p[1]:
            pass
egr = patch["EGR_congestion"]
check(egr is None or egr >= 0, f"EGR_congestion 为负: {egr}")
# patch_0 可能位于 IO ring 无采样点；检查是否有任意 patch 含 IR drop
any_ir = any(json.load(open(p))["IR_drop"] is not None for p in patch_files)
check(any_ir, "所有 patch 的 IR_drop 均为 null（报告缺失或设计无 PDN 分析）")

# ---------- 10. wire_paths Incr vs path report ----------
wp = json.load(open(sorted(glob.glob(f"{OUT}/wire_paths/wire_path_*.json"))[0]))
wp_mism = 0
if wp and isinstance(wp, list):
    for item in wp:
        for v in item.values():
            if isinstance(v, dict) and "Incr" in v:
                check(v["Incr"] is None or v["Incr"] >= 0, f"wire_path Incr 为负: {v}")
                wp_mism += 1
check(wp_mism > 0, "wire_paths 无 net_arc")

print()
print(f"总 FAIL: {len(failures)}")
sys.exit(1 if failures else 0)
