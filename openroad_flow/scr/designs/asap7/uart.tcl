global vars

set vars(design_name) "uart"
set vars(design_nick) "uart"
set vars(corner)      "TC"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/uart/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/uart/constraint.sdc"
set vars(verilog_top_params) [dict create DATA_WIDTH 8]

set vars(die_area)      {0 0 17 17}
set vars(core_area)     {1.08 1.08 16 16}
set vars(place_density) 0.70
set vars(tns_end_percent) 100
set vars(skip_gate_cloning) 1
set vars(abc_area) 0
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
