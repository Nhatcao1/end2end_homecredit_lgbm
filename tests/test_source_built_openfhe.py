from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from code.heir.python_api.source_built_openfhe import (
    RUNNER,
    SourceBuiltOpenFheColumnMax,
    _next_power_of_two,
)


class SourceBuiltOpenFheColumnMaxTest(unittest.TestCase):
    def test_runner_derives_subtraction_after_encryption(self):
        self.assertIn("context->EvalSub(leftCt, rightCt)", RUNNER)
        self.assertIn("EvalMaxSchemeSwitching", RUNNER)
        self.assertIn("SerializeToFile(argv[7], derivedCt", RUNNER)
        self.assertNotIn("import openfhe", RUNNER)

    def test_power_of_two_padding(self):
        self.assertEqual(2, _next_power_of_two(1))
        self.assertEqual(128, _next_power_of_two(100))
        self.assertEqual(128, _next_power_of_two(128))

    def test_python_orchestrator_uses_cmake_install_and_real_candidate_padding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "max"
            commands: list[list[str]] = []

            def fake_run(command, _cwd):
                commands.append(command)
                if str(command[0]).endswith("column_max_runner"):
                    Path(command[8]).write_bytes(b"encrypted-max")
                    Path(command[9]).write_text(
                        '{"maximum_normalized":0.25}\n',
                        encoding="utf-8",
                    )
                return 0.0, ""

            with patch(
                "code.heir.python_api.source_built_openfhe.run",
                side_effect=fake_run,
            ):
                result = SourceBuiltOpenFheColumnMax(
                    input_scale=1024,
                    openfhe_dir="/usr/local/lib/OpenFHE",
                ).run_subtract_max(
                    [10, 20, 30],
                    [1, 2, 3],
                    output_dir=root,
                )
            self.assertEqual(256.0, result["maximum"])
            self.assertEqual(1, result["padding_count"])
            self.assertTrue(
                any(
                    "-DOpenFHE_DIR=/usr/local/lib/OpenFHE" in command
                    for command in commands
                )
            )
            resumed = SourceBuiltOpenFheColumnMax(
                input_scale=1024
            ).load_completed(root)
            self.assertEqual(256.0, resumed["maximum"])
            self.assertTrue(resumed["resumed"])


if __name__ == "__main__":
    unittest.main()
