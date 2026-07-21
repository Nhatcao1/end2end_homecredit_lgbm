from __future__ import annotations
import unittest
from pathlib import Path

from code.heir.scripts.run_ckks_sum_benchmark import raise_mean_context_budget

class RunCkksSumBenchmarkTest(unittest.TestCase):
    def test_mean_context_budget_is_raised_in_translated_source(self) -> None:
        patched, original = raise_mean_context_budget(
            "params.SetMultiplicativeDepth(1);", 8
        )
        self.assertEqual(original, 1)
        self.assertEqual(patched, "params.SetMultiplicativeDepth(8);")

    def test_sum_is_an_isolated_generated_kernel_benchmark(self) -> None:
        source=(Path(__file__).resolve().parents[1]/"code"/"heir"/"scripts"/"run_ckks_sum_benchmark.py").read_text(encoding="utf-8")
        self.assertIn("encrypted_sum(ctx",source)
        self.assertIn("pandas_sum",source)
        self.assertIn("OMP_NUM_THREADS=1",source)
        self.assertIn("CKKS-SUM-01",source)
        self.assertIn("CKKS-MEAN-01",source)
        self.assertIn("## CKKS-SUM-01",source)
        self.assertIn("## CKKS-MEAN-01",source)
        self.assertIn("EvalMult(value,inverse)",source)
        self.assertIn("--input-scale",source)
        self.assertIn("inputScale",source)
        self.assertIn("encoded=values/input_scale",source)
        self.assertIn("raise_mean_context_budget",source)
        self.assertIn("--ckks-mul-depth",source)

if __name__=="__main__": unittest.main()
