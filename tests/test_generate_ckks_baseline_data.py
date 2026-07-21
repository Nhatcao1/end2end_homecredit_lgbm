from __future__ import annotations

import csv
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
    / "generate_ckks_baseline_data.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("generate_ckks_baseline_data", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load CKKS data generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenerateCkksBaselineDataTest(unittest.TestCase):
    def test_generates_reproducible_decimal_pairs_for_each_workload(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "data"
            manifest = module.generate(root, value_counts=(3,), decimal_places=(2,), base_seed=9)
            self.assertEqual(manifest["value_counts"], [3])
            self.assertEqual(len(manifest["datasets"]["add_sub"]), 1)
            path = root / "add_sub_3_2dp.csv"
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(rows[0]["left"].split(".")[1]), 2)
            self.assertTrue((root / "dataset_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
