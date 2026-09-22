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
├── final.openroad.log
├── congestion.openroad.log
├── path_report.rpt
├── path_report.json
├── path_report_unconstrained.rpt
├── grt_path_report.rpt
├── grt_path_report.json
├── power.rpt
├── instance_power.rpt
├── wns.rpt
├── tns.rpt
├── design_area.rpt
├── drc.rpt
├── congestion.rpt
├── ir_drop_VDD.csv
├── ir_drop_VSS.csv
└── net_power.csv
```

这些 report 会被 `ml_vector_parser.py` 回填到 `wire_paths`、`wire_graph`、`patchs`、`*.pwr`、`*_instance.*` 等文件中。

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

因此 `path_report.json`、`grt_path_report.json` 和下游 `wire_paths/wire_path_*.json` 不再只覆盖 top 100 timing paths，而是覆盖所有枚举到的 endpoints。若 OpenSTA 对某个 endpoint 没有 constrained timing path，合并结果中会加入 `endpoint_has_timing_path: false` 的 no-path 占位记录，`wire_paths` 中对应写成单节点占位。

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
├── <top>.rpt
├── <top>.pwr
├── <top>_instance.csv
├── <top>_instance.json
├── <top>_instance.pwr
├── <top>_pwr_graph.json
├── instances/instances.json
├── nets/net_*.json
├── patchs/patch_*.json
├── wire_paths/wire_path_*.json
├── wire_graph/timing_wire_graph.json
├── instance_graph/timing_instance_graph.json
└── tech/*.json
```

其中 `<top>` 是 DEF 中的真实设计名，例如 `aes`、`PPU`、`gcd`，不一定等于目录名 `bench_aes`。输出目录中的 `<design>` 会做数据集命名规范化，例如去掉 `bench_` 前缀以及部分策略后缀，使 `bench_gcd/base` 输出为 `gcd/...`，避免多出 `bench` 字段或额外层级。

## 主要 JSON 内容来源

```text
instances/instances.json
  来源：DEF components、LEF cell size/pin、Liberty cell leakage、instance_power.rpt

nets/net_*.json
  来源：DEF nets/routing、SPEF net R/C、OpenSTA timing report、DRC/congestion marker report、net_power.csv
  CTS/place 阶段没有 detailed-route SPEF，R 写 null；C 优先来自 grt/OpenSTA timing report，缺失时写 null。

wire_paths/wire_path_*.json
  来源：OpenSTA path_report.json/path_report.rpt，SPEF net R/C

wire_graph/timing_wire_graph.json
  来源：DEF netlist connectivity、Liberty pin direction、OpenSTA pin metrics、instance power、SPEF

instance_graph/timing_instance_graph.json
  来源：DEF instances/nets、Liberty pin direction、instance power

patchs/patch_*.json
  来源：DEF die/core geometry、instance/pin/net/wire 分布、global-route congestion.rpt、drc.rpt、ir_drop_VDD.csv/ir_drop_VSS.csv、timing/power report
  CTS 阶段不使用 route/final 的 DRC、IR-drop、per-net power，相关字段缺少真实来源时写 null。

tech/*.json
  来源：platform LEF/Liberty

<top>.rpt, <top>.pwr, <top>_instance.*
  来源：power.rpt、instance_power.rpt、path/timing report
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
  解析：映射到 nets/net_*.json 的 power，并按 wire segment 均分。
  语义：power_source 会写 estimated_from_spef_activity，表示这是基于寄生电容和 activity 的估算，不是 OpenROAD 原生 per-net power report。
```
