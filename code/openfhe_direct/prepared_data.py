"""Small loader for client-prepared installment/payment groups."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreparedPaymentGroup:
    applicant_id: str
    slot_count: int
    installment: list[float]
    payment: list[float]


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
