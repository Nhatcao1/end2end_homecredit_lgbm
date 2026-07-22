from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "code"
    / "heir"
    / "scripts"
    / "prepare_payment_diff_groupby_test_data.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_payment_diff_groupby_test_data", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load groupby fixture script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreparePaymentDiffGroupbyFixtureTest(unittest.TestCase):
    def test_writes_masked_parent_blocks_and_private_pandas_equivalent_reference(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "installments.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["SK_ID_CURR", "AMT_PAYMENT", "AMT_INSTALMENT"])
                writer.writerows(
                    [
                        ["A", "640", "800"],
                        ["A", "600", "500"],
                        ["B", "80", "100"],
                        ["B", "200", "200"],
                        ["C", "", "100"],
                        ["", "5", "9"],
                    ]
                )
            output = root / "fixture"
            report = module.prepare(
                source,
                output,
                group_count=2,
                bucket_size=4,
                vector_size=8,
                max_rows=0,
                seed="test",
            )

            self.assertEqual(report["layout"]["selected_groups"], 2)
            self.assertEqual(report["layout"]["selected_real_rows"], 4)
            self.assertEqual(report["layout"]["padding_lanes"], 4)
            self.assertFalse(report["scope"]["derived_payment_diff_in_he_ready_file"])

            blocks_text = (output / "he_ready" / "group_blocks.csv").read_text(encoding="utf-8")
            self.assertNotIn("SK_ID_CURR", blocks_text)
            self.assertNotIn("PAYMENT_DIFF", blocks_text)
            with (output / "he_ready" / "group_blocks.csv").open("r", encoding="utf-8", newline="") as handle:
                blocks = list(csv.DictReader(handle))
            self.assertEqual(len(blocks), 8)
            self.assertEqual(sum(int(row["validity_mask"]) for row in blocks), 4)
            self.assertTrue(all(row["AMT_PAYMENT"] == "0" for row in blocks if row["validity_mask"] == "0"))

            with (output / "client_private" / "pandas_groupby_reference.csv").open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                references = list(csv.DictReader(handle))
            self.assertEqual(len(references), 2)
            by_sum = sorted(float(row["payment_diff_sum"]) for row in references)
            self.assertEqual(by_sum, [20.0, 60.0])
            self.assertTrue(all(row["payment_diff_var"] for row in references))
            manifest = json.loads((output / "layout_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "client_only_payment_diff_groupby_fixture_prepared")


if __name__ == "__main__":
    unittest.main()
