from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from code.heir.common import read_csv
from code.heir.function_benchmark import prepare_function_task
from code.heir.report import write_function_report
from code.heir.workloads.catalog import TASKS, get_task


def write_table(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def required_fields(filename: str) -> list[str]:
    fields = {"SK_ID_CURR"}
    for task in TASKS:
        if task.input_file != filename:
            continue
        if task.branch_column:
            fields.add(task.branch_column)
        for feature in task.features:
            fields.update(feature.source_columns or (feature.name,))
    if filename == "bureau.csv":
        fields.add("SK_ID_BUREAU")
    return sorted(fields)


def row(filename: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {field: 1 for field in required_fields(filename)}
    value.update(overrides)
    return value


def reference_value(
    path: Path, app_index: int, feature: str, operation: str
) -> float | None:
    for item in read_csv(path):
        if (
            int(item["app_index"]) == app_index
            and item["feature"] == feature
            and item["operation"] == operation
        ):
            return float(item["value"]) if item["value"] else None
    raise AssertionError(f"missing reference {app_index}/{feature}/{operation}")


class FunctionBenchmarkTest(unittest.TestCase):
    def make_data(self, root: Path) -> tuple[Path, Path]:
        data = root / "data"
        application = data / "application_train.csv"
        write_table(
            application,
            ["SK_ID_CURR", "TARGET"],
            [
                {"SK_ID_CURR": 101, "TARGET": 0},
                {"SK_ID_CURR": 102, "TARGET": 1},
                {"SK_ID_CURR": 103, "TARGET": 0},
            ],
        )
        write_table(
            data / "bureau.csv",
            required_fields("bureau.csv"),
            [
                row(
                    "bureau.csv",
                    SK_ID_CURR=101,
                    SK_ID_BUREAU=1001,
                    CREDIT_ACTIVE="Active",
                    DAYS_CREDIT=-10,
                    AMT_CREDIT_SUM=100,
                ),
                row(
                    "bureau.csv",
                    SK_ID_CURR=101,
                    SK_ID_BUREAU=1002,
                    CREDIT_ACTIVE="Closed",
                    DAYS_CREDIT=-20,
                    AMT_CREDIT_SUM=50,
                    AMT_ANNUITY="",
                ),
                row(
                    "bureau.csv",
                    SK_ID_CURR=102,
                    SK_ID_BUREAU=1003,
                    CREDIT_ACTIVE="Active",
                    DAYS_CREDIT=-5,
                    AMT_CREDIT_SUM=75,
                ),
            ],
        )
        write_table(
            data / "bureau_balance.csv",
            ["SK_ID_BUREAU", "MONTHS_BALANCE", "STATUS"],
            [
                {"SK_ID_BUREAU": 1001, "MONTHS_BALANCE": -1, "STATUS": 0},
                {"SK_ID_BUREAU": 1001, "MONTHS_BALANCE": -2, "STATUS": 0},
                {"SK_ID_BUREAU": 1002, "MONTHS_BALANCE": -1, "STATUS": "C"},
                {"SK_ID_BUREAU": 1003, "MONTHS_BALANCE": -1, "STATUS": 0},
            ],
        )
        write_table(
            data / "previous_application.csv",
            required_fields("previous_application.csv"),
            [
                row(
                    "previous_application.csv",
                    SK_ID_CURR=101,
                    NAME_CONTRACT_STATUS="Approved",
                    AMT_APPLICATION=80,
                    AMT_CREDIT=100,
                    CNT_PAYMENT=10,
                ),
                row(
                    "previous_application.csv",
                    SK_ID_CURR=101,
                    NAME_CONTRACT_STATUS="Refused",
                    AMT_APPLICATION=50,
                    AMT_CREDIT=100,
                    CNT_PAYMENT=20,
                ),
                row(
                    "previous_application.csv",
                    SK_ID_CURR=102,
                    NAME_CONTRACT_STATUS="Approved",
                    AMT_APPLICATION=90,
                    AMT_CREDIT=100,
                    CNT_PAYMENT=5,
                ),
            ],
        )
        write_table(
            data / "POS_CASH_balance.csv",
            required_fields("POS_CASH_balance.csv"),
            [
                row("POS_CASH_balance.csv", SK_ID_CURR=101, MONTHS_BALANCE=-1, SK_DPD=2),
                row("POS_CASH_balance.csv", SK_ID_CURR=101, MONTHS_BALANCE=-2, SK_DPD=0),
                row("POS_CASH_balance.csv", SK_ID_CURR=102, MONTHS_BALANCE=-1, SK_DPD=1),
            ],
        )
        write_table(
            data / "installments_payments.csv",
            required_fields("installments_payments.csv"),
            [
                row(
                    "installments_payments.csv",
                    SK_ID_CURR=101,
                    DAYS_INSTALMENT=-20,
                    DAYS_ENTRY_PAYMENT=-10,
                    AMT_INSTALMENT=100,
                    AMT_PAYMENT=80,
                ),
                row(
                    "installments_payments.csv",
                    SK_ID_CURR=101,
                    DAYS_INSTALMENT=-5,
                    DAYS_ENTRY_PAYMENT=-10,
                    AMT_INSTALMENT=50,
                    AMT_PAYMENT=60,
                ),
                row(
                    "installments_payments.csv",
                    SK_ID_CURR=102,
                    DAYS_INSTALMENT=-5,
                    DAYS_ENTRY_PAYMENT=-5,
                    AMT_INSTALMENT=25,
                    AMT_PAYMENT=25,
                ),
            ],
        )
        write_table(
            data / "credit_card_balance.csv",
            required_fields("credit_card_balance.csv"),
            [
                row("credit_card_balance.csv", SK_ID_CURR=101, AMT_BALANCE=10),
                row("credit_card_balance.csv", SK_ID_CURR=101, AMT_BALANCE=20),
                row("credit_card_balance.csv", SK_ID_CURR=102, AMT_BALANCE=5),
            ],
        )
        return data, application

    def test_all_thirteen_tasks_prepare_separate_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, application = self.make_data(root)
            for task in TASKS:
                run_dir = root / "runs" / task.task_id
                summary = prepare_function_task(task, data, application, run_dir, 0, 0)
                preview = read_csv(run_dir / "plaintext_reference.csv")
                write_function_report(run_dir / "benchmark_report.md", summary, preview)
                self.assertEqual(summary["backend_status"], "prepared_only")
                self.assertTrue(preview)
                self.assertTrue((run_dir / "kernel_oracle.csv").is_file())
                self.assertTrue((run_dir / "tensor_manifest.csv").is_file())
                self.assertTrue((run_dir / "benchmark_report.md").is_file())
                report = (run_dir / "benchmark_report.md").read_text(encoding="utf-8")
                self.assertIn("means the trusted-client Python preparation ran", report)
                self.assertIn("Not executed in this run", report)
                self.assertIn("## Source Python and benchmark mapping", report)
                self.assertIn("## Simplified HEIR arithmetic", report)
                self.assertEqual(
                    {contract["kernel_id"] for contract in summary["kernel_contracts"]},
                    set(task.kernel_ids),
                )

            self.assertEqual(len(TASKS), 13)

    def test_counts_branches_transforms_and_sample_variance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, application = self.make_data(root)

            def prepare(task_id: str) -> Path:
                path = root / task_id
                prepare_function_task(get_task(task_id), data, application, path, 0, 0)
                return path / "plaintext_reference.csv"

            pos = prepare("POS01")
            self.assertEqual(reference_value(pos, 0, "ROW_COUNT", "count"), 2.0)
            self.assertEqual(reference_value(pos, 2, "ROW_COUNT", "count"), 0.0)

            bureau = prepare("B01")
            self.assertEqual(reference_value(bureau, 0, "DAYS_CREDIT", "mean"), -15.0)
            self.assertEqual(reference_value(bureau, 0, "DAYS_CREDIT", "var"), 50.0)
            self.assertEqual(reference_value(bureau, 0, "MONTHS_BALANCE_SIZE", "sum"), 3.0)

            active = prepare("B02")
            closed = prepare("B03")
            self.assertEqual(reference_value(active, 0, "AMT_CREDIT_SUM", "sum"), 100.0)
            self.assertEqual(reference_value(closed, 0, "AMT_CREDIT_SUM", "sum"), 50.0)

            approved = prepare("P02")
            refused = prepare("P03")
            self.assertAlmostEqual(
                reference_value(approved, 0, "APP_CREDIT_PERC", "mean") or 0, 0.8
            )
            self.assertAlmostEqual(
                reference_value(refused, 0, "APP_CREDIT_PERC", "mean") or 0, 0.5
            )

            difference = prepare("I03")
            self.assertEqual(reference_value(difference, 0, "PAYMENT_DIFF", "sum"), 10.0)
            self.assertEqual(reference_value(difference, 0, "PAYMENT_DIFF", "var"), 450.0)


if __name__ == "__main__":
    unittest.main()
