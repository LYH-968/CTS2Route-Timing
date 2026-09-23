global vars

set vars(design_name) "mock_cpu"
set vars(design_nick) "mock-cpu"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/fifo/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/mock-cpu/constraint.sdc"

set vars(core_utilization)  40
set vars(core_aspect_ratio) 1
set vars(core_margin)       2
set vars(place_density)     0.71
set vars(tns_end_percent)   100
set vars(io_constraints)    "$vars(flow_home)/designs/asap7/mock-cpu/io.tcl"
set vars(cts_buffer_list)   [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
