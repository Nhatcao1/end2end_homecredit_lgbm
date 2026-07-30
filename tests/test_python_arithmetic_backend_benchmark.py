from pathlib import Path
import importlib.util
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "code/heir/scripts/run_python_arithmetic_backend_benchmark.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "run_python_arithmetic_backend_benchmark",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PythonArithmeticBackendBenchmarkTest(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("numpy"),
        "NumPy is installed by the benchmark environment",
    )
    def test_same_deterministic_inputs_are_reused_by_backends(self):
        module = load_module()
        left_a, right_a = module._inputs("subtract", 10, 3, 42)
        left_b, right_b = module._inputs("subtract", 10, 3, 42)
        self.assertTrue((left_a == left_b).all())
        self.assertTrue((right_a == right_b).all())

    @unittest.skipUnless(
        importlib.util.find_spec("numpy"),
        "NumPy is installed by the benchmark environment",
    )
    def test_error_contract(self):
        module = load_module()
        np = module._numpy()
        errors = module._errors(
            np.asarray([1.0, 2.000001]),
            np.asarray([1.0, 2.0]),
        )
        self.assertAlmostEqual(1e-6, errors["max_abs_error"], places=10)
        self.assertTrue(
            module._passes(
                errors,
                absolute_tolerance=1e-5,
                relative_tolerance=1e-6,
            )
        )

    def test_benchmark_contract_is_visible_in_source(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('choices=("heir", "openfhe-python", "both")', source)
        self.assertIn("OfficialCkksBinaryColumn", source)
        self.assertIn("OpenFhePythonBinaryColumn", source)
        self.assertIn("evaluation_values_per_second", source)
        self.assertIn("online_values_per_second", source)
        self.assertIn("max_abs_error", source)
        self.assertIn("max_relative_error", source)
        self.assertIn("actual_ring_dimension", source)
        self.assertIn("OpenFHE/HEIR evaluation latency", source)
        self.assertNotIn("CMAKE", source)

    def test_timing_helper_forwards_keyword_arguments(self):
        module = load_module()

        result, elapsed = module._timed(
            lambda value, *, increment: value + increment,
            4,
            increment=3,
        )

        self.assertEqual(7, result)
        self.assertGreaterEqual(elapsed, 0.0)


if __name__ == "__main__":
    unittest.main()
