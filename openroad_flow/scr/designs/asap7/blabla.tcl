global vars

set vars(design_name) "blabla"
set vars(design_nick) "blabla"

# blabla.v 已内联 blabla_core + blabla_qr；src/blabla/ 下的 blabla_core.v/blabla_qr.v 是重复副本，不能一起读
set vars(verilog_files) [list "$vars(flow_home)/designs/src/blabla/blabla.v"]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/blabla/constraint.sdc"

set vars(abc_area)          1
set vars(core_utilization)  50
set vars(core_aspect_ratio) 1
set vars(core_margin)       2
set vars(place_density)     0.60
set vars(cts_buffer_list)   [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
