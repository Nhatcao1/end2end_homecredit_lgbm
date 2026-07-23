#!/usr/bin/env python3
"""Prepare PSI-ready, client-private installment blocks grouped by ``SK_ID_CURR``.

This script implements *only* the structural part of the original Pandas
``groupby('SK_ID_CURR')``.  It does not calculate PAYMENT_DIFF, filter numeric
values, aggregate, encrypt, or create an HE feature.  It turns arbitrary-sized
customer groups into fixed-capacity blocks which a later encryption step can
pad with zeros and a 1/0 padding mask.

The input is externally partitioned before sorting, so preparation does not
need to hold the complete installments CSV in memory.  Raw identifiers and raw
parent values remain under ``client_private/``; the public layout contains only
opaque group ordinals, block positions, and row/padding counts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import shutil
import sys
import time
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json


REQUIRED_COLUMNS = ("SK_ID_CURR", "AMT_PAYMENT", "AMT_INSTALMENT")


def _partition_for(identifier: str, partition_count: int) -> int:
    """Return a repeatable client-side partition for an identifier."""
    digest = hashlib.blake2b(identifier.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % partition_count


def _read_eligible_ids(path: Path, column: str) -> set[str]:
    """Read client-held PSI intersection IDs, when an optional filter is used."""
    if not path.is_file():
        raise FileNotFoundError(f"eligible-ID file is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if column not in (reader.fieldnames or []):
            raise ValueError(f"eligible-ID file is missing column {column!r}")
        return {row[column].strip() for row in reader if row[column].strip()}


def _partition_source(
    input_csv: Path,
    staging: Path,
    *,
    partition_count: int,
    max_rows: int,
    eligible_ids: set[str] | None,
) -> dict[str, int]:
    """Write every selected raw row to one client-private ID partition."""
    writers: dict[int, csv.writer] = {}
    handles: dict[int, object] = {}
    raw_rows = selected_rows = missing_id_rows = 0
    try:
        with input_csv.open("r", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"installments CSV is missing columns: {sorted(missing)}")
            for source_row, row in enumerate(reader):
                if max_rows and raw_rows >= max_rows:
                    break
                raw_rows += 1
                identifier = row["SK_ID_CURR"].strip()
                if not identifier:
                    missing_id_rows += 1
                    continue
                if eligible_ids is not None and identifier not in eligible_ids:
                    continue
                partition = _partition_for(identifier, partition_count)
                if partition not in writers:
                    path = staging / f"partition_{partition:04d}.csv"
                    handle = path.open("w", encoding="utf-8", newline="")
                    handles[partition] = handle
                    writer = csv.writer(handle)
                    writer.writerow(["SK_ID_CURR", "source_row", "AMT_PAYMENT", "AMT_INSTALMENT"])
                    writers[partition] = writer
                writers[partition].writerow(
                    [identifier, source_row, row["AMT_PAYMENT"], row["AMT_INSTALMENT"]]
                )
                selected_rows += 1
    finally:
        for handle in handles.values():
            handle.close()
    return {
        "raw_rows": raw_rows,
        "selected_rows": selected_rows,
        "missing_id_rows": missing_id_rows,
        "partitions_with_rows": len(writers),
    }


class _ParentRowShards:
    """Write real parent-column rows in a bounded number of client-private files."""

    def __init__(self, directory: Path, blocks_per_shard: int) -> None:
        self.directory = directory
        self.blocks_per_shard = blocks_per_shard
        self.shard = -1
        self.blocks_in_shard = blocks_per_shard
        self.handle: object | None = None
        self.writer: csv.writer | None = None
        self.files: list[str] = []

    def begin_block(self) -> None:
        if self.blocks_in_shard >= self.blocks_per_shard:
            if self.handle is not None:
                self.handle.close()
            self.shard += 1
            path = self.directory / f"parent_rows_{self.shard:05d}.csv"
            self.handle = path.open("w", encoding="utf-8", newline="")
            self.writer = csv.writer(self.handle)
            self.writer.writerow(
                ["opaque_group_id", "group_block_index", "lane", "AMT_PAYMENT", "AMT_INSTALMENT"]
            )
            self.files.append(str(path.name))
            self.blocks_in_shard = 0
        self.blocks_in_shard += 1

    def write_rows(self, group_id: int, block_index: int, rows: Iterable[dict[str, str]]) -> None:
        if self.writer is None:
            raise RuntimeError("begin_block must be called before writing parent rows")
        for lane, row in enumerate(rows):
            self.writer.writerow(
                [group_id, block_index, lane, row["AMT_PAYMENT"], row["AMT_INSTALMENT"]]
            )

    def close(self) -> list[str]:
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        return self.files


def prepare(
    input_csv: Path,
    output_dir: Path,
    *,
    bucket_size: int,
    vector_size: int,
    partition_count: int,
    blocks_per_shard: int,
    max_rows: int,
    eligible_ids_csv: Path | None = None,
    eligible_id_column: str = "SK_ID_CURR",
    keep_staging: bool = False,
) -> dict[str, object]:
    """Create compact customer blocks without deriving any HE feature values."""
    total_started = time.perf_counter()
    if not input_csv.is_file():
        raise FileNotFoundError(f"installments CSV is missing: {input_csv}")
    if bucket_size < 1:
        raise ValueError("bucket_size must be positive")
    if vector_size < bucket_size or vector_size % bucket_size:
        raise ValueError("vector_size must be an exact positive multiple of bucket_size")
    if partition_count < 1:
        raise ValueError("partition_count must be positive")
    if blocks_per_shard < 1:
        raise ValueError("blocks_per_shard must be positive")

    client_private = output_dir / "client_private"
    staging = client_private / "staging"
    parent_rows = client_private / "parent_rows"
    public_layout = output_dir / "layout"
    staging.mkdir(parents=True)
    parent_rows.mkdir(parents=True)
    public_layout.mkdir(parents=True)

    eligible_started = time.perf_counter()
    eligible_ids = (
        _read_eligible_ids(eligible_ids_csv, eligible_id_column)
        if eligible_ids_csv is not None
        else None
    )
    eligible_seconds = time.perf_counter() - eligible_started
    partition_started = time.perf_counter()
    partition_info = _partition_source(
        input_csv,
        staging,
        partition_count=partition_count,
        max_rows=max_rows,
        eligible_ids=eligible_ids,
    )
    partition_seconds = time.perf_counter() - partition_started

    group_mapping_path = client_private / "group_mapping.csv"
    block_layout_path = public_layout / "block_layout.csv"
    blocks_per_ciphertext = vector_size // bucket_size
    group_count = block_count = padding_rows = largest_group_rows = 0
    parent_writer = _ParentRowShards(parent_rows, blocks_per_shard)
    grouping_started = time.perf_counter()
    try:
        with group_mapping_path.open("w", encoding="utf-8", newline="") as mapping_handle, block_layout_path.open(
            "w", encoding="utf-8", newline=""
        ) as layout_handle:
            mapping_writer = csv.writer(mapping_handle)
            mapping_writer.writerow(["opaque_group_id", "SK_ID_CURR", "source_rows"])
            layout_writer = csv.writer(layout_handle)
            layout_writer.writerow(
                [
                    "packed_ciphertext_batch",
                    "segment_index",
                    "opaque_group_id",
                    "group_block_index",
                    "real_rows",
                    "padding_rows",
                ]
            )
            for partition_path in sorted(staging.glob("partition_*.csv")):
                with partition_path.open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                rows.sort(key=lambda row: (row["SK_ID_CURR"], int(row["source_row"])))
                for identifier, grouped_rows_iter in itertools.groupby(rows, key=lambda row: row["SK_ID_CURR"]):
                    grouped_rows = list(grouped_rows_iter)
                    group_id = group_count
                    group_count += 1
                    largest_group_rows = max(largest_group_rows, len(grouped_rows))
                    mapping_writer.writerow([group_id, identifier, len(grouped_rows)])
                    for group_block_index, start in enumerate(range(0, len(grouped_rows), bucket_size)):
                        block_rows = grouped_rows[start : start + bucket_size]
                        real_rows = len(block_rows)
                        block_padding = bucket_size - real_rows
                        parent_writer.begin_block()
                        parent_writer.write_rows(group_id, group_block_index, block_rows)
                        # One packed CKKS vector later holds several independent
                        # customer blocks.  The scheduled segment is metadata,
                        # not a feature calculation or an encrypted operation.
                        layout_writer.writerow(
                            [
                                block_count // blocks_per_ciphertext,
                                block_count % blocks_per_ciphertext,
                                group_id,
                                group_block_index,
                                real_rows,
                                block_padding,
                            ]
                        )
                        block_count += 1
                        padding_rows += block_padding
    finally:
        parent_files = parent_writer.close()
    grouping_seconds = time.perf_counter() - grouping_started

    cleanup_started = time.perf_counter()
    if not keep_staging:
        shutil.rmtree(staging)
    cleanup_seconds = time.perf_counter() - cleanup_started
    total_seconds = time.perf_counter() - total_started
    artifact_paths = [
        group_mapping_path,
        block_layout_path,
        *(parent_rows / name for name in parent_files),
    ]
    artifact_bytes = sum(path.stat().st_size for path in artifact_paths)

    report = {
        "status": "client_only_installments_group_blocks_prepared",
        "source_csv": str(input_csv.resolve()),
        "source_row_limit": max_rows or "all source rows",
        "psi_selection": {
            "applied": eligible_ids is not None,
            "eligible_ids_csv": str(eligible_ids_csv.resolve()) if eligible_ids_csv is not None else None,
            "eligible_id_column": eligible_id_column if eligible_ids is not None else None,
            "eligible_id_count": len(eligible_ids) if eligible_ids is not None else None,
        },
        "source_rows": partition_info,
        "group_layout": {
            "group_key": "SK_ID_CURR",
            "opaque_group_ids": "sequential opaque ordinals; raw mapping retained only in client_private/group_mapping.csv",
            "groups": group_count,
            "largest_group_rows": largest_group_rows,
            "bucket_size": bucket_size,
            "blocks": block_count,
            "ckks_vector_size": vector_size,
            "blocks_per_ciphertext": blocks_per_ciphertext,
            "planned_packed_ciphertext_batches": (block_count + blocks_per_ciphertext - 1)
            // blocks_per_ciphertext,
            "implicit_zero_padding_rows": padding_rows,
            "padding_mask_rule": "for each block: [1] * real_rows + [0] * padding_rows; materialize only during later encryption",
        },
        "artifacts": {
            "public_layout": "layout/block_layout.csv",
            "client_private_group_mapping": "client_private/group_mapping.csv",
            "client_private_parent_rows": [f"client_private/parent_rows/{name}" for name in parent_files],
            "staging_retained": keep_staging,
        },
        "benchmark": {
            "scope": (
                "client-only post-PSI grouping, opaque-ID mapping, and fixed-block "
                "layout; no HEIR/OpenFHE operation"
            ),
            "timings_seconds": {
                "read_eligible_psi_ids": eligible_seconds,
                "partition_source_rows": partition_seconds,
                "sort_group_and_write_blocks": grouping_seconds,
                "remove_temporary_partitions": cleanup_seconds,
                "total": total_seconds,
            },
            "throughput_rows_per_second": {
                "source_scan": (
                    partition_info["raw_rows"] / partition_seconds
                    if partition_seconds
                    else 0.0
                ),
                "selected_rows_grouped": (
                    partition_info["selected_rows"] / grouping_seconds
                    if grouping_seconds
                    else 0.0
                ),
                "end_to_end_source_rows": (
                    partition_info["raw_rows"] / total_seconds
                    if total_seconds
                    else 0.0
                ),
            },
            "output_artifact_bytes": artifact_bytes,
            "padding_ratio": (
                padding_rows / (block_count * bucket_size) if block_count else 0.0
            ),
        },
        "scope_note": "No derived feature, numeric sanitation, validity mask, aggregate, ciphertext, or HE operation was created.",
    }
    write_json(output_dir / "group_preparation_report.json", report)
    timings = report["benchmark"]["timings_seconds"]
    throughput = report["benchmark"]["throughput_rows_per_second"]
    (output_dir / "GROUP_PREPARATION_BENCHMARK.md").write_text(
        f"""# Installments group preparation benchmark

This is a client-only preparation benchmark. It optionally consumes a completed
PSI intersection, groups installment rows by `SK_ID_CURR`, replaces identifiers
with private opaque ordinals, and writes fixed-size block metadata. It does
**not** calculate `PAYMENT_DIFF`, encrypt anything, or invoke HEIR/OpenFHE.

| Source rows scanned | Rows selected after PSI | Groups | Blocks | Padding lanes | Padding ratio |
|---:|---:|---:|---:|---:|---:|
| {partition_info['raw_rows']} | {partition_info['selected_rows']} | {group_count} | {block_count} | {padding_rows} | {report['benchmark']['padding_ratio']:.6f} |

| Stage | Seconds |
|---|---:|
| Read completed PSI identifiers | {timings['read_eligible_psi_ids']:.9f} |
| Partition source rows | {timings['partition_source_rows']:.9f} |
| Sort, group, and write fixed blocks | {timings['sort_group_and_write_blocks']:.9f} |
| Remove temporary partitions | {timings['remove_temporary_partitions']:.9f} |
| **Total preparation** | **{timings['total']:.9f}** |

| Source scan rows/s | Selected rows grouped/s | End-to-end source rows/s | Output artifact bytes |
|---:|---:|---:|---:|
| {throughput['source_scan']:.2f} | {throughput['selected_rows_grouped']:.2f} | {throughput['end_to_end_source_rows']:.2f} | {artifact_bytes} |

Raw `SK_ID_CURR` values and parent columns remain under `client_private/`.
Only `layout/block_layout.csv` is non-identifying layout metadata.
""",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--partitions", type=int, default=128)
    parser.add_argument("--blocks-per-shard", type=int, default=10_000)
    parser.add_argument("--max-rows", type=int, default=0, help="raw source-row cap; 0 means all rows")
    parser.add_argument("--eligible-ids-csv", type=Path)
    parser.add_argument("--eligible-id-column", default="SK_ID_CURR")
    parser.add_argument("--keep-staging", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.max_rows < 0:
        raise ValueError("max_rows must be zero (all rows) or positive")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    report = prepare(
        args.input_csv.resolve(),
        root,
        bucket_size=args.bucket_size,
        vector_size=args.vector_size,
        partition_count=args.partitions,
        blocks_per_shard=args.blocks_per_shard,
        max_rows=args.max_rows,
        eligible_ids_csv=args.eligible_ids_csv.resolve() if args.eligible_ids_csv else None,
        eligible_id_column=args.eligible_id_column,
        keep_staging=args.keep_staging,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
