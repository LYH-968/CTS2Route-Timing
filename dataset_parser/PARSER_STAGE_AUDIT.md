# Parser 三阶段逐字段审计报告

对象：`dataset_parser/ml_vector_parser.py`（把原始结果文件解析成向量的解析器）
阶段划分：**place**（3_place 检查点）/ **cts**（4_cts 检查点）/ **route**（6_final 检查点）
审计目标：(1) 每个字段从哪个文件解析；(2) 该文件在该阶段是否可得（无数据泄露）；(3) 解析是否正确。

---

## 0. 结论摘要

| 类别 | 结论 |
| --- | --- |
| 几何类字段（DEF/LEF 来源） | 与 ODB 真值**逐 dbu 完全一致**，无解析错误 |
| 跨阶段泄露 | 发现 **2 处**（route 的 power 报告被拷进 place/cts；place 缺报告时会回落读 cts 报告），已修复 |
| 阶段语义不一致 | 发现 **2 处**（`edge_resistance` 空值写成 0；RUDY/`congestion_est` 在 route 换用布线 bbox），已修复 |
| 解析错误 | 发现 **2 处**（marker 报告 `congestion.rpt`/`drc.rpt` 格式解析完全错误；段解析越界守卫差一位），已修复 |
| 精度损失 | 发现 **1 处**（`aspect_ratio` 被 `int(round())` 抹平），已修复 |
| 兜底常量 | 发现 **1 处**（DEF 缺 `UNITS` 时硬编码 dbu=1000，nangate45 实为 2000），已修复 |
| 语义差异（非错误，已文档化） | `<inst>.leakage_power` 在 route 用实测值、place/cts 用 liberty 常数 |

---

## 1. 每个阶段能拿到哪些文件（输入边界）

解析器读的文件只有三类：**本阶段检查点产生的 DEF/Verilog/SPEF**、**本阶段报告**、**阶段无关的平台文件**。

| 输入 | place | cts | route | 说明 |
| --- | --- | --- | --- | --- |
| `3_place.def` / `.v` / `.odb` | ✅ | ❌ | ❌ | 缺失时由 `write_def.tcl` 从 `3_place.odb` 导出到 `--cache-root` |
| `4_cts.def` / `.v` / `.odb` | ❌ | ✅ | ❌ | 同上，源 `4_cts.odb` |
| `6_final.def` / `.v` / `.odb` | ❌ | ❌ | ✅ | DEF 缺失时回落 `5_route.odb` |
| `6_final.spef` | ❌ | ❌ | ✅ | 全阶段唯一 R/C 真值来源 |
| `<stage>_grt_path_report.json/.rpt` | `place_…` | `grt_path_report.…` | `path_report.…` | 本阶段寄生模型下的时序路径 |
| `<stage>_net_timing.csv` / `_net_cap.csv` / `_pin_timing.csv` / `_design_info.json` | ✅ | ✅ | ✅ | 全部带阶段前缀，互不串用 |
| `instance_power.rpt` / `power.rpt` | ❌ | ❌ | ✅ | 由 route 阶段 `final_report_tcl`（6_final.odb + SPEF）产生 |
| `drc.rpt`（`check_drc`） | ❌ | ❌ | ✅ | route 独有 |
| `congestion.rpt`（GRT marker） | ❌ | ❌ | ✅ | route 独有 |
| `ir_drop_VDD/VSS.csv` / `net_power.csv` | ❌ | ❌ | ✅ | route 独有 |
| 平台 `*.lef` / liberty / `setRC.tcl` | ✅ | ✅ | ✅ | 阶段无关 |

> `*_endpoint_json/`、`*_endpoints.txt` 是报告生成器写出但**解析器从不读取**的产物（仅作人工排查用）。

---

## 2. 逐字段审计表

判定列的含义：**阶段内可得** = 该字段的数据源是本阶段文件；**解析正确** = 与独立重算/ODB 真值一致。

### 2.1 `design_info.json`

| 字段 | 来源文件 | 阶段内可得 | 解析正确 |
| --- | --- | --- | --- |
| `stage` / `stage_label` / `parasitic_source` / `clock_mode` / `routing_information_used` / `source_checkpoint` | `<stage>_design_info.json`（生成器按阶段显式写入） | ✅ | ✅ |
| `design` / `platform` / `variant` / `liberty_file` | 命令行 + liberty 路径 | ✅ | ✅ |
| `dbu` | 本阶段 DEF `UNITS DISTANCE MICRONS` | ✅ | ✅（修复：缺 `UNITS` 时改用平台值而非硬编码 1000） |
| `diearea` / `die_area_um2` / `core_area_um2` | 本阶段 DEF `DIEAREA` / `<stage>_design_info.csv` | ✅ | ✅ 与 ODB 逐 dbu 一致 |
| `instance_count` / `net_count` / `port_count` | 本阶段 DEF | ✅ | ✅ |
| `utilization` / `total_inst_area_um2` | 本阶段 DEF（实例面积 / die 面积） | ✅ | ✅ |
| `clock_periods_s` / `wns_max` / `wns_min` / `tns_max` / `tns_min` / `endpoint_count_*` | 本阶段 combined 时序报告 | ✅ | ✅ |
| `process` / `temperature` / `voltage` | liberty `operating_conditions` | ✅ | ✅ |
| `min/max_routing_layer` / `min_clk_routing_layer` / `routing_layer_adjustment` | 平台 config | ✅ | ✅ |
| `r_est_method` / `wire_cap_source` / `congestion_est_method` / `congestion_method` | 解析器自述的算法说明 | ✅ | ✅ |
| `parsed_stages`（新增） | 本次运行的阶段开关结果 | ✅ | ✅ |

### 2.2 `tech/tech.json`、`tech/cells.json`

| 字段 | 来源 | 阶段内可得 | 解析正确 |
| --- | --- | --- | --- |
| 层数/层宽/pitch/spacing、cell 尺寸与引脚、LEF via | 平台 `*.lef` | ✅（阶段无关） | ✅ |

### 2.3 `instances/instances.json`

| 字段 | 来源 | 阶段内可得 | 解析正确 |
| --- | --- | --- | --- |
| `name` / `cell` / `llx` / `lly` / `orient` / `w` / `h` | 本阶段 DEF `COMPONENTS` + LEF `MACRO SIZE` | ✅ | ✅ 全阶段 0 处不一致（orientation 经 DEF→ODB 枚举映射后逐实例比对） |
| 被过滤的实例 | `TAPCELL*`、`FILLCELL*`、`VDD/VSS` 特殊网络 | — | ✅ 设计如此（route 阶段过滤 9648 个 filler） |

### 2.4 `nets/net_*.json` → `feature`

| 字段 | 来源文件 | place | cts | route | 解析正确 |
| --- | --- | --- | --- | --- | --- |
| `llx/lly/urx/ury/width/height/area/volume` | 本阶段 DEF `NETS` 走线段 bbox；**无走线时用引脚 bbox** | 引脚 bbox | 引脚 bbox | 走线 bbox | ✅（语义差异已文档化，见 §3.6） |
| `wire_len` / `via_num` | 本阶段 DEF ROUTED 段 | 0 | 0 | 8853.5 µm | ✅ 与 ODB 逐 net 完全一致，via 2394/2394 |
| `layer_ratio` | 同上 | 全 0 | 全 0 | ✅ | ✅ |
| `R` / `C` | `6_final.spef` | null | null | ✅ | ✅ route-only，阶段守卫正确 |
| `wire_cap` | 本阶段时序报告的 `Net_wire_capacitance`（place/cts 为 placement 估计，route 为 SPEF） | ✅ | ✅ | ✅ | ✅ |
| `r_est` | `wire_cap × (res/cap)`，比值来自平台 `setRC.tcl` | ✅ | ✅ | ✅ | ✅（单层均匀线模型下与估计值精确相等） |
| `wire_cap_est` | 由 `arcs` 的 liberty 引脚电容反推 | ✅ | ✅ | ✅ | ✅ |
| `drc_num` / `drc_type` | `drc.rpt` | null | null | ✅ | ✅（修复后按 marker 文本格式解析；当前 drc.rpt 为空 = 实测 0） |
| `congestion` | `congestion.rpt`（GRT marker） | null | null | ✅ | ✅ 修复：原实现按「每行前 4 个数字」取 bbox 且未做 µm→dbu 换算 |
| `congestion_est` | RUDY（引脚 bbox） | ✅ | ✅ | ✅ | ✅ 修复：原 route 用走线 bbox，与 place/cts 语义不同 |
| `power` / `power_source` | `net_power.csv` | null | null | ✅ | ✅ route-only |
| `delay` / `delay_min` / `slew` / `slew_min` | 本阶段 net/pin timing | ✅ | ✅ | ✅ | ✅ |
| `use` / `ndr` | 本阶段 DEF | ✅ | ✅ | ✅ | ✅ |
| `aspect_ratio` | 由 bbox 计算 | ✅ | ✅ | ✅ | ✅ 修复：原 `int(round(w/h))` 把 0.41/1.18 都抹成整数 |
| `place_feature` | 引脚坐标（DEF） | ✅ | ✅ | ✅ | ✅ |

### 2.5 `nets/net_*.json` → `wires[]`

| 字段 | 来源 | place | cts | route | 解析正确 |
| --- | --- | --- | --- | --- | --- |
| 段列表（layer/坐标/via） | 本阶段 DEF `ROUTED` | 空 | 空 | ✅ | ✅ 段数 = ODB（段+via）4377/4377，逐 net 0 处不符 |
| `wire_width` | LEF 层宽 | — | — | ✅ | ✅ |
| `wire_len` / `wire_density` | 段长 / patch 面积 | 0 | 0 | ✅ | ✅ |
| `R` / `C` | SPEF ÷ 段数 | null | null | ✅ | ✅ |
| `power` | `net_power.csv` ÷ 段数 | null | null | ✅ | ✅ |
| `delay` / `slew` | 本阶段时序 | ✅ | ✅ | ✅ | ✅ |
| `drc_num` / `drc_type` / `congestion` | marker 报告 | null | null | ✅ | ✅ |

### 2.6 `nets/net_*.json` → `arcs[]`（28 个键）

| 字段组 | 来源 | 阶段内可得 | 解析正确 |
| --- | --- | --- | --- |
| `driver_*` / `sink_*` 的 slew/arrival/required/slack | 本阶段 `<stage>_pin_timing.csv` + `*_grt_path_report.json` | ✅ | ✅ |
| `driver_x_um` / `sink_x_um` 等坐标 | 本阶段 DEF 引脚位置 | ✅ | ✅ |
| `driver_liberty_cap` / `sink_liberty_cap` | liberty 引脚电容 | ✅ | ✅ |
| `driver_cell` / `sink_cell` / `rf` | 本阶段 DEF + 报告 transition | ✅ | ✅ |
| `sink_required_valid` | 该阶段是否真有 required time | ✅ | ✅ 无约束引脚不会被编造 |

### 2.7 `patchs/patch_*.json`

| 字段 | 来源 | place | cts | route | 解析正确 |
| --- | --- | --- | --- | --- | --- |
| `cell_density` / `pin_density` / `net_density` | 本阶段 DEF 实例/引脚/网络 | ✅ | ✅ | ✅ | ✅ |
| `RUDY_congestion` | 引脚坐标 RUDY | ✅ | ✅ | ✅ | ✅ 修复：原 route 用走线 bbox |
| `EGR_congestion` | DEF 走线 ÷ patch 面积 | null | null | ✅ | ✅ 无走线时 null 而非 0 |
| `drc_num` / `congestion` | marker 报告 | null | null | ✅ | ✅ |
| `power` | `instance_power.rpt` 汇总 | null | null | ✅ | ✅ |
| `IR_drop` | `ir_drop_VDD/VSS.csv` | null | null | ✅ | ✅（坐标 µm→dbu 换算正确） |
| `timing` | 本阶段 net timing | ✅ | ✅ | ✅ | ✅ |
| `patch_layer[].feature.wire_len/wire_density/wire_width` | 本阶段 DEF + LEF | 0 | 0 | ✅ | ✅ |
| `patch_layer[].feature.congestion` | marker | null | null | ✅ | ✅ |

### 2.8 `wire_graph/timing_wire_graph.json`

| 字段 | 来源 | place | cts | route | 解析正确 |
| --- | --- | --- | --- | --- | --- |
| `node_coord` | DEF 引脚坐标 ÷ dbu | ✅ | ✅ | ✅ | ✅ |
| `node_slews` / `node_capacitances` / `node_arrive_times` / `node_required_times` / `node_required_valid` | 本阶段 pin timing | ✅ | ✅ | ✅ | ✅ |
| `is_clock_pin` | liberty 引脚 `clock : true` | ✅ | ✅ | ✅ | ✅ |
| `node_internal_power` / `node_net_power` | `instance_power.rpt` | null | null | ✅ | ✅ |
| `edge_delay` | 本阶段 arc delay | ✅ | ✅ | ✅ | ✅ |
| `edge_resistance` | SPEF | **null（修复）** | **null（修复）** | ✅ | ✅ 原来 place/cts 写 0.0，与 `R=null` 语义冲突 |
| `inst_arc_internal_power` | `instance_power.rpt` | null | null | ✅ | ✅ |
| `sizer_cells` / `node_toggle` / `node_sp` | 设计上恒为 null（无对应数据源） | — | — | — | ✅ 显式占位 |

### 2.9 `instance_graph/timing_instance_graph.json`

| 字段 | 来源 | place | cts | route | 解析正确 |
| --- | --- | --- | --- | --- | --- |
| `leakage_power` | route：`instance_power.rpt` 实测；place/cts：liberty `cell_leakage_power` 常数 | ✅ | ✅ | ✅ | ⚠️ 语义随阶段不同，已在 `design_info` 说明（见 §3.7） |
| `edges` | 本阶段 DEF 驱动→负载关系 | ✅ | ✅ | ✅ | ✅ |

### 2.10 `wire_paths/wire_path_*.json`

| 字段 | 来源 | 阶段内可得 | 解析正确 |
| --- | --- | --- | --- |
| `Point` / `Capacitance` / `slew` / `trans_type` | 本阶段 `*_grt_path_report.json` 的 `source_path` | ✅ | ✅ |
| `net_arc.Incr` / `wire_delay` | 本阶段相邻点到达时间差 | ✅ | ✅ |
| `wire_R` / `wire_C` | SPEF（无 SPEF 则 null） | ✅ | ✅ |
| 无时序路径的 endpoint | 写成 `(NO_TIMING_PATH)` + `Capacitance: null` | ✅ | ✅ 不编造数值 |

### 2.11 实例功率与拷贝的原始报告

| 文件 | 来源 | 阶段内可得 | 解析正确 |
| --- | --- | --- | --- |
| `<design>_instance.csv/.json`、`<design>_pwr_graph.json` | `instance_power.rpt` | place/cts 全 null | ✅ 守卫正确 |
| `timing_power_benchmark.json` | 计数 | ✅ | ✅ |
| `<design>.rpt`（拷贝的路径报告） | `place_…` / `grt_path_report` / `path_report` | ✅ 各取各阶段 | ✅ |
| `<design>.pwr`、`<design>_instance.pwr` | `power.rpt` / `instance_power.rpt` | **place/cts 改为空文件（修复）** | ✅ 原来把 route 报告拷进了 place/cts |

---

## 3. 发现的问题与处理

### 3.1 【数据泄露】route 的功率报告被拷进 place/cts 向量目录

`_write_reports()` 对所有阶段无条件拷贝 `power.rpt` 与 `instance_power.rpt`（这两个文件只在 route 阶段由 `6_final.odb + 6_final.spef` 产生）。place/cts 的向量目录里因此出现布线后的实测功率，直接构成跨阶段泄露。
**修复**：只有 `stage == "route"` 才拷贝，其余阶段写空文件。

### 3.2 【数据泄露】place 阶段在缺报告时回落读 cts 报告

`load_ml_reports()` 里 `first_existing("place_grt_path_report.json", "grt_path_report.json")` —— place 报告缺失时会静默改用 CTS 阶段的报告，把 CTS 时钟树的结果喂给 placement 输入。
**修复**：删除回落，只认本阶段报告；缺失时向 stderr 打印 `[leak-guard]` 警告并正常产出空时序数据。

### 3.3 【解析错误】marker 报告（congestion.rpt / drc.rpt）格式解析错误

原实现按「每行前 4 个数字当 bbox」解析。而真实文件是 `odb::_dbMarker::writeTR` 写的文本：

```
violation type: Horizontal congestion
	srcs: net:req_msg[31] net:_123_
	comment: capacity:12 usage:15 overflow:3
	bbox = (1.5000, 2.0000) - (3.0000, 4.5000) on Layer metal2
```

两个致命问题：(1) `writeTR` 输出的是**微米**（除以了 `dbUnitsPerMicron`），而网络 bbox 全部以 **dbu** 保存，量纲差 1000/2000 倍，marker 永远不可能与网络相交；(2) 网络名里的数字（`req_msg[31]`）和 `comment` 行会被当成坐标，可能凭空造出 marker。GRT 与 DRC（`check_drc` / `detailed_route -output_drc`）用的是同一个 writer。
**修复**：按 `violation type:` / `srcs:` / `comment:` / `bbox = (…) on Layer …` 逐行解析，坐标 ×dbu 换算，并保留 `layer` / `capacity` / `usage` / `overflow`；仅在文件完全没有 `violation type:` 头时才退回旧的数字启发式。
同时确认了「文件存在但无 marker」的语义：GRT 的 `getCongestionGrid()` 只输出 `overflow > 0` 的 gcell 边，因此**没有 marker = 实测 0 拥塞**（报告生成器写的 `#` 占位注释表示同一件事），而**文件整体缺失 = 未测量 → null**。

### 3.4 【空值语义】`edge_resistance` 在 place/cts 写成 0.0

`wire_graph` 的网络边直接用 `net.resistance`，无 SPEF 时为 0.0，而同一数据源的 `net.feature.R` 写的是 `null`。同一份量在两条路径上一个 0 一个 null。
**修复**：改为 `net.resistance if net.has_spef else None`。

### 3.5 【越界守卫】DEF 段解析 `idx + 4 < len(tokens)`

访问 `tokens[idx..idx+3]` 却要求 `idx + 4 < len(tokens)`，多要了一个 token：当某条 `ROUTED` 以裸点组收尾时，最后一个点会被丢弃。
**修复**：改为 `idx + 3 < len(tokens)`。（对现有 24 份 DEF 实测数值无变化，属潜在隐患。）

### 3.6 【阶段不一致】RUDY / `congestion_est` 在 route 换了 bbox 定义

`_compute_patch_rudy()` 与 `_net_json()` 的 `congestion_est` 都取「走线 bbox，没有走线才用引脚 bbox」。于是 place/cts 算的是引脚 bbox 上的 RUDY，route 算的是布线 bbox 上的 RUDY —— 同一个字段在不同阶段是两个不同的量。而 RUDY 本来的定义就是基于引脚包围盒的估计量。
**修复**：新增 `_pin_bbox()`，两处统一用引脚 bbox。修复后 cts 与 route 的 `congestion_est` 数值完全一致（例如同一网络均为 `25650.002384318`），证明定义已对齐。

### 3.7 【语义差异，已文档化】`instance_graph.leakage_power` 双来源

route 用 `instance_power.rpt` 的实测泄漏，place/cts 用 liberty `cell_leakage_power` 常数。两者都只依赖「已知信息」，**不是泄露**，但同名不同义。作为 CTS→route 任务的输入/标签对照是合理的，故保留数值、在代码与 `design_info` 中显式注明。若后续要严格对齐，需在生成器里让 place/cts 也走 liberty 常数并改名区分。

### 3.8 【精度损失】`aspect_ratio` 被取整

`int(round(width / height))` 把 0.41、1.18、0.22 这类比值抹成 0/1/0。**修复**：改为 `round(width / height, 6)`；高度为 0 的退化包围盒仍记 0。

### 3.9 【兜底常量】DEF 缺 `UNITS` 时硬编码 dbu=1000

nangate45 实际是 2000，一旦触发会让所有几何量差 2 倍。OpenROAD 写出的 DEF 都带 `UNITS`，故只是兜底路径。
**修复**：`DefParser` 接收 `default_dbu`；`main()` 依次从 `6_final/5_route/4_cts/3_place.def` 探测真实 dbu（都没有时按平台：asap7=1000，其余=2000），并在触发兜底时打印警告。

---

## 4. 三阶段开关

优先级（从高到低）：**单个 `--place/--no-place` 等 → `--stages` → `--stage` → 配置文件 → 内置默认（place+route）**。

```bash
# 1) 配置文件（推荐，仓库自带 dataset_parser/stage_config.json）
cat dataset_parser/stage_config.json
#   {"stages": {"place": true, "cts": true, "route": true}}

# 2) 命令行临时覆盖
python3 dataset_parser/ml_vector_parser.py ... --stages place,cts      # 只解析 place+cts
python3 dataset_parser/ml_vector_parser.py ... --no-route --no-place   # 只解析 cts
python3 dataset_parser/ml_vector_parser.py ... --cts                   # 在上面基础上再打开 cts

# 3) 旧用法完全兼容（sweep 脚本与 Makefile 无需改动）
python3 dataset_parser/ml_vector_parser.py ... --stage all     # place+cts+route
python3 dataset_parser/ml_vector_parser.py ... --stage both    # place+route
python3 dataset_parser/ml_vector_parser.py ... --stage route   # 仅 route
```

- 被关闭的阶段**既不读取文件也不产出向量目录**，因此不会出现「目录在但内容是空壳」。
- 解析器启动时打印 `[stages] parsing place, cts, route (dbu=2000)`。
- 生效结果写入每份 `design_info.json` 的 `parsed_stages`，向量目录自带开关记录。
- 全部关闭会直接报错退出：`no stage enabled: turn on at least one of place/cts/route`。
- `sweep_nangate45.py` 同样接入：`--stages place,cts`，并据此决定 parse / check / verify 跑哪些阶段（route 关闭时跳过 route 向量校验）。

---

## 5. 复核证据（修复后重跑）

| 复核项 | 结果 |
| --- | --- |
| 走线几何 vs ODB `dbWireDecoder` 真值 | 总长 8853.5 µm / 8853.5 µm，via 2394 / 2394，**0 个网络长度或 via 数不符** |
| `wires[]` 条数 vs ODB（段+via） | 4377 / 4377，410 个网络**全部相等** |
| 实例坐标/朝向 vs ODB（三阶段） | 0 处不符（334 / 341 / 341 个实例） |
| 网络引脚集合 vs ODB（三阶段） | 0 处不符（422 / 427 / 427 个网络） |
| die/core 面积 vs ODB（三阶段） | 逐 dbu 完全一致 |
| `check_consistency.py`（三阶段 × 3 个 variant） | 全部 PASS |
| 端到端重跑 `sweep_nangate45.py --designs gcd --phases parse,check,verify` | `fails=0/3` |
| 修复前后逐字段 diff（gcd/base 三阶段，3.3 万个文件） | 仅 5 类字段变化，**无文件增删**：<br>· place/cts `.edges`（803/810 条网络边 → `edge_resistance` null）<br>· place/cts `aspect_ratio`（257/263 个网络）<br>· place/cts `gcd.pwr`、`gcd_instance.pwr`（840/679071 B → 0 B）<br>· route `RUDY_congestion`（3579 个 patch）、`congestion_est`（427 个网络）<br>· 三阶段 `parsed_stages`（新增键）<br>几何、时序、功耗、R/C 字段**无任何变化** |
