"""Read sanitized real installments parents for HE benchmark inputs.

The preparation step has already removed invalid rows.  This module never
derives PAYMENT_DIFF; it returns only the raw parents and the validity mask.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstallmentsParents:
    payment: list[float]
    installment: list[float]
    valid: list[float]
    files_used: list[str]


def load_prepared_parents(prepared_dir: Path, count: int) -> InstallmentsParents:
    if count < 1:
        raise ValueError("value count must be positive")
    batches = prepared_dir / "batches"
    paths = sorted(batches.glob("batch_*.csv"))
    if not paths:
        raise FileNotFoundError(f"no prepared installments batches under {batches}")
    payment: list[float] = []
    installment: list[float] = []
    used: list[str] = []
    for path in paths:
        added = False
        with path.open(newline="", encoding="utf-8-sig") as source:
            for row in csv.DictReader(source):
                if float(row["valid"]) != 1.0:
                    continue
                paid, due = float(row["AMT_PAYMENT"]), float(row["AMT_INSTALMENT"])
                if not math.isfinite(paid) or not math.isfinite(due):
                    raise ValueError(f"prepared batch unexpectedly contains non-finite input: {path}")
                payment.append(paid)
                installment.append(due)
                added = True
                if len(payment) == count:
                    break
        if added:
            used.append(str(path.relative_to(prepared_dir)))
        if len(payment) == count:
            break
    if len(payment) != count:
        raise ValueError(f"prepared data has only {len(payment)} valid rows; requested {count}")
    return InstallmentsParents(payment, installment, [1.0] * count, used)


def public_power_of_two_scale(*columns: list[float]) -> float:
    maximum = max(abs(value) for column in columns for value in column)
    return float(1 << max(1, math.ceil(math.log2(max(1.0, 2.0 * maximum + 1.0)))) )
