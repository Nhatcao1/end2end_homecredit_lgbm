from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct.benchmarks.api_latency import run_benchmark
from tests.test_openfhe_direct_credit_api import _OpenFHE


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/prepared/examples/payment_diff_demo_group.csv"
BENCHMARK = ROOT / "code/openfhe_direct/benchmarks/api_latency.py"
COMPATIBILITY_ENTRY = (
    ROOT
    / "code/heir/scripts/run_python_arithmetic_backend_benchmark.py"
)


class OpenFHEDirectApiBenchmarkTest(unittest.TestCase):
    def test_all_public_calculation_methods_are_timed(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            summary = run_benchmark(
                prepared_group=FIXTURE,
                output_dir=output,
                repetitions=2,
                multiplicative_depth=4,
                ring_dimension=0,
                absolute_tolerance=1e-9,
                relative_tolerance=1e-9,
                overwrite=False,
                _openfhe_module=_OpenFHE(),
            )

            self.assertEqual("PASS", summary["status"])
            self.assertEqual(
                {
                    "encrypt",
                    "decrypt",
                    "add",
                    "subtract",
                    "multiply",
                    "add_public_scalar",
                    "add_public_vector",
                    "multiply_public_scalar",
                    "multiply_public_vector",
                    "square",
                    "sum",
                    "mean",
                    "variance_components",
                    "variance",
                    "covariance_components",
                    "correlation_components",
                    "weighted_sum",
                    "risk_score",
                },
                {
                    str(row["function"])
                    for row in summary["functions"]
                },
            )
            self.assertTrue((output / "results.csv").is_file())
            self.assertTrue((output / "summary.json").is_file())
            self.assertTrue((output / "REPORT.md").is_file())

    def test_benchmark_uses_only_the_direct_session(self):
        source = BENCHMARK.read_text(encoding="utf-8")
        compatibility = COMPATIBILITY_ENTRY.read_text(encoding="utf-8")

        self.assertIn("OpenFHECreditSession", source)
        self.assertIn("session.sum(", source)
        self.assertIn("session.mean(", source)
        self.assertIn("session.variance_components(", source)
        self.assertNotIn("OfficialCkks", source)
        self.assertNotIn("OpenFhePythonBinaryColumn", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("code.heir", source)
        self.assertNotIn("from heir", source)
        self.assertIn(
            "code.openfhe_direct.benchmarks.api_latency import main",
            compatibility,
        )


if __name__ == "__main__":
    unittest.main()
