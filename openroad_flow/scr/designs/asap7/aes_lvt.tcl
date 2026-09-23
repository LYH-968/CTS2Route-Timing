source [file join [file dirname [info script]] "aes.tcl"]

set vars(design_nick) "aes_lvt"
set vars(sdc_file)    "$vars(flow_home)/designs/asap7/aes_lvt/constraint.sdc"
set vars(vt_list)     [list LVT]
set vars(recover_power) 100
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_L" "BUFx10_ASAP7_75t_L"]
