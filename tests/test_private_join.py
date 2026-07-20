from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from code.bridge.psi_to_heir import build_heir_alignment
from code.heir.common import read_csv, sha256_file
from code.heir.function_benchmark import prepare_complete_function
from code.heir.workloads.catalog import get_function
from code.private_join.contracts import validate_aligned_outputs
from code.private_join.scripts.run_secretflow_psi import _compose_base
from code.private_join.secretflow_adapter import prepare_secretflow_inputs


def write_table(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PrivateJoinTest(unittest.TestCase):
    def test_secretflow_deployment_contract(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        receiver = json.loads(
            (repository / "deploy/secretflow_psi/configs/receiver.config").read_text(
                encoding="utf-8"
            )
        )
        sender = json.loads(
            (repository / "deploy/secretflow_psi/configs/sender.config").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            receiver["psi_config"]["protocol_config"]["protocol"],
            "PROTOCOL_RR22",
        )
        self.assertEqual(
            receiver["psi_config"]["protocol_config"]["role"],
            "ROLE_RECEIVER",
        )
        self.assertEqual(
            sender["psi_config"]["protocol_config"]["role"],
            "ROLE_SENDER",
        )
        self.assertTrue(
            receiver["psi_config"]["protocol_config"]["broadcast_result"]
        )
        self.assertTrue(
            sender["psi_config"]["protocol_config"]["broadcast_result"]
        )
        self.assertEqual(receiver["link_config"], sender["link_config"])
        self.assertEqual(
            _compose_base(Path("deploy"), Path("compose.yml")),
            [
                "docker",
                "compose",
                "--project-directory",
                "deploy",
                "-f",
                "compose.yml",
            ],
        )

    def test_prepare_inputs_deduplicates_history_but_not_receiver(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receiver = root / "application.csv"
            sender = root / "history.csv"
            write_table(
                receiver,
                ["SK_ID_CURR", "TARGET"],
                [
                    {"SK_ID_CURR": 103, "TARGET": 0},
                    {"SK_ID_CURR": 101, "TARGET": 1},
                    {"SK_ID_CURR": 102, "TARGET": 0},
                ],
            )
            write_table(
                sender,
                ["SK_ID_CURR", "VALUE"],
                [
                    {"SK_ID_CURR": 102, "VALUE": 1},
                    {"SK_ID_CURR": 102, "VALUE": 2},
                    {"SK_ID_CURR": 103, "VALUE": 3},
                    {"SK_ID_CURR": 999, "VALUE": 4},
                ],
            )
            manifest = prepare_secretflow_inputs(
                receiver,
                sender,
                root / "receiver" / "psi_input.csv",
                root / "sender" / "psi_input.csv",
                root / "manifest.json",
            )
            self.assertEqual(manifest["receiver"]["unique_keys"], 3)
            self.assertEqual(manifest["sender"]["unique_keys"], 3)
            self.assertEqual(manifest["sender"]["duplicate_rows_removed"], 1)
            self.assertEqual(
                [row["SK_ID_CURR"] for row in read_csv(root / "sender" / "psi_input.csv")],
                ["102", "103", "999"],
            )

            write_table(
                root / "duplicate_receiver.csv",
                ["SK_ID_CURR"],
                [{"SK_ID_CURR": 1}, {"SK_ID_CURR": 1}],
            )
            with self.assertRaisesRegex(ValueError, "duplicates"):
                prepare_secretflow_inputs(
                    root / "duplicate_receiver.csv",
                    sender,
                    root / "bad_receiver.csv",
                    root / "bad_sender.csv",
                    root / "bad_manifest.json",
                )

    def test_psi_outputs_must_have_identical_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            receiver = root / "receiver.csv"
            sender = root / "sender.csv"
            write_table(
                receiver,
                ["SK_ID_CURR"],
                [{"SK_ID_CURR": 101}, {"SK_ID_CURR": 102}],
            )
            write_table(
                sender,
                ["SK_ID_CURR"],
                [{"SK_ID_CURR": 102}, {"SK_ID_CURR": 101}],
            )
            with self.assertRaisesRegex(ValueError, "different orders"):
                validate_aligned_outputs(receiver, sender, "SK_ID_CURR")

    def test_bridge_preserves_left_join_and_feeds_function_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            application = root / "application_train.csv"
            write_table(
                application,
                ["SK_ID_CURR", "TARGET"],
                [
                    {"SK_ID_CURR": 101, "TARGET": 0},
                    {"SK_ID_CURR": 102, "TARGET": 1},
                    {"SK_ID_CURR": 103, "TARGET": 0},
                ],
            )
            receiver_psi = root / "receiver_psi.csv"
            sender_psi = root / "sender_psi.csv"
            intersection = [{"SK_ID_CURR": 102}, {"SK_ID_CURR": 103}]
            write_table(receiver_psi, ["SK_ID_CURR"], intersection)
            write_table(sender_psi, ["SK_ID_CURR"], intersection)
            execution_summary = root / "secretflow_run_summary.json"
            execution_summary.write_text(
                json.dumps(
                    {
                        "status": "secretflow_psi_completed",
                        "configured_images": ["secretflow/psi-anolis8@sha256:test"],
                        "timings_seconds": {"compose_wall_seconds": 1.25},
                        "validated_output": {
                            "receiver_output_sha256": sha256_file(receiver_psi),
                            "sender_output_sha256": sha256_file(sender_psi),
                        },
                        "traces": {
                            "receiver": {"sha256": "receiver-trace"},
                            "sender": {"sha256": "sender-trace"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            bridge_dir = root / "bridge"
            manifest = build_heir_alignment(
                application,
                receiver_psi,
                sender_psi,
                bridge_dir,
                sender_name="pos",
                shuffle_seed=7,
                execution_summary_path=execution_summary,
            )
            self.assertEqual(manifest["counts"]["receiver_applications"], 3)
            self.assertEqual(manifest["counts"]["intersection"], 2)
            self.assertEqual(manifest["counts"]["receiver_unmatched"], 1)
            self.assertEqual(manifest["psi_execution_evidence"]["status"], "recorded")

            sender_layout = read_csv(
                bridge_dir / "private_exchange" / "sender_application_layout.csv"
            )
            self.assertEqual(len(sender_layout), 3)
            self.assertEqual(sum(bool(row["SK_ID_CURR"]) for row in sender_layout), 2)
            self.assertNotIn("TARGET", sender_layout[0])
            public_text = (
                (bridge_dir / "alignment_manifest.json").read_text(encoding="utf-8")
                + (bridge_dir / "psi_bridge_report.md").read_text(encoding="utf-8")
                + (bridge_dir / "heir_staging" / "sender_presence_mask.csv").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("101", public_text)
            self.assertNotIn("102", public_text)
            self.assertNotIn("103", public_text)

            data = root / "data"
            write_table(
                data / "POS_CASH_balance.csv",
                ["SK_ID_CURR", "MONTHS_BALANCE", "SK_DPD", "SK_DPD_DEF"],
                [
                    {
                        "SK_ID_CURR": 102,
                        "MONTHS_BALANCE": -1,
                        "SK_DPD": 2,
                        "SK_DPD_DEF": 0,
                    },
                    {
                        "SK_ID_CURR": 103,
                        "MONTHS_BALANCE": -2,
                        "SK_DPD": 0,
                        "SK_DPD_DEF": 0,
                    },
                ],
            )
            function_summary = prepare_complete_function(
                get_function("pos"),
                data,
                bridge_dir / "private_exchange" / "sender_application_layout.csv",
                root / "function_run",
                0,
                0,
            )
            self.assertEqual(function_summary["application_rows"], 3)
            self.assertEqual(function_summary["source_rows_matched"], 2)
            references = read_csv(root / "function_run" / "plaintext_reference.csv")
            counts = [
                float(row["value"])
                for row in references
                if row["component_id"] == "POS01" and row["operation"] == "count"
            ]
            self.assertEqual(sorted(counts), [0.0, 1.0, 1.0])

            audit = json.loads(
                (bridge_dir / "client_private" / "psi_output_audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(audit["intersection_rows"], 2)


if __name__ == "__main__":
    unittest.main()
