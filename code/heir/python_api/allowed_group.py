"""Client preparation for one explicitly allowed complete installment group."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
import math
from pathlib import Path

from code.heir.python_api.official_groupby import OpaquePaymentGroup


FIELDS = (
    "SK_ID_CURR",
    "opaque_group_id",
    "lane",
    "source_row",
    "AMT_INSTALMENT",
    "AMT_PAYMENT",
    "VALID_MASK",
)


class CompleteGroupDoesNotFitError(ValueError):
    """The client refused to truncate or split an allowed complete group."""


@dataclass(frozen=True)
class PreparedAllowedGroup:
    raw_applicant_id: str
    group: OpaquePaymentGroup
    bucket_size: int
    validity_mask: tuple[int, ...]
    source_rows: tuple[int, ...]
    removed_null_rows: int = 0
    source_rows_scanned: int = 0
    allowed_rows_before_null_removal: int = 0


def _number(value: str | None) -> float | None:
    try:
        result = float(value or "")
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def prepare_allowed_group_csv(
    installments: Path,
    *,
    allowed_sk_id_curr: str,
    bucket_size: int,
    output_csv: Path,
    overwrite: bool = False,
) -> PreparedAllowedGroup:
    """Write one complete, fixed-width group selected by the data owner.

    Rows remain in stable source order. Null/non-numeric parent rows are
    removed before the completeness check. The function never truncates or
    splits a group: an oversized complete group is rejected before HE.
    """
    if bucket_size < 2:
        raise ValueError("bucket_size must be at least two")
    allowed = str(allowed_sk_id_curr).strip()
    if not allowed:
        raise ValueError("allowed SK_ID_CURR must not be empty")
    if not installments.is_file():
        raise FileNotFoundError(f"installments CSV is missing: {installments}")
    if output_csv.exists() and not overwrite:
        raise FileExistsError(
            f"refusing to overwrite prepared group: {output_csv}"
        )

    clean: list[tuple[int, float, float]] = []
    removed_null_rows = 0
    source_rows_scanned = 0
    allowed_rows = 0
    with installments.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"SK_ID_CURR", "AMT_INSTALMENT", "AMT_PAYMENT"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"installments CSV is missing columns: {sorted(missing)}"
            )
        for source_row, row in enumerate(reader):
            source_rows_scanned += 1
            if (row.get("SK_ID_CURR") or "").strip() != allowed:
                continue
            allowed_rows += 1
            installment = _number(row.get("AMT_INSTALMENT"))
            payment = _number(row.get("AMT_PAYMENT"))
            if installment is None or payment is None:
                removed_null_rows += 1
                continue
            clean.append((source_row, installment, payment))

    if not clean:
        raise ValueError(
            f"allowed applicant {allowed} has no clean installment rows"
        )
    if len(clean) > bucket_size:
        raise CompleteGroupDoesNotFitError(
            f"allowed applicant {allowed} has {len(clean)} clean rows, "
            f"exceeding bucket {bucket_size}; the complete group was not "
            "sent to HE because this simple benchmark neither truncates nor "
            "splits groups"
        )

    # The source-row index makes the stable ordering explicit and auditable.
    clean.sort(key=lambda row: row[0])
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for lane in range(bucket_size):
            if lane < len(clean):
                source_row, installment, payment = clean[lane]
                mask = 1
            else:
                source_row, installment, payment, mask = "", 0.0, 0.0, 0
            writer.writerow(
                {
                    "SK_ID_CURR": allowed,
                    "opaque_group_id": 0,
                    "lane": lane,
                    "source_row": source_row,
                    "AMT_INSTALMENT": installment,
                    "AMT_PAYMENT": payment,
                    "VALID_MASK": mask,
                }
            )
    return replace(
        load_prepared_allowed_group(output_csv),
        removed_null_rows=removed_null_rows,
        source_rows_scanned=source_rows_scanned,
        allowed_rows_before_null_removal=allowed_rows,
    )


def load_prepared_allowed_group(path: Path) -> PreparedAllowedGroup:
    """Validate and load a fixed-width client-prepared group CSV."""
    if not path.is_file():
        raise FileNotFoundError(f"prepared group CSV is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != set(FIELDS):
            raise ValueError(f"unexpected prepared group schema in {path}")
        rows = list(reader)
    if len(rows) < 2:
        raise ValueError("prepared group must contain at least two lanes")
    if [int(row["lane"]) for row in rows] != list(range(len(rows))):
        raise ValueError("prepared group lanes must be contiguous from zero")
    applicants = {row["SK_ID_CURR"].strip() for row in rows}
    opaque = {int(row["opaque_group_id"]) for row in rows}
    if len(applicants) != 1 or opaque != {0}:
        raise ValueError("prepared file must contain one opaque applicant group")

    masks = tuple(int(row["VALID_MASK"]) for row in rows)
    if any(mask not in (0, 1) for mask in masks):
        raise ValueError("VALID_MASK must contain only zero or one")
    real_count = sum(masks)
    if real_count < 2 or masks != (1,) * real_count + (0,) * (
        len(rows) - real_count
    ):
        raise ValueError(
            "VALID_MASK must be contiguous real lanes followed by padding"
        )
    for row in rows[real_count:]:
        if float(row["AMT_INSTALMENT"]) != 0.0 or float(
            row["AMT_PAYMENT"]
        ) != 0.0:
            raise ValueError("mask-zero parent padding must be numeric zero")

    real = rows[:real_count]
    return PreparedAllowedGroup(
        raw_applicant_id=next(iter(applicants)),
        group=OpaquePaymentGroup(
            opaque_group_id=0,
            installment=tuple(float(row["AMT_INSTALMENT"]) for row in real),
            payment=tuple(float(row["AMT_PAYMENT"]) for row in real),
        ),
        bucket_size=len(rows),
        validity_mask=masks,
        source_rows=tuple(int(row["source_row"]) for row in real),
    )
