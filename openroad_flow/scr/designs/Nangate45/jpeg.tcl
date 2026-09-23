global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "jpeg_encoder"
set vars(design_nick) "jpeg"

set vars(verilog_files) [nangate45_sorted_glob "$vars(flow_home)/designs/src/jpeg/*.v"]
set vars(verilog_include_dirs) [list "$vars(flow_home)/designs/src/jpeg/include"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/jpeg/constraint.sdc"

set vars(abc_area) 1
set vars(core_utilization) 45
set vars(place_density_lb_addon) 0.20
set vars(tns_end_percent) 100
