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

    def test_python_baseline_excludes_padding_from_sum(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            groups = {
                7: [
                    {"AMT_PAYMENT": "640", "AMT_INSTALMENT": "800", "validity_mask": "1"},
                    {"AMT_PAYMENT": "600", "AMT_INSTALMENT": "500", "validity_mask": "1"},
                    {"AMT_PAYMENT": "0", "AMT_INSTALMENT": "0", "validity_mask": "0"},
                ]
            }
            output = root / "python_results.csv"
            module._python_baseline(groups, repetitions=1, output=output)
            with output.open("r", encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(float(row["payment_diff_sum"]), 60.0)

    def test_ciphertext_manifest_records_only_ciphertext_files(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "ciphertexts" / "parents"
            feature = root / "ciphertexts" / "payment_diff"
            parent.mkdir(parents=True); feature.mkdir(parents=True)
            (parent / "amt_payment_r1_g0.ct").write_bytes(b"parent")
            (feature / "payment_diff_r1_g0.ct").write_bytes(b"feature")
            (feature / "not_a_ciphertext.txt").write_text("ignore", encoding="utf-8")
            manifest = module._ciphertext_manifest(root)
            self.assertEqual(manifest["artifact_count"], 2)
            self.assertEqual(
                {item["role"] for item in manifest["artifacts"]},
                {"encrypted AMT_PAYMENT parent", "encrypted PAYMENT_DIFF feature"},
            )


if __name__ == "__main__":
    unittest.main()
