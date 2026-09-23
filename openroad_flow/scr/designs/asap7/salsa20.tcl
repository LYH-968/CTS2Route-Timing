global vars

set vars(design_name) "salsa20"
set vars(design_nick) "salsa20"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/salsa20/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/salsa20/constraint.sdc"

set vars(abc_area)          1
set vars(core_utilization)  50
set vars(core_aspect_ratio) 1
set vars(core_margin)       2
set vars(place_density)     0.60
set vars(cts_buffer_list)   [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
