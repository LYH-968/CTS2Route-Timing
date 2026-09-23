global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "gcd"
set vars(design_nick) "gcd"

set vars(verilog_files) [list "$vars(flow_home)/designs/src/gcd/gcd.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/gcd/constraint.sdc"

set vars(abc_area) 1
set vars(adder_map_file) ""
set vars(core_utilization) 55
set vars(place_density_lb_addon) 0.20
set vars(tns_end_percent) 100
