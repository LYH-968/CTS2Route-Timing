import sys
import unittest
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

CODE_DIR = Path(__file__).resolve().parents[1] / "code"
sys.path.insert(0, str(CODE_DIR))

from train_cts_route_v2 import (  # noqa: E402
    MaskedDelayLoss,
    format_metrics_table,
    format_per_record_table,
    metric_bundle,
    summarise_by_record,
)


class MetricsProtocolTests(unittest.TestCase):
    def test_m0_masked_smooth_l1_matches_pytorch_baseline_loss(self):
        beta = 0.02
        error = torch.tensor([-0.04, -0.01, 0.0, 0.01, 0.04])
        mask = torch.ones_like(error)
        m0_loss = MaskedDelayLoss("smooth_l1", beta=beta)(
            error, torch.zeros_like(error), mask
        )
        baseline_loss = F.smooth_l1_loss(
            error, torch.zeros_like(error), beta=beta, reduction="mean"
        )
        self.assertAlmostEqual(float(m0_loss), float(baseline_loss), places=7)

    def test_mape_and_clock_period_normalized_mae(self):
        metrics = metric_bundle(
            np.array([1.0, 2.0, 3.0]),
            np.array([1.0, 2.0, 2.0]),
            clock_period_ns=2.0,
        )
        self.assertAlmostEqual(metrics["mae"], 1.0 / 3.0)
        self.assertAlmostEqual(metrics["MAPE_percent"], 100.0 / 9.0)
        self.assertAlmostEqual(metrics["normalized_MAE"], 1.0 / 6.0)

    def test_error_metrics_include_records_with_undefined_r2(self):
        flat_label_record = metric_bundle(
            np.array([1.0, 1.0]),
            np.array([1.2, 1.0]),
            clock_period_ns=2.0,
        )
        varying_record = metric_bundle(
            np.array([1.0, 2.0]),
            np.array([1.0, 2.0]),
            clock_period_ns=4.0,
        )
        summary = summarise_by_record({
            "flat__strategy": {"model": flat_label_record, "baselines": {}},
            "varying__strategy": {"model": varying_record, "baselines": {}},
        })["model"]
        self.assertEqual(summary["pairs"], 1)
        self.assertEqual(summary["metric_pairs"], 2)
        self.assertAlmostEqual(summary["MAPE_percent"], 5.0)
        self.assertAlmostEqual(summary["normalized_MAE"], 0.025)

    def test_final_tables_show_mape_and_normalized_mae(self):
        metrics = metric_bundle(
            np.array([1.0, 2.0, 3.0]),
            np.array([1.0, 2.0, 2.0]),
            clock_period_ns=2.0,
        )
        record_table = format_per_record_table(
            "test",
            {"aes__base": {"model": metrics, "baselines": {"geom_linreg": metrics}}},
        )
        design_table = format_metrics_table(
            "test",
            {"aes": {"model": metrics, "baselines": {
                "const": metrics,
                "geom_linreg": metrics,
            }}},
        )
        self.assertIn("MAPE", record_table)
        self.assertIn("nMAE", record_table)
        self.assertIn("MAPE%", design_table)
        self.assertIn("nMAE", design_table)


if __name__ == "__main__":
    unittest.main()
