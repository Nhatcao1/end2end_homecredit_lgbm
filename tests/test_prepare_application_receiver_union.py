"""Tests for the client-side Home Credit application receiver union."""

from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "code/private_join/scripts/prepare_application_receiver_union.py"


def _module():
    spec = importlib.util.spec_from_file_location("prepare_application_receiver_union", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReceiverUnionTest(unittest.TestCase):
    def test_preserves_train_target_and_blanks_test_target(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train, test, output = root / "train.csv", root / "test.csv", root / "union.csv"
            train.write_text("SK_ID_CURR,TARGET\n1,0\n2,1\n", encoding="utf-8")
            test.write_text("SK_ID_CURR\n3\n4\n", encoding="utf-8")
            result = module.prepare(train, test, output)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(result["rows"]["union"], 4)
            self.assertEqual(rows, [
                {"SK_ID_CURR": "1", "TARGET": "0"},
                {"SK_ID_CURR": "2", "TARGET": "1"},
                {"SK_ID_CURR": "3", "TARGET": ""},
                {"SK_ID_CURR": "4", "TARGET": ""},
            ])

    def test_rejects_train_test_key_collision(self) -> None:
        module = _module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            train, test = root / "train.csv", root / "test.csv"
            train.write_text("SK_ID_CURR,TARGET\n1,0\n", encoding="utf-8")
            test.write_text("SK_ID_CURR\n1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                module.prepare(train, test, root / "union.csv")


if __name__ == "__main__":
    unittest.main()
