from pathlib import Path
from types import SimpleNamespace
import unittest

from code.openfhe_service.credit_rating_demo import (
    calculate_credit_group,
    load_prepared_group,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "data/prepared/examples/payment_diff_demo_group.csv"
)


class _PublicScalar:
    def __init__(self, value):
        self.value = float(value)


class _PublicVector:
    def __init__(self, values):
        self.value = [float(value) for value in values]


class _Ciphertext:
    def __init__(self, value):
        self.value = value

    def decrypt(self):
        return self.value


def _values(value):
    if isinstance(value, _Ciphertext):
        return value.value
    return value.value


class _FakeHEClient:
    def encrypt(self, values):
        return _Ciphertext(list(values))

    def add(self, left, right):
        left_values = _values(left)
        right_values = _values(right)
        if isinstance(right_values, float):
            right_values = [right_values] * len(left_values)
        return _Ciphertext(
            [a + b for a, b in zip(left_values, right_values)]
        )

    def subtract(self, left, right):
        return _Ciphertext(
            [a - b for a, b in zip(_values(left), _values(right))]
        )

    def multiply(self, left, right):
        left_values = _values(left)
        right_values = _values(right)
        if isinstance(right_values, float):
            right_values = [right_values] * len(left_values)
        return _Ciphertext(
            [a * b for a, b in zip(left_values, right_values)]
        )

    def square(self, value):
        return _Ciphertext([item * item for item in _values(value)])

    def sum(self, value):
        return _Ciphertext(sum(_values(value)))

    def mean(self, value):
        values = _values(value)
        return _Ciphertext(sum(values) / len(values))

    def variance_components(self, value):
        values = _values(value)
        return SimpleNamespace(
            sum_x=_Ciphertext(sum(values)),
            sum_x2=_Ciphertext(sum(item * item for item in values)),
        )

    def covariance_components(self, left, right):
        x = _values(left)
        y = _values(right)
        return SimpleNamespace(
            sum_x=_Ciphertext(sum(x)),
            sum_y=_Ciphertext(sum(y)),
            sum_xy=_Ciphertext(sum(a * b for a, b in zip(x, y))),
        )

    def correlation_components(self, left, right):
        x = _values(left)
        y = _values(right)
        return SimpleNamespace(
            sum_x=_Ciphertext(sum(x)),
            sum_y=_Ciphertext(sum(y)),
            sum_x2=_Ciphertext(sum(value * value for value in x)),
            sum_y2=_Ciphertext(sum(value * value for value in y)),
            sum_xy=_Ciphertext(sum(a * b for a, b in zip(x, y))),
        )

    def weighted_sum(self, value, weights):
        return _Ciphertext(
            sum(
                item * weight
                for item, weight in zip(
                    _values(value),
                    _values(weights),
                )
            )
        )

    def risk_score(self, value, weights, bias):
        weighted = self.weighted_sum(value, weights).value
        return _Ciphertext(weighted + bias.value)


class OpenFheServiceCreditRatingDemoTest(unittest.TestCase):
    def test_prepared_credit_group_runs_every_service_operation(self):
        group = load_prepared_group(FIXTURE)
        result = calculate_credit_group(
            _FakeHEClient(),
            _PublicScalar,
            _PublicVector,
            group,
        )

        self.assertEqual("DEMO_001", result["applicant_id"])
        self.assertEqual([160.0, -100.0, 0.0], result["payment_diff"])
        self.assertEqual(60.0, result["sum"])
        self.assertEqual(20.0, result["mean"])
        self.assertEqual(
            {"sum_x": 60.0, "sum_x2": 35600.0},
            result["variance_components"],
        )
        self.assertAlmostEqual(20.0, result["weighted_sum"])
        self.assertAlmostEqual(21.5, result["risk_score"])


if __name__ == "__main__":
    unittest.main()
