"""Small loader for client-prepared installment/payment groups."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import math
from pathlib import Path


@dataclass(frozen=True)
class PreparedPaymentGroup:
    applicant_id: str
    slot_count: int
    installment: list[float]
    payment: list[float]


@dataclass(frozen=True)
class PreparedParentColumns:
    """Sanitized parent columns loaded from client-prepared batch files."""

    installment: list[float]
    payment: list[float]
    files_used: list[str]


def load_prepared_group(path: Path) -> PreparedPaymentGroup:
    """Read mask-one rows; validate and discard trailing zero padding."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"prepared group is empty: {path}")

    installment: list[float] = []
    payment: list[float] = []
    padding_started = False
    for expected_lane, row in enumerate(rows):
        if int(row["lane"]) != expected_lane:
            raise ValueError(f"non-contiguous lanes in {path}")
        mask = int(row["VALID_MASK"])
        due = float(row["AMT_INSTALMENT"])
        paid = float(row["AMT_PAYMENT"])
        if mask == 0:
            padding_started = True
            if due != 0.0 or paid != 0.0:
                raise ValueError(f"padding must be zero in {path}")
            continue
        if mask != 1 or padding_started:
            raise ValueError(f"invalid mask layout in {path}")
        installment.append(due)
        payment.append(paid)

    applicants = {row["SK_ID_CURR"] for row in rows}
    if len(applicants) != 1 or len(installment) < 2:
        raise ValueError(f"expected one group with at least two rows in {path}")
    return PreparedPaymentGroup(
        applicant_id=applicants.pop(),
        slot_count=len(rows),
        installment=installment,
        payment=payment,
    )


def load_prepared_parent_columns(
    prepared_dir: Path,
    value_count: int,
) -> PreparedParentColumns:
    """Load the first N valid parent rows from fixed-width prepared batches."""
    if value_count < 1:
        raise ValueError("value_count must be positive")
    batches = prepared_dir / "batches"
    paths = sorted(batches.glob("batch_*.csv"))
    if not paths:
        raise FileNotFoundError(
            f"no prepared installments batches under {batches}"
        )

    installment: list[float] = []
    payment: list[float] = []
    files_used: list[str] = []
    for path in paths:
        used = False
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if float(row["valid"]) != 1.0:
                    continue
                due = float(row["AMT_INSTALMENT"])
                paid = float(row["AMT_PAYMENT"])
                if not math.isfinite(due) or not math.isfinite(paid):
                    raise ValueError(
                        f"non-finite value in prepared batch: {path}"
                    )
                installment.append(due)
                payment.append(paid)
                used = True
                if len(installment) == value_count:
                    break
        if used:
            files_used.append(str(path.relative_to(prepared_dir)))
        if len(installment) == value_count:
            break

    if len(installment) != value_count:
        raise ValueError(
            f"prepared data has {len(installment)} valid rows; "
            f"requested {value_count}"
        )
    return PreparedParentColumns(
        installment=installment,
        payment=payment,
        files_used=files_used,
    )
