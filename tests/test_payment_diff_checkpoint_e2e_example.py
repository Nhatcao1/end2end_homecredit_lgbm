from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "code/heir/examples/payment_diff_checkpoint_e2e.py"


class PaymentDiffCheckpointE2EExampleTest(unittest.TestCase):
    def test_example_runs_full_aggregate_lifecycle_without_benchmark_report(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("prepare_post_psi_groups(", source)
        self.assertIn(
            "compile_checkpointable_binary_column_aggregate(",
            source,
        )
        self.assertIn('operation="subtract"', source)
        self.assertIn(
            "save_binary_column_aggregate_checkpoint(",
            source,
        )
        self.assertIn(
            "load_binary_column_aggregate_checkpoint(",
            source,
        )
        self.assertIn('("sum", "mean", "variance")', source)
        self.assertIn("OfficialOpenFheColumnOps(", source)
        self.assertIn("maximum.maximum(", source)
        self.assertIn("PAYMENT_DIFF_MEAN", source)
        self.assertIn("PAYMENT_DIFF_SUM", source)
        self.assertIn("PAYMENT_DIFF_VAR", source)
        self.assertIn("subprocess.run(", source)
        self.assertIn("--resume-checkpoints", source)
        self.assertNotIn("REPORT.md", source)
        self.assertNotIn("perf_counter", source)

    def test_help_is_available_without_he_setup(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("--checkpoint-dir", completed.stdout)


if __name__ == "__main__":
    unittest.main()
