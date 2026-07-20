"""Dependency-free contracts for preparing and validating PSI identifier files."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from code.heir.common import sha256_file, write_csv


@dataclass(frozen=True)
class PreparedKeyFile:
    """Summary of one party's identifier-only PSI input."""

    source_rows: int
    unique_keys: int
    duplicate_rows_removed: int
    output_file: str
    output_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AlignmentValidation:
    """Validated agreement between receiver and sender PSI outputs."""

    intersection_rows: int
    receiver_output_sha256: str
    sender_output_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fieldnames(path: Path, reader: csv.DictReader) -> list[str]:
    fields = list(reader.fieldnames or [])
    if not fields:
        raise ValueError(f"{path} has no CSV header")
    return fields


def prepare_key_file(
    source_path: Path,
    output_path: Path,
    key_column: str,
    *,
    deduplicate: bool,
) -> PreparedKeyFile:
    """Stream one column, validate it, and write a deterministic unique-key CSV."""
    source_rows = duplicate_rows = 0
    seen: set[str] = set()
    keys: list[str] = []
    with source_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = _fieldnames(source_path, reader)
        if key_column not in fields:
            raise ValueError(f"{source_path} is missing key column {key_column}")
        for row_number, row in enumerate(reader, start=2):
            source_rows += 1
            key = (row.get(key_column) or "").strip()
            if not key:
                raise ValueError(
                    f"{source_path}:{row_number} has an empty {key_column}"
                )
            if key in seen:
                duplicate_rows += 1
                if not deduplicate:
                    raise ValueError(
                        f"{source_path}:{row_number} duplicates {key_column}={key}"
                    )
                continue
            seen.add(key)
            keys.append(key)
    if not keys:
        raise ValueError(f"{source_path} contains no PSI keys")
    keys.sort()
    write_csv(output_path, [key_column], ({key_column: key} for key in keys))
    return PreparedKeyFile(
        source_rows=source_rows,
        unique_keys=len(keys),
        duplicate_rows_removed=duplicate_rows,
        output_file=str(output_path),
        output_sha256=sha256_file(output_path),
    )


def prepare_key_union(
    source_paths: Sequence[Path],
    output_path: Path,
    key_column: str,
) -> dict[str, Any]:
    """Create one deterministic PSI input from the union of several CSV key sets."""
    if not source_paths:
        raise ValueError("at least one sender source is required")

    all_keys: set[str] = set()
    source_rows = 0
    source_summaries: list[dict[str, Any]] = []
    for source_path in source_paths:
        rows = 0
        keys_in_source: set[str] = set()
        with source_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = _fieldnames(source_path, reader)
            if key_column not in fields:
                raise ValueError(f"{source_path} is missing key column {key_column}")
            for row_number, row in enumerate(reader, start=2):
                rows += 1
                key = (row.get(key_column) or "").strip()
                if not key:
                    raise ValueError(
                        f"{source_path}:{row_number} has an empty {key_column}"
                    )
                keys_in_source.add(key)
                all_keys.add(key)
        if not keys_in_source:
            raise ValueError(f"{source_path} contains no PSI keys")
        source_rows += rows
        source_summaries.append(
            {
                "source_file": str(source_path),
                "source_sha256": sha256_file(source_path),
                "source_rows": rows,
                "unique_keys": len(keys_in_source),
                "duplicate_rows_removed": rows - len(keys_in_source),
            }
        )

    ordered_keys = sorted(all_keys)
    write_csv(
        output_path,
        [key_column],
        ({key_column: key} for key in ordered_keys),
    )
    return {
        "source_rows": source_rows,
        "unique_keys": len(ordered_keys),
        "duplicate_rows_removed": source_rows - len(ordered_keys),
        "source_files": source_summaries,
        "output_file": str(output_path),
        "output_sha256": sha256_file(output_path),
    }


def read_unique_keys(path: Path, key_column: str) -> list[str]:
    """Read an ordered PSI result and reject blank or duplicate identifiers."""
    keys: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = _fieldnames(path, reader)
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
            keys.append(key)
    return keys


def validate_aligned_outputs(
    receiver_path: Path,
    sender_path: Path,
    key_column: str,
) -> tuple[list[str], AlignmentValidation]:
    """Require both PSI parties to expose the exact same ordered intersection."""
    receiver_keys = read_unique_keys(receiver_path, key_column)
    sender_keys = read_unique_keys(sender_path, key_column)
    if receiver_keys != sender_keys:
        if set(receiver_keys) == set(sender_keys):
            raise ValueError("PSI outputs contain the same keys in different orders")
        receiver_only = len(set(receiver_keys).difference(sender_keys))
        sender_only = len(set(sender_keys).difference(receiver_keys))
        raise ValueError(
            "PSI output sets differ: "
            f"receiver_only={receiver_only}, sender_only={sender_only}"
        )
    return receiver_keys, AlignmentValidation(
        intersection_rows=len(receiver_keys),
        receiver_output_sha256=sha256_file(receiver_path),
        sender_output_sha256=sha256_file(sender_path),
    )
