global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "ibex_core"
set vars(design_nick) "ibex"

set vars(verilog_files) [concat \
    [nangate45_sorted_glob "$vars(flow_home)/designs/src/ibex_sv/*.sv"] \
    [list "$vars(flow_home)/designs/src/ibex_sv/syn/rtl/prim_clock_gating.v"]]
set vars(verilog_include_dirs) [list \
    "$vars(flow_home)/designs/src/ibex_sv/vendor/lowrisc_ip/prim/rtl"]
set vars(synth_hdl_frontend) "slang"
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/ibex/constraint.sdc"

set vars(core_utilization) 50
set vars(place_density_lb_addon) 0.20
set vars(tns_end_percent) 100
