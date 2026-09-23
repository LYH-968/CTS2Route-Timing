global vars

set vars(design_name) "aes_cipher_top"
set vars(design_nick) "aes"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/aes/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/aes/constraint.sdc"

set vars(die_area) "0 0 70 70"
set vars(core_area) "5 5 66 66"
set vars(place_density) 0.68
set vars(tns_end_percent) 100
set vars(remove_cells_for_eqy) [list "TAPCELL*"]
set vars(abc_area) 1
set vars(enable_equivalence_check) 0
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
