from __future__ import annotations

import unittest

from code.heir.scripts.run_installments_aggregation_benchmark import markdown_report, python_baseline


class InstallmentsAggregationBenchmarkTest(unittest.TestCase):
    def test_report_never_claims_ratio_aggregates(self) -> None:
        report = markdown_report(
            output=None, baseline=python_baseline(),
            ratio={"status": "executed", "wall_seconds": 1.0, "result": {"execution": {}}},
            diff={"status": "executed", "wall_seconds": 1.0, "result": {"execution": {}, "aggregation_comparison": []}},
        )
        self.assertIn("PAYMENT_PERC` | `executed` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN", report)
        self.assertIn("exhausted the small server", report)


if __name__ == "__main__":
    unittest.main()
