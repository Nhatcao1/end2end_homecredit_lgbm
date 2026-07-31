from pathlib import Path
from statistics import variance
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct.multigroup_e2e_benchmark import (
    run_multigroup_benchmark,
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
    def encrypt_column(self, values):
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

    def decrypt_scalar(self, scalar):
        return scalar.value


class OpenFHEDirectMultigroupE2ETest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
