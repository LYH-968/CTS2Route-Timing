global vars

set vars(design_name) "gcd"
set vars(design_nick) "gcd"

set vars(verilog_files) [lsort [glob -nocomplain "$vars(flow_home)/designs/src/gcd/*.v"]]
set vars(sdc_file)      "$vars(flow_home)/designs/asap7/gcd/constraint.sdc"

set vars(die_area)      {0 0 16.2 16.2}
set vars(core_area)     {1.08 1.08 15.12 15.12}
set vars(place_density) 0.35

set vars(skip_last_gasp) 1
set vars(abc_area)       0
set vars(cts_buffer_list) [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
