from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "code/heir/examples/payment_diff_simple_api_e2e.py"
)
sys.path.insert(0, str(ROOT))


class PaymentDiffSimpleApiE2ETest(unittest.TestCase):
    def test_example_uses_source_built_checkpoint_api(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("SourceBuiltCkksSession.create(", source)
        self.assertIn("SourceBuiltCkksSession.load(", source)
        self.assertIn("he.encrypt_column(", source)
        self.assertIn('he.load_column("AMT_INSTALMENT")', source)
        self.assertIn('he.load_column("AMT_PAYMENT")', source)
        self.assertIn("he.add(installment_ct, payment_ct)", source)
        self.assertIn("he.subtract(", source)
        self.assertIn("he.multiply(installment_ct, payment_ct)", source)
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
        self.assertNotIn("plaintext_reference", source)
        self.assertNotIn("absolute_error", source)

    def test_roundtrip_uses_distinct_python_processes(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("subprocess.run(save_command", source)
        self.assertIn('"--stage", "evaluate"', source)
        self.assertIn(
            "parent_ciphertexts_reloaded_in_fresh_process",
            source,
        )


if __name__ == "__main__":
    unittest.main()
