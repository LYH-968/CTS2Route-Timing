global vars

set vars(design_name) "riscv_top"
set vars(design_nick) "riscv32i"

set vars(verilog_files) [concat \
    [lsort [glob -nocomplain "$vars(flow_home)/designs/src/riscv32i/*.v"]] \
    [list "$vars(flow_home)/platforms/asap7/verilog/fakeram7_256x32.sv"]]
set vars(synth_blackboxes) [list fakeram7_256x32]
set vars(synth_minimum_keep_size) 10000
set vars(sdc_file) "$vars(flow_home)/designs/asap7/riscv32i/constraint.sdc"

set vars(additional_lefs) [list "$vars(flow_home)/platforms/asap7/lef/fakeram7_256x32.lef"]
set vars(additional_libs) [list "$vars(flow_home)/platforms/asap7/lib/NLDM/fakeram7_256x32.lib"]

set vars(die_area)                {0 0 72 72} 
set vars(core_area)               {2 2 70 70}
set vars(place_density_lb_addon)  0.30
set vars(io_constraints)          "$vars(flow_home)/designs/asap7/riscv32i/io.tcl"
set vars(macro_place_halo)        {2 2}
set vars(tns_end_percent)         100
set vars(cts_cluster_size)        10
set vars(cts_cluster_diameter)    50
set vars(cts_buffer_list)         [list "BUFx4_ASAP7_75t_R" "BUFx10_ASAP7_75t_R"]
