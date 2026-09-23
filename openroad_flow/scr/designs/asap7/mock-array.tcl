global vars

proc mock_array_eval {expr_text} {
    global vars
    set design_dir "$vars(flow_home)/designs/asap7/mock-array"
    set table [expr {[info exists ::env(MOCK_ARRAY_TABLE)] && $::env(MOCK_ARRAY_TABLE) ne "" ? $::env(MOCK_ARRAY_TABLE) : "8 8 20 20 20 22"}]
    set scale [expr {[info exists ::env(MOCK_ARRAY_SCALE)] && $::env(MOCK_ARRAY_SCALE) ne "" ? $::env(MOCK_ARRAY_SCALE) : "45"}]
    return [string trim [exec bash -lc \
        "export MOCK_ARRAY_TABLE='$table'; export MOCK_ARRAY_SCALE='$scale'; cd '$design_dir' && python3 -c \"import config; print($expr_text)\""]]
}

set vars(design_name) "MockArray"
set vars(design_nick) "mock-array"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/mock-array/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/mock-array/constraints.sdc"

set vars(place_density)          0.30
set vars(core_area)              [split [mock_array_eval "f'{config.margin_x} {config.margin_y} {config.core_width + config.margin_x} {config.core_height + config.margin_y}'"]]
set vars(die_area)               [split [mock_array_eval "f'0 0 {config.die_width} {config.die_height}'"]]
set vars(macro_place_halo)       {0 2.16}
set vars(rtlmp_boundary_wt)      0
set vars(rtlmp_max_inst)         250
set vars(rtlmp_min_inst)         50
set vars(rtlmp_max_macro)        64
set vars(rtlmp_min_macro)        8
set vars(blocks)                 [list Element]
set vars(gds_allow_empty)        "Element"
set vars(pdn_tcl)                "$vars(flow_home)/platforms/asap7/openRoad/pdn/BLOCKS_grid_strategy.tcl"
set vars(io_constraints)         "$vars(flow_home)/designs/asap7/mock-array/io.tcl"
set vars(detailed_route_end_iteration) 6
set vars(max_routing_layer)      "M9"
set vars(macro_rows_halo_x)      0.5
set vars(macro_rows_halo_y)      0.5
set vars(io_placer_v)            [list M5 M7]
set vars(io_placer_h)            [list M4 M6]
set vars(cts_buffer_list)        [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]

set vars(block_config_map) [dict create \
    Element [file join [file dirname [info script]] "mock-array_Element.tcl"]]
set vars(block_design_nick_map) [dict create Element "mock-array_Element"]
