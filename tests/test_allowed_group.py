import csv
from pathlib import Path
import tempfile
import unittest

from code.heir.python_api.allowed_group import (
    CompleteGroupDoesNotFitError,
    load_prepared_allowed_group,
    prepare_allowed_group_csv,
)


class AllowedGroupPreparationTest(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        path = root / "installments.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "SK_ID_CURR",
                    "AMT_INSTALMENT",
                    "AMT_PAYMENT",
                ],
            )
            writer.writeheader()
            writer.writerows(
                [
                    {
                        "SK_ID_CURR": "10",
                        "AMT_INSTALMENT": "800",
                        "AMT_PAYMENT": "640",
                    },
                    {
                        "SK_ID_CURR": "20",
                        "AMT_INSTALMENT": "500",
                        "AMT_PAYMENT": "400",
                    },
                    {
                        "SK_ID_CURR": "10",
                        "AMT_INSTALMENT": "",
                        "AMT_PAYMENT": "12",
                    },
                    {
                        "SK_ID_CURR": "10",
                        "AMT_INSTALMENT": "500",
                        "AMT_PAYMENT": "600",
                    },
                    {
                        "SK_ID_CURR": "10",
                        "AMT_INSTALMENT": "1000",
                        "AMT_PAYMENT": "1000",
                    },
                ]
            )
        return path

    def test_complete_allowed_group_is_masked_without_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "group.csv"
            prepared = prepare_allowed_group_csv(
                self._source(root),
                allowed_sk_id_curr="10",
                bucket_size=0,
                output_csv=output,
            )
            self.assertEqual((1, 1, 1, 0), prepared.validity_mask)
            self.assertEqual((800.0, 500.0, 1000.0), prepared.group.installment)
            self.assertEqual((640.0, 600.0, 1000.0), prepared.group.payment)
            self.assertEqual(1, prepared.removed_null_rows)
            self.assertEqual(5, prepared.source_rows_scanned)
            reloaded = load_prepared_allowed_group(output)
            self.assertEqual(3, reloaded.group.real_count)

    def test_complete_group_larger_than_bucket_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(CompleteGroupDoesNotFitError):
                prepare_allowed_group_csv(
                    self._source(root),
                    allowed_sk_id_curr="10",
                    bucket_size=2,
                    output_csv=root / "group.csv",
                )


if __name__ == "__main__":
    unittest.main()
