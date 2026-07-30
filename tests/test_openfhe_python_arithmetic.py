import unittest
from unittest.mock import patch

from code.heir.python_api.openfhe_python_arithmetic import (
    OpenFhePythonBinaryColumn,
)


class _Plaintext:
    def __init__(self, values):
        self.values = list(values)

    def SetLength(self, length):
        self.values = self.values[:length]

    def GetRealPackedValue(self):
        return self.values


class _Context:
    def __init__(self, parameters):
        self.parameters = parameters

    def Enable(self, _feature):
        return None

    def KeyGen(self):
        return type(
            "Keys",
            (),
            {"publicKey": "public", "secretKey": "secret"},
        )()

    def EvalMultKeyGen(self, _key):
        return None

    def MakeCKKSPackedPlaintext(self, values):
        return _Plaintext(values)

    def Encrypt(self, _key, plaintext):
        return list(plaintext.values)

    def EvalAdd(self, left, right):
        return [a + b for a, b in zip(left, right)]

    def EvalSub(self, left, right):
        return [a - b for a, b in zip(left, right)]

    def EvalMult(self, left, right):
        return [a * b for a, b in zip(left, right)]

    def Decrypt(self, _key, encrypted):
        return _Plaintext(encrypted)

    def GetRingDimension(self):
        return self.parameters.ring_dimension


class _Parameters:
    def __getattr__(self, name):
        if not name.startswith("Set"):
            raise AttributeError(name)

        def setter(value):
            attribute = {
                "SetRingDim": "ring_dimension",
                "SetBatchSize": "batch_size",
                "SetMultiplicativeDepth": "multiplicative_depth",
            }.get(name, name)
            setattr(self, attribute, value)

        return setter


class _OpenFhe:
    FLEXIBLEAUTO = "FLEXIBLEAUTO"
    HEStd_128_classic = "HEStd_128_classic"
    PKE = "PKE"
    KEYSWITCH = "KEYSWITCH"
    LEVELEDSHE = "LEVELEDSHE"

    @staticmethod
    def CCParamsCKKSRNS():
        return _Parameters()

    @staticmethod
    def GenCryptoContext(parameters):
        return _Context(parameters)


class OpenFhePythonBinaryColumnTest(unittest.TestCase):
    def test_each_operation_runs_the_same_public_lifecycle(self):
        expected = {
            "add": (4.0, 6.0),
            "subtract": (-2.0, -2.0),
            "multiply": (3.0, 8.0),
        }
        with patch(
            "code.heir.python_api.openfhe_python_arithmetic._load_openfhe",
            return_value=_OpenFhe,
        ):
            for operation, wanted in expected.items():
                with self.subTest(operation=operation):
                    program = OpenFhePythonBinaryColumn(
                        operation=operation,
                        width=4,
                        input_scale=16.0,
                        ring_dimension=16,
                    )
                    program.setup()
                    encrypted = program.encrypt([1.0, 2.0], [3.0, 4.0])
                    result = program.eval(encrypted)
                    self.assertEqual(
                        wanted,
                        program.decrypt(result, valid_count=2),
                    )

    def test_range_and_setup_contracts_fail_clearly(self):
        program = OpenFhePythonBinaryColumn(
            operation="add",
            width=4,
            input_scale=8.0,
            ring_dimension=16,
        )
        with self.assertRaisesRegex(RuntimeError, "setup"):
            program.encrypt([1.0], [2.0])

        with patch(
            "code.heir.python_api.openfhe_python_arithmetic._load_openfhe",
            return_value=_OpenFhe,
        ):
            program.setup()
            with self.assertRaisesRegex(ValueError, "normalized range"):
                program.encrypt([5.0], [1.0])


if __name__ == "__main__":
    unittest.main()
