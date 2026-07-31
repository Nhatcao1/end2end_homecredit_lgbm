import csv
from pathlib import Path
from statistics import variance
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct.multigroup_e2e_benchmark import (
    run_multigroup_benchmark,
)
from code.openfhe_direct.prepared_data import (
    load_prepared_group_population,
)


ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    ROOT / "data/prepared/examples/payment_diff_demo_group.csv",
    ROOT / "data/prepared/examples/payment_diff_demo_group_002.csv",
    ROOT / "data/prepared/examples/payment_diff_demo_group_003.csv",
]


class _Column:
    def __init__(self, values):
        self.values = list(values)


class _Scalar:
    def __init__(self, value):
        self.value = float(value)


class _NumericSession:
    def encrypt(self, values):
        return _Column(values)

    def subtract(self, left, right):
        return _Column(
            left_value - right_value
            for left_value, right_value in zip(left.values, right.values)
        )

    def sum(self, column):
        return _Scalar(sum(column.values))

    def mean(self, column):
        return _Scalar(sum(column.values) / len(column.values))

    def variance(self, column):
        return _Scalar(variance(column.values))

    def minimum(self, column):
        return _Scalar(min(column.values))

    def maximum(self, column):
        return _Scalar(max(column.values))

    def decrypt(self, scalar):
        return scalar.value


class OpenFHEDirectMultigroupE2ETest(unittest.TestCase):
    @staticmethod
    def _write_population(root: Path) -> None:
        private = root / "client_private"
        parents = private / "parent_rows"
        parents.mkdir(parents=True)
        with (private / "group_mapping.csv").open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["opaque_group_id", "SK_ID_CURR", "source_rows"])
            writer.writerows(
                [
                    [0, "private-a", 2],
                    [1, "private-b", 4],
                    [2, "private-c", 3],
                ]
            )
        with (parents / "parent_rows_00000.csv").open(
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "opaque_group_id",
                    "group_block_index",
                    "lane",
                    "AMT_PAYMENT",
                    "AMT_INSTALMENT",
                ]
            )
            writer.writerows(
                [
                    [0, 0, 0, 10, 15],
                    [0, 0, 1, 20, 18],
                    [1, 0, 0, 3, 9],
                    [1, 0, 1, 4, 11],
                    # Group 1 continues in another preparation block.
                    [1, 1, 0, 6, 5],
                    [1, 1, 1, 8, 14],
                    [2, 0, 0, 7, 12],
                    [2, 0, 1, 8, 16],
                    [2, 0, 2, 9, 18],
                ]
            )

    def test_three_groups_use_one_context_and_return_all_aggregates(self):
        factory_calls = []

        def factory(**kwargs):
            factory_calls.append(kwargs)
            return _NumericSession()

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "result"
            result = run_multigroup_benchmark(
                prepared_groups=GROUPS,
                output_dir=root,
                repetitions=2,
                ring_dimension=16,
                slot_count=0,
                input_scale=0.0,
                absolute_tolerance=1e-9,
                relative_tolerance=1e-9,
                overwrite=False,
                _session_factory=factory,
            )

            self.assertEqual("PASS", result["status"])
            self.assertEqual(3, result["group_count"])
            self.assertTrue(result["one_shared_context"])
            self.assertTrue(result["scheme_switching_minmax"])
            self.assertEqual(8, result["slot_count"])
            self.assertEqual(1, len(factory_calls))
            self.assertEqual(8, factory_calls[0]["slot_count"])
            self.assertTrue(factory_calls[0]["enable_minmax"])
            self.assertEqual(
                [
                    "PAYMENT_DIFF_SUM",
                    "PAYMENT_DIFF_MEAN",
                    "PAYMENT_DIFF_VAR",
                    "PAYMENT_DIFF_MIN",
                    "PAYMENT_DIFF_MAX",
                ],
                result["outputs"],
            )
            self.assertTrue((root / "REPORT.md").is_file())
            self.assertTrue((root / "accuracy.csv").is_file())
            self.assertTrue((root / "timings.csv").is_file())

    def test_population_selection_reassembles_and_runs_groups_separately(self):
        factory_calls = []

        def factory(**kwargs):
            factory_calls.append(kwargs)
            return _NumericSession()

        with TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            population_root = temporary_root / "population"
            self._write_population(population_root)

            selected = load_prepared_group_population(
                population_root,
                group_count=2,
                selection_policy="largest",
            )
            self.assertEqual(["opaque_1", "opaque_2"], [
                group.applicant_id for group in selected.groups
            ])
            self.assertEqual(4, len(selected.groups[0].installment))

            result = run_multigroup_benchmark(
                prepared_population_dir=population_root,
                group_count=2,
                selection_policy="largest",
                output_dir=temporary_root / "result",
                repetitions=1,
                ring_dimension=16,
                slot_count=0,
                input_scale=0.0,
                absolute_tolerance=1e-9,
                relative_tolerance=1e-9,
                overwrite=False,
                _session_factory=factory,
            )

            self.assertEqual("PASS", result["status"])
            self.assertEqual(2, result["group_count"])
            self.assertEqual(7, result["real_rows"])
            self.assertTrue(result["independent_group_execution"])
            self.assertEqual(
                ["opaque_1", "opaque_2"],
                result["selected_opaque_groups"],
            )
            self.assertEqual(
                "prepared_population",
                result["selection"]["input_mode"],
            )
            self.assertEqual(3, result["selection"]["population_group_count"])
            self.assertEqual(1, len(factory_calls))

    def test_requires_multiple_groups(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "at least two"):
                run_multigroup_benchmark(
                    prepared_groups=GROUPS[:1],
                    output_dir=Path(temporary) / "result",
                    repetitions=1,
                    ring_dimension=16,
                    slot_count=0,
                    input_scale=0.0,
                    absolute_tolerance=1e-9,
                    relative_tolerance=1e-9,
                    overwrite=False,
                    _session_factory=lambda **_: _NumericSession(),
                )

    def test_benchmark_contains_no_direct_he_implementation(self):
        source = (
            ROOT / "code/openfhe_direct/multigroup_e2e_benchmark.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHECreditSession", source)
        self.assertNotIn("code.heir", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("EvalAdd", source)
        self.assertNotIn("EvalMin", source)


if __name__ == "__main__":
    unittest.main()
