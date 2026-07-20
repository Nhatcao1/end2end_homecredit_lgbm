"""Integrity and compatibility contracts for persistent encrypted DAG stages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from code.heir.common import sha256_file, write_json


STAGE_ORDER = (
    "bureau",
    "previous",
    "pos",
    "installments",
    "credit_card",
)


def combined_sha256(paths: Iterable[Path]) -> str:
    """Hash file names and contents in deterministic order."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        digest.update(path.name.encode("utf-8"))
        digest.update(sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def artifact_records(root: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    records = []
    for path in sorted(paths, key=lambda item: str(item)):
        if not path.is_file():
            raise FileNotFoundError(f"missing DAG artifact: {path}")
        records.append(
            {
                "file": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def write_completion(
    stage_dir: Path,
    *,
    stage: str,
    session_id: str,
    layout_sha256: str,
    required_paths: Iterable[Path],
) -> dict[str, Any]:
    """Write the last stage artifact only after all required outputs exist."""
    completion = {
        "status": "encrypted_complete",
        "stage": stage,
        "session_id": session_id,
        "applicant_layout_sha256": layout_sha256,
        "artifacts": artifact_records(stage_dir, required_paths),
    }
    write_json(stage_dir / "COMPLETED.json", completion)
    return completion


def validate_completion(
    stage_dir: Path,
    *,
    expected_stage: str,
    session_id: str,
    layout_sha256: str,
) -> dict[str, Any]:
    path = stage_dir / "COMPLETED.json"
    if not path.is_file():
        raise ValueError(f"{stage_dir} has no encrypted completion marker")
    completion = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "status": "encrypted_complete",
        "stage": expected_stage,
        "session_id": session_id,
        "applicant_layout_sha256": layout_sha256,
    }
    for key, value in expected.items():
        if completion.get(key) != value:
            raise ValueError(
                f"{path} has incompatible {key}: {completion.get(key)!r}"
            )
    artifacts = completion.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError(f"{path} records no stage artifacts")
    for artifact in artifacts:
        artifact_path = stage_dir / artifact["file"]
        if not artifact_path.is_file():
            raise FileNotFoundError(f"completed artifact disappeared: {artifact_path}")
        if artifact_path.stat().st_size != artifact["bytes"]:
            raise ValueError(f"completed artifact size changed: {artifact_path}")
        if sha256_file(artifact_path) != artifact["sha256"]:
            raise ValueError(f"completed artifact hash changed: {artifact_path}")
    return completion


def validate_encrypted_bundle(
    manifest_path: Path,
    *,
    session_id: str,
    context_id: str,
    key_set_id: str,
    layout_sha256: str,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "bundle_status": "encrypted_complete",
        "scheme": "CKKS",
        "session_id": session_id,
        "crypto_context_id": context_id,
        "key_set_id": key_set_id,
        "applicant_layout_sha256": layout_sha256,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(
                f"{manifest_path} has incompatible {key}: {manifest.get(key)!r}"
            )
    ciphertexts = manifest.get("ciphertext_files")
    if not isinstance(ciphertexts, list) or not ciphertexts:
        raise ValueError(f"{manifest_path} contains no serialized ciphertexts")
    stage_dir = manifest_path.parent
    for record in ciphertexts:
        ciphertext = stage_dir / record["file"]
        if not ciphertext.is_file() or ciphertext.stat().st_size == 0:
            raise FileNotFoundError(f"missing serialized ciphertext: {ciphertext}")
        if sha256_file(ciphertext) != record["sha256"]:
            raise ValueError(f"ciphertext hash mismatch: {ciphertext}")
    return manifest
