from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from code.heir.common import read_csv
from code.heir.workloads.pos_cash.pos_count import prepare_pos_count


def write_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PosCountPreparationTest(unittest.TestCase):
    def test_anonymous_mask_reproduces_pos_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            application = root / "application_train.csv"
            pos = root / "POS_CASH_balance.csv"
            run_dir = root / "run"
            write_rows(
                application,
                ["SK_ID_CURR", "TARGET"],
                [
                    {"SK_ID_CURR": 101, "TARGET": 0},
                    {"SK_ID_CURR": 102, "TARGET": 1},
                    {"SK_ID_CURR": 103, "TARGET": 0},
                ],
            )
            write_rows(
                pos,
                ["SK_ID_CURR", "MONTHS_BALANCE"],
                [
                    {"SK_ID_CURR": 101, "MONTHS_BALANCE": -1},
                    {"SK_ID_CURR": 102, "MONTHS_BALANCE": -1},
                    {"SK_ID_CURR": 101, "MONTHS_BALANCE": -2},
                    {"SK_ID_CURR": 999, "MONTHS_BALANCE": -1},
                ],
            )

            summary = prepare_pos_count(application, pos, run_dir, 0, 0)
            reference = read_csv(run_dir / "plaintext_reference.csv")
            masks = read_csv(run_dir / "tensors" / "history_mask_matrix.csv")

            self.assertEqual([row["POS_COUNT"] for row in reference], ["2", "1", "0"])
            self.assertEqual(summary["slots_per_application"], 2)
            self.assertEqual([float(row["value"]) for row in masks], [1, 1, 1, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()
