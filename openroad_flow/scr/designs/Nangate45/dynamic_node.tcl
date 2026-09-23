global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "dynamic_node_top_wrap"
set vars(design_nick) "dynamic_node"

set vars(verilog_files) [list "$vars(flow_home)/designs/src/dynamic_node/dynamic_node.pickle.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/dynamic_node/constraint.sdc"

set vars(core_utilization) 40
set vars(core_aspect_ratio) 1
set vars(core_margin) 5
set vars(place_density_lb_addon) 0.20
set vars(tns_end_percent) 100
