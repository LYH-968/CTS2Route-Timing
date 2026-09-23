global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "bp_multi_top"
set vars(design_nick) "bp_multi"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/bp_multi_top/pickled.v" \
    "$vars(flow_home)/designs/nangate45/bp_multi_top/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/bp_multi_top/constraint.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/bp_multi_top/io.tcl"

nangate45_add_fakerams {512x64 256x96 32x64 64x7 64x15 64x96}

set vars(synth_hierarchical) 1
set vars(abc_area) 1
set vars(die_area) {0 0 1100 1100}
set vars(core_area) {10.07 9.8 1090 1090}
set vars(macro_place_halo) {10 10}
set vars(place_density_lb_addon) 0.05
set vars(skip_gate_cloning) 1
