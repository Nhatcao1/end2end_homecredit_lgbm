from __future__ import annotations

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "code/heir/scripts/run_payment_diff_groupby_e2e.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_payment_diff_groupby_e2e", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaymentDiffEndToEndPreparationTest(unittest.TestCase):
    def test_e2e_runner_uses_reusable_group_statistics_runtime(self) -> None:
        module = load_module()
        header = Path(__file__).resolve().parents[1] / "code/heir/runtime/group_statistics.h"
        source = header.read_text(encoding="utf-8")
        self.assertIn("mean_from_sum", source)
        self.assertIn("sample_variance_from_moments", source)
        self.assertIn("evaluate_group_statistics", source)
        self.assertIn('#include "group_statistics.h"', module.RUNNER)
        self.assertIn("heir::runtime::evaluate_group_statistics", module.RUNNER)

    def test_same_context_max_route_generates_comparison_tree_keys(self) -> None:
        module = load_module()
        self.assertIn("switchParameters.SetComputeArgmin(true)", module.RUNNER)
        self.assertIn("argmax_artifact_retained", module.RUNNER)
        self.assertIn("EvalMaxSchemeSwitching(values[0]", module.RUNNER)
        self.assertIn("one_crypto_context", module.RUNNER)

    def test_uses_only_matched_psi_groups_and_keeps_parent_columns(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge = root / "bridge" / "private_exchange"; bridge.mkdir(parents=True)
            (bridge / "sender_application_layout.csv").write_text(
                "app_index,SK_ID_CURR\n0,10\n1,20\n2,\n", encoding="utf-8"
            )
            source = root / "installments.csv"
            source.write_text(
                "SK_ID_CURR,AMT_PAYMENT,AMT_INSTALMENT\n"
                "10,60,100\n10,40,50\n20,10,20\n20,30,30\n30,1,1000\n",
                encoding="utf-8",
            )
            result = module._prepare(source, bridge.parent, root / "out", 2, 4)
            self.assertEqual(result["groups"], 2)
            with (root / "out" / "client_private" / "group_mapping.csv").open(newline="", encoding="utf-8") as handle:
                keys = {row["SK_ID_CURR"] for row in csv.DictReader(handle)}
            self.assertEqual(keys, {"10", "20"})
            with (root / "out" / "he_ready" / "group_blocks.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 8)
            self.assertNotIn("PAYMENT_DIFF", rows[0])
            self.assertEqual(sum(int(row["validity_mask"]) for row in rows), 4)

    def test_max_padding_scale_exceeds_parent_difference_bound(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge = root / "bridge" / "private_exchange"; bridge.mkdir(parents=True)
            (bridge / "sender_application_layout.csv").write_text("app_index,SK_ID_CURR\n0,1\n1,2\n", encoding="utf-8")
            source = root / "installments.csv"
            source.write_text("SK_ID_CURR,AMT_PAYMENT,AMT_INSTALMENT\n1,1,100\n1,2,200\n2,1,10\n2,2,20\n", encoding="utf-8")
            result = module._prepare(source, bridge.parent, root / "out", 2, 4)
            self.assertGreater(float(result["input_scale"]), 2 * (200 + 2))


if __name__ == "__main__":
    unittest.main()
