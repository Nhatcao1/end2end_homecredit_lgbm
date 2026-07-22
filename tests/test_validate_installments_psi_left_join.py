from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "code"
    / "private_join"
    / "scripts"
    / "validate_installments_psi_left_join.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("validate_installments_psi_left_join", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load PSI left-join validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


class ValidateInstallmentsPsiLeftJoinTest(unittest.TestCase):
    def test_matches_plaintext_receiver_left_join_without_exposing_target(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            train, test, installments = root / "train.csv", root / "test.csv", root / "installments.csv"
            write_csv(train, ["SK_ID_CURR", "TARGET"], [{"SK_ID_CURR": "101", "TARGET": 0}, {"SK_ID_CURR": "102", "TARGET": 1}])
            write_csv(test, ["SK_ID_CURR"], [{"SK_ID_CURR": "103"}])
            write_csv(installments, ["SK_ID_CURR"], [{"SK_ID_CURR": "102"}, {"SK_ID_CURR": "102"}, {"SK_ID_CURR": "900"}])
            bridge = root / "bridge"
            write_csv(bridge / "client_private" / "receiver_application_layout.csv", ["app_index", "SK_ID_CURR", "TARGET"], [{"app_index": 0, "SK_ID_CURR": "103", "TARGET": ""}, {"app_index": 1, "SK_ID_CURR": "101", "TARGET": 0}, {"app_index": 2, "SK_ID_CURR": "102", "TARGET": 1}])
            write_csv(bridge / "private_exchange" / "sender_application_layout.csv", ["app_index", "SK_ID_CURR"], [{"app_index": 0, "SK_ID_CURR": ""}, {"app_index": 1, "SK_ID_CURR": ""}, {"app_index": 2, "SK_ID_CURR": "102"}])
            result = module.validate(train, test, installments, bridge)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["checks"]["plaintext_left_join_matched_applicants"], 1)
            self.assertEqual(result["checks"]["psi_bridge_blank_sender_slots"], 2)
            self.assertEqual(result["checks"]["true_positives"], 1)
            self.assertEqual(result["checks"]["false_positives"], 0)
            self.assertEqual(result["checks"]["false_negatives"], 0)
            self.assertEqual(result["checks"]["precision"], 1.0)
            self.assertEqual(result["checks"]["recall"], 1.0)

    def test_validates_old_bridge_when_private_receiver_layout_is_absent(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            train, test, installments = root / "train.csv", root / "test.csv", root / "installments.csv"
            write_csv(train, ["SK_ID_CURR", "TARGET"], [{"SK_ID_CURR": "101", "TARGET": 0}])
            write_csv(test, ["SK_ID_CURR"], [{"SK_ID_CURR": "102"}])
            write_csv(installments, ["SK_ID_CURR"], [{"SK_ID_CURR": "102"}])
            bridge = root / "bridge"
            write_csv(bridge / "private_exchange" / "sender_application_layout.csv", ["app_index", "SK_ID_CURR"], [{"app_index": 0, "SK_ID_CURR": "102"}, {"app_index": 1, "SK_ID_CURR": ""}])
            result = module.validate(train, test, installments, bridge)
            self.assertEqual(result["status"], "PASS")
            self.assertFalse(result["checks"]["receiver_private_layout_available"])
            self.assertIsNone(result["checks"]["sender_slots_match_receiver_positions"])


if __name__ == "__main__":
    unittest.main()
