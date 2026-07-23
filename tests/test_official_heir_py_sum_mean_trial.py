from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.scripts.run_official_heir_py_sum_mean_trial import (
    _summary,
    _write_report,
)


class OfficialHeirPySumMeanTrialTest(unittest.TestCase):
    def test_summary_keeps_sum_and_mean_distinct(self) -> None:
        rows = []
        for operation, expected in (("SUM", 60.0), ("MEAN", 20.0)):
            rows.append(
                {
                    "operation": operation,
                    "python_result": expected,
                    "he_result": expected + 1e-8,
                    "absolute_error": 1e-8,
                    "compile_seconds_once": 2.0,
                    "setup_seconds_once": 1.0,
                    "python_seconds": 0.001,
                    "encrypt_seconds": 0.1,
                    "evaluation_seconds": 0.2,
                    "online_seconds": 0.3,
                    "audit_decrypt_seconds": 0.05,
                }
            )
        summary = _summary(rows, tolerance=1e-6)
        self.assertEqual(["SUM", "MEAN"], [item["operation"] for item in summary])
        self.assertTrue(all(item["accuracy_status"] == "PASS" for item in summary))

    def test_report_identifies_official_api_and_separate_contexts(self) -> None:
        summary = [
            {
                "operation": operation,
                "python_result": result,
                "he_result_last_audit": result,
                "max_absolute_error": 0.0,
                "accuracy_status": "PASS",
                "compile_seconds_once": 1.0,
                "setup_seconds_once": 2.0,
                "python_median_seconds": 0.001,
                "encrypt_median_seconds": 0.1,
                "evaluation_median_seconds": 0.2,
                "online_median_seconds": 0.3,
                "audit_decrypt_median_seconds": 0.05,
            }
            for operation, result in (("SUM", 60.0), ("MEAN", 20.0))
        ]
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "REPORT.md"
            _write_report(
                report,
                values=[160.0, -100.0, 0.0],
                width=8,
                repetitions=1,
                tolerance=1e-6,
                summary=summary,
            )
            text = report.read_text(encoding="utf-8")
        self.assertIn("official OpenFHE Python client interface", text)
        self.assertIn("separate compiled programs", text)
        self.assertIn("| SUM |", text)
        self.assertIn("| MEAN |", text)


if __name__ == "__main__":
    unittest.main()
