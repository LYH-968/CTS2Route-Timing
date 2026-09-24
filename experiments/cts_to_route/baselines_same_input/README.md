# Same-Input Baselines

This experiment compares non-GNN models with M0/M1 while keeping the dataset
records, physical train/validation/test split, route-delay labels, label mask,
and CTS-visible feature sources fixed. The current physical graph directories
contain the same 50 train, 10 validation, and 40 test records recorded by M0.
The runner also reproduces M0's seeded 20% within-record validation split over
the train records, then combines it with the ten held-out validation records.

## Input contract

The feature builder reads only:

- `g.ndata["feat"]` and `g.edata["feat"]` from the CTS DGL graph;
- `node_ids_flat` and `node_ptr` for the CTS path node sequence;
- the three CTS path geometry values used by M0;
- the four CTS design scalars used by M0.

It does not read `target_route`, `slack`, `baseline_cts_grt`, report fields, or
route-stage features while constructing model inputs. `target_route[:, 0]` and
`label_mask` are accessed only as target and validity mask.

Categorical `cell_type_id` is encoded as a per-path histogram over the same
906-entry vocabulary used by the GNN embedding. Continuous node features use
masked mean, max, and last-node pooling, mirroring M0's path pooling. DGL edge
features are pooled over the directed consecutive path edges in the CTS graph.
All continuous inputs use training-only normalization, following M0's graph,
path-geometry, and design-scalar normalization statistics.

The common supervised objective is masked SmoothL1/Huber with `beta=0.02 ns`,
matching M0/M1's `--delay-loss smooth_l1 --delay-huber-beta 0.02` settings:
`0.5 * error^2 / beta` when `abs(error) < beta`, otherwise
`abs(error) - 0.5 * beta`. Training records receive equal total weight, and
model selection uses validation macro-average per-record MAE. Ridge adds its
L2 coefficient penalty, while MLP/XGBoost retain their estimator-specific
weight-decay/tree regularization; these penalties are distinct from the shared
supervised loss. For XGBoost, the custom gradient is the SmoothL1 gradient and
tree construction uses a positive `1 / beta` Hessian majorizer for numerical
stability; the majorizer is an optimizer approximation, not a change to the
reported pointwise loss.

The primary reported R2 is the arithmetic mean of per-(design, strategy) R2.
Per-record and macro-average metrics include MAE, RMSE, MAPE, and
`normalized_MAE = MAE / clock_period`. First compute each metric per
`(design, strategy)`, then take the unweighted macro-average across records.
MAPE is reported as a percentage and protects the denominator with `1e-6 ns`;
because percentage error can become large near zero-delay labels, interpret it
alongside MAE and normalized MAE. Critical-path diagnostics include
top-5% delay-ranking recall, bottom-5%-slack path recall, and delay MAE on the
bottom-5%-slack paths (max/min path types evaluated separately). Slack is read
only for evaluation and never enters model input, training loss, or model
selection.

## Models

- Ridge SmoothL1 regression
- XGBoost SmoothL1-gradient regressor with stable Hessian majorizer
- MLP

Each model gets the exact same feature matrix and data split. Preprocessing
statistics are fitted on training data only. Outputs belong under this
directory's `results/`, with checkpoints and logs kept in separate subfolders.

## Run

```powershell
D:/Anaconda3/envs/pytorch/python.exe `
  E:/CTS/experiments/cts_to_route/baselines_same_input/code/run_baselines.py `
  --graph-root E:/CTS/dataset/asap7/graphs_cts_v3 `
  --output-dir E:/CTS/experiments/cts_to_route/baselines_same_input/results/run_20260923
```

Use `--smoke` for a quick feature-contract and path-mapping check without
fitting models. The baseline runner does not require rebuilding DGL graphs or
reading CTS timing reports because none of its input features come from those
reports.

Protocol unit tests:

```powershell
D:/Anaconda3/envs/pytorch/python.exe `
  -m unittest discover `
  E:/CTS/experiments/cts_to_route/baselines_same_input/tests
```
