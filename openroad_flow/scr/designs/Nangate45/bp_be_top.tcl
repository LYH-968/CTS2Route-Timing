global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "bp_be_top"
set vars(design_nick) "bp_be"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/bp_be_top/pickled.v" \
    "$vars(flow_home)/designs/nangate45/bp_be_top/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/bp_be_top/constraint.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/bp_be_top/io.tcl"
set vars(fastroute_tcl) "$vars(flow_home)/designs/nangate45/bp_be_top/fastroute.tcl"

nangate45_add_fakerams {512x64 64x15 64x96}

set vars(synth_hierarchical) 1
set vars(die_area) {0 0 800 700}
set vars(core_area) {10.07 11.2 790 690}
set vars(macro_place_halo) {10 10}
set vars(place_density_lb_addon) 0.10
set vars(tns_end_percent) 100
set vars(cts_cluster_size) 30
set vars(cts_cluster_diameter) 50
set vars(synth_minimum_keep_size) 3000
