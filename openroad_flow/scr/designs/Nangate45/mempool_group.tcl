global vars

source [file join [file dirname [info script]] "common.tcl"]

set vars(design_name) "mempool_group"
set vars(design_nick) "mempool_group"

set vars(verilog_files) [list \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi/src/axi_pkg.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/cf_math_pkg.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/riscv_instr.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_pkg.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/mempool_pkg.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/cluster_interconnect/rtl/tcdm_interconnect/tcdm_interconnect_pkg.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/cluster_interconnect/rtl/variable_latency_interconnect/variable_latency_interconnect.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi_hier_interco.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/mempool_tile.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi/src/axi_mux.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi/src/axi_id_remap.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/mempool_cc.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache_pkg.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/tcdm_adapter.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/fakeram45_256x32.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/fakeram45_64x64.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/tech_cells_generic/src/rtl/tc_sram.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/spill_register.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/fall_through_register.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/stream_xbar.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/address_scrambler.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/tcdm_shim.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_demux.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_axi_adapter.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi/src/axi_cut.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi/src/axi_id_prepend.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/rr_arb_tree.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/fifo_v3.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/lzc.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_ipu.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/isochronous_spill_register.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache_lookup.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache_l0.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/stream_arbiter.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache_refill.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/onehot_to_bin.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/stream_demux.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/deprecated/fifo_v2.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/spill_register_flushable.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/stream_arbiter_flushable.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/latch_scm.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_lsu.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/tech_cells_generic/src/rtl/tc_clk.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache_handler.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch_addr_demux.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_regfile_ff.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_shared_muldiv.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/common_cells/src/deprecated/find_first_one.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_onehot.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/snitch/src/snitch_icache/snitch_icache_lfsr.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/axi/src/axi_intf.sv" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/mempool_group.sv"]
set vars(verilog_include_dirs) [list \
    "$vars(flow_home)/designs/src/mempool_group/rtl" \
    "$vars(flow_home)/designs/src/mempool_group/rtl/register_interface/include"]
set vars(sdc_file) "$vars(flow_home)/designs/nangate45/mempool_group/mempool_group.sdc"
set vars(synth_hdl_frontend) "slang"

nangate45_add_fakerams {256x32 64x64}

set vars(synth_hierarchical) 1
set vars(die_area) {0 0 1100 1100}
set vars(core_area) {10 12 1090 1090}
set vars(macro_place_halo) {10 10}
