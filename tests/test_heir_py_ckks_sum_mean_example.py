import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from code.heir.python_api.official_ckks_aggregates import (
    _pack,
    compile_max,
    compile_mean,
    compile_sum,
)


class HeirPyCkksAggregateApiTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy not installed")
    def test_packing_keeps_real_values_and_zero_pads(self) -> None:
        self.assertEqual(
            [160.0, -100.0, 0.0, 0.0],
            _pack([160.0, -100.0, 0.0], width=4, valid_count=3).tolist(),
        )

    def test_packing_rejects_wrong_public_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "compiled for 3 real values"):
            _pack([1.0, 2.0], width=4, valid_count=3)

    def test_factories_call_official_compile_with_single_result_mlir(self) -> None:
        calls = []

        def fake_compile(**kwargs):
            calls.append(kwargs)
            return object()

        target = (
            "code.heir.python_api.official_ckks_aggregates."
            "_load_official_heir_compile"
        )
        with patch(target, return_value=fake_compile):
            sum_program = compile_sum(width=8, valid_count=3)
            mean_program = compile_mean(width=8, valid_count=3)

        self.assertEqual(["ckks", "ckks"], [call["scheme"] for call in calls])
        self.assertIn("func.func @fixed_count_sum", sum_program.mlir)
        self.assertIn("func.func @fixed_count_mean", mean_program.mlir)
        self.assertNotIn("-> (f64, f64)", sum_program.mlir + mean_program.mlir)

    def test_exact_max_is_not_faked_as_ckks_arithmetic(self) -> None:
        with self.assertRaisesRegex(NotImplementedError, "CKKS-to-FHEW"):
            compile_max(width=8, valid_count=3)

    def test_encrypt_uses_compiled_argument_name_not_source_ssa_name(self) -> None:
        encrypted = object()

        class FakeOfficialProgram:
            compilation_result = SimpleNamespace(
                arg_enc_funcs={"arg0": object()},
            )

            def setup(self):
                return None

            def encrypt_arg0(self, packed):
                self.packed = packed
                return encrypted

        target = (
            "code.heir.python_api.official_ckks_aggregates."
            "_load_official_heir_compile"
        )
        pack_target = "code.heir.python_api.official_ckks_aggregates._pack"
        with patch(target, return_value=lambda **_: FakeOfficialProgram()):
            program = compile_sum(width=8, valid_count=3)
        program.setup()
        with patch(pack_target, return_value="packed-values"):
            result = program.encrypt([1.0, 2.0, 3.0])

        self.assertIs(encrypted, result)
        self.assertEqual("packed-values", program._program.packed)

    def test_example_uses_application_api_and_explicit_ciphertexts(self) -> None:
        source = (
            ROOT / "code/heir/examples/heir_py_ckks_sum_mean.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from code.heir.python_api import compile_mean, compile_sum", source)
        self.assertIn("encrypted_sum = sum_program.eval(encrypted_sum_input)", source)
        self.assertIn("encrypted_mean = mean_program.eval(encrypted_mean_input)", source)
        self.assertIn("sum_program.decrypt(encrypted_sum)", source)
        self.assertNotIn("return encrypted_sum, encrypted_mean", source)
        self.assertNotIn("subprocess.", source)


if __name__ == "__main__":
    unittest.main()
