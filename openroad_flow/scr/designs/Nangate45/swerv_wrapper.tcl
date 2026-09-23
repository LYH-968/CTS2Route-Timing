global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "swerv_wrapper"
set vars(design_nick) "swerv_wrapper"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/swerv/swerv_wrapper.sv2v.v" \
    "$vars(flow_home)/designs/nangate45/swerv/macros.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/swerv_wrapper/constraint.sdc"
set vars(io_constraints) "$vars(flow_home)/designs/nangate45/swerv_wrapper/io.tcl"
set vars(fastroute_tcl) "$vars(flow_home)/designs/nangate45/swerv_wrapper/fastroute.tcl"

nangate45_add_fakerams {2048x39 256x34 64x21}

set vars(die_area) {0 0 1100 1000}
set vars(core_area) {10.07 11.2 1090 990}
set vars(macro_place_halo) {10 10}
set vars(place_density_lb_addon) 0.08
set vars(tns_end_percent) 100
