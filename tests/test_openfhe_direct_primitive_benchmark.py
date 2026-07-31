from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.primitive_benchmark import (
    OPERATIONS,
    run_primitive_matrix,
)
from tests.test_openfhe_direct_credit_api import _OpenFHE


class OpenFHEDirectPrimitiveBenchmarkTest(unittest.TestCase):
    def test_matrix_uses_session_api_for_every_operation_and_size(self):
        session_count = 0

        def factory(**kwargs):
            nonlocal session_count
            session_count += 1
            return OpenFHECreditSession(
                **kwargs,
                _openfhe_module=_OpenFHE(),
            )

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            batches = root / "prepared" / "batches"
            batches.mkdir(parents=True)
            (batches / "batch_000000.csv").write_text(
                "AMT_PAYMENT,AMT_INSTALMENT,valid\n"
                "1,11,1\n"
                "2,22,1\n"
                "3,33,1\n"
                "4,44,1\n"
                "5,55,1\n",
                encoding="utf-8",
            )

            result = run_primitive_matrix(
                prepared_dir=root / "prepared",
                value_counts=[3, 5],
                operations=OPERATIONS,
                slot_count=4,
                repetitions=2,
                multiplicative_depth=4,
                scaling_mod_size=50,
                first_mod_size=60,
                ring_dimension=0,
                absolute_tolerance=1e-9,
                relative_tolerance=1e-9,
                output_dir=root / "result",
                overwrite=False,
                _session_factory=factory,
            )

            self.assertEqual("PASS", result["status"])
            self.assertFalse(result["heir_compiler_used"])
            self.assertEqual(2, session_count)
            self.assertEqual(
                ["CT+CT", "CT-CT", "CT×CT", "CT+PT", "CT×PT"],
                result["operations"],
            )
            self.assertTrue((root / "result/rows_3/results.csv").is_file())
            self.assertTrue((root / "result/rows_5/REPORT.md").is_file())

    def test_source_contains_no_heir_or_direct_openfhe_operations(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "code/openfhe_direct/primitive_benchmark.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OpenFHECreditSession", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("heir-opt", source)
        self.assertNotIn("EvalAdd", source)
        self.assertNotIn("EvalMult", source)


if __name__ == "__main__":
    unittest.main()
