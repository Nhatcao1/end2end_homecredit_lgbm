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
    / "prepare_installments_group_blocks.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_installments_group_blocks", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load grouping script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrepareInstallmentsGroupBlocksTest(unittest.TestCase):
    def test_groups_rows_and_records_implicit_padding_without_deriving_features(self) -> None:
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
                        ["B", "80", "100"],
                        ["A", "600", "500"],
                        ["B", "200", "200"],
                        ["A", "1000", "1000"],
                        ["B", "300", "300"],
                        ["B", "400", "400"],
                        ["B", "500", "500"],
                        ["C", "1", "2"],
                        ["", "9", "10"],
                    ]
                )
            output = root / "prepared"
            report = module.prepare(
                source,
                output,
                bucket_size=4,
                partition_count=3,
                blocks_per_shard=2,
                max_rows=0,
            )

            self.assertEqual(report["source_rows"]["raw_rows"], 10)
            self.assertEqual(report["source_rows"]["selected_rows"], 9)
            self.assertEqual(report["source_rows"]["missing_id_rows"], 1)
            self.assertEqual(report["group_layout"]["groups"], 3)
            self.assertEqual(report["group_layout"]["blocks"], 4)
            self.assertEqual(report["group_layout"]["implicit_zero_padding_rows"], 7)
            self.assertFalse((output / "client_private" / "staging").exists())

            layout_text = (output / "layout" / "block_layout.csv").read_text(encoding="utf-8")
            self.assertNotIn("SK_ID_CURR", layout_text)
            self.assertNotIn("PAYMENT_DIFF", layout_text)
            mapping = (output / "client_private" / "group_mapping.csv").read_text(encoding="utf-8")
            self.assertIn("SK_ID_CURR", mapping)
            self.assertIn("A", mapping)

            parent_rows = []
            for path in (output / "client_private" / "parent_rows").glob("*.csv"):
                with path.open("r", encoding="utf-8", newline="") as handle:
                    parent_rows.extend(csv.DictReader(handle))
            self.assertEqual(len(parent_rows), 9)
            self.assertNotIn("PAYMENT_DIFF", parent_rows[0])
            self.assertEqual(
                json.loads((output / "group_preparation_report.json").read_text(encoding="utf-8"))["status"],
                "client_only_installments_group_blocks_prepared",
            )


if __name__ == "__main__":
    unittest.main()
