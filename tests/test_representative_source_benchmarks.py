from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from code.heir.operations.source_benchmarks import benchmark_spec, plaintext_audit_reference, prepare_binary_source_benchmark


def write_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class RepresentativeSourceBenchmarkTest(unittest.TestCase):
    def test_payment_difference_is_raw_packed_then_reference_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_rows(
                root / "installments_payments.csv",
                ["AMT_INSTALMENT", "AMT_PAYMENT"],
                [
                    {"AMT_INSTALMENT": 100, "AMT_PAYMENT": 80},
                    {"AMT_INSTALMENT": "", "AMT_PAYMENT": 15},
                    {"AMT_INSTALMENT": 40, "AMT_PAYMENT": 50},
                ],
            )
            payload = prepare_binary_source_benchmark(
                benchmark_spec("installments_payment_diff"), root
            )
            self.assertEqual(payload["left"], [100.0, 0.0, 40.0])
            self.assertEqual(payload["right"], [80.0, 15.0, 50.0])
            self.assertEqual(payload["left_validity"], [1.0, 0.0, 1.0])
            self.assertNotIn("plaintext_reference", payload)
            self.assertEqual(plaintext_audit_reference(payload), [20.0, -15.0, -10.0])
            self.assertEqual(payload["valid_pair_count"], 2)

    def test_days_employed_sentinel_is_null_packing_not_client_division(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_rows(
                root / "application_train.csv",
                ["DAYS_EMPLOYED", "DAYS_BIRTH"],
                [{"DAYS_EMPLOYED": 365243, "DAYS_BIRTH": -10000}],
            )
            payload = prepare_binary_source_benchmark(
                benchmark_spec("application_days_employed_perc"), root
            )
            self.assertEqual(payload["left"], [0.0])
            self.assertEqual(payload["left_validity"], [0.0])
            self.assertNotIn("plaintext_reference", payload)
            self.assertEqual(payload["operation_contract"]["status"], "planned")


if __name__ == "__main__":
    unittest.main()
