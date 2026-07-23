"""SecretFlow PSI input preparation and output validation facade."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Sequence

from code.heir.common import write_json
from code.private_join.contracts import (
    prepare_key_file,
    prepare_key_union,
    validate_aligned_outputs,
)


def prepare_secretflow_inputs(
    receiver_source: Path,
    sender_source: Path | Sequence[Path],
    receiver_output: Path,
    sender_output: Path,
    manifest_path: Path,
    key_column: str = "SK_ID_CURR",
) -> dict[str, Any]:
    """Prepare unique identifier-only inputs for a two-party PSI run."""
    total_started = time.perf_counter()
    receiver_started = time.perf_counter()
    receiver = prepare_key_file(
        receiver_source, receiver_output, key_column, deduplicate=False
    )
    receiver_seconds = time.perf_counter() - receiver_started
    # Home Credit history tables contain many rows per applicant by design. A
    # single sender may contribute several tables, so PSI uses their key union.
    sender_sources = (
        [sender_source] if isinstance(sender_source, Path) else list(sender_source)
    )
    sender_started = time.perf_counter()
    sender = prepare_key_union(sender_sources, sender_output, key_column)
    sender_seconds = time.perf_counter() - sender_started
    total_seconds = time.perf_counter() - total_started
    manifest: dict[str, Any] = {
        "status": "psi_inputs_prepared",
        "implementation": "SecretFlow PSI v2 external container",
        "key_column": key_column,
        "receiver": receiver.to_dict(),
        "sender": sender,
        "benchmark": {
            "scope": "client-only PSI input preparation; PSI protocol not run",
            "timings_seconds": {
                "receiver_key_preparation": receiver_seconds,
                "sender_union_preparation": sender_seconds,
                "total": total_seconds,
            },
            "throughput_rows_per_second": {
                "receiver": receiver.source_rows / receiver_seconds,
                "sender": sender["source_rows"] / sender_seconds,
                "combined": (
                    receiver.source_rows + sender["source_rows"]
                )
                / total_seconds,
            },
            "output_bytes": {
                "receiver_key_file": receiver_output.stat().st_size,
                "sender_key_file": sender_output.stat().st_size,
                "total_key_files": (
                    receiver_output.stat().st_size + sender_output.stat().st_size
                ),
            },
        },
        "privacy_rule": (
            "files contain raw PSI identifiers and must remain party-private; "
            "they are never HEIR tensors"
        ),
    }
    write_json(manifest_path, manifest)
    return manifest


def validate_secretflow_outputs(
    receiver_output: Path,
    sender_output: Path,
    audit_path: Path,
    key_column: str = "SK_ID_CURR",
) -> dict[str, Any]:
    """Validate the ordered output pair without claiming protocol attestation."""
    _, validation = validate_aligned_outputs(
        receiver_output, sender_output, key_column
    )
    audit: dict[str, Any] = {
        "status": "psi_output_pair_validated",
        "cryptographic_attestation": "not_provided_by_csv_bridge",
        "key_column": key_column,
        **validation.to_dict(),
    }
    write_json(audit_path, audit)
    return audit
