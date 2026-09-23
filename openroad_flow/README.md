# openroad_flow

一套面向 ASAP7 的 stage-based OpenROAD flow。

这套脚本将 ORFS 的 RTL-to-layout 流程拆成独立阶段，方便按阶段调试、单独重跑和做数据采集。当前重点目标不是 GDS，而是稳定产出用于数据库和模型训练的：

- `SPEF`
- `SDF`

## 1. 目录说明

项目中最核心的目录是：

- `scr/`
  主要 Tcl 和 shell 脚本
- `scr/designs/asap7/`
  各个设计的独立配置文件
- `runs/`
  新版 stage-based flow 的运行目录

当前主要脚本如下：

- `scr/config.tcl`
  共享配置、默认参数、公共 helper
- `scr/read_lef.tcl`
  统一读取 LEF
- `scr/read_lib.tcl`
  统一读取 Liberty
- `scr/run_synthesis.tcl`
  综合阶段
- `scr/run_floorplan.tcl`
  floorplan、macro placement、tapcell、PDN
- `scr/run_placement.tcl`
  placement 和 placement optimization
- `scr/run_cts.tcl`
  CTS 和 CTS 后 timing repair
- `scr/run_routing.tcl`
  global route、detailed route、filler
- `scr/run_finishing.tcl`
  RC extraction、SPEF、SDF、报告
- `scr/run_abstract.tcl`
  block/abstract 辅助流程
- `scr/run_flow.sh`
  主入口脚本

## 2. 设计配置方式

每个设计单独维护一个配置文件，位于：

- `scr/designs/asap7/<design>.tcl`

例如：

- `scr/designs/asap7/gcd.tcl`
- `scr/designs/asap7/ibex.tcl`
- `scr/designs/asap7/jpeg.tcl`
- `scr/designs/asap7/cva6.tcl`

推荐做法是：

- 主流程尽量共用
- 设计差异集中到各自的 `config.tcl` 子配置，也就是 `scr/designs/asap7/*.tcl`

通常放在设计配置里的内容包括：

- `design_name`
- `design_nick`
- `verilog_files`
- `sdc_file`
- `die_area` / `core_area` / `core_utilization`
- `place_density`
- `vt_list`
- `additional_lefs`
- `additional_libs`
- macro 相关参数
- 某些设计特化的 CTS / routing 参数

## 3. 支持的 ASAP7 设计

当前 `scr/designs/asap7/` 下可直接运行的设计有：

```text
aes
aes-block
aes-block_aes_rcon
aes-block_aes_sbox
aes-mbff
aes_lvt
cva6
ethmac
ethmac_lvt
gcd
gcd-ccs
ibex
jpeg
jpeg_lvt
mock-alu
mock-array
mock-array_Element
mock-cpu
riscv32i
riscv32i-mock-sram
riscv32i-mock-sram_fakeram7_256x32
swerv_wrapper
uart
```

## 4. 运行方法

进入项目目录：

```bash
cd /home/lyh/OpenROAD-flow-scripts/openroad_flow
```

### 4.1 跑完整 flow

```bash
bash scr/run_flow.sh --design gcd
```

默认会按顺序执行：

```text
synthesis floorplan placement cts routing finishing
```

### 4.2 只跑部分阶段

```bash
bash scr/run_flow.sh --design ibex synthesis floorplan
bash scr/run_flow.sh --design jpeg routing finishing
bash scr/run_flow.sh --design cva6 placement cts
```

### 4.3 常用附加参数

```bash
--design <设计名>
```

指定设计。

```bash
--platform <平台名>
```

指定平台。当前主要使用 `asap7`。

```bash
--variant <名字>
```

指定 run 变体名，用于区分不同实验目录。

例如：

```bash
bash scr/run_flow.sh --design ibex --variant debug
```

结果会写到：

```text
runs/asap7/ibex/debug/
```

```bash
--run-root <路径>
```

手动指定本次运行目录。

```bash
--config <配置文件路径>
```

手动指定设计配置 Tcl。

例如：

```bash
bash scr/run_flow.sh \
  --design ibex \
  --config /home/lyh/OpenROAD-flow-scripts/openroad_flow/scr/designs/asap7/ibex.tcl
```

## 5. 阶段说明

### synthesis

输入：

- RTL
- SDC
- Liberty

输出：

- `1_synth.v`
- `1_synth.sdc`

### floorplan

主要包含：

- floorplan 初始化
- make tracks
- tie fanout repair
- setup repair
- macro placement
- tapcell
- PDN

输出：

- `2_floorplan.odb`
- `2_floorplan.sdc`
- `2_floorplan.def`
- `2_floorplan.v`

### placement

主要包含：

- global placement
- IO placement
- timing/routability driven placement
- repair_design
- detailed placement

输出：

- `3_place.odb`
- `3_place.sdc`
- `3_place.def`
- `3_place.v`

### cts

主要包含：

- clock tree synthesis
- CTS 后 legalization
- propagated clock
- CTS 后 timing repair

输出：

- `4_cts.odb`
- `4_cts.sdc`
- `4_cts.def`
- `4_cts.v`

### routing

主要包含：

- pin access
- global route
- incremental repair
- detailed route
- antenna repair
- filler placement

输出：

- `5_route.odb`
- `5_route.sdc`
- `5_route.def`
- `5_route.v`

### finishing

主要包含：

- parasitic extraction
- `write_spef`
- `write_sdf`
- 末阶段报告

输出：

- `6_final.odb`
- `6_final.def`
- `6_final.v`
- `6_final.sdc`
- `6_final.spef`
- `6_final.sdf`

## 6. 结果目录结构

默认运行目录格式为：

```text
runs/<platform>/<design>/<variant>/
```

例如：

```text
runs/asap7/gcd/base/
```

其中通常包含：

- `result/`
  中间 checkpoint 和最终结果
- `log/`
  每个 stage 的日志
- `report/`
  报告文件
- `objects/`
  中间辅助对象

## 7. 最关心的最终文件

如果你的目标是时序/RC 数据集，最关键的就是：

- [6_final.spef](/home/lyh/OpenROAD-flow-scripts/openroad_flow/runs/asap7/gcd/base/result/6_final.spef)
- [6_final.sdf](/home/lyh/OpenROAD-flow-scripts/openroad_flow/runs/asap7/gcd/base/result/6_final.sdf)

一般来说，判断一个设计是否“最终跑通”，最直接的方法就是看该设计的 `result/` 下是否同时存在：

- `6_final.spef`
- `6_final.sdf`

## 8. 常见用法

### 从头跑一个设计

```bash
bash scr/run_flow.sh --design jpeg
```

### 只补跑后端阶段

```bash
bash scr/run_flow.sh --design ibex floorplan placement cts routing finishing
```

### 只补跑 routing 和 finishing

```bash
bash scr/run_flow.sh --design jpeg_lvt routing finishing
```

### 用单独 variant 保留实验结果

```bash
bash scr/run_flow.sh --design cva6 --variant rerun1 placement cts routing finishing
```

## 9. 调试建议

- 优先看 `runs/<platform>/<design>/<variant>/log/`
- 阶段失败时，先确认上一阶段输出是否齐全
- 大设计尽量少并发，尤其是 `routing`
- 如果 `5_route.odb` 已经存在但 routing 进程未正常退出，可以单独补跑 `finishing`

## 10. 约束说明

这套 flow 的设计原则是：

- `flow/` 官方 ORFS 目录只读，只用于参考
- 只修改 `openroad_flow/`
- 保留 `read_lef.tcl` / `read_lib.tcl` 的组织方式
- 保留按阶段拆分的 `run_*.tcl`

## 11. 后续维护建议

- 新增设计时，优先新建 `scr/designs/asap7/<design>.tcl`
- 尽量不要复制整套 stage Tcl
- 只有在某个设计确实需要特化时，才在设计配置里加专用参数
- 如果多个设计复用同一类配置，可以像 `jpeg_lvt` 继承 `jpeg.tcl` 一样组织

