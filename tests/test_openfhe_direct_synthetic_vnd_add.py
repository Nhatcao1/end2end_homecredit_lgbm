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
from code.openfhe_direct.benchmarks.synthetic_vnd.add import (
    run_add_matrix,
)


class _BgvParameters:
    def SetPlaintextModulus(self, value):
        self.plaintext_modulus = value

    def SetMultiplicativeDepth(self, value):
        self.depth = value

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

    def MakePackedPlaintext(self, values):
        return _BgvPlaintext(values)

    def Encrypt(self, public_key, plaintext):
        assert public_key == "public"
        return list(plaintext.values)

    def EvalAdd(self, left, right):
        return [a + b for a, b in zip(left, right)]

    def Decrypt(self, secret_key, ciphertext):
        assert secret_key == "secret"
        return _BgvPlaintext(ciphertext)


class _BgvOpenFHE:
    PKE = "PKE"
    KEYSWITCH = "KEYSWITCH"
    LEVELEDSHE = "LEVELEDSHE"

    def __init__(self):
        self.context = _BgvContext()

    @staticmethod
    def CCParamsBGVRNS():
        return _BgvParameters()

    def GenCryptoContext(self, parameters):
        self.parameters = parameters
        return self.context


class OpenFHEDirectSyntheticVndAddTest(unittest.TestCase):
    def test_generator_is_prefix_consistent_and_within_vnd_range(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            result = generate_dataset(
                output_dir=root,
                value_counts=[2, 5],
                minimum_value=100_000,
                maximum_value=200_000_000,
                seed=123,
                overwrite=False,
            )

            def rows(path):
                with path.open("r", encoding="utf-8", newline="") as handle:
                    return list(csv.DictReader(handle))

            small = rows(root / "vnd_pairs_2.csv")
            large = rows(root / "vnd_pairs_5.csv")
            self.assertEqual(small, large[:2])
            self.assertTrue(result["prefix_consistent"])
            generated_values = {
                int(row[column])
                for row in large
                for column in ("LEFT_VALUE", "RIGHT_VALUE")
            }
            self.assertGreater(len(generated_values), 2)
            for row in large:
                self.assertGreaterEqual(int(row["LEFT_VALUE"]), 100_000)
                self.assertLessEqual(int(row["RIGHT_VALUE"]), 200_000_000)

    def test_ct_add_reports_magnitude_latency_and_accuracy(self):
        class Array(list):
            def tolist(self):
                return list(self)

        class NumPyDouble:
            int64 = int

            @staticmethod
            def asarray(values, dtype):
                del dtype
                return Array(values)

            @staticmethod
            def add(left, right):
                return Array(a + b for a, b in zip(left, right))

        def factory(**kwargs):
            return OpenFHEBgvSession(
                **kwargs,
                _openfhe_module=_BgvOpenFHE(),
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            generate_dataset(
                output_dir=data,
                value_counts=[5],
                minimum_value=100_000,
                maximum_value=200_000_000,
                seed=123,
                overwrite=False,
            )
            with patch.dict("sys.modules", {"numpy": NumPyDouble()}):
                result = run_add_matrix(
                    dataset_dir=data,
                    value_counts=[5],
                    slot_count=4,
                    repetitions=2,
                    multiplicative_depth=1,
                    plaintext_modulus=1_000_112_129,
                    ring_dimension=0,
                    output_dir=root / "result",
                    overwrite=False,
                    _session_factory=factory,
                )

            self.assertEqual("PASS", result["status"])
            report = (root / "result/values_5/REPORT.md").read_text()
            self.assertIn("Vector length", report)
            self.assertNotIn("Rows:", report)
            self.assertIn("BGV", report)
            self.assertIn("CT+CT", report)
            self.assertIn("numpy.add(int64)", report)
            self.assertIn("Expected-result range", report)
            self.assertIn("MAE", report)
            self.assertIn("HE online", report)

    def test_benchmark_calls_session_only(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "code/openfhe_direct/benchmarks/synthetic_vnd/add.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHEBgvSession", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("EvalAdd", source)
        self.assertNotIn("heir-opt", source)
        self.assertNotIn("PAYMENT_DIFF", source)


if __name__ == "__main__":
    unittest.main()
