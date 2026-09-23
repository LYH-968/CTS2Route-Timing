global vars

proc mock_array_element_eval {expr_text} {
    global vars
    set design_dir "$vars(flow_home)/designs/asap7/mock-array"
    set table [expr {[info exists ::env(MOCK_ARRAY_TABLE)] && $::env(MOCK_ARRAY_TABLE) ne "" ? $::env(MOCK_ARRAY_TABLE) : "8 8 20 20 20 22"}]
    set scale [expr {[info exists ::env(MOCK_ARRAY_SCALE)] && $::env(MOCK_ARRAY_SCALE) ne "" ? $::env(MOCK_ARRAY_SCALE) : "45"}]
    return [string trim [exec bash -lc \
        "export MOCK_ARRAY_TABLE='$table'; export MOCK_ARRAY_SCALE='$scale'; cd '$design_dir' && python3 -c \"import config; print($expr_text)\""]]
}

set vars(design_name) "Element"
set vars(design_nick) "mock-array_Element"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/mock-array/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/mock-array/constraints.sdc"

set vars(place_density)             0.82
set vars(core_area)                 [split [mock_array_element_eval "f'{config.ce_margin_x} {config.ce_margin_y} {config.ce_width - config.ce_margin_x} {config.ce_height - config.ce_margin_y}'"]]
set vars(die_area)                  [split [mock_array_element_eval "f'0 0 {config.ce_width} {config.ce_height}'"]]
set vars(io_constraints)            "$vars(flow_home)/designs/asap7/mock-array/Element/io.tcl"
set vars(pdn_tcl)                   "$vars(flow_home)/platforms/asap7/openRoad/pdn/BLOCK_grid_strategy.tcl"
set vars(detailed_route_end_iteration) 6
set vars(min_routing_layer)         "M2"
set vars(max_routing_layer)         "M5"
set vars(io_placer_h)               [list M2 M4]
set vars(io_placer_v)               [list M3 M5]
set vars(place_pins_args)           [list -annealing]
set vars(gnd_nets_voltages)         [list]
set vars(pwr_nets_voltages)         [list]
set vars(cts_buffer_list)           [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
