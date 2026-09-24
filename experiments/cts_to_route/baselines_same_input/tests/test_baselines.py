import importlib.util
import math
import unittest
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "code" / "run_baselines.py"
SPEC = importlib.util.spec_from_file_location("run_baselines", SCRIPT)
BASELINES = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BASELINES)


class BaselineProtocolTests(unittest.TestCase):
    def test_mape_and_normalized_mae(self):
        row = {
            "y": np.array([1.0, 2.0, 3.0]),
            "pred": np.array([1.0, 2.0, 2.0]),
            "clock_period_ns": 2.0,
        }
        metrics = BASELINES.macro_metrics([row])
        self.assertAlmostEqual(metrics["mae"], 1.0 / 3.0)
        self.assertAlmostEqual(metrics["MAPE_percent"], 100.0 / 9.0)
        self.assertAlmostEqual(metrics["normalized_MAE"], 1.0 / 6.0)

    def test_mape_zero_floor_and_record_macro_normalization(self):
        records = [
            {
                "y": np.array([0.0, 1.0]),
                "pred": np.array([0.1, 1.0]),
                "clock_period_ns": 2.0,
            },
            {
                "y": np.array([2.0]),
                "pred": np.array([1.0]),
                "clock_period_ns": 4.0,
            },
        ]
        metrics = BASELINES.macro_metrics(records)
        expected_first_mape = (0.1 / 1e-6 + 0.0) / 2 * 100.0
        expected_second_mape = 0.5 * 100.0
        self.assertAlmostEqual(
            metrics["MAPE_percent"], (expected_first_mape + expected_second_mape) / 2
        )
        self.assertAlmostEqual(metrics["normalized_MAE"], (0.025 + 0.25) / 2)

    def test_split_is_deterministic_and_disjoint(self):
        indices = np.arange(100, dtype=np.int64)
        train_a, val_a = BASELINES.split_path_indices(indices, 0.2, 42)
        train_b, val_b = BASELINES.split_path_indices(indices, 0.2, 42)
        np.testing.assert_array_equal(train_a, train_b)
        np.testing.assert_array_equal(val_a, val_b)
        self.assertEqual(len(val_a), 20)
        self.assertFalse(np.intersect1d(train_a, val_a).size)
        np.testing.assert_array_equal(np.sort(np.concatenate([train_a, val_a])), indices)

    def test_record_balanced_weights(self):
        records = [{"y": np.zeros(2)}, {"y": np.zeros(6)}]
        weights = BASELINES.record_balanced_weights(records)
        self.assertAlmostEqual(float(weights[:2].sum()), float(weights[2:].sum()))

    def test_path_selection_keeps_all_fields_aligned(self):
        row = {
            "key": "d__s",
            "path_indices": np.array([2, 5, 8]),
            "x": np.arange(6).reshape(3, 2),
            "y": np.array([20, 50, 80]),
            "slack": np.array([-2, -5, -8]),
            "slack_mask": np.array([True, False, True]),
        }
        selected = BASELINES.select_record_paths(row, np.array([5, 8]))
        np.testing.assert_array_equal(selected["y"], [50, 80])
        np.testing.assert_array_equal(selected["slack"], [-5, -8])
        np.testing.assert_array_equal(selected["path_indices"], [5, 8])


if __name__ == "__main__":
    unittest.main()
