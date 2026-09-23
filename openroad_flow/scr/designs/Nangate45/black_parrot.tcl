global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "black_parrot"
set vars(design_nick) "bp"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/black_parrot/pickled.v" \
    "$vars(flow_home)/designs/nangate45/black_parrot/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/black_parrot/constraint.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/black_parrot/io.tcl"

nangate45_add_fakerams {512x64 256x95 64x7 64x15 64x96}

set vars(synth_hierarchical) 1
set vars(abc_area) 1
set vars(die_area) {0 0 1350 1300}
set vars(core_area) {10.07 11.2 1340 1290}
set vars(place_density_lb_addon) 0.05
set vars(macro_place_halo) {10 10}
set vars(tns_end_percent) 100
