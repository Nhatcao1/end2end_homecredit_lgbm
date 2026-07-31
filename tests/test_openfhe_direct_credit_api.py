from pathlib import Path
from types import SimpleNamespace
import unittest

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.prepared_data import load_prepared_group


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "data/prepared/examples/payment_diff_demo_group.csv"
)
EXAMPLE = ROOT / "code/openfhe_direct/credit_rating_example.py"


class _Parameters:
    def SetMultiplicativeDepth(self, value):
        self.depth = value

    def SetScalingModSize(self, value):
        self.scaling = value

    def SetFirstModSize(self, value):
        self.first = value

    def SetBatchSize(self, value):
        self.batch = value

    def SetRingDim(self, value):
        self.ring = value


class _Plaintext:
    def __init__(self, values):
        self.values = list(values)

    def SetLength(self, length):
        self.values = self.values[:length]

    def GetRealPackedValue(self):
        return self.values


class _Context:
    def __init__(self):
        self.features = []
        self.mult_keys_generated = False
        self.sum_keys_generated = False

    def Enable(self, feature):
        self.features.append(feature)

    def KeyGen(self):
        return SimpleNamespace(publicKey="public", secretKey="secret")

    def EvalMultKeyGen(self, secret_key):
        self.mult_keys_generated = secret_key == "secret"

    def EvalSumKeyGen(self, secret_key):
        self.sum_keys_generated = secret_key == "secret"

    def MakeCKKSPackedPlaintext(self, values):
        return _Plaintext(values)

    def Encrypt(self, public_key, plaintext):
        assert public_key == "public"
        return list(plaintext.values)

    def Decrypt(self, secret_key, ciphertext):
        assert secret_key == "secret"
        return _Plaintext(ciphertext)

    def EvalAdd(self, left, right):
        if isinstance(right, (int, float)):
            return [value + right for value in left]
        if isinstance(right, _Plaintext):
            right = right.values
        return [a + b for a, b in zip(left, right)]

    def EvalSub(self, left, right):
        return [a - b for a, b in zip(left, right)]

    def EvalMult(self, left, right):
        if isinstance(right, (int, float)):
            return [value * right for value in left]
        if isinstance(right, _Plaintext):
            right = right.values
        return [a * b for a, b in zip(left, right)]

    def EvalSum(self, ciphertext, count):
        return [sum(ciphertext[:count])]


class _OpenFHE:
    PKE = "PKE"
    KEYSWITCH = "KEYSWITCH"
    LEVELEDSHE = "LEVELEDSHE"
    ADVANCEDSHE = "ADVANCEDSHE"

    def __init__(self):
        self.context = _Context()

    @staticmethod
    def CCParamsCKKSRNS():
        return _Parameters()

    def GenCryptoContext(self, parameters):
        self.parameters = parameters
        return self.context


class OpenFHEDirectCreditApiTest(unittest.TestCase):
    def test_direct_class_runs_credit_operations(self):
        fake_openfhe = _OpenFHE()
        session = OpenFHECreditSession(
            slot_count=3,
            multiplicative_depth=4,
            _openfhe_module=fake_openfhe,
        )
        group = load_prepared_group(FIXTURE)
        self.assertEqual(4, group.slot_count)
        installment = session.encrypt(group.installment)
        payment = session.encrypt(group.payment)
        difference = session.subtract(installment, payment)

        self.assertEqual([160.0, -100.0, 0.0], session.decrypt(difference))
        self.assertEqual(
            [650.0, 610.0, 1010.0],
            session.decrypt(session.add_public_vector(payment, [10, 10, 10])),
        )
        self.assertEqual(
            [80.0, -50.0, 0.0],
            session.decrypt(session.multiply_public_scalar(difference, 0.5)),
        )
        self.assertEqual(60.0, session.decrypt(session.sum(difference)))
        self.assertEqual(20.0, session.decrypt(session.mean(difference)))

        variance = session.variance_components(difference)
        self.assertEqual(60.0, session.decrypt(variance.sum_x))
        self.assertEqual(35600.0, session.decrypt(variance.sum_x2))

        covariance = session.covariance_components(installment, payment)
        self.assertEqual(2300.0, session.decrypt(covariance.sum_x))
        self.assertEqual(2240.0, session.decrypt(covariance.sum_y))
        self.assertEqual(1812000.0, session.decrypt(covariance.sum_xy))

        weights = [1 / 3, 1 / 3, 1 / 3]
        self.assertAlmostEqual(
            20.0,
            session.decrypt(session.weighted_sum(difference, weights)),
        )
        self.assertAlmostEqual(
            21.5,
            session.decrypt(session.risk_score(difference, weights, 1.5)),
        )
        self.assertTrue(fake_openfhe.context.mult_keys_generated)
        self.assertTrue(fake_openfhe.context.sum_keys_generated)

    def test_example_has_no_gateway_or_heir_path(self):
        source = EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("OpenFHECreditSession(", source)
        self.assertIn("session.subtract(", source)
        self.assertNotIn("gateway", source.lower())
        self.assertNotIn("he_client", source)
        self.assertNotIn("heir", source.lower())


if __name__ == "__main__":
    unittest.main()
