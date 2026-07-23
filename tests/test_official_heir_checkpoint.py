from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.checkpoint import (
    _SerializationTranslateProxy,
    patch_generated_pybind,
)


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


if __name__ == "__main__":
    unittest.main()
