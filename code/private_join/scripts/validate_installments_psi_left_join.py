#!/usr/bin/env python3
"""Validate the existing PSI bridge against the original application left join.

This is deliberately a small correctness check, not an HE benchmark and not a
second PSI implementation. The client reads the original key relationship:

``application_train ∪ application_test LEFT JOIN unique installments keys``

and compares it with the private receiver/sender layouts created by
``code.bridge.psi_to_heir`` after SecretFlow PSI. Reports contain counts and
PASS/FAIL only; raw identifiers stay in input and bridge-private files.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json


KEY = "SK_ID_CURR"


def _read_unique_keys(path: Path, *, source_name: str) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    keys: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if KEY not in (reader.fieldnames or []):
            raise ValueError(f"{source_name} is missing {KEY}")
        for row_number, row in enumerate(reader, start=2):
            key = (row.get(KEY) or "").strip()
            if not key:
                raise ValueError(f"{source_name}:{row_number} has empty {KEY}")
            if key in seen:
                raise ValueError(f"{source_name}:{row_number} duplicates {KEY}={key}")
            seen.add(key)
            keys.append(key)
    return keys


def _read_history_keys(path: Path) -> set[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    keys: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if KEY not in (reader.fieldnames or []):
            raise ValueError(f"installments source is missing {KEY}")
        for row_number, row in enumerate(reader, start=2):
            key = (row.get(KEY) or "").strip()
            if not key:
                raise ValueError(f"installments:{row_number} has empty {KEY}")
            keys.add(key)
    return keys


def _read_bridge_layouts(bridge_dir: Path) -> tuple[dict[int, str], dict[int, str], bool]:
    receiver_path = bridge_dir / "client_private" / "receiver_application_layout.csv"
    sender_path = bridge_dir / "private_exchange" / "sender_application_layout.csv"
    receiver: dict[int, str] = {}
    sender: dict[int, str] = {}
    with receiver_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"app_index", KEY, "TARGET"}
        if set(reader.fieldnames or []) != expected:
            raise ValueError("receiver bridge layout schema is unexpected")
        for row in reader:
            index = int(row["app_index"])
            if index in receiver:
                raise ValueError("receiver bridge layout duplicates app_index")
            receiver[index] = row[KEY].strip()
    target_in_sender_exchange = False
    with sender_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        target_in_sender_exchange = "TARGET" in (reader.fieldnames or [])
        expected = {"app_index", KEY}
        if set(reader.fieldnames or []) != expected:
            raise ValueError("sender bridge layout schema is unexpected")
        for row in reader:
            index = int(row["app_index"])
            if index in sender:
                raise ValueError("sender bridge layout duplicates app_index")
            sender[index] = row[KEY].strip()
    return receiver, sender, target_in_sender_exchange


def validate(
    application_train: Path,
    application_test: Path,
    installments: Path,
    bridge_dir: Path,
) -> dict[str, object]:
    """Compare exact plaintext left-join membership against PSI bridge slots."""
    train_keys = _read_unique_keys(application_train, source_name="application_train")
    test_keys = _read_unique_keys(application_test, source_name="application_test")
    overlap = set(train_keys).intersection(test_keys)
    if overlap:
        raise ValueError("application train/test share SK_ID_CURR values; expected disjoint populations")
    application_keys = [*train_keys, *test_keys]
    history_keys = _read_history_keys(installments)
    expected_matches = set(application_keys).intersection(history_keys)
    receiver, sender, target_in_sender_exchange = _read_bridge_layouts(bridge_dir)
    receiver_keys = set(receiver.values())
    bridge_matches = {key for key in sender.values() if key}
    bridge_blank_slots = sum(not key for key in sender.values())
    receiver_layout_matches_source = receiver_keys == set(application_keys) and len(receiver) == len(application_keys)
    sender_slot_alignment = (
        set(receiver) == set(sender)
        and all(not key or key == receiver[index] for index, key in sender.items())
    )
    result = {
        "status": "PASS" if (
            receiver_layout_matches_source
            and sender_slot_alignment
            and expected_matches == bridge_matches
            and bridge_blank_slots == len(application_keys) - len(expected_matches)
            and not target_in_sender_exchange
        ) else "FAIL",
        "scope": "exact client-side validation of application receiver-left join membership; no HE operation",
        "checks": {
            "application_train_test_union_rows": len(application_keys),
            "unique_installment_applicants": len(history_keys),
            "plaintext_left_join_matched_applicants": len(expected_matches),
            "psi_bridge_matched_applicants": len(bridge_matches),
            "plaintext_left_join_unmatched_applicants": len(application_keys) - len(expected_matches),
            "psi_bridge_blank_sender_slots": bridge_blank_slots,
            "receiver_layout_matches_train_test_union": receiver_layout_matches_source,
            "sender_slots_match_receiver_positions": sender_slot_alignment,
            "matched_applicant_set_matches_plaintext": expected_matches == bridge_matches,
            "target_excluded_from_sender_exchange": not target_in_sender_exchange,
        },
        "privacy_note": "raw identifiers were read only inside this client-side validator and are not written to its result/report",
    }
    return result


def _report(result: dict[str, object]) -> str:
    checks = result["checks"]
    assert isinstance(checks, dict)
    return f"""# Installments PSI left-join validation

This is an exact client-side comparison of the original relationship:

```text
application_train ∪ application_test LEFT JOIN installments by SK_ID_CURR
```

It does not benchmark HE arithmetic. PSI has no approximation tolerance: every
membership/slot check must pass.

| Check | Plaintext / expected | PSI bridge | Result |
|---|---:|---:|---|
| Application rows | {checks['application_train_test_union_rows']} | {checks['application_train_test_union_rows'] if checks['receiver_layout_matches_train_test_union'] else 'different'} | {'PASS' if checks['receiver_layout_matches_train_test_union'] else 'FAIL'} |
| Matched applicants | {checks['plaintext_left_join_matched_applicants']} | {checks['psi_bridge_matched_applicants']} | {'PASS' if checks['matched_applicant_set_matches_plaintext'] else 'FAIL'} |
| Unmatched / blank sender slots | {checks['plaintext_left_join_unmatched_applicants']} | {checks['psi_bridge_blank_sender_slots']} | {'PASS' if checks['plaintext_left_join_unmatched_applicants'] == checks['psi_bridge_blank_sender_slots'] else 'FAIL'} |
| Sender slot points to the same receiver applicant | — | — | {'PASS' if checks['sender_slots_match_receiver_positions'] else 'FAIL'} |
| TARGET excluded from sender exchange | — | — | {'PASS' if checks['target_excluded_from_sender_exchange'] else 'FAIL'} |

Overall result: **{result['status']}**.

Raw identifiers remain in client input and bridge-private files; this report
contains counts only.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-train", type=Path, required=True)
    parser.add_argument("--application-test", type=Path, required=True)
    parser.add_argument("--installments", type=Path, required=True)
    parser.add_argument("--bridge-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    result = validate(
        args.application_train.resolve(), args.application_test.resolve(),
        args.installments.resolve(), args.bridge_dir.resolve(),
    )
    write_json(root / "result.json", result)
    (root / "REPORT.md").write_text(_report(result), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
