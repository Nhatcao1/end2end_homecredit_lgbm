from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.official_openfhe_minmax import (
    EncryptedMinMax,
    OfficialOpenFheMinMax,
    public_power_of_two_scale,
)


class FakeParameters:
    def __getattr__(self, _):
        return lambda *args: None


class FakePlaintext:
    def __init__(self, values):
        self.values = values

    def SetLength(self, _):
        return None

    def GetRealPackedValue(self):
        return self.values


class FakeContext:
    def __init__(self):
        self.enabled = []
        self.encoded = None

    def Enable(self, feature):
        self.enabled.append(feature)

    def KeyGen(self):
        return SimpleNamespace(publicKey="public", secretKey="secret")

    def EvalSchemeSwitchingSetup(self, params):
        return "lwe-secret"

    def EvalSchemeSwitchingKeyGen(self, keys, lwe_secret):
        return None

    def EvalCompareSwitchPrecompute(self, *args):
        self.precompute = args

    def MakeCKKSPackedPlaintext(self, values):
        self.encoded = values
        return values

    def Encrypt(self, public_key, plaintext):
        return ("input-ct", plaintext)

    def EvalMinSchemeSwitching(self, *args):
        return ["minimum-ct", "argmin-ct"]

    def EvalMaxSchemeSwitching(self, *args):
        return ["maximum-ct", "argmax-ct"]

    def Decrypt(self, secret_key, ciphertext):
        value = -0.25 if ciphertext == "minimum-ct" else 0.25
        return FakePlaintext([value])


class FakeOpenFhe:
    PKE = "PKE"
    KEYSWITCH = "KEYSWITCH"
    LEVELEDSHE = "LEVELEDSHE"
    ADVANCEDSHE = "ADVANCEDSHE"
    SCHEMESWITCH = "SCHEMESWITCH"
    FHE = "FHE"
    FLEXIBLEAUTO = "FLEXIBLEAUTO"
    HEStd_NotSet = "HEStd_NotSet"
    UNIFORM_TERNARY = "UNIFORM_TERNARY"
    HYBRID = "HYBRID"
    TOY = "TOY"

    def __init__(self):
        self.context = FakeContext()

    def CCParamsCKKSRNS(self):
        return FakeParameters()

    def SchSwchParams(self):
        return FakeParameters()

    def GenCryptoContext(self, parameters):
        return self.context


class OfficialOpenFheMinMaxTest(unittest.TestCase):
    def test_public_scale_is_power_of_two_and_strictly_contains_values(self):
        scale = public_power_of_two_scale([160.0, -100.0, 250.0])
        self.assertEqual(512.0, scale)
        self.assertTrue(all(-0.5 < value / scale <= 0.5 for value in [160, -100, 250]))

    def test_python_wrapper_keeps_results_encrypted_until_decrypt(self):
        fake = FakeOpenFhe()
        target = "code.heir.python_api.official_openfhe_minmax._load_openfhe"
        with patch(target, return_value=fake):
            program = OfficialOpenFheMinMax(
                valid_count=3,
                input_scale=512.0,
                ring_dimension=16,
            )
            program.setup()
        input_ct = program.encrypt([160.0, -100.0, 250.0])
        encrypted = program.eval(input_ct)

        self.assertEqual(
            [160.0 / 512.0, -100.0 / 512.0, 250.0 / 512.0, 160.0 / 512.0],
            fake.context.encoded,
        )
        self.assertEqual(
            EncryptedMinMax("minimum-ct", "maximum-ct"),
            encrypted,
        )
        self.assertEqual((-128.0, 128.0), program.decrypt(encrypted))
        self.assertEqual((1, 1, True), fake.context.precompute)


if __name__ == "__main__":
    unittest.main()
