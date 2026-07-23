from pathlib import Path
import importlib.util
import unittest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "code/heir/scripts/run_official_python_payment_diff_e2e.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "run_official_python_payment_diff_e2e",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfficialPythonPaymentDiffE2ETest(unittest.TestCase):
    def test_benchmark_has_no_embedded_build_runner(self) -> None:
        module = load_module()
        self.assertFalse(hasattr(module, "CMAKE"))
        self.assertFalse(hasattr(module, "RUNNER"))
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("OfficialPaymentDiffGroupStatistics", source)
        self.assertIn("OfficialOpenFhePaymentDiffMax", source)
        self.assertNotIn("subprocess", source)

    def test_scale_strictly_contains_both_parent_columns(self) -> None:
        module = load_module()

        class Group:
            payment = (640.0, 600.0)
            installment = (800.0, 500.0)

        scale = module._input_scale([Group()])
        self.assertTrue(
            all(
                -0.5 < value / scale <= 0.5
                for value in [640.0, 600.0, 800.0, 500.0]
            )
        )


if __name__ == "__main__":
    unittest.main()
