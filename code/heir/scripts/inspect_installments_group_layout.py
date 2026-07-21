#!/usr/bin/env python3
"""Client-only inspection of installment group counts and block sizes.

No ciphertext, validity mask, or feature calculation is created here. The
result helps choose public group buckets before implementing grouped HE.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json


def describe(counts: Counter[int]) -> dict[str, float | int]:
    values = sorted(counts.values())
    if not values:
        return {"groups": 0, "min_rows": 0, "max_rows": 0, "mean_rows": 0.0}

    def percentile(fraction: float) -> int:
        return values[min(len(values) - 1, int(math.ceil(fraction * len(values))) - 1)]

    return {
        "groups": len(values),
        "min_rows": values[0],
        "max_rows": values[-1],
        "mean_rows": sum(values) / len(values),
        "p50_rows": percentile(0.50),
        "p90_rows": percentile(0.90),
        "p95_rows": percentile(0.95),
        "p99_rows": percentile(0.99),
    }


def bucket_coverage(counts: Counter[int], capacities: tuple[int, ...] = (8, 16, 32, 64, 128)) -> dict[str, int]:
    """Return number of groups fitting each public capacity; no encoding occurs."""
    values = counts.values()
    return {str(capacity): sum(count <= capacity for count in values) for capacity in capacities}


def inspect(input_csv: Path, *, chunk_rows: int, max_rows: int) -> dict[str, object]:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("this client-only inspector requires pandas; run `python3 -m pip install pandas`") from error
    if not input_csv.is_file():
        raise FileNotFoundError(input_csv)
    raw_counts: Counter[int] = Counter()
    clean_counts: Counter[int] = Counter()
    raw_rows = clean_rows = missing_group_rows = 0
    for chunk in pd.read_csv(
        input_csv,
        usecols=["SK_ID_CURR", "AMT_PAYMENT", "AMT_INSTALMENT"],
        chunksize=chunk_rows,
    ):
        if max_rows and raw_rows >= max_rows:
            break
        if max_rows and raw_rows + len(chunk) > max_rows:
            chunk = chunk.iloc[: max_rows - raw_rows]
        raw_rows += len(chunk)
        ids = pd.to_numeric(chunk["SK_ID_CURR"], errors="coerce")
        has_id = ids.notna()
        missing_group_rows += int((~has_id).sum())
        raw_counts.update(int(value) for value in ids[has_id])
        payment = pd.to_numeric(chunk["AMT_PAYMENT"], errors="coerce")
        installment = pd.to_numeric(chunk["AMT_INSTALMENT"], errors="coerce")
        valid = has_id & payment.notna() & installment.notna() & (installment > 0)
        clean_ids = ids[valid]
        clean_counts.update(int(value) for value in clean_ids)
        clean_rows += len(clean_ids)
    return {
        "status": "client_only_group_layout_inspected",
        "source_csv": str(input_csv.resolve()),
        "requested_raw_row_limit": max_rows or "all source rows",
        "raw_rows_processed": raw_rows,
        "rows_without_sk_id_curr": missing_group_rows,
        "raw_groups": describe(raw_counts),
        "after_numeric_sanitation": {
            "rows": clean_rows,
            "groups": describe(clean_counts),
            "rule": "SK_ID_CURR present; AMT_PAYMENT and AMT_INSTALMENT numeric; AMT_INSTALMENT > 0",
            "candidate_public_bucket_coverage": bucket_coverage(clean_counts),
        },
        "privacy_note": "This report runs entirely with the data owner. Do not send raw SK_ID_CURR or this per-group mapping to the HE evaluator; later encoding uses opaque group ordinals.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chunk-rows", type=int, default=100_000)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    if args.max_rows < 0:
        raise ValueError("max_rows must be zero (all) or positive")
    report = inspect(args.input_csv.resolve(), chunk_rows=args.chunk_rows, max_rows=args.max_rows)
    write_json(args.output.resolve(), report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
