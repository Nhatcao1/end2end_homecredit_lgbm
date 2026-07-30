from pathlib import Path
import unittest

from code.heir.python_api.backend_policy import (
    backend_manifest,
    calculation_route,
    require_backend,
)


ROOT = Path(__file__).resolve().parents[1]


class BackendPolicyTest(unittest.TestCase):
    def test_heir_is_preferred_for_supported_calculations(self):
        for operation in (
            "add",
            "subtract",
            "multiply",
            "sum",
            "count",
            "mean",
            "square_sum",
            "variance",
            "weighted_sum",
            "dot_product",
            "polynomial",
        ):
            self.assertEqual("heir", calculation_route(operation).backend)

    def test_openfhe_python_is_limited_to_scheme_switching(self):
        for operation in ("minimum", "maximum", "comparison"):
            self.assertEqual(
                "openfhe-python",
                calculation_route(operation).backend,
            )

    def test_aliases_and_backend_guard(self):
        self.assertEqual("maximum", calculation_route("max").operation)
        self.assertEqual("variance", calculation_route("var").operation)
        require_backend("sum", "heir")
        with self.assertRaisesRegex(RuntimeError, "must use"):
            require_backend("max", "heir")

    def test_manifest_is_json_serializable_shape(self):
        manifest = backend_manifest("subtract", "mean", "max")
        self.assertEqual(
            {"subtract", "mean", "maximum"},
            set(manifest),
        )

    def test_canonical_benchmarks_import_policy(self):
        for relative in (
            "code/heir/scripts/run_official_python_payment_diff_e2e.py",
            "code/heir/scripts/run_official_python_var_minmax_trial.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("backend_manifest", source)
            self.assertIn("require_backend", source)

    def test_setup_script_pins_both_python_packages(self):
        source = (
            ROOT / "scripts/setup_heir_openfhe_python.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("heir_py[python,openfhe]", source)
        self.assertIn("openfhe==", source)
        self.assertIn("1.5.1.0.24.4", source)
        self.assertIn("pandas>=2.2,<4", source)
        self.assertIn(".venv-heir-python", source)


if __name__ == "__main__":
    unittest.main()
