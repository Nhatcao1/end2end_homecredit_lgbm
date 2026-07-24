from pathlib import Path
import hashlib
import json
import tempfile
import unittest


from code.heir.python_api.source_built_session import (
    RUNNER,
    SourceBuiltCkksSession,
)


class SourceBuiltSimpleSessionTest(unittest.TestCase):
    def test_runner_serializes_and_reloads_context_and_ciphertexts(self):
        self.assertIn("Serial::SerializeToFile", RUNNER)
        self.assertIn("Serial::DeserializeFromFile", RUNNER)
        self.assertIn('"public/context.bin"', RUNNER)
        self.assertIn('"client_private/audit_secret.key"', RUNNER)
        self.assertIn("context->EvalSub(left, right)", RUNNER)
        self.assertIn("context->EvalSum(masked, width)", RUNNER)
        self.assertIn("context->EvalMinSchemeSwitching", RUNNER)
        self.assertIn("context->EvalMaxSchemeSwitching", RUNNER)

    def test_load_validates_saved_context_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = root / "public/context.bin"
            context.parent.mkdir(parents=True)
            context.write_bytes(b"fake-context")
            runner = root / "runner/build/simple_ckks_session_runner"
            runner.parent.mkdir(parents=True)
            runner.write_bytes(b"fake-runner")
            manifest = {
                "format": "source-built-simple-ckks-session-v1",
                "backend": "source-built OpenFHE C++",
                "width": 8,
                "ring_dimension": 16384,
                "input_scale": 4096.0,
                "openfhe_dir": "/usr/local/lib/OpenFHE",
                "context_sha256": hashlib.sha256(
                    context.read_bytes()
                ).hexdigest(),
                "columns": {},
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            session = SourceBuiltCkksSession.load(root)
            self.assertEqual(8, session.width)
            self.assertEqual(4096.0, session.input_scale)

            context.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "context hash"):
                SourceBuiltCkksSession.load(root)


if __name__ == "__main__":
    unittest.main()
