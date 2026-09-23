source [file join [file dirname [info script]] "ethmac.tcl"]

set vars(design_nick) "ethmac_lvt"
set vars(sdc_file)    "$vars(flow_home)/designs/asap7/ethmac_lvt/constraint.sdc"
set vars(vt_list)     [list LVT]
set vars(recover_power) 1
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_L" "BUFx10_ASAP7_75t_L"]
