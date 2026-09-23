global vars

set vars(design_name) "MockAlu"
set vars(design_nick) "mock-alu"
set vars(corner)      "BC"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/mock-alu/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/mock-alu/constraints.sdc"

set vars(core_utilization)      50
set vars(core_aspect_ratio)     1
set vars(core_margin)           2
set vars(place_density)         0.75
set vars(place_pins_args)       [list -annealing]
set vars(routing_layer_adjustment) 0.45
set vars(cts_buffer_list)       [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
