global vars

set src_home "$vars(flow_home)/designs/src/cva6"

set vars(design_name) "cva6"
set vars(design_nick) "cva6"
set vars(synth_hdl_frontend) "slang"
set vars(vt_list) [list RVT LVT SLVT]
set vars(verilog_defines) [list -D HPDCACHE_ASSERT_OFF]
set vars(synth_blackboxes) [list fakeram7_64x256 fakeram7_128x64 fakeram7_64x28 fakeram7_64x25]

set vars(verilog_files) [concat \
    [lsort [glob -nocomplain "$src_home/common/local/util/*.sv"]] \
    [list \
        "$src_home/core/include/config_pkg.sv" \
        "$src_home/core/include/cv32a65x_config_pkg.sv" \
        "$src_home/core/include/riscv_pkg.sv" \
        "$src_home/core/include/ariane_pkg.sv" \
        "$src_home/core/include/build_config_pkg.sv" \
        "$src_home/core/include/std_cache_pkg.sv" \
        "$src_home/core/include/wt_cache_pkg.sv"] \
    [lsort [glob -nocomplain "$src_home/vendor/pulp-platform/common_cells/src/*.sv"]] \
    [list "$src_home/core/cvfpu/src/fpnew_pkg.sv"] \
    [lsort [glob -nocomplain "$src_home/vendor/pulp-platform/axi/src/*.sv"]] \
    [list \
        "$src_home/core/cvfpu/src/fpnew_cast_multi.sv" \
        "$src_home/core/cvfpu/src/fpnew_classifier.sv" \
        "$src_home/core/cvfpu/src/fpnew_divsqrt_multi.sv" \
        "$src_home/core/cvfpu/src/fpnew_fma.sv" \
        "$src_home/core/cvfpu/src/fpnew_fma_multi.sv" \
        "$src_home/core/cvfpu/src/fpnew_noncomp.sv" \
        "$src_home/core/cvfpu/src/fpnew_opgroup_block.sv" \
        "$src_home/core/cvfpu/src/fpnew_opgroup_fmt_slice.sv" \
        "$src_home/core/cvfpu/src/fpnew_opgroup_multifmt_slice.sv" \
        "$src_home/core/cvfpu/src/fpnew_rounding.sv" \
        "$src_home/core/cvfpu/src/fpnew_top.sv"] \
    [lsort [glob -nocomplain "$src_home/core/*.sv"]] \
    [lsort [glob -nocomplain "$src_home/core/pmp/src/*.sv"]] \
    [list \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_pkg.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_amo.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_cmo.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_core_arbiter.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_ctrl.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_ctrl_pe.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_flush.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_memctrl.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_miss_handler.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_mshr.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_rtab.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_uncached.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_victim_plru.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_victim_random.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_victim_sel.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hpdcache_wbuf.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hwpf_stride/hwpf_stride_pkg.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hwpf_stride/hwpf_stride.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hwpf_stride/hwpf_stride_arb.sv" \
        "$src_home/core/cache_subsystem/hpdcache/rtl/src/hwpf_stride/hwpf_stride_wrapper.sv"] \
    [lsort [glob -nocomplain "$src_home/core/cache_subsystem/*.sv"]] \
    [lsort [glob -nocomplain "$src_home/core/cache_subsystem/hpdcache/rtl/src/common/*.sv"]] \
    [lsort [glob -nocomplain "$src_home/core/cache_subsystem/hpdcache/rtl/src/common/macros/blackbox/*.sv"]] \
    [lsort [glob -nocomplain "$src_home/core/cache_subsystem/hpdcache/rtl/src/utils/*.sv"]] \
    [lsort [glob -nocomplain "$src_home/core/cva6_mmu/*.sv"]] \
    [list \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/defs_div_sqrt_mvp.sv" \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/control_mvp.sv" \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/div_sqrt_top_mvp.sv" \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/iteration_div_sqrt_mvp.sv" \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/norm_div_sqrt_mvp.sv" \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/nrbd_nrsc_mvp.sv" \
        "$src_home/core/cvfpu/src/fpu_div_sqrt_mvp/hdl/preprocess_mvp.sv" \
        "$src_home/core/cvxif_example/include/cvxif_instr_pkg.sv"] \
    [lsort [glob -nocomplain "$src_home/core/frontend/*.sv"]] \
    [list \
        "$src_home/vendor/pulp-platform/tech_cells_generic/src/rtl/tc_sram.sv" \
        "$vars(flow_home)/platforms/asap7/verilog/fakeram7_64x256.sv" \
        "$vars(flow_home)/platforms/asap7/verilog/fakeram7_128x64.sv" \
        "$vars(flow_home)/platforms/asap7/verilog/fakeram7_64x28.sv" \
        "$vars(flow_home)/platforms/asap7/verilog/fakeram7_64x25.sv"]]
set vars(verilog_include_dirs) [list \
    "$src_home/core/include" \
    "$src_home/core/cvfpu/src/common_cells/include" \
    "$src_home/core/cache_subsystem/hpdcache/rtl/include"]
set vars(additional_lefs) [list \
    "$vars(flow_home)/platforms/asap7/lef/fakeram7_64x256.lef" \
    "$vars(flow_home)/platforms/asap7/lef/fakeram7_128x64.lef" \
    "$vars(flow_home)/platforms/asap7/lef/fakeram7_64x28.lef" \
    "$vars(flow_home)/platforms/asap7/lef/fakeram7_64x25.lef"]
set vars(additional_libs) [list \
    "$vars(flow_home)/platforms/asap7/lib/NLDM/fakeram7_64x256.lib" \
    "$vars(flow_home)/platforms/asap7/lib/NLDM/fakeram7_128x64.lib" \
    "$vars(flow_home)/platforms/asap7/lib/NLDM/fakeram7_64x28.lib" \
    "$vars(flow_home)/platforms/asap7/lib/NLDM/fakeram7_64x25.lib"]
set vars(sdc_file) "$vars(flow_home)/designs/asap7/cva6/constraint.sdc"

set vars(core_utilization)      70
set vars(core_margin)           2
set vars(macro_place_halo)      {3 3}
set vars(place_density)         0.73
set vars(routing_layer_adjustment) 0.20
set vars(skip_last_gasp)        1
set vars(synth_minimum_keep_size) 40000
set vars(cts_lib_name)          "asap7sc7p5t_INVBUF_SLVT_FF_nldm_211120"
