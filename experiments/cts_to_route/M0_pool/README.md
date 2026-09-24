# M0: CTS-to-Route Pool GNN

本目录是 M0 方案的独立实验目录。M0 使用 `model_variant=pool`，不启用 `linear_skip`。

## 目录

- `code/`: 本方案运行所需的训练器和公共模型模块
- `configs/`: 训练配置和参数快照
- `logs/`: 训练日志
- `results/`: 评估汇总和结果文件
- `weights/`: `best_model.pt`、`last_model.pt` 等 checkpoint

## 当前状态

- 当前已保存到 epoch 44
- 原始运行目录：`E:/CTS/outputs/cts_route_m0_pool_20260922`
- 输入图数据保持在：`E:/CTS/dataset/asap7/graphs_cts_v3`
- 训练入口：`code/train_cts_route_v2.py`
- 公共模块：`code/cts_route_common.py`

## 说明

`cts_route_common.py` 是从旧的 `unified_29_7_common.py` 重构而来，只保留当前 CTS-to-Route 训练所需的 schema、DGL 图处理、normalizer、GNN encoder、路径几何、split 和评估工具；旧的 Placement-to-Route、Power Prediction、semantic feature 和 unified legacy schema 代码已从 M0 副本移除。

当前目录中的文件是从原运行目录复制的快照。后续 M0 的代码、配置、日志、结果和权重应优先放在本目录对应子目录中，避免与 M1 或历史实验混用。
