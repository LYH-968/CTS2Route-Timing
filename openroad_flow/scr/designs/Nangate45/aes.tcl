global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "aes_cipher_top"
set vars(design_nick) "aes"

set vars(verilog_files) [nangate45_sorted_glob "$vars(flow_home)/designs/src/aes/*.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/aes/constraint.sdc"
set vars(floorplan_def) "$vars(flow_home)/designs/nangate45/aes/aes_ng45_fp.def"

set vars(place_density_lb_addon) 0.20
set vars(tns_end_percent) 100
set vars(skip_incremental_repair) 1
