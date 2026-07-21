from __future__ import annotations
import unittest
from pathlib import Path

class RunCkksSumBenchmarkTest(unittest.TestCase):
    def test_sum_is_an_isolated_generated_kernel_benchmark(self) -> None:
        source=(Path(__file__).resolve().parents[1]/"code"/"heir"/"scripts"/"run_ckks_sum_benchmark.py").read_text(encoding="utf-8")
        self.assertIn("encrypted_sum(ctx",source)
        self.assertIn("python_sum",source)
        self.assertIn("OMP_NUM_THREADS=1",source)
        self.assertIn("CKKS-SUM-01",source)

if __name__=="__main__": unittest.main()
