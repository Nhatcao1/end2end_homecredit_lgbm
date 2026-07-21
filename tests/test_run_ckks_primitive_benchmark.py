from __future__ import annotations

import unittest
from pathlib import Path


class RunCkksPrimitiveBenchmarkTest(unittest.TestCase):
    def test_runner_uses_generated_ct_ct_kernels_and_writes_python_comparison(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "code" / "heir" / "scripts" / "run_ckks_primitive_benchmark.py").read_text(encoding="utf-8")
        self.assertIn("encrypted_add(context", source)
        self.assertIn("encrypted_subtract(context", source)
        self.assertIn("encrypted_multiply(context", source)
        self.assertIn("python_baseline", source)
        self.assertIn("max absolute error ≤ 1e-6", source)
        self.assertIn("online_seconds", source)
        self.assertIn("OMP_NUM_THREADS=1", source)
        self.assertIn("setup_seconds", source)
        self.assertIn("1000000", source)


if __name__ == "__main__":
    unittest.main()
