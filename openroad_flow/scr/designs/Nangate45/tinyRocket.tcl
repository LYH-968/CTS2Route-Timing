global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "RocketTile"
set vars(design_nick) "tinyRocket"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/tinyRocket/AsyncResetReg.v" \
    "$vars(flow_home)/designs/src/tinyRocket/ClockDivider2.v" \
    "$vars(flow_home)/designs/src/tinyRocket/ClockDivider3.v" \
    "$vars(flow_home)/designs/src/tinyRocket/plusarg_reader.v" \
    "$vars(flow_home)/designs/src/tinyRocket/freechips.rocketchip.system.TinyConfig.v" \
    "$vars(flow_home)/designs/nangate45/tinyRocket/freechips.rocketchip.system.TinyConfig.v"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/tinyRocket/constraint.sdc"
set vars(additional_lefs) [nangate45_sorted_glob "$vars(flow_home)/designs/nangate45/tinyRocket/*.lef"]
set vars(additional_libs) [nangate45_sorted_glob "$vars(flow_home)/designs/nangate45/tinyRocket/*.lib"]

set vars(synth_hierarchical) 1
set vars(synth_minimum_keep_size) 5000
set vars(die_area) {0 0 424.92 499.4}
set vars(core_area) {10.07 9.8 414.85 489.6}
set vars(tns_end_percent) 100
