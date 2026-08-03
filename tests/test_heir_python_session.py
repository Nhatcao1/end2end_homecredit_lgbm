from pathlib import Path
import unittest

from code.heir_python import HeirCkksSession


class _FakeProgram:
    def __init__(self, operation):
        self.operation = operation
        self.setup_calls = 0
        self.encrypt_calls = 0

    def setup(self):
        self.setup_calls += 1

    def encrypt(self, values):
        self.encrypt_calls += 1
        return tuple(values)

    def eval(self, encrypted):
        total = sum(encrypted)
        return total if self.operation == "sum" else total / len(encrypted)

    def decrypt(self, encrypted):
        return encrypted


class HeirPythonSessionTest(unittest.TestCase):
    def setUp(self):
        self.programs = {}

        def factory(**options):
            program = _FakeProgram(options["operation"])
            self.programs[options["operation"]] = program
            return program

        self.session = HeirCkksSession(
            width=8,
            valid_count=3,
            _program_factory=factory,
        )

    def test_sum_and_mean_are_encrypted_until_explicit_decrypt(self):
        self.session.setup()
        encrypted = self.session.encrypt([160.0, -100.0, 0.0])
        encrypted_sum = self.session.sum(encrypted)
        encrypted_mean = self.session.mean(encrypted)

        self.assertEqual(60.0, self.session.decrypt(encrypted_sum))
        self.assertEqual(20.0, self.session.decrypt(encrypted_mean))
        self.assertFalse(
            self.session.uses_one_ciphertext_for_both_aggregates
        )
        self.assertEqual(1, self.programs["sum"].encrypt_calls)
        self.assertEqual(1, self.programs["mean"].encrypt_calls)

    def test_setup_is_required_and_idempotent(self):
        with self.assertRaisesRegex(RuntimeError, "call setup"):
            self.session.encrypt([1.0, 2.0, 3.0])
        self.session.setup()
        self.session.setup()
        self.assertEqual(1, self.programs["sum"].setup_calls)
        self.assertEqual(1, self.programs["mean"].setup_calls)

    def test_rejects_foreign_ciphertext(self):
        self.session.setup()
        other = HeirCkksSession(
            width=8,
            valid_count=3,
            _program_factory=lambda **options: _FakeProgram(
                options["operation"]
            ),
        )
        other.setup()
        foreign = other.encrypt([1.0, 2.0, 3.0])
        with self.assertRaisesRegex(ValueError, "another session"):
            self.session.sum(foreign)

    def test_example_uses_only_session_api(self):
        root = Path(__file__).resolve().parents[1]
        source = (
            root / "code/heir_python/example_sum_mean.py"
        ).read_text(encoding="utf-8")
        self.assertIn("HeirCkksSession", source)
        self.assertIn("session.sum(encrypted_values)", source)
        self.assertIn("session.mean(encrypted_values)", source)
        self.assertNotIn("import openfhe", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
