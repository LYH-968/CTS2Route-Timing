# gcd-style ML Dataset Parser

本 parser 用于把 OpenROAD-flow-scripts 已完成的全流程设计结果整理成与 `/home/lyh/OpenROAD-flow-scripts/iDATA/gcd` 一致的机器学习数据集结构。重点输出为 `place/vectors`、`cts/vectors` 和 `route/vectors` 下的 JSON vectors。

## 代码文件结构

代码位于：

```text
/home/lyh/OpenROAD-flow-scripts/dataset_parser/
├── ml_dataset_parser.py       # 推荐入口：批量生成 ml_reports 并调用 vectors parser
├── generate_ml_reports.py     # 调用 OpenROAD/OpenSTA 生成 timing、power、DRC、IR-drop、congestion reports
├── ml_vector_parser.py        # 解析 DEF/LEF/Liberty/SPEF/report，写出 gcd-style vectors
├── run_asap7_dataset_sweep.py # ASAP7 多 design、多策略顺序跑 flow 并解析数据集
├── parse_openroad_flow_asap7_runs.py  # 解析已有 openroad_flow/runs/asap7 目录
├── check_consistency.py       # 跨文件命名/id 一致性检查器
├── verify_features.py         # 从源文件独立重算关键特征并与输出对照
└── README.md
```

`flow/util/` 下保留了同名兼容入口，旧命令仍可运行，但后续维护应修改 `dataset_parser/` 中的文件。

推荐使用 `ml_dataset_parser.py`，它会自动串联：

1. `generate_ml_reports.py`
2. `ml_vector_parser.py`

如果已经生成过 `ml_reports`，可以使用 `--skip-reports` 只重新解析 vectors。

## ASAP7 数据集扩增

`run_asap7_dataset_sweep.py` 默认顺序运行 10 个 ASAP7 design、20 组策略参数。每组策略会先执行 `make finish`，再生成 `ml_reports`，最后解析 `place`、`cts`、`route` 三个 stage 的 vectors。策略名会同时作为 `FLOW_VARIANT` 和输出 scenario 名，便于把不同参数结果隔离存放。

先预览前两组命令：

```bash
python3 dataset_parser/run_asap7_dataset_sweep.py --dry-run --limit 2
```

后台启动完整 sweep，并自动跳过已完成的 flow + parsed route graph：

```bash
mkdir -p dataset_parser/logs/asap7_sweep
nohup python3 dataset_parser/run_asap7_dataset_sweep.py --resume \
  > dataset_parser/logs/asap7_sweep/nohup.log 2>&1 &
```

只跑单个 design：

```bash
python3 dataset_parser/run_asap7_dataset_sweep.py --design aes --resume
```

只跑指定策略：

```bash
python3 dataset_parser/run_asap7_dataset_sweep.py \
  --design aes \
  --strategy util70_den70 \
  --strategy cts_small_cluster \
  --resume
```

每个 job 的日志与状态表位于：

```text
/home/lyh/OpenROAD-flow-scripts/dataset_parser/logs/asap7_sweep/
```

## design 需要提供的源文件

每个 design 默认需要位于：

```text
/home/lyh/OpenROAD-flow-scripts/flow/results/<platform>/<design>/<variant>/
```

当前数据使用：

```text
/home/lyh/OpenROAD-flow-scripts/flow/results/nangate45/<design>/base/
```

必需文件：

```text
3_place.odb      # placement 阶段数据库，用于导出 3_place.def 和 3_place.v
6_final.def      # route/final 阶段 DEF，优先使用
6_final.v        # route/final 阶段网表，解析顶层 port
6_final.spef     # route/final 阶段寄生参数，解析 net R/C
6_final.odb      # 生成 final timing/power report
6_final.sdc      # final 时序约束
4_cts.odb        # 生成 global-route congestion report
4_cts.sdc        # CTS/global-route 时序约束
```

可选但常见文件：

```text
5_route.odb      # 当 6_final.def 或 6_final.v 缺失时，用于回退导出 DEF/Verilog
5_route.sdc
route.guide
6_final.sdf
clock_period.txt
updated_clks.sdc
```

platform 技术文件默认来自：

```text
/home/lyh/OpenROAD-flow-scripts/flow/platforms/<platform>/
├── lef/*.lef
├── lib/*.lib
├── setRC.tcl
└── fastroute.tcl
```

当前 Nangate45 主要使用：

```text
/home/lyh/OpenROAD-flow-scripts/flow/platforms/nangate45/lef/
/home/lyh/OpenROAD-flow-scripts/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib
```

## 源文件目录结构

一个 design 的典型输入目录如下：

```text
/home/lyh/OpenROAD-flow-scripts/flow/results/nangate45/bench_gcd/base/
├── 1_synth.v
├── 1_synth.sdc
├── 2_floorplan.odb
├── 3_place.odb
├── 3_place.sdc
├── 4_cts.odb
├── 4_cts.sdc
├── 5_route.odb
├── 5_route.sdc
├── 6_final.def
├── 6_final.v
├── 6_final.sdc
├── 6_final.sdf
├── 6_final.spef
├── 6_final.odb
└── route.guide
```

## 生成 report 的存放地址

`generate_ml_reports.py` 生成的中间 report 位于：

```text
/home/lyh/OpenROAD-flow-scripts/flow/reports/<platform>/<design>/<variant>/ml_reports/
```

当前数据对应：

```text
/home/lyh/OpenROAD-flow-scripts/flow/reports/nangate45/<design>/base/ml_reports/
```

目录内容：

```text
ml_reports/
├── final.openroad.log          # 6_final.odb 上的最终时序/功耗报告
├── congestion.openroad.log     # 4_cts.odb 上的 grt 时序/拥塞报告
├── place.openroad.log          # 3_place.odb 上的 place 阶段时序/拥塞报告
├── path_report.rpt / .json     # route 阶段 endpoint 时序（6_final.odb + SPEF）
├── path_report_unconstrained.rpt
├── grt_path_report.rpt / .json # cts 阶段 endpoint 时序（4_cts.odb）
├── place_grt_path_report.rpt / .json  # place 阶段 endpoint 时序（3_place.odb）
├── congestion.rpt              # cts 阶段 global-route 拥塞（4_cts.odb）
├── place_congestion.rpt        # place 阶段 global-route 拥塞（3_place.odb）
├── power.rpt
├── instance_power.rpt
├── wns.rpt
├── tns.rpt
├── design_area.rpt
├── drc.rpt
├── ir_drop_VDD.csv
├── ir_drop_VSS.csv
├── net_power.csv
├── route_net_timing.csv / route_net_cap.csv  # route 阶段全量 net 时序
├── cts_net_timing.csv / cts_net_cap.csv      # cts 阶段全量 net 时序
├── place_net_timing.csv / place_net_cap.csv  # place 阶段全量 net 时序
├── route_pin_timing.csv / cts_pin_timing.csv / place_pin_timing.csv  # 全量 pin 级特征
└── route_design_info.json / cts_design_info.json / place_design_info.json
```

这些 report 会被 `ml_vector_parser.py` 回填到 `wire_paths`、`wire_graph`、`patchs`、`*.pwr`、`*_instance.*` 等文件中。

## 三阶段数据来源（各阶段只从自身阶段结果提取，pre-route 阶段无 Routing 泄漏）

```text
place 阶段: 3_place.def / 3_place.v + place_grt_path_report.json
            + place_net_timing.csv / place_net_cap.csv / place_pin_timing.csv
            （全部由 3_place.odb 生成；寄生 = estimate_parasitics -placement）
cts   阶段: 4_cts.def / 4_cts.v + grt_path_report.json
            + cts_net_timing.csv / cts_net_cap.csv / cts_pin_timing.csv
            （全部由 4_cts.odb 生成；寄生 = estimate_parasitics -placement，传播时钟）
route 阶段: 6_final.def / 6_final.v / 6_final.spef + path_report.json + congestion.rpt
            + power/drc/ir_drop/net_power + route_net_timing.csv / route_net_cap.csv / route_pin_timing.csv
```

**严格阶段边界（CTS → Routing 时序预测专用）**：place/cts 阶段的时序、slew、电容
全部基于 `estimate_parasitics -placement`（Steiner 线长估计寄生，即 ORFS cts.tcl
自身使用的 CTS 阶段寄生源）。**禁止** global_route / estimate_parasitics -global_routing /
SPEF / GRT congestion 进入 pre-route 特征——这些是 future information。因此：
- place/cts 的 patch/net 的 `congestion` 字段一律为 null（GRT 拥塞属路由期信息）；
- 4_cts.odb 是 CTS 完成（含 CTS + legalization + repair_timing）后、GRT 前的 checkpoint
  （ORFS cts.tcl 在 write_db 4_1_cts.odb 前 source POST_CTS_TCL，之后 5_1_grt 才跑 GRT）。

## 阶段元数据（design_info.json）

```json
{
  "stage": "cts",
  "stage_label": "post_cts_pre_grt",
  "parasitic_source": "placement_estimation",
  "clock_mode": "propagated",
  "routing_information_used": false,
  "source_checkpoint": "4_cts.odb"
}
```

place 为 `post_place_pre_cts`，route 为 `post_route`（`parasitic_source=extracted_spef`，
`routing_information_used=true`——route 是标签侧）。

## 全量 net 时序（`*_net_timing.csv`）

旧的 net 时序只覆盖 endpoint 关键路径上的 net（实测 gcd 覆盖率仅约 27%）。现在每个阶段报告生成时，通过 OpenSTA `sta::` SWIG Tcl API 遍历**全部信号 net** 的 wire edge（driver→sink 弧），逐弧 dump 4 个转换角（rise/fall × min/max）的 delay 与 slew，以及每 net 电容：

```text
<stage>_net_timing.csv: driver_pin,sink_pin,net,rf,delay_min_s,delay_max_s,
                        driver_slew_min_s,driver_slew_max_s,sink_slew_min_s,sink_slew_max_s
<stage>_net_cap.csv:    net,cap_f
```

覆盖范围：所有有驱动的信号 net（含顶层 input port 驱动的 net 与时钟树 net），实测 gcd 100% net 有弧级时序。没有时序数据的 net（无驱动/常数）在 vectors 中写 `null`，不再写 0。

### 全量 pin 级特征（`*_pin_timing.csv`）

```text
<stage>_pin_timing.csv: pin,net,is_driver,x_um,y_um,arrival_min_s,arrival_max_s,slack_min_s,slack_max_s,
                        slew_rise_min_s,slew_rise_max_s,slew_fall_min_s,slew_fall_max_s,
                        req_min_s,req_max_s,req_valid
```

- pin 坐标：`sta::pin_location`（真实 pin 位置，微米；不再是 instance 中心）
- arrival：数据路径 arrival（无数据路径的 pin 写 null）
- slack：仅 endpoint 有值，非 endpoint 写 null
- slew：4 角（rise/fall × min/max），基于该阶段寄生估计
- **required**：`Vertex_requireds_clk` 逐 pin 计算（遍历 clocks × clk_rf 取最紧值；
  rise/fall 维度折叠，req_valid=1 表示存在有效 required，否则写 null 不填 0）
- pin 电容：per-pin liberty 电容由 Python 从 .lib 的 `capacitance` 解析（`sta::LibertyPort_capacitance` 在本 OpenROAD 版本对 macro/port pin 会 segfault，故弃用）
- wire_graph 节点新增 `is_clock_pin`（Liberty `clock : true` 属性）与 `node_required_valid`

### 电容语义（net feature）

- `C`：net 总负载电容（pin 电容 + 该阶段寄生估计的线电容）——即 driver 看到的 load_cap
- `wire_cap_est`：估计线电容 = C − Σ 各 sink 的 intrinsic liberty pin 电容
- arcs 的 `sink_liberty_cap`：sink pin 的 Liberty intrinsic pin 电容

### 设计级元数据（`<stage>_design_info.json`）

包含：design/platform/variant/stage、liberty 文件名与 PVT（process/temperature/voltage，来自 .lib operating_conditions）、routing 约束（min/max routing layer、ROUTING_LAYER_ADJUSTMENT）、die/core 面积、时钟周期、WNS/TNS（max/min，由全 endpoint 路径报告计算）、实例/网表统计、实际利用率（实例面积/核心面积）。sweep 策略参数（如 CORE_UTILIZATION/PLACE_DENSITY）通过 `--scenario-params` 透传，作为 `scenario_params` 字段写入。vectors 输出目录中对应文件为 `design_info.json`。

vectors 中的回填方式：

```text
net feature:  delay/delay_min/slew/slew_min = 各弧聚合（max），C = net_cap
net arcs:     逐弧 {driver, sink, rf, delay_min, delay_max, driver_slew_*, sink_slew_*}
wire feature: delay = net delay 按 segment 数均分（近似），slew = net slew
wire_graph:   net edge 的 edge_delay = 按 sink 匹配的 [rise_max, fall_max, rise_min, fall_min]
              node_slews = 逐 pin 的 4 角 slew（来自 dump，缺失时回退路径报告值）
```

## 结果文件存放地址

默认输出目录：

```text
/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_nangate45/
```

可通过 `--out` 修改。

运行示例：

```bash
python3 /home/lyh/OpenROAD-flow-scripts/dataset_parser/ml_dataset_parser.py \
  --flow-root /home/lyh/OpenROAD-flow-scripts \
  --platform nangate45 \
  --variant base \
  --out /home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_nangate45
```

默认会以严格 endpoint 覆盖为目标生成 OpenSTA timing report。实现方式不是只提高 top path 数量，而是显式枚举：

```text
all_registers -data_pins
all_outputs
```

然后对每个 endpoint 分别调用：

```text
report_checks -path_delay max -to <endpoint> -endpoint_path_count 1
report_checks -path_delay min -to <endpoint> -endpoint_path_count 1
```

每个 endpoint 的 JSON 会先写到：

```text
ml_reports/path_report_endpoint_json/
ml_reports/grt_path_report_endpoint_json/
```

随后由 `generate_ml_reports.py` 合并成现有文件名：

```text
ml_reports/path_report.json
ml_reports/grt_path_report.json
```

因此 `path_report.json`、`grt_path_report.json` 和下游 `wire_paths`（`chunk_*.jsonl.zst`）不再只覆盖 top 100 timing paths，而是覆盖所有枚举到的 endpoints。若 OpenSTA 对某个 endpoint 没有 constrained timing path，合并结果中会加入 `endpoint_has_timing_path: false` 的 no-path 占位记录，`wire_paths` 中对应写成单节点占位。

可通过下面参数调整覆盖范围：

```bash
--group-path-count 1000000
--endpoint-path-count 1
```

只处理单个 design：

```bash
python3 /home/lyh/OpenROAD-flow-scripts/dataset_parser/ml_dataset_parser.py \
  --design bench_gcd
```

已存在 `ml_reports` 时只重新生成 vectors：

```bash
python3 /home/lyh/OpenROAD-flow-scripts/dataset_parser/ml_dataset_parser.py \
  --skip-reports
```

只生成 reports，不写 vectors：

```bash
python3 /home/lyh/OpenROAD-flow-scripts/dataset_parser/ml_dataset_parser.py \
  --reports-only
```

## 结果文件结构目录

每个 design 输出如下。`place`、`cts`、`route` 三个阶段的内部结构一致：

```text
/home/lyh/OpenROAD-flow-scripts/parsed_ml_dataset_nangate45/<design>/
├── place/vectors/<design>_place_vectors/vectors/<strategy>/
├── cts/vectors/<design>_cts_vectors/vectors/<strategy>/
└── route/vectors/<design>_route_vectors/vectors/<strategy>/

每个 <strategy>/ 下包含：
├── <top>.rpt                    # 未压缩（标签源，需可读）
├── <top>.pwr
├── <top>_instance.csv
├── <top>_instance.json.zst
├── <top>_instance.pwr
├── <top>_pwr_graph.json.zst
├── design_info.json.zst
├── instances/instances.json.zst
├── nets/index.json.zst
├── nets/chunk_*.jsonl.zst
├── patchs/index.json.zst
├── patchs/chunk_*.jsonl.zst
├── wire_paths/index.json.zst
├── wire_paths/chunk_*.jsonl.zst
├── wire_graph/timing_wire_graph.json.zst
├── instance_graph/timing_instance_graph.json.zst
└── tech/*.json.zst
```

其中 `<top>` 是 DEF 中的真实设计名，例如 `aes`、`PPU`、`gcd`，不一定等于目录名 `bench_aes`。输出目录中的 `<design>` 会做数据集命名规范化，例如去掉 `bench_` 前缀以及部分策略后缀，使 `bench_gcd/base` 输出为 `gcd/...`，避免多出 `bench` 字段或额外层级。

> **2026-09-23 起：除 `.rpt`/`.pwr`/`.csv` 外全部 zstd 压缩，且写入时即压缩。**
> 61 run × 3 阶段的未压缩输出实测约 **192 GB**（patchs 118 GB / nets 45 GB / 其它 30 GB），
> 本机可用磁盘 34 GB，且逐文件布局的 patch 文件数超 inode 上限，所以压缩不是可选项。
> 压缩由 `zstd.open(..., "wt")` 直接流式写入 `<name>.json.zst`，**不会先落一份未压缩副本**；
> zstd -3 的吞吐（约 245 MB/s）远高于解析产生数据的速度（约 10 MB/s），因此不增加解析耗时。
> 实测压缩比：patchs 28.4×、nets 打包 16.9×（同样数据若保持单文件 `.json.zst` 只有 9.2×，
> 少了跨记录冗余）、整目录混合约 18~34×。
>
> `patchs`（patch id）、`nets`（net id）、`wire_paths`（path id）都按 id 打成
> `chunk_*.jsonl.zst`，每片 10000 条，记录 R 位于 `chunk_{R//10000}.jsonl.zst` 的第
> `R%10000` 行。读取统一走 `ml_vector_parser` 的 `iter_patches` / `load_patch` /
> `iter_nets` / `load_net` / `iter_wire_paths` / `load_wire_path` 与 `read_json`
> （`open_text_read` 对未压缩文件仍然兼容，旧的单文件目录也能读）。

## 命名约定（跨文件一致性）

所有文件中的设计实体命名**统一规范化**，保证同一实体在各文件中同名、同 id，可直接 join：

```text
转义清理: DEF 的 \[ \] 转义统一去掉（ctrl.state.out\[0\]$_DFF_P_ → ctrl.state.out[0]$_DFF_P_）

instance 名: instances.json / instance_graph / wire_graph 节点前缀 / arcs 的 driver|sink 前缀 / wire_paths 的 Point 前缀 —— 完全一致
net 名:     nets 记录（原 net_*.json）的 name / arcs 所属 net —— 一致；patchs 的 sub_nets 与 patch_layer 用 net id 引用（id == net 记录在 chunk 中的行号）
port 名:    wire_graph 的 is_port 节点 / arcs 的 driver|sink / DEF PINS —— 一致

pin 分隔符（两种固定约定，可互相转换）:
  wire_graph 节点:   "inst:pin"（冒号，与 iDATA/gcd 参考格式一致）
  arcs / wire_paths: "inst/pin"（斜杠，OpenROAD 2.0 get_full_name 原生格式）
```

一致性可用 `check_consistency.py` 验证：

```bash
python3 dataset_parser/check_consistency.py \
  parsed_ml_dataset_asap7/gcd/route/vectors/gcd_route_vectors/vectors/base
# 检查: 名字转义残留、instance/net/port 跨文件同名、id 与文件序号一致、
#       pins/arcs/wire_paths 引用的实例存在性、sub_nets id 指向存在的 net
```

## 数值验证（`verify_features.py`）

从源文件独立重算关键特征并与输出对照（instance 坐标/朝向、net 几何、SPEF R、
电容语义、arcs 时序、WNS/TNS/时钟/面积、instance power、LEF cell 尺寸、cell_density）：

```bash
python3 dataset_parser/verify_features.py \
  --results-dir flow/results/asap7/gcd/base \
  --reports-dir flow/reports/asap7/gcd/base/ml_reports \
  --out parsed_ml_dataset_asap7/gcd/route/vectors/gcd_route_vectors/vectors/base \
  --lib flow/objects/asap7/gcd/base/lib/merged.lib \
  --platform-dir flow/platforms/asap7
```

单位约定（全部归一化）：电容一律 **pF**（Liberty `capacitive_load_unit` 按 ff/pf 转换，
SPEF `*C_UNIT` 为 pF）；电阻 **Ω**；泄漏功耗 **W**（`leakage_power_unit` pW/nW 转换）；
时间 **ns**；坐标 **µm**（pin 坐标）与 **dbu**（DEF 几何）。net 电容语义 = 线电容 + 各 sink
liberty pin 电容（SPEF `PIN_CAP NONE` 时头标仅含线电容）。

## 主要 JSON 内容来源

```text
instances/instances.json.zst
  来源：DEF components、LEF cell size/pin、Liberty cell leakage、instance_power.rpt
  含 status/is_fixed/orient（DEF PLACED/FIXED 状态与朝向）

nets/chunk_*.jsonl.zst（原 net_*.json，每 net 一条记录）
  来源：DEF nets/routing、SPEF net R/C、OpenSTA timing report、全量 net 时序 dump（*_net_timing.csv / *_net_cap.csv / *_pin_timing.csv）、DRC/congestion marker report、net_power.csv
  delay/delay_min/slew/slew_min 来自全量 net 时序 dump（覆盖所有有驱动信号 net），缺失时写 null（不再写 0）。
  R 仅在 route 阶段来自 SPEF；C 优先来自 net_cap dump，其次 SPEF，其次 timing report。
  feature.use / feature.ndr 来自 DEF 的 USE 与 NONDEFAULTRULE 声明。
  arcs：每条 driver→sink 弧含 4 角 delay/slew、driver/sink 的 lib cell、真实 pin 坐标（µm）、liberty pin 电容、sink 的 arrival/slack。

tech/cells.json.zst
  来源：LEF MACRO + Liberty pin 方向；含每 cell 的 pin 数量与方向。

tech/tech.json.zst
  来源：platform LEF；layers 含 width/pitch/direction/min_width/spacing；ndr_rules 来自 LEF NONDEFAULTRULE。

wire_paths/chunk_*.jsonl.zst（原 wire_path_*.json，每路径一条记录）
  来源：OpenSTA path_report.json/path_report.rpt，SPEF net R/C

wire_graph/timing_wire_graph.json.zst
  来源：DEF netlist connectivity、Liberty pin direction、OpenSTA pin metrics、instance power、SPEF

instance_graph/timing_instance_graph.json.zst
  来源：DEF instances/nets、Liberty pin direction、instance power

patchs/index.json.zst + patchs/chunk_*.jsonl.zst
  来源：DEF die/core geometry、instance/pin/net/wire 分布、global-route congestion.rpt、drc.rpt、ir_drop_VDD.csv/ir_drop_VSS.csv、timing/power report
  CTS 阶段不使用 route/final 的 DRC、IR-drop、per-net power，相关字段缺少真实来源时写 null。
  打包格式：patch 是数量最大的记录（1000µm die 有 361201 个，全数据集逐文件写约 6000 万个文件，
  远超 inode 上限），因此每 10000 个打成一片 JSONL。patch id P 位于 chunk_{P//10000}.jsonl.zst 的第
  P%10000 行；index.json.zst 记录 count/num_chunks/cols/rows/patch_size_dbu。nets 与 wire_paths
  用同一套打包方式（分别按 net id / path id）。读取统一走 ml_vector_parser.iter_*()（顺序遍历）
  和 load_*()（随机单点）。

tech/*.json.zst
  来源：platform LEF/Liberty

<top>.rpt, <top>.pwr, <top>_instance.csv, <top>_instance.pwr
  来源：power.rpt、instance_power.rpt、path/timing report
  这几个保持未压缩：<top>.rpt 是 route 标签源且需要人可读。
```

## 关键字段真实性

```text
DRC
  生成：generate_ml_reports.py 优先复制 flow/reports/<platform>/<design>/<variant>/5_route_drc.rpt 到 ml_reports/drc.rpt；
       final OpenROAD 会尝试 check_drc -output_file。
  解析：ml_vector_parser.py 将 marker bbox 映射到 net、wire segment、patch。
  语义：报告存在且为空时写 0；报告缺失时写 null。

详细 congestion
  生成：global_route -congestion_report_file 写入 ml_reports/congestion.rpt。
  解析：可报告 congestion marker 映射到 net、wire segment、patch。
  语义：报告存在且无 marker 时写 0；报告缺失时写 null。

IR-drop
  生成：analyze_power_grid 输出 ml_reports/ir_drop_VDD.csv 和 ml_reports/ir_drop_VSS.csv。
  解析：按坐标映射到 patch，VDD drop 使用 abs(nominal_voltage - Voltage)，VSS drop 使用 Voltage。
  语义：patch 内存在采样点时写该 patch 最大 drop；报告缺失时写 null。

per-net power
  生成：net_power.csv 当前由 6_final.spef 的 net capacitance、clock_period.txt、默认 activity 估算。
  解析：映射到 nets 记录的 power，并按 wire segment 均分。
  语义：power_source 会写 estimated_from_spef_activity，表示这是基于寄生电容和 activity 的估算，不是 OpenROAD 原生 per-net power report。

instance power
  生成：route 阶段 report_power -instances。
  语义：place/cts 阶段无 power 报告，`<top>_instance.csv/json/pwr` 与 wire_graph 的 power 字段写 null（不再写 0）。
```
