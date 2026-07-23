from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "code/heir/examples/payment_diff_post_psi.py"


class PaymentDiffPostPsiExampleTest(unittest.TestCase):
    def test_example_is_api_orchestration_not_a_benchmark_runner(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("prepare_post_psi_groups(", source)
        self.assertIn("OfficialPaymentDiffGroupStatistics(", source)
        self.assertIn("OfficialOpenFhePaymentDiffMax(", source)
        self.assertIn("statistics_ct = statistics.eval(", source)
        self.assertIn("maximum_ct = maximum.eval(", source)
        self.assertNotIn("CMAKE", source)
        self.assertNotIn("REPORT.md", source)
        self.assertNotIn("perf_counter", source)

    def test_help_runs_without_he_runtime_setup(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--bridge-dir", completed.stdout)
        self.assertIn("--output-csv", completed.stdout)


if __name__ == "__main__":
    unittest.main()
