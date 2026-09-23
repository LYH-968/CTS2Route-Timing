#!/usr/bin/env python3
"""为 6 个新 design（blabla/picorv32/ppu/salsa20/s35932/ethmac）生成 openroad_flow setup 文件。

生成内容：
  1. RTL 副本：flow/designs/src/<nick>/*.v（picorv32/ppu/salsa20/s35932；blabla/ethmac 已存在）
  2. constraint.sdc：flow/designs/asap7/<nick>/constraint.sdc（5 个新 design；ethmac 已存在）
  3. 基础 tcl：scr/designs/asap7/<nick>.tcl（5 个新 design；ethmac 已存在）
  4. 9 个 variant tcl：scr/designs/asap7/generated_variants/<nick>_<variant>.tcl（6 个 design）

只用 utilization 法（core_utilization + core_aspect_ratio + core_margin + place_density），
与 jpeg/ethmac 一致；variant 只改面积/密度/宽高比，不动时钟频率、布线层、skip_* 开关。
"""
import os
import shutil

FLOW_ROOT = "/home/lyh/OpenROAD-flow-scripts"
FLOW_HOME = os.path.join(FLOW_ROOT, "flow")
SRC = os.path.join(FLOW_HOME, "designs", "src")
INITIAL = os.path.join(SRC, "openroad_initial_files")
ASAP7 = os.path.join(FLOW_HOME, "designs", "asap7")
DESIGNS_DIR = os.path.join(FLOW_ROOT, "openroad_flow", "scr", "designs", "asap7")
VARDIR = os.path.join(DESIGNS_DIR, "generated_variants")

# nick: (top_module, clock_port, clock_period_ps, base_core_utilization, base_place_density)
DESIGNS = {
    "blabla":   ("blabla",   "clk", 1800, 50, 0.60),
    "picorv32": ("picorv32", "clk", 1000, 50, 0.60),
    "ppu":      ("PPU",      "clk", 1000, 50, 0.60),
    "salsa20":  ("salsa20",  "clk", 1000, 50, 0.60),
    "s35932":   ("s35932",   "CK",  2000, 45, 0.60),
    "ethmac":   ("ethmac",   "wb_clk_i", 1000, 40, 0.60),  # 基础 tcl/sdc 已存在，只补 variant
}

# RTL 源文件（openroad_initial_files 下的目录名 -> 文件名）
RTL_SRC = {
    "picorv32": "picorv32.v",
    "ppu":      "PPU.v",
    "salsa20":  "salsa20.v",
    "s35932":   "s35932.v",
}

# variant 覆盖规则：相对 base 的增量（或绝对值）
# (variant_name, override_kind)  ->  kind 决定如何算值
VARIANTS = [
    ("area_compact", "area", +10),
    ("area_loose",   "area", -10),
    ("area_xloose",  "area", -15),
    ("area_xxloose", "area", -20),
    ("dens_low",     "dens", -0.10),
    ("dens_high",    "dens", +0.10),
    ("dens_xhigh",   "dens", +0.15),
    ("ar_wide",      "aspect", 1.6),
    ("ar_tall",      "aspect", 0.625),
]


def sdc_text(top, clk_port, period):
    return (
        "current_design {top}\n"
        "\n"
        "set clk_name {clk}\n"
        "set clk_port_name {clk}\n"
        "set clk_period {period}\n"
        "set clk_io_pct 0.2\n"
        "\n"
        "set clk_port [get_ports $clk_port_name]\n"
        "\n"
        "create_clock -name $clk_name -period $clk_period $clk_port\n"
        "\n"
        "set non_clock_inputs [all_inputs -no_clocks]\n"
        "\n"
        "set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs\n"
        "set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]\n"
    ).format(top=top, clk=clk_port, period=period)


def base_tcl_text(nick, top):
    return (
        "global vars\n"
        "\n"
        'set vars(design_name) "{top}"\n'
        'set vars(design_nick) "{nick}"\n'
        "\n"
        'set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/{nick}/*.v"]]\n'
        'set vars(sdc_file)      "$vars(flow_home)/designs/asap7/{nick}/constraint.sdc"\n'
        "\n"
        "set vars(abc_area)          1\n"
        "set vars(core_utilization)  {util}\n"
        "set vars(core_aspect_ratio) 1\n"
        "set vars(core_margin)       2\n"
        "set vars(place_density)     {dens:.2f}\n"
        'set vars(cts_buffer_list)   [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]\n'
    ).format(top=top, nick=nick, util=DESIGNS[nick][3], dens=DESIGNS[nick][4])


def variant_tcl_text(nick, vname, kind, val):
    override = ""
    if kind == "area":
        override = "set vars(core_utilization) %d" % val
    elif kind == "dens":
        override = "set vars(place_density) %.2f" % val
    elif kind == "aspect":
        override = "set vars(core_aspect_ratio) %s" % ("%g" % val)
    return (
        "global vars\n"
        "set _variant_dir [file dirname [info script]]\n"
        'source [file normalize [file join $_variant_dir ".." "{nick}.tcl"]]\n'
        'set vars(flow_variant) "{nick}_{vname}"\n'
        "{override}\n"
    ).format(nick=nick, vname=vname, override=override)


def compute_variant_value(base, kind, delta):
    if kind == "area":
        return int(round(base[0] + delta))
    if kind == "dens":
        return base[1] + delta
    return delta  # aspect: 绝对值


def main():
    created = []
    for nick, (top, clk, period, util, dens) in DESIGNS.items():
        # 1. RTL 副本（只对在 RTL_SRC 里的 design）
        if nick in RTL_SRC:
            src_dir = os.path.join(INITIAL, {"ppu": "PPU"}.get(nick, nick))
            src_file = os.path.join(src_dir, RTL_SRC[nick])
            dst_dir = os.path.join(SRC, nick)
            os.makedirs(dst_dir, exist_ok=True)
            dst_file = os.path.join(dst_dir, RTL_SRC[nick])
            if not os.path.exists(src_file):
                raise SystemExit(f"RTL 源不存在: {src_file}")
            shutil.copyfile(src_file, dst_file)
            created.append(dst_file)

        # 2. constraint.sdc（blabla/ethmac 已存在，跳过）
        sdc_path = os.path.join(ASAP7, nick, "constraint.sdc")
        if not os.path.exists(sdc_path):
            os.makedirs(os.path.dirname(sdc_path), exist_ok=True)
            with open(sdc_path, "w") as f:
                f.write(sdc_text(top, clk, period))
            created.append(sdc_path)

        # 3. 基础 tcl（ethmac 已存在，跳过）
        base_path = os.path.join(DESIGNS_DIR, nick + ".tcl")
        if not os.path.exists(base_path):
            with open(base_path, "w") as f:
                f.write(base_tcl_text(nick, top))
            created.append(base_path)

        # 4. variant tcl
        os.makedirs(VARDIR, exist_ok=True)
        for vname, kind, delta in VARIANTS:
            val = compute_variant_value((util, dens), kind, delta)
            vpath = os.path.join(VARDIR, f"{nick}_{vname}.tcl")
            with open(vpath, "w") as f:
                f.write(variant_tcl_text(nick, vname, kind, val))
            created.append(vpath)

    print(f"生成/复制 {len(created)} 个文件:")
    for p in created:
        print("  " + p)


if __name__ == "__main__":
    main()
