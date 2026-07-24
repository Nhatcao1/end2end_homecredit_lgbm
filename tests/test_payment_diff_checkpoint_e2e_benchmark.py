from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "code/heir/scripts/run_payment_diff_checkpoint_e2e_benchmark.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_payment_diff_checkpoint_e2e_benchmark",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load checkpoint E2E benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaymentDiffCheckpointE2EBenchmarkTest(unittest.TestCase):
    def test_benchmark_invokes_external_probe_for_exact_example(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'EXAMPLE = ROOT / "code/heir/examples/payment_diff_checkpoint_e2e.py"',
            source,
        )
        self.assertIn(
            '"code/heir/benchmarking/payment_diff_checkpoint_probe.py"',
            source,
        )
        self.assertIn('"--execution-json"', source)
        self.assertIn("subprocess.run(", source)
        self.assertNotIn(
            "compile_checkpointable_binary_column_aggregate",
            source,
        )

    def test_accuracy_uses_relative_tolerance(self):
        benchmark = _load_module()
        rows = benchmark._accuracy_rows(
            {
                "PAYMENT_DIFF_MAX": 100.0,
                "PAYMENT_DIFF_MEAN": 10.0,
                "PAYMENT_DIFF_SUM": 200.0,
                "PAYMENT_DIFF_VAR": 25.0,
            },
            {
                "PAYMENT_DIFF_MAX": 100.0001,
                "PAYMENT_DIFF_MEAN": 11.0,
                "PAYMENT_DIFF_SUM": 200.0001,
                "PAYMENT_DIFF_VAR": 25.00001,
            },
            1e-5,
        )
        statuses = {row["output"]: row["status"] for row in rows}
        self.assertEqual("PASS", statuses["PAYMENT_DIFF_MAX"])
        self.assertEqual("FAIL", statuses["PAYMENT_DIFF_MEAN"])


if __name__ == "__main__":
    unittest.main()
