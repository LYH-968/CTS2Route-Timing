global vars

set vars(design_name) "ethmac"
set vars(design_nick) "ethmac"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/ethmac/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/ethmac/constraint.sdc"

set vars(abc_area)          1
set vars(core_utilization)  40
set vars(core_aspect_ratio) 1
set vars(core_margin)       2
set vars(place_density)     0.60
set vars(cts_buffer_list)   [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
