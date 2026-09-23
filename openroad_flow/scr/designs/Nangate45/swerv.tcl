global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "swerv"
set vars(design_nick) "swerv"

set vars(verilog_files) [list "$vars(flow_home)/designs/src/swerv/swerv_wrapper.sv2v.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/swerv/constraint.sdc"

set vars(core_utilization) 40
set vars(core_aspect_ratio) 1
set vars(core_margin) 5
set vars(place_density_lb_addon) 0.25
set vars(tns_end_percent) 100
