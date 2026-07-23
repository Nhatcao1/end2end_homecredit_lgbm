from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.checkpoint import (
    _SerializationTranslateProxy,
    patch_generated_pybind,
    save_binary_column_checkpoint,
)
from types import SimpleNamespace


MINIMAL_PYBIND = """\
#include <pybind11/pybind11.h>
#include "fixed_count_sum.h"
void bind_common(py::module &m) {}
PYBIND11_MODULE(_heir_fixed_count_sum, m) {
  bind_common(m);
}
"""


class _FakeTranslate:
    def run_binary(self, *, input, options):
        output = Path(str(options[options.index("-o") + 1]))
        output.write_text(MINIMAL_PYBIND, encoding="utf-8")
        return "translated"


class OfficialHeirCheckpointTest(unittest.TestCase):
    def test_patch_adds_native_serializers_once(self) -> None:
        patched = patch_generated_pybind(MINIMAL_PYBIND)
        self.assertIn('"cryptocontext-ser.h"', patched)
        self.assertIn("SerializeEvalSumKey", patched)
        self.assertIn("SerializeEvalMultKey", patched)
        self.assertIn('m.def("__checkpoint_save_context"', patched)
        self.assertIn('m.def("__checkpoint_load_ciphertexts"', patched)
        self.assertEqual(patched, patch_generated_pybind(patched))

    def test_translate_proxy_patches_only_pybind_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bindings.cpp"
            proxy = _SerializationTranslateProxy(_FakeTranslate())
            result = proxy.run_binary(
                input="module",
                options=["--emit-openfhe-pke-pybind", "-o", output],
            )
            self.assertEqual("translated", result)
            self.assertIn(
                "__checkpoint_save_context",
                output.read_text(encoding="utf-8"),
            )

    def test_invalid_generated_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "PYBIND11_MODULE"):
            patch_generated_pybind("not a generated binding")

    def test_binary_column_checkpoint_has_no_business_feature_name(self):
        class FakeModule:
            @staticmethod
            def _write(path):
                Path(path).write_bytes(b"artifact")

        for name in (
            "__checkpoint_save_context",
            "__checkpoint_save_public_key",
            "__checkpoint_save_private_key",
            "__checkpoint_save_ciphertexts",
        ):
            setattr(
                FakeModule,
                name,
                staticmethod(lambda value, path: FakeModule._write(path)),
            )

        program = SimpleNamespace(
            operation="subtract",
            width=4,
            input_scale=8.0,
            output_scale=8.0,
            mlir="func.func @generic",
            _is_setup=True,
            _program=SimpleNamespace(
                crypto_context="context",
                keypair=SimpleNamespace(
                    publicKey="public",
                    secretKey="secret",
                ),
                compilation_result=SimpleNamespace(module=FakeModule()),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            manifest = save_binary_column_checkpoint(
                program,
                encrypted_columns=(["left"], ["right"]),
                result_ciphertext=["result"],
                valid_count=2,
                checkpoint_dir=Path(directory),
            )
            public_names = {
                path.name
                for path in (Path(directory) / "public").iterdir()
            }

        self.assertEqual("binary_column", manifest["operation"])
        self.assertEqual("subtract", manifest["binary_operation"])
        self.assertEqual(
            {"context.bin", "public.key", "left.ct", "right.ct", "result.ct"},
            public_names,
        )
        self.assertNotIn("PAYMENT_DIFF", str(manifest))


if __name__ == "__main__":
    unittest.main()
