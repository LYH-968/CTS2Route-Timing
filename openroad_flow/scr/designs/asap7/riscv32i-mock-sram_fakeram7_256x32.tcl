global vars

set vars(design_name) "fakeram7_256x32"
set vars(design_nick) "riscv32i-mock-sram_fakeram7_256x32"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/asap7/riscv32i-mock-sram/fakeram7_256x32/mock-fakeram7_256x32.v"]
set vars(sdc_file) "$vars(flow_home)/designs/asap7/riscv32i-mock-sram/fakeram7_256x32/constraints.sdc"

set vars(core_utilization)     50
set vars(core_aspect_ratio)    8
set vars(place_density)        0.80
set vars(max_routing_layer)    "M4"
set vars(min_clk_routing_layer) "M2"
set vars(place_pins_args)      [list -min_distance 6 -min_distance_in_tracks]
set vars(io_constraints)       "$vars(flow_home)/designs/asap7/riscv32i-mock-sram/fakeram7_256x32/io.tcl"
set vars(pdn_tcl)              "$vars(flow_home)/platforms/asap7/openRoad/pdn/BLOCK_grid_strategy.tcl"
