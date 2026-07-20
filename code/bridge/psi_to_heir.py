#!/usr/bin/env python3
"""Convert validated PSI alignment into dense anonymous HEIR application slots."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import secrets
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from code.heir.common import sha256_file, write_csv, write_json, write_values
from code.private_join.contracts import validate_aligned_outputs


def _read_receiver_rows(
    path: Path, key_column: str, target_column: str
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = set(reader.fieldnames or [])
        if key_column not in fields:
            raise ValueError(f"{path} is missing key column {key_column}")
        for row_number, row in enumerate(reader, start=2):
            key = (row.get(key_column) or "").strip()
            if not key:
                raise ValueError(f"{path}:{row_number} has an empty {key_column}")
            if key in seen:
                raise ValueError(
                    f"{path}:{row_number} duplicates {key_column}={key}"
                )
            seen.add(key)
            rows.append(
                {
                    key_column: key,
                    "TARGET": (row.get(target_column) or "").strip()
                    if target_column
                    else "",
                }
            )
    if not rows:
        raise ValueError(f"{path} contains no receiver applications")
    return rows


def _markdown_report(manifest: dict[str, Any]) -> str:
    counts = manifest["counts"]
    evidence = manifest["psi_execution_evidence"]
    evidence_result = (
        "Recorded — external logs/traces, not cryptographic attestation"
        if evidence["status"] == "recorded"
        else "Not provided"
    )
    evidence_rows = ""
    if evidence["status"] == "recorded":
        evidence_rows = f"""
## Server execution evidence

| Measure | Value |
|---|---|
| Configured image(s) | {', '.join(evidence['configured_images'])} |
| Compose wall seconds | {evidence['compose_wall_seconds']:.6f} |
| Receiver trace | {evidence['receiver_trace']} |
| Sender trace | {evidence['sender_trace']} |
| Execution-summary SHA256 | `{evidence['summary_sha256']}` |
"""
    return f"""# SecretFlow PSI to HEIR bridge report

## Status

`{manifest['status']}` / `{manifest['ciphertext_status']}`

The bridge validated two externally produced PSI result files and converted
their common ordered intersection into a dense anonymous applicant layout. CSV
validation does not cryptographically attest which binary or protocol produced
those files.

## Acceptance criteria

| Criterion | Result |
|---|---|
| Receiver keys are unique | Pass |
| Receiver and sender PSI outputs have identical ordered keys | Pass |
| PSI matches are a subset of receiver applications | Pass |
| Dense sender layout preserves receiver-left-join row count | Pass |
| TARGET excluded from sender exchange | Pass |
| Raw identifiers excluded from HE staging tensors | Pass |
| SecretFlow server execution evidence | {evidence_result} |
| SecretFlow protocol execution cryptographically attested | Not provided by CSV/log bridge |
| CKKS encryption or HEIR evaluation executed | Not run |

## Which parts use privacy technology?

| Pipeline part | Owner | Current status | Output |
|---|---|---|---|
| Read and deduplicate identifier columns | Each data owner | Client only | party-private PSI input |
| Match `{manifest['key_column']}` | SecretFlow `{manifest['protocol']}` | External PSI outputs validated | ordered intersection |
| Assign random dense app_index and left-join slots | Python bridge | Executed | private receiver/sender layouts |
| Create sender-presence mask | Python bridge | Plaintext staging only | tensor requiring encryption |
| Encrypt aligned numeric tensors | HEIR/OpenFHE boundary | Not implemented in this bridge | future CKKS ciphertexts |
| Function aggregation and scoring | HEIR kernels | Not run | future encrypted feature bundle |

## Join scope

| Measure | Value |
|---|---:|
| Receiver applications | {counts['receiver_applications']} |
| PSI intersection | {counts['intersection']} |
| Receiver rows without sender history | {counts['receiver_unmatched']} |
| Dense HEIR slots | {counts['dense_slots']} |

The sender exchange contains only matched identifiers and their random receiver
slot positions. Blank rows preserve unmatched receiver positions. It never
contains TARGET or identifiers belonging only to the receiver.

## Leakage and trust boundary

- This benchmark configuration reveals the intersection and its cardinality to
  both PSI parties.
- PSI input/output files and both application-layout CSV files are private
  runtime artifacts and must not be committed.
- `heir_staging/sender_presence_mask.csv` is plaintext client-side staging. It
  must be encrypted before leaving the data owner.
- The public alignment manifest contains counts and schema information but no
  raw applicant identifiers or identifier-derived hashes.
{evidence_rows}
"""


def build_heir_alignment(
    receiver_source: Path,
    receiver_psi_output: Path,
    sender_psi_output: Path,
    output_dir: Path,
    *,
    key_column: str = "SK_ID_CURR",
    target_column: str = "TARGET",
    protocol: str = "PROTOCOL_RR22",
    sender_name: str = "sender",
    shuffle_seed: int | None = None,
    execution_summary_path: Path | None = None,
) -> dict[str, Any]:
    """Build receiver-private and sender-exchange layouts for one PSI join."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite output directory: {output_dir}")
    output_dir.mkdir(parents=True)

    intersection, validation = validate_aligned_outputs(
        receiver_psi_output, sender_psi_output, key_column
    )
    receiver_rows = _read_receiver_rows(receiver_source, key_column, target_column)
    receiver_by_key = {row[key_column]: row for row in receiver_rows}
    unknown = [key for key in intersection if key not in receiver_by_key]
    if unknown:
        raise ValueError(
            f"PSI output contains {len(unknown)} keys absent from receiver source"
        )

    if shuffle_seed is None:
        secrets.SystemRandom().shuffle(receiver_rows)
    else:
        random.Random(shuffle_seed).shuffle(receiver_rows)
    app_index = {row[key_column]: index for index, row in enumerate(receiver_rows)}
    matched = set(intersection)

    receiver_layout = [
        {
            "app_index": index,
            key_column: row[key_column],
            "TARGET": row["TARGET"],
        }
        for index, row in enumerate(receiver_rows)
    ]
    sender_layout = [
        {
            "app_index": index,
            key_column: row[key_column] if row[key_column] in matched else "",
        }
        for index, row in enumerate(receiver_rows)
    ]
    exchange_rows = [
        {"app_index": app_index[key], key_column: key}
        for key in intersection
    ]
    presence = [
        1.0 if row[key_column] in matched else 0.0 for row in receiver_rows
    ]

    receiver_layout_path = (
        output_dir / "client_private" / "receiver_application_layout.csv"
    )
    sender_layout_path = (
        output_dir / "private_exchange" / "sender_application_layout.csv"
    )
    match_mapping_path = (
        output_dir / "private_exchange" / "sender_match_mapping.csv"
    )
    presence_path = output_dir / "heir_staging" / "sender_presence_mask.csv"
    write_csv(
        receiver_layout_path,
        ["app_index", key_column, "TARGET"],
        receiver_layout,
    )
    write_csv(
        sender_layout_path,
        ["app_index", key_column],
        sender_layout,
    )
    write_csv(match_mapping_path, ["app_index", key_column], exchange_rows)
    write_values(presence_path, presence)

    private_layout_hash = hashlib.sha256(
        "\n".join(row[key_column] for row in receiver_rows).encode("utf-8")
    ).hexdigest()
    write_json(
        output_dir / "client_private" / "psi_output_audit.json",
        {
            **validation.to_dict(),
            "private_receiver_layout_sha256": private_layout_hash,
            "receiver_source_sha256": sha256_file(receiver_source),
        },
    )

    schema = {
        "dense_index": "app_index",
        "sender_presence_tensor": "heir_staging/sender_presence_mask.csv",
        "sender_layout": "private_exchange/sender_application_layout.csv",
        "receiver_layout": "client_private/receiver_application_layout.csv",
    }
    schema_hash = hashlib.sha256(
        json.dumps(schema, sort_keys=True).encode("utf-8")
    ).hexdigest()
    execution_evidence: dict[str, Any] = {"status": "not_provided"}
    if execution_summary_path is not None and execution_summary_path.is_file():
        execution_summary = json.loads(
            execution_summary_path.read_text(encoding="utf-8")
        )
        if execution_summary.get("status") != "secretflow_psi_completed":
            raise ValueError("SecretFlow execution summary does not record a completed run")
        recorded = execution_summary.get("validated_output", {})
        if (
            recorded.get("receiver_output_sha256")
            != validation.receiver_output_sha256
            or recorded.get("sender_output_sha256")
            != validation.sender_output_sha256
        ):
            raise ValueError("SecretFlow execution summary hashes do not match PSI outputs")
        traces = execution_summary.get("traces", {})
        execution_evidence = {
            "status": "recorded",
            "configured_images": execution_summary.get("configured_images", []),
            "compose_wall_seconds": execution_summary.get("timings_seconds", {}).get(
                "compose_wall_seconds", 0.0
            ),
            "receiver_trace": traces.get("receiver", {}).get("sha256", "missing"),
            "sender_trace": traces.get("sender", {}).get("sha256", "missing"),
            "summary_sha256": sha256_file(execution_summary_path),
        }
    manifest: dict[str, Any] = {
        "status": "psi_outputs_validated_bridge_prepared",
        "ciphertext_status": "plaintext_staging_only",
        "psi_execution_attestation": "not_provided_by_csv_bridge",
        "psi_execution_evidence": execution_evidence,
        "protocol": protocol,
        "join_semantics": "receiver_left_join",
        "result_revealed_to": ["receiver", "sender"],
        "key_column": key_column,
        "sender_name": sender_name,
        "layout_id": secrets.token_hex(16),
        "alignment_schema_sha256": schema_hash,
        "counts": {
            "receiver_applications": len(receiver_rows),
            "intersection": len(intersection),
            "receiver_unmatched": len(receiver_rows) - len(intersection),
            "dense_slots": len(receiver_rows),
        },
        "outputs": schema,
        "heir_contract": {
            "raw_identifiers_in_he_tensors": False,
            "target_in_sender_exchange": False,
            "sender_application_argument": str(sender_layout_path),
            "receiver_application_argument": str(receiver_layout_path),
            "application_row_limit": 0,
        },
        "privacy_boundary": (
            "raw identifiers remain in client_private/private_exchange; only "
            "anonymous fixed-shape numeric tensors may cross into HEIR"
        ),
    }
    write_json(output_dir / "alignment_manifest.json", manifest)
    (output_dir / "psi_bridge_report.md").write_text(
        _markdown_report(manifest), encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver-source", type=Path, required=True)
    parser.add_argument("--receiver-psi-output", type=Path, required=True)
    parser.add_argument("--sender-psi-output", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--key", default="SK_ID_CURR")
    parser.add_argument("--target-column", default="TARGET")
    parser.add_argument("--protocol", default="PROTOCOL_RR22")
    parser.add_argument("--sender-name", default="sender")
    parser.add_argument("--shuffle-seed", type=int)
    parser.add_argument(
        "--execution-summary",
        type=Path,
        default=Path("data/psi/secretflow_run_summary.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_heir_alignment(
        args.receiver_source,
        args.receiver_psi_output,
        args.sender_psi_output,
        args.output_dir,
        key_column=args.key,
        target_column=args.target_column,
        protocol=args.protocol,
        sender_name=args.sender_name,
        shuffle_seed=args.shuffle_seed,
        execution_summary_path=args.execution_summary,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
