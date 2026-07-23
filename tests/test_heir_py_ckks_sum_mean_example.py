from pathlib import Path
import unittest


class HeirPyCkksSumMeanExampleTest(unittest.TestCase):
    def test_example_uses_python_frontend_for_encrypted_sum_and_mean(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "code/heir/examples/heir_py_ckks_sum_mean.py").read_text(encoding="utf-8")
        self.assertIn("from heir import compile", source)
        self.assertIn('@compile(scheme="ckks", debug=True)', source)
        self.assertIn("encrypted_mean = encrypted_sum * (1.0 / WIDTH)", source)
        self.assertIn("encrypted_sum_and_mean.setup()", source)
        self.assertIn("encrypted_sum_and_mean.eval(*encrypted_values)", source)
        self.assertIn("encrypted_sum_and_mean.decrypt_result(encrypted_result)", source)
        self.assertNotIn("subprocess.", source)


if __name__ == "__main__":
    unittest.main()
