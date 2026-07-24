from pathlib import Path
import importlib.util
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "code/heir/examples/payment_diff_simple_api_e2e.py"
)
sys.path.insert(0, str(ROOT))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "payment_diff_simple_api_e2e",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load simple PAYMENT_DIFF example")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaymentDiffSimpleApiE2ETest(unittest.TestCase):
    def test_reference_matches_sample_variance_contract(self):
        module = load_module()
        result = module.plaintext_reference([160.0, -100.0, 0.0])
        self.assertEqual(60.0, result["sum"])
        self.assertEqual(20.0, result["mean"])
        self.assertEqual(17200.0, result["variance"])
        self.assertEqual(-100.0, result["minimum"])
        self.assertEqual(160.0, result["maximum"])

    def test_example_uses_only_simple_ciphertext_operations(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("CkksSession.create(", source)
        self.assertIn("he.encrypt_column(", source)
        self.assertIn("he.subtract(", source)
        self.assertIn("he.sum(", source)
        self.assertIn("he.mean(", source)
        self.assertIn("he.variance(", source)
        self.assertIn("he.minimum(", source)
        self.assertIn("he.maximum(", source)
        self.assertIn("he.decrypt_scalar(", source)
        self.assertNotIn(
            "compile_checkpointable_binary_column_aggregate",
            source,
        )
        self.assertNotIn("perf_counter", source)
        self.assertNotIn("CMake", source)


if __name__ == "__main__":
    unittest.main()
