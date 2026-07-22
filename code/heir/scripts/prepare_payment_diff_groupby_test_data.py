#!/usr/bin/env python3
"""Create a small, real-data, client-side fixture for grouped PAYMENT_DIFF.

The original feature code groups installment rows by ``SK_ID_CURR``.  This
preparer proves that data layout before any HE is involved:

* it chooses complete, numeric customer groups from the real source CSV;
* it writes fixed-size parent-column blocks with an explicit 1/0 mask;
* it writes a plaintext groupby reference for audit only.

``PAYMENT_DIFF`` is intentionally *not* put in the HE-ready file.  A later HE
runner must encrypt the two parent columns and calculate
``AMT_INSTALMENT - AMT_PAYMENT`` itself.  Raw identifiers and plaintext
aggregates stay under ``client_private/``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from pathlib import Path


REQUIRED_COLUMNS = ("SK_ID_CURR", "AMT_PAYMENT", "AMT_INSTALMENT")


def _numeric(value: str) -> float | None:
    """Return one finite numeric amount, otherwise ``None``."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _candidate_key(identifier: str, seed: str) -> bytes:
    """Stable client-side ordering; the raw identifier is never emitted publicly."""
    return hashlib.blake2b(
        f"{seed}|{identifier}".encode("utf-8"), digest_size=16
    ).digest()


def _read_valid_group_counts(input_csv: Path, max_rows: int) -> tuple[Counter[str], dict[str, int]]:
    """First pass: count only complete rows that can enter PAYMENT_DIFF."""
    counts: Counter[str] = Counter()
    metrics = {"source_rows": 0, "missing_identifier_rows": 0, "invalid_parent_rows": 0}
    with input_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"installments CSV is missing columns: {sorted(missing)}")
        for row in reader:
            if max_rows and metrics["source_rows"] >= max_rows:
                break
            metrics["source_rows"] += 1
            identifier = row["SK_ID_CURR"].strip()
            if not identifier:
                metrics["missing_identifier_rows"] += 1
                continue
            if _numeric(row["AMT_PAYMENT"]) is None or _numeric(row["AMT_INSTALMENT"]) is None:
                metrics["invalid_parent_rows"] += 1
                continue
            counts[identifier] += 1
    return counts, metrics


def _read_selected_rows(
    input_csv: Path, selected: set[str], max_rows: int
) -> dict[str, list[tuple[float, float]]]:
    """Second pass: retain parent columns for the already-selected complete groups."""
    rows: dict[str, list[tuple[float, float]]] = defaultdict(list)
    processed = 0
    with input_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if max_rows and processed >= max_rows:
                break
            processed += 1
            identifier = row["SK_ID_CURR"].strip()
            if identifier not in selected:
                continue
            payment = _numeric(row["AMT_PAYMENT"])
            installment = _numeric(row["AMT_INSTALMENT"])
            if payment is None or installment is None:
                continue
            rows[identifier].append((payment, installment))
    return rows


def _sample_variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = math.fsum(values) / len(values)
    return math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _format_number(value: float) -> str:
    return format(value, ".17g")


def _write_markdown(path: Path, report: dict[str, object]) -> None:
    layout = report["layout"]
    source = report["source"]
    path.write_text(
        "# PAYMENT_DIFF groupby preparation fixture\n\n"
        "This is a client-only layout proof. It contains no ciphertext and does not "
        "claim an HE result. The HE-ready file retains parent columns only; later HE "
        "must calculate `AMT_INSTALMENT - AMT_PAYMENT` after encryption.\n\n"
        "## Selection\n\n"
        f"- Source rows read: `{source['source_rows']}`\n"
        f"- Selected complete applicant groups: `{layout['selected_groups']}`\n"
        f"- Real selected rows: `{layout['selected_real_rows']}`\n"
        f"- Fixed block size: `{layout['bucket_size']}`\n"
        f"- Padding lanes: `{layout['padding_lanes']}`\n\n"
        "## Files\n\n"
        "- `he_ready/group_blocks.csv`: numeric parent columns and a 1/0 validity mask; safe input for a later HE test.\n"
        "- `client_private/pandas_groupby_reference.csv`: plaintext `count`, `sum`, `mean`, and sample `var` audit reference.\n"
        "- `client_private/group_mapping.csv`: raw `SK_ID_CURR` mapping; never send this file to the HE evaluator.\n"
        "- `layout_manifest.json`: non-sensitive shape and padding metadata.\n",
        encoding="utf-8",
    )


def prepare(
    input_csv: Path,
    output_dir: Path,
    *,
    group_count: int,
    bucket_size: int,
    vector_size: int,
    max_rows: int,
    seed: str,
    selection: str,
) -> dict[str, object]:
    """Write a small complete-group fixture from the raw installments source."""
    if not input_csv.is_file():
        raise FileNotFoundError(f"installments CSV is missing: {input_csv}")
    if group_count < 1:
        raise ValueError("group_count must be positive")
    if bucket_size < 1:
        raise ValueError("bucket_size must be positive")
    if vector_size < bucket_size or vector_size % bucket_size:
        raise ValueError("vector_size must be a multiple of bucket_size")

    counts, source_metrics = _read_valid_group_counts(input_csv, max_rows)
    fitting = [identifier for identifier, count in counts.items() if count <= bucket_size]
    if selection == "hash-sample":
        selected_identifiers = sorted(fitting, key=lambda item: _candidate_key(item, seed))[:group_count]
        selection_method = "deterministic client-only hash ranking among groups that fit exactly one block"
    elif selection == "largest-fitting":
        selected_identifiers = sorted(
            fitting, key=lambda item: (-counts[item], _candidate_key(item, seed))
        )[:group_count]
        selection_method = "largest complete numeric groups that fit exactly one block; hash only breaks equal-size ties"
    else:
        raise ValueError(f"unknown selection mode: {selection}")
    if len(selected_identifiers) < group_count:
        raise ValueError(
            f"only {len(selected_identifiers)} complete numeric groups fit bucket_size={bucket_size}; "
            f"requested {group_count}"
        )
    selected_rows = _read_selected_rows(input_csv, set(selected_identifiers), max_rows)
    missing = [identifier for identifier in selected_identifiers if len(selected_rows[identifier]) != counts[identifier]]
    if missing:
        raise RuntimeError("the two source passes disagree on selected group rows")

    private = output_dir / "client_private"
    he_ready = output_dir / "he_ready"
    private.mkdir(parents=True)
    he_ready.mkdir(parents=True)
    blocks_per_ciphertext = vector_size // bucket_size
    mapping_path = private / "group_mapping.csv"
    reference_path = private / "pandas_groupby_reference.csv"
    blocks_path = he_ready / "group_blocks.csv"
    padding_lanes = real_rows = 0
    with mapping_path.open("w", encoding="utf-8", newline="") as mapping_file, reference_path.open(
        "w", encoding="utf-8", newline=""
    ) as reference_file, blocks_path.open("w", encoding="utf-8", newline="") as blocks_file:
        mapping = csv.writer(mapping_file)
        reference = csv.writer(reference_file)
        blocks = csv.writer(blocks_file)
        mapping.writerow(["opaque_group_id", "SK_ID_CURR", "real_rows"])
        reference.writerow(["opaque_group_id", "count", "payment_diff_sum", "payment_diff_mean", "payment_diff_var"])
        blocks.writerow(
            [
                "packed_ciphertext_batch",
                "segment_index",
                "opaque_group_id",
                "lane",
                "AMT_PAYMENT",
                "AMT_INSTALMENT",
                "validity_mask",
            ]
        )
        for opaque_group_id, identifier in enumerate(selected_identifiers):
            parents = selected_rows[identifier]
            if len(parents) > bucket_size:
                raise RuntimeError("selected group no longer fits its declared bucket")
            mapping.writerow([opaque_group_id, identifier, len(parents)])
            differences = [installment - payment for payment, installment in parents]
            total = math.fsum(differences)
            mean = total / len(differences)
            variance = _sample_variance(differences)
            reference.writerow(
                [
                    opaque_group_id,
                    len(differences),
                    _format_number(total),
                    _format_number(mean),
                    "" if variance is None else _format_number(variance),
                ]
            )
            batch, segment = divmod(opaque_group_id, blocks_per_ciphertext)
            for lane in range(bucket_size):
                if lane < len(parents):
                    payment, installment = parents[lane]
                    blocks.writerow(
                        [batch, segment, opaque_group_id, lane, _format_number(payment), _format_number(installment), 1]
                    )
                    real_rows += 1
                else:
                    blocks.writerow([batch, segment, opaque_group_id, lane, "0", "0", 0])
                    padding_lanes += 1

    report: dict[str, object] = {
        "status": "client_only_payment_diff_groupby_fixture_prepared",
        "source": {
            "csv": str(input_csv.resolve()),
            "source_row_limit": max_rows or "all source rows",
            **source_metrics,
            "numeric_sanitation_rule": "SK_ID_CURR present; AMT_PAYMENT and AMT_INSTALMENT finite numeric",
        },
        "selection": {
            "method": selection_method,
            "seed": seed,
            "eligible_complete_groups": len(fitting),
            "excluded_oversized_groups": sum(count > bucket_size for count in counts.values()),
            "raw_identifiers_written_only_to": "client_private/group_mapping.csv",
        },
        "layout": {
            "group_key": "SK_ID_CURR",
            "selected_groups": len(selected_identifiers),
            "selected_real_rows": real_rows,
            "bucket_size": bucket_size,
            "ckks_vector_size": vector_size,
            "blocks_per_ciphertext": blocks_per_ciphertext,
            "planned_packed_ciphertext_batches": math.ceil(len(selected_identifiers) / blocks_per_ciphertext),
            "padding_lanes": padding_lanes,
            "mask_rule": "real parent row=1; zero padded lane=0",
        },
        "artifacts": {
            "he_ready_parent_blocks": "he_ready/group_blocks.csv",
            "client_private_reference": "client_private/pandas_groupby_reference.csv",
            "client_private_mapping": "client_private/group_mapping.csv",
        },
        "scope": {
            "he_operation": "not run",
            "derived_payment_diff_in_he_ready_file": False,
            "reference_definition": "Pandas-equivalent groupby after the documented row filter: PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT; count/sum/mean/sample variance (ddof=1)",
        },
    }
    (output_dir / "layout_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_markdown(output_dir / "GROUPBY_PREPARATION_REPORT.md", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--group-count", type=int, default=10)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--max-rows", type=int, default=0, help="source-row cap; 0 reads all rows")
    parser.add_argument("--seed", default="payment-diff-groupby-fixture-v1")
    parser.add_argument("--selection", choices=("hash-sample", "largest-fitting"), default="hash-sample")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.max_rows < 0:
        raise ValueError("max_rows must be zero (all) or positive")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    report = prepare(
        args.input_csv.resolve(),
        root,
        group_count=args.group_count,
        bucket_size=args.bucket_size,
        vector_size=args.vector_size,
        max_rows=args.max_rows,
        seed=args.seed,
        selection=args.selection,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
