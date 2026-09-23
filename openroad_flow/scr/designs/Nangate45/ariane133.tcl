global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "ariane"
set vars(design_nick) "ariane133"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/ariane133/ariane.sv2v.v" \
    "$vars(flow_home)/designs/nangate45/ariane133/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/ariane133/ariane.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/ariane133/io.tcl"

nangate45_add_fakerams {256x16}

set vars(synth_hierarchical) 1
set vars(die_area) {0 0 1500 1500}
set vars(core_area) {10 12 1448 1448}
set vars(macro_place_halo) {10 10}
set vars(tns_end_percent) 100
set vars(skip_gate_cloning) 1
