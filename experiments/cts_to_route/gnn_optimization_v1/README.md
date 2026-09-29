# CTS-to-Route GNN Optimization v1

This workspace is reserved for GNN architecture and training experiments. It
does not modify the dataset, graph builder, M0 checkpoint, MLP baseline, or
held-out test partition.

## Leakage boundary

- The optimizer discovers only `train` and `val` graph directories.
- Train paths are split with seed 42; the held-out `val` records are appended
  to validation, matching the existing M0/MLP protocol.
- Feature normalizers and target transforms are fitted from training paths
  and training graphs only.
- Route delay and `label_mask` are target/validity only. Route slack, AT/RAT,
  reports, `baseline_cts_grt`, and test records are not loaded by the
  optimization runner.
- Checkpoint selection and early stopping use validation macro-record MAE.
- A test evaluation is a separate, explicit, post-freeze action and is not
  implemented in this optimization runner.

## Experiment organization

Each run gets a new directory under `results/` with `configs/`, `logs/`,
`weights/`, and `metrics/`. Never reuse a non-empty run directory.

## Intended investigations

1. Reproduce the archived GNN validation score with a test-blind runner.
2. Compare path pooling, explicit path-edge encoding, and sequence-aware
   readout on the same train/validation partition.
3. Compare `ns` and clock-normalized delay targets without changing the
   physical split or supervised SmoothL1 definition.
4. Report MAE, MAPE, normalized MAE and per-record metrics. 
