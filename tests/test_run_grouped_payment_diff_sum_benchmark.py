from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "code"
    / "heir"
    / "scripts"
    / "run_grouped_payment_diff_sum_benchmark.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("run_grouped_payment_diff_sum_benchmark", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load grouped sum benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GroupedPaymentDiffSumBenchmarkTest(unittest.TestCase):
    def test_reads_masked_groups_and_scales_from_parents(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); blocks = root / "group_blocks.csv"
            with blocks.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["packed_ciphertext_batch", "segment_index", "opaque_group_id", "lane", "AMT_PAYMENT", "AMT_INSTALMENT", "validity_mask"])
                writer.writerows([[0, 0, 0, 0, 640, 800, 1], [0, 0, 0, 1, 600, 500, 1], [0, 0, 0, 2, 0, 0, 0], [0, 0, 0, 3, 0, 0, 0], [0, 1, 1, 0, 80, 100, 1], [0, 1, 1, 1, 200, 200, 1], [0, 1, 1, 2, 0, 0, 0], [0, 1, 1, 3, 0, 0, 0]])
            groups, bucket = module._read_blocks(blocks)
            self.assertEqual(bucket, 4); self.assertEqual(set(groups), {0, 1}); self.assertEqual(module._scale(groups), 1024.0)
            self.assertEqual(sum(int(row["validity_mask"]) for row in groups[0]), 2)


if __name__ == "__main__":
    unittest.main()
