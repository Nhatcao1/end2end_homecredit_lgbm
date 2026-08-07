import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from code.openfhe_direct import OpenFHEBgvSession
from code.openfhe_direct.benchmarks.synthetic_vnd.generate_dataset import (
    generate_dataset,
)
from code.openfhe_direct.benchmarks.synthetic_vnd.sum import (
    packed_plaintext_modulus,
    run_sum_matrix,
)


class _BgvParameters:
    def SetPlaintextModulus(self, value):
        self.plaintext_modulus = value

    def SetMultiplicativeDepth(self, value):
        self.depth = value

    def SetScalingTechnique(self, value):
        self.scaling_technique = value

    def SetScalingModSize(self, value):
        self.scaling_mod_size = value

    def SetFirstModSize(self, value):
        self.first_mod_size = value

    def SetBatchSize(self, value):
        self.batch = value

    def SetRingDim(self, value):
        self.ring = value


class _BgvPlaintext:
    def __init__(self, values):
        self.values = list(values)

    def SetLength(self, length):
        self.values = self.values[:length]

    def GetPackedValue(self):
        return self.values


class _BgvContext:
    def Enable(self, feature):
        del feature

    def KeyGen(self):
        return SimpleNamespace(publicKey="public", secretKey="secret")

    def EvalSumKeyGen(self, secret_key):
        self.sum_key = secret_key

    def EvalMultKeyGen(self, secret_key):
        self.mult_key = secret_key

    def MakePackedPlaintext(self, values):
        return _BgvPlaintext(values)

    def Encrypt(self, public_key, plaintext):
        assert public_key == "public"
        return list(plaintext.values)

    def EvalAdd(self, left, right):
        return [a + b for a, b in zip(left, right)]

    def EvalMult(self, left, right):
        return [a * b for a, b in zip(left, right)]

    def EvalSum(self, ciphertext, count):
        return [sum(ciphertext[:count])]

    def Decrypt(self, secret_key, ciphertext):
        assert secret_key == "secret"
        return _BgvPlaintext(ciphertext)


class _BgvOpenFHE:
    PKE = "PKE"
    KEYSWITCH = "KEYSWITCH"
    LEVELEDSHE = "LEVELEDSHE"
    ADVANCEDSHE = "ADVANCEDSHE"
    FIXEDMANUAL = "FIXEDMANUAL"

    def __init__(self):
        self.context = _BgvContext()

    @staticmethod
    def CCParamsBGVRNS():
        return _BgvParameters()

    def GenCryptoContext(self, parameters):
        self.parameters = parameters
        return self.context


class OpenFHEDirectSyntheticVndSumTest(unittest.TestCase):
    def test_generator_writes_prefix_consistent_random_vectors(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            result = generate_dataset(
                output_dir=root,
                value_counts=[2, 5],
                minimum_value=100_000,
                maximum_value=20_000_000,
                seed=123,
                overwrite=False,
            )

            def rows(path):
                with path.open("r", encoding="utf-8", newline="") as handle:
                    return list(csv.DictReader(handle))

            small = rows(root / "vnd_values_2.csv")
            large = rows(root / "vnd_values_5.csv")
            self.assertEqual(small, large[:2])
            self.assertTrue(result["prefix_consistent"])
            generated = {int(row["VALUE"]) for row in large}
            self.assertGreater(len(generated), 2)
            self.assertTrue(
                all(100_000 <= value <= 20_000_000 for value in generated)
            )

    def test_bgv_sum_reports_latency_and_exact_total(self):
        class NumPyDouble:
            int64 = int

            @staticmethod
            def asarray(values, dtype):
                del dtype
                return list(values)

            @staticmethod
            def sum(values, dtype):
                del dtype
                return sum(values)

        fake_modules = []

        def factory(**kwargs):
            fake = _BgvOpenFHE()
            fake_modules.append(fake)
            return OpenFHEBgvSession(
                **kwargs,
                _openfhe_module=fake,
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            generate_dataset(
                output_dir=data,
                value_counts=[5],
                minimum_value=100_000,
                maximum_value=20_000_000,
                seed=123,
                overwrite=False,
            )
            with patch.dict("sys.modules", {"numpy": NumPyDouble()}):
                result = run_sum_matrix(
                    dataset_dir=data,
                    value_counts=[5],
                    slot_count=8,
                    repetitions=2,
                    multiplicative_depth=0,
                    plaintext_modulus_bits=40,
                    ring_dimension=8,
                    output_dir=root / "result",
                    overwrite=False,
                    _session_factory=factory,
                )

            self.assertEqual("PASS", result["status"])
            report = (root / "result/values_5/REPORT.md").read_text()
            self.assertIn("BGV SUM", report)
            self.assertIn("Plaintext vector SUM", report)
            self.assertIn("Absolute error (VND)", report)
            self.assertNotIn("Expected-result range", report)
            self.assertEqual(
                "FIXEDMANUAL",
                fake_modules[0].parameters.scaling_technique,
            )
            self.assertEqual(59, fake_modules[0].parameters.scaling_mod_size)
            self.assertEqual(60, fake_modules[0].parameters.first_mod_size)

    def test_bit_count_selects_a_packed_plaintext_prime(self):
        modulus = packed_plaintext_modulus(40, 16_384)
        self.assertLessEqual(modulus.bit_length(), 40)
        self.assertEqual(0, (modulus - 1) % (2 * 16_384))
        self.assertGreater(modulus // 2, 40_000_000_000)

    def test_bgv_session_exposes_ciphertext_multiply_when_depth_is_available(self):
        fake = _BgvOpenFHE()
        session = OpenFHEBgvSession(
            slot_count=2,
            plaintext_modulus=1_000_003,
            multiplicative_depth=1,
            ring_dimension=8,
            _openfhe_module=fake,
        )
        evaluator = session.evaluator_view()
        left = session.encrypt([20, 30])
        right = session.encrypt([4, 5])

        observed = session.decrypt(evaluator.multiply(left, right))

        self.assertEqual([80, 150], observed)
        self.assertEqual("secret", fake.context.mult_key)

    def test_benchmark_calls_session_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "code/openfhe_direct/benchmarks/synthetic_vnd/sum.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHEBgvSession", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("EvalSum", source)
        self.assertNotIn("heir-opt", source)
        self.assertNotIn("PAYMENT_DIFF", source)


if __name__ == "__main__":
    unittest.main()
