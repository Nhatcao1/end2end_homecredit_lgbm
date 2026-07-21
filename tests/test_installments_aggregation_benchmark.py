from __future__ import annotations

import unittest

from code.heir.scripts.run_installments_aggregation_benchmark import input_shape, markdown_report


class InstallmentsAggregationBenchmarkTest(unittest.TestCase):
    def test_report_never_claims_ratio_aggregates(self) -> None:
        baseline = {
            "payment_perc": {"feature_seconds": 0.1, "stats": {"max": 1.2, "mean": 1.0, "sum": 3.0, "var": 0.04}},
            "payment_diff": {"feature_seconds": 0.1, "stats": {"max": 160.0, "mean": 20.0, "sum": 60.0, "var": 17200.0}},
            "pandas_aggregation_seconds": 0.2,
        }
        report = markdown_report(
            input_rows=input_shape(8), baseline=baseline,
            ratio={"status": "executed", "wall_seconds": 1.0, "result": {"execution": {}}},
            diff={"status": "executed", "wall_seconds": 1.0, "result": {"execution": {}, "aggregation_comparison": []}},
        )
        self.assertIn("PAYMENT_PERC` | `executed` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN", report)
        self.assertIn("exhausted the small server", report)
        self.assertIn("AMT_PAYMENT` parent column | 3 | 3 | 8 | 5", report)
        self.assertIn("Pandas whole-dataframe route", report)
        self.assertIn("General grouped HE reduction", report)

    def test_input_shape_records_real_and_padding_lanes(self) -> None:
        self.assertEqual(input_shape(8)["zero_padding_lanes"], 5)
        with self.assertRaises(ValueError):
            input_shape(2)


if __name__ == "__main__":
    unittest.main()
