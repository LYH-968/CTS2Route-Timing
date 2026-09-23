source [file join [file dirname [info script]] "jpeg.tcl"]

set vars(design_nick) "jpeg_lvt"
set vars(sdc_file)    "$vars(flow_home)/designs/asap7/jpeg_lvt/jpeg_encoder15_7nm.sdc"
set vars(vt_list)     [list LVT]
set vars(recover_power) 100
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_L" "BUFx10_ASAP7_75t_L"]
