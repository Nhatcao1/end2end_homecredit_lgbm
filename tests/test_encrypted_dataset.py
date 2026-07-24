from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.encrypted_dataset import EncryptedDataset, FORMAT


class FakeModule:
    @staticmethod
    def _write(path):
        Path(path).write_bytes(b"artifact")


for name, callable_ in {
    "__checkpoint_save_context": lambda value, path: FakeModule._write(path),
    "__checkpoint_save_public_key": lambda value, path: FakeModule._write(path),
    "__checkpoint_save_private_key": lambda value, path: FakeModule._write(path),
    "__checkpoint_save_ciphertexts": lambda value, path: FakeModule._write(path),
    "__checkpoint_load_context": lambda path: "loaded-context",
    "__checkpoint_load_public_key": lambda path: "loaded-public",
    "__checkpoint_load_private_key": lambda path: "loaded-secret",
    "__checkpoint_load_ciphertexts": lambda path: [Path(path).name],
}.items():
    setattr(FakeModule, name, staticmethod(callable_))


def fake_program():
    return SimpleNamespace(
        operation="subtract",
        width=8,
        input_scale=2048.0,
        output_scale=2048.0,
        mlir="func.func @encrypted_column_subtract_8",
        _is_setup=True,
        eval=lambda encrypted: ("evaluated", encrypted),
        decrypt=lambda encrypted, valid_count: tuple(
            float(index) for index in range(valid_count)
        ),
        _program=SimpleNamespace(
            crypto_context="context",
            keypair=SimpleNamespace(
                publicKey="public",
                secretKey="secret",
            ),
            compilation_result=SimpleNamespace(module=FakeModule()),
        ),
    )


class EncryptedDatasetTest(unittest.TestCase):
    def test_save_uses_named_columns_and_optional_private_key(self):
        dataset = EncryptedDataset(
            program=fake_program(),
            columns={
                "AMT_INSTALMENT": ["installment-ct"],
                "AMT_PAYMENT": ["payment-ct"],
            },
            valid_count=3,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dataset"
            manifest = dataset.save(root, include_audit_key=True)

            self.assertEqual(FORMAT, manifest["format"])
            self.assertEqual(
                ["AMT_INSTALMENT", "AMT_PAYMENT"],
                manifest["column_order"],
            )
            self.assertEqual("subtract", manifest["binary_operation"])
            self.assertEqual(3, manifest["valid_count"])
            self.assertTrue(
                (root / "public/columns/column_000000.ct").is_file()
            )
            self.assertTrue(
                (root / "client_private/audit_secret.key").is_file()
            )

    def test_load_validates_and_restores_named_columns(self):
        dataset = EncryptedDataset(
            program=fake_program(),
            columns={"left": ["left-ct"], "right": ["right-ct"]},
            valid_count=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dataset"
            dataset.save(root, include_audit_key=True)
            restored_program = fake_program()
            restored_program._is_setup = False

            with patch(
                "code.heir.python_api.encrypted_dataset."
                "compile_checkpointable_binary_column",
                return_value=restored_program,
            ):
                restored = EncryptedDataset.load(root, for_audit=True)

            self.assertEqual(("left", "right"), restored.column_names)
            self.assertEqual("loaded-context", restored.program._program.crypto_context)
            self.assertEqual(
                "loaded-secret",
                restored.program._program.keypair.secretKey,
            )

    def test_evaluator_load_rejects_client_decryption(self):
        dataset = EncryptedDataset(
            program=fake_program(),
            columns={"left": ["left-ct"], "right": ["right-ct"]},
            valid_count=2,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dataset"
            dataset.save(root)
            restored_program = fake_program()
            restored_program._is_setup = False
            with patch(
                "code.heir.python_api.encrypted_dataset."
                "compile_checkpointable_binary_column",
                return_value=restored_program,
            ):
                restored = EncryptedDataset.load(root)

            with self.assertRaisesRegex(RuntimeError, "no audit secret"):
                restored.decrypt_result(["result"])

    def test_evaluate_uses_named_column_order_without_decrypting(self):
        dataset = EncryptedDataset(
            program=fake_program(),
            columns={"installment": "installment-ct", "payment": "payment-ct"},
            valid_count=2,
        )
        with patch(
            "code.heir.python_api.encrypted_dataset._wrap_binary_inputs",
            return_value=("wrapped-installment", "wrapped-payment"),
        ):
            result = dataset.evaluate("installment", "payment")

        self.assertEqual(
            (
                "evaluated",
                ("wrapped-installment", "wrapped-payment"),
            ),
            result,
        )


if __name__ == "__main__":
    unittest.main()
