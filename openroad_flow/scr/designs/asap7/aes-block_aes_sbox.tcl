global vars

set vars(design_name) "aes_sbox"
set vars(design_nick) "aes-block_aes_sbox"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/aes/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/aes/constraint.sdc"

set vars(abc_area)             1
set vars(core_utilization)     40
set vars(core_aspect_ratio)    1
set vars(core_margin)          2
set vars(place_density)        0.70
set vars(max_routing_layer)    "M5"
set vars(place_pins_args)      [list -annealing]
set vars(pdn_tcl)              "$vars(flow_home)/platforms/asap7/openRoad/pdn/BLOCK_grid_strategy.tcl"
