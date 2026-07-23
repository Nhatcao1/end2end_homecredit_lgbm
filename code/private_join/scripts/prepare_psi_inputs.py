#!/usr/bin/env python3
"""Prepare receiver and sender identifier-only CSV files for SecretFlow PSI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.private_join.secretflow_adapter import prepare_secretflow_inputs


def _markdown_report(result: dict[str, object]) -> str:
    receiver = result["receiver"]
    sender = result["sender"]
    benchmark = result["benchmark"]
    timings = benchmark["timings_seconds"]
    throughput = benchmark["throughput_rows_per_second"]
    sizes = benchmark["output_bytes"]
    return f"""# PSI input preparation benchmark

This report measures only client-side identifier preparation. It does **not**
run SecretFlow PSI, build the post-PSI bridge, encrypt data, or invoke HEIR.

| Stage | Source rows | Unique keys | Duplicates removed | Seconds | Rows/s |
|---|---:|---:|---:|---:|---:|
| Receiver key preparation | {receiver['source_rows']} | {receiver['unique_keys']} | {receiver['duplicate_rows_removed']} | {timings['receiver_key_preparation']:.9f} | {throughput['receiver']:.2f} |
| Sender key-union preparation | {sender['source_rows']} | {sender['unique_keys']} | {sender['duplicate_rows_removed']} | {timings['sender_union_preparation']:.9f} | {throughput['sender']:.2f} |

| Total preparation | Combined rows/s | Receiver output bytes | Sender output bytes | Total output bytes |
|---:|---:|---:|---:|---:|
| {timings['total']:.9f} | {throughput['combined']:.2f} | {sizes['receiver_key_file']} | {sizes['sender_key_file']} | {sizes['total_key_files']} |

The output CSVs contain raw identifiers and must remain with their respective
PSI parties. They are not HE input tensors.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver-source", type=Path, required=True)
    parser.add_argument(
        "--sender-source",
        type=Path,
        action="append",
        required=True,
        help=(
            "sender CSV containing the join key; repeat once per table to build "
            "one sender key universe"
        ),
    )
    parser.add_argument("--key", default="SK_ID_CURR")
    parser.add_argument(
        "--receiver-output",
        type=Path,
        default=Path("data/psi/receiver/psi_input.csv"),
    )
    parser.add_argument(
        "--sender-output",
        type=Path,
        default=Path("data/psi/sender/psi_input.csv"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/psi/psi_input_manifest.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        help=(
            "Markdown benchmark report; defaults to "
            "PSI_PREPARATION_BENCHMARK.md beside --manifest"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = prepare_secretflow_inputs(
        args.receiver_source,
        args.sender_source,
        args.receiver_output,
        args.sender_output,
        args.manifest,
        args.key,
    )
    report_path = (
        args.report
        if args.report is not None
        else args.manifest.with_name("PSI_PREPARATION_BENCHMARK.md")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
