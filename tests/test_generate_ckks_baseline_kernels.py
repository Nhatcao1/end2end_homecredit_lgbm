from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "code"
    / "heir"
    / "scripts"
    / "generate_ckks_baseline_kernels.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("generate_ckks_baseline_kernels", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load CKKS baseline generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenerateCkksBaselineKernelsTest(unittest.TestCase):
    def test_generates_once_for_streamed_core_sizes_and_scaled_extended_sources(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "generated"
            manifest = module.generate(
                root,
                slot_count=8,
                ciphertext_degree=16,
                lower=False,
                heir_opt="unused",
                heir_translate="unused",
            )
            self.assertEqual(manifest["requested_ring_dimension"], 16)
            self.assertEqual(manifest["requested_slot_count"], 8)
            self.assertEqual(manifest["core_runtime_value_counts"], [1000, 50000, 1000000])
            labels = [kernel["report_label"] for kernel in manifest["kernels"]]
            self.assertEqual(labels[:2], ["CT+CT", "CT-CT"])
            self.assertIn("CKKS-VAR-01", labels)
            self.assertIn("CKKS-POLY-01", labels)
            add_source = (root / "00_encrypted_add" / "source.mlir").read_text(encoding="utf-8")
            self.assertIn("tensor<8xf64>", add_source)
            self.assertIn("encrypted_add", add_source)


if __name__ == "__main__":
    unittest.main()
