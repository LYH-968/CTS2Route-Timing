global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "bp_fe_top"
set vars(design_nick) "bp_fe"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/bp_fe_top/pickled.v" \
    "$vars(flow_home)/designs/nangate45/bp_fe_top/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/bp_fe_top/constraint.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/bp_fe_top/io.tcl"
set vars(fastroute_tcl) "$vars(flow_home)/designs/nangate45/bp_fe_top/fastroute.tcl"

nangate45_add_fakerams {512x64 64x7 64x96}

set vars(synth_hierarchical) 1
set vars(die_area) {0 0 800 600}
set vars(core_area) {10 10 790 590}
set vars(macro_place_halo) {10 10}
set vars(place_density_lb_addon) 0.11
set vars(tns_end_percent) 100
