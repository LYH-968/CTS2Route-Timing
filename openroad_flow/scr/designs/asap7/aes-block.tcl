global vars

set vars(design_name) "aes_cipher_top"
set vars(design_nick) "aes-block"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/aes/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/aes-block/constraint.sdc"

set vars(abc_area)             1
set vars(core_utilization)     20
set vars(core_aspect_ratio)    1
set vars(core_margin)          2
set vars(place_density)        0.53
set vars(blocks)               [list aes_rcon aes_sbox]
set vars(synth_hierarchical)   1
set vars(place_pins_args)      [list -annealing]
set vars(min_routing_layer)    "M2"
set vars(max_routing_layer)    "M9"
set vars(gnd_nets_voltages)    [list]
set vars(pwr_nets_voltages)    [list]
set vars(macro_place_halo)     {5 5}
set vars(routing_layer_adjustment) 0.3
set vars(cts_buffer_list)      [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]

set vars(block_config_map) [dict create \
    aes_rcon [file join [file dirname [info script]] "aes-block_aes_rcon.tcl"] \
    aes_sbox [file join [file dirname [info script]] "aes-block_aes_sbox.tcl"]]
set vars(block_design_nick_map) [dict create \
    aes_rcon "aes-block_aes_rcon" \
    aes_sbox "aes-block_aes_sbox"]
