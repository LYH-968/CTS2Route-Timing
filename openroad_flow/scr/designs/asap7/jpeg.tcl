global vars

set vars(design_name) "jpeg_encoder"
set vars(design_nick) "jpeg"

set vars(verilog_files)        [lsort [glob -nocomplain "$vars(flow_home)/designs/src/jpeg/*.v"]]
set vars(verilog_include_dirs) [list "$vars(flow_home)/designs/src/jpeg/include"]
set vars(sdc_file)             "$vars(flow_home)/designs/asap7/jpeg/jpeg_encoder15_7nm.sdc"

set vars(core_utilization) 60
set vars(core_aspect_ratio) 1
set vars(core_margin) 2
set vars(place_density) 0.60
set vars(tns_end_percent) 100
set vars(remove_cells_for_eqy) [list "TAPCELL*"]
set vars(abc_area) 1
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
