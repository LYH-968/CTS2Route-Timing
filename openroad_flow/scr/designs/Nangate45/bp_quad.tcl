global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "bsg_chip"
set vars(design_nick) "bp_quad"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/bp_quad/bsg_chip_block.sv2v.v" \
    "$vars(flow_home)/designs/nangate45/bp_quad/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/bp_quad/bsg_chip.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/bp_quad/io.tcl"

nangate45_add_fakerams {256x48 32x32 64x124 512x64 64x62 128x116}

set vars(synth_hierarchical) 1
set vars(die_area) {0 0 3600 3600}
set vars(core_area) {10 12 3590 3590}
set vars(macro_place_halo) {10 10}
