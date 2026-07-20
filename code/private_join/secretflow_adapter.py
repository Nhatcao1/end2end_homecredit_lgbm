"""SecretFlow PSI input preparation and output validation facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from code.heir.common import write_json
from code.private_join.contracts import (
    prepare_key_file,
    validate_aligned_outputs,
)


def prepare_secretflow_inputs(
    receiver_source: Path,
    sender_source: Path,
    receiver_output: Path,
    sender_output: Path,
    manifest_path: Path,
    key_column: str = "SK_ID_CURR",
) -> dict[str, Any]:
    """Prepare unique identifier-only inputs for a two-party PSI run."""
    receiver = prepare_key_file(
        receiver_source, receiver_output, key_column, deduplicate=False
    )
    # Home Credit history tables contain many rows per applicant by design.
    sender = prepare_key_file(
        sender_source, sender_output, key_column, deduplicate=True
    )
    manifest: dict[str, Any] = {
        "status": "psi_inputs_prepared",
        "implementation": "SecretFlow PSI v2 external container",
        "key_column": key_column,
        "receiver": receiver.to_dict(),
        "sender": sender.to_dict(),
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
