from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from code.heir_python.aggregates import (
    compile_mean,
    compile_sum,
    compile_variance,
)


class _CompiledProgram:
    compilation_result = SimpleNamespace(arg_enc_funcs={"arg0": object()})

    def setup(self):
        return None

    def encrypt_arg0(self, packed):
        return packed

    def eval(self, encrypted):
        return encrypted

    def decrypt_result(self, encrypted):
        return encrypted[0]


class HeirPythonPackageTest(unittest.TestCase):
    def test_compile_wrappers_are_owned_by_new_package(self):
        calls = []

        def fake_compile(**options):
            calls.append(options)
            return _CompiledProgram()

        fake_heir = ModuleType("heir")
        fake_heir.compile = fake_compile
        with patch.dict("sys.modules", {"heir": fake_heir}):
            programs = [
                compile_sum(width=8, valid_count=3),
                compile_mean(width=8, valid_count=3),
                compile_variance(width=8, valid_count=3),
            ]

        self.assertEqual(["ckks", "ckks", "ckks"], [c["scheme"] for c in calls])
        self.assertIn("@fixed_count_sum", programs[0].mlir)
        self.assertIn("@fixed_count_mean", programs[1].mlir)
        self.assertIn("@fixed_count_variance", programs[2].mlir)

    def test_new_package_has_no_legacy_heir_dependency(self):
        package = Path(__file__).resolve().parents[1] / "code/heir_python"
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in package.glob("*.py")
        )
        self.assertNotIn("code.heir.", sources)
        self.assertNotIn("subprocess", sources)
        self.assertNotIn("import openfhe", sources)


if __name__ == "__main__":
    unittest.main()
