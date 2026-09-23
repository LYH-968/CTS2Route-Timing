source [file join [file dirname [info script]] "riscv32i.tcl"]

set vars(design_nick)    "riscv32i-mock-sram"
set vars(io_constraints) "$vars(flow_home)/designs/asap7/riscv32i-mock-sram/io.tcl"
set vars(additional_lefs) [list]
set vars(additional_libs) [list]

set vars(blocks) [list fakeram7_256x32]
set vars(block_config_map) [dict create \
    fakeram7_256x32 [file join [file dirname [info script]] "riscv32i-mock-sram_fakeram7_256x32.tcl"]]
set vars(block_design_nick_map) [dict create fakeram7_256x32 "riscv32i-mock-sram_fakeram7_256x32"]
