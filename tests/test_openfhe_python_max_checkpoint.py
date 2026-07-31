import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from code.heir.python_api import openfhe_python_max_checkpoint as checkpoint


class _Key:
    def GetKeyTag(self):
        return "test-key"


class _Keys:
    publicKey = _Key()
    secretKey = _Key()


class _Plaintext:
    def __init__(self, values):
        self.values = values

    def SetLength(self, length):
        self.values = self.values[:length]

    def GetRealPackedValue(self):
        return self.values


class _Context:
    def EvalSub(self, left, right):
        return [a - b for a, b in zip(left, right)]

    def Decrypt(self, secret_key, ciphertext):
        del secret_key
        return _Plaintext(ciphertext)

    def GetRingDimension(self):
        return 16

    def SerializeEvalAutomorphismKey(self, path, mode):
        Path(path).write_bytes(str(mode).encode())
        return True


class _Engine:
    def __init__(self, *, valid_count, input_scale, ring_dimension):
        self.valid_count = valid_count
        self.input_scale = input_scale
        self.ring_dimension = ring_dimension
        self.multiplicative_depth = 17
        self._context = _Context()
        self._keys = _Keys()

    def setup(self):
        return None

    def encrypt(self, values):
        return [float(value) / self.input_scale for value in values]

    def eval_max(self, values):
        return [max(values)]


class _OpenFhe:
    BINARY = "binary"

    @staticmethod
    def SerializeToFile(path, value, mode):
        Path(path).write_text(
            json.dumps({"mode": mode, "value": repr(value)}),
            encoding="utf-8",
        )
        return True


class OpenFhePythonMaxCheckpointTest(unittest.TestCase):
    def test_subtract_max_writes_python_checkpoint_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "maximum"
            with (
                patch.object(
                    checkpoint,
                    "OfficialOpenFheMinMax",
                    _Engine,
                ),
                patch.object(checkpoint, "_load_openfhe", return_value=_OpenFhe),
                patch.object(checkpoint, "_package_version", return_value="test"),
            ):
                maximum = checkpoint.OpenFhePythonColumnMaxCheckpoint(
                    input_scale=2048.0,
                    ring_dimension=16,
                )
                result = maximum.run_subtract_max(
                    [800.0, 500.0, 1000.0],
                    [640.0, 600.0, 1000.0],
                    output_dir=root,
                )
                resumed = maximum.load_completed(root)

            self.assertAlmostEqual(160.0, result["maximum"])
            self.assertAlmostEqual(160.0, resumed["maximum"])
            self.assertTrue(resumed["resumed"])
            manifest = json.loads(
                (root / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                "official OpenFHE Python",
                manifest["backend"],
            )
            self.assertFalse(manifest["mid_scheme_switch_resume_supported"])
            self.assertTrue((root / "ciphertexts/maximum.ct").is_file())
            self.assertTrue((root / "public/context.bin").is_file())
            self.assertTrue(
                (root / "public/eval_automorphism.keys").is_file()
            )
            self.assertTrue(
                (root / "client_private/audit_secret.key").is_file()
            )

    def test_load_rejects_tampered_ciphertext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "maximum"
            with (
                patch.object(
                    checkpoint,
                    "OfficialOpenFheMinMax",
                    _Engine,
                ),
                patch.object(checkpoint, "_load_openfhe", return_value=_OpenFhe),
                patch.object(checkpoint, "_package_version", return_value="test"),
            ):
                maximum = checkpoint.OpenFhePythonColumnMaxCheckpoint(
                    input_scale=2048.0,
                    ring_dimension=16,
                )
                maximum.run_subtract_max(
                    [800.0, 500.0],
                    [640.0, 600.0],
                    output_dir=root,
                )
                (root / "ciphertexts/maximum.ct").write_text(
                    "tampered",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    maximum.load_completed(root)


if __name__ == "__main__":
    unittest.main()
