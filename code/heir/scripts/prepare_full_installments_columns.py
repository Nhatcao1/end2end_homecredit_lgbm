#!/usr/bin/env python3
"""Stream all installment rows into sanitized, fixed-width HE input batches.

This is client-side representation preparation only. It never calculates a
feature for the HE path: each output row contains raw ``AMT_PAYMENT`` and
``AMT_INSTALMENT`` values.  The accompanying plaintext statistics are an audit
oracle, using the same Pandas expressions as the source notebook.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json


def prepare(
    input_csv: Path,
    output_dir: Path,
    *,
    vector_size: int,
    chunk_rows: int,
    max_rows: int,
) -> dict[str, object]:
    """Read the full CSV in chunks and write raw numeric HE batches plus manifest."""
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError(
            "full-data preparation requires pandas; install it in the active "
            "environment with `python3 -m pip install pandas`"
        ) from error
    if vector_size < 2:
        raise ValueError("vector_size must be at least 2")
    if not input_csv.is_file():
        raise FileNotFoundError(f"installments CSV is missing: {input_csv}")

    batches = output_dir / "batches"
    batches.mkdir(parents=True)
    raw_rows = kept_rows = dropped_rows = 0
    diff_sum = diff_sum_squares = 0.0
    ratio_sum = ratio_sum_squares = 0.0
    ratio_min = math.inf
    ratio_max = -math.inf
    pandas_feature_seconds = 0.0
    pandas_aggregation_seconds = 0.0
    batch_index = 0
    buffered_payment: list[float] = []
    buffered_installment: list[float] = []
    manifest_rows: list[dict[str, object]] = []

    def flush(*, final: bool = False) -> None:
        nonlocal batch_index, buffered_payment, buffered_installment
        while len(buffered_payment) >= vector_size or (final and buffered_payment):
            take = min(vector_size, len(buffered_payment))
            payment = buffered_payment[:take]
            installment = buffered_installment[:take]
            del buffered_payment[:take]
            del buffered_installment[:take]
            padding = vector_size - take
            path = batches / f"batch_{batch_index:06d}.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["AMT_PAYMENT", "AMT_INSTALMENT", "valid"])
                writer.writerows(zip(payment, installment, [1.0] * take))
                writer.writerows([(0.0, 0.0, 0.0)] * padding)
            manifest_rows.append(
                {
                    "batch": batch_index,
                    "file": str(path.relative_to(output_dir)),
                    "real_rows": take,
                    "padding_rows": padding,
                }
            )
            batch_index += 1

    for chunk in pd.read_csv(
        input_csv,
        usecols=["AMT_PAYMENT", "AMT_INSTALMENT"],
        chunksize=chunk_rows,
    ):
        if max_rows and raw_rows >= max_rows:
            break
        if max_rows and raw_rows + len(chunk) > max_rows:
            chunk = chunk.iloc[: max_rows - raw_rows]
        raw_rows += len(chunk)
        payment = pd.to_numeric(chunk["AMT_PAYMENT"], errors="coerce")
        installment = pd.to_numeric(chunk["AMT_INSTALMENT"], errors="coerce")
        valid = payment.notna() & installment.notna() & (installment > 0)
        clean_payment = payment[valid].astype(float)
        clean_installment = installment[valid].astype(float)
        # ``np.isfinite`` is deliberately avoided: Pandas already normalizes
        # malformed CSV values to NaN and this keeps the client dependency small.
        finite = clean_payment.map(math.isfinite) & clean_installment.map(math.isfinite)
        clean_payment = clean_payment[finite]
        clean_installment = clean_installment[finite]
        dropped_rows += len(chunk) - len(clean_payment)
        kept_rows += len(clean_payment)

        # Plaintext oracle only. Raw parent columns, not these derived values,
        # are written to HE batches.
        feature_started = time.perf_counter()
        diff = clean_installment - clean_payment
        ratio = clean_payment / clean_installment
        pandas_feature_seconds += time.perf_counter() - feature_started
        aggregate_started = time.perf_counter()
        diff_sum += float(diff.sum())
        diff_sum_squares += float((diff * diff).sum())
        ratio_sum += float(ratio.sum())
        ratio_sum_squares += float((ratio * ratio).sum())
        if not ratio.empty:
            ratio_min = min(ratio_min, float(ratio.min()))
            ratio_max = max(ratio_max, float(ratio.max()))
        pandas_aggregation_seconds += time.perf_counter() - aggregate_started

        buffered_payment.extend(clean_payment.tolist())
        buffered_installment.extend(clean_installment.tolist())
        flush()
    flush(final=True)
    if kept_rows < 2:
        raise ValueError("fewer than two usable rows remain after client numeric sanitation")

    def moments(total: float, squares: float) -> dict[str, float]:
        mean = total / kept_rows
        return {
            "sum": total,
            "mean": mean,
            "sample_var": (squares - total * mean) / (kept_rows - 1),
        }

    result = {
        "status": "full_installments_columns_prepared",
        "source_csv": str(input_csv.resolve()),
        "client_sanitation": {
            "raw_rows": raw_rows,
            "requested_raw_row_limit": max_rows or "all source rows",
            "kept_rows": kept_rows,
            "dropped_rows": dropped_rows,
            "rule": "drop only missing/non-finite AMT_PAYMENT or AMT_INSTALMENT, and AMT_INSTALMENT <= 0",
        },
        "packing": {
            "vector_size": vector_size,
            "batch_count": batch_index,
            "last_batch_real_rows": manifest_rows[-1]["real_rows"],
            "last_batch_padding_rows": manifest_rows[-1]["padding_rows"],
        },
        "plaintext_pandas_oracle": {
            "scope": "whole sanitized dataframe; no groupby",
            "source_expressions": [
                "ins['PAYMENT_PERC'] = ins['AMT_PAYMENT'] / ins['AMT_INSTALMENT']",
                "ins['PAYMENT_DIFF'] = ins['AMT_INSTALMENT'] - ins['AMT_PAYMENT']",
            ],
            "payment_diff": moments(diff_sum, diff_sum_squares),
            "payment_perc": {**moments(ratio_sum, ratio_sum_squares), "min": ratio_min, "max": ratio_max},
            "timings_seconds": {
                "pandas_feature_expressions": pandas_feature_seconds,
                "pandas_whole_dataframe_aggregation": pandas_aggregation_seconds,
            },
        },
        "manifest": "batch_manifest.json",
    }
    write_json(output_dir / "batch_manifest.json", {"batches": manifest_rows})
    write_json(output_dir / "preparation_report.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--chunk-rows", type=int, default=100_000)
    parser.add_argument("--max-rows", type=int, default=0, help="raw source-row cap; 0 means all rows")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    if args.max_rows < 0:
        raise ValueError("max_rows must be zero (all rows) or positive")
    print(json.dumps(prepare(args.input_csv.resolve(), root, vector_size=args.vector_size, chunk_rows=args.chunk_rows, max_rows=args.max_rows), indent=2))


if __name__ == "__main__":
    main()
