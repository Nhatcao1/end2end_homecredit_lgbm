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
    source_row_count: int | None = None
    dropped_invalid_rows: int = 0


@dataclass(frozen=True)
class PreparedGroupSelection:
    """Real groups selected from one population-wide client preparation."""

    groups: list[PreparedPaymentGroup]
    population_group_count: int
    eligible_group_count: int
    selection_policy: str
    source_directory: str


@dataclass(frozen=True)
class PreparedParentColumns:
    """Sanitized parent columns loaded from client-prepared batch files."""

    installment: list[float]
    payment: list[float]
    files_used: list[str]


def public_power_of_two_scale(values: list[float]) -> float:
    """Choose a public scale placing finite values inside (-0.5, 0.5]."""
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("scale values must be non-empty and finite")
    maximum = max(abs(value) for value in values)
    required = max(2.0, 2.0 * maximum + 1.0)
    return float(1 << math.ceil(math.log2(required)))


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
        source_row_count=len(installment),
    )


def _spread_selection(
    candidates: list[tuple[int, int]],
    count: int,
) -> list[tuple[int, int]]:
    """Select across the complete public group-size distribution."""
    ordered = sorted(candidates, key=lambda item: (item[1], item[0]))
    if count == 1:
        return [ordered[len(ordered) // 2]]
    return [
        ordered[index * (len(ordered) - 1) // (count - 1)]
        for index in range(count)
    ]


def load_prepared_group_population(
    prepared_population_dir: Path,
    *,
    group_count: int,
    opaque_group_ids: list[int] | None = None,
    selection_policy: str = "spread",
) -> PreparedGroupSelection:
    """Load complete real groups from a population-wide client preparation.

    The mapping defines the full candidate population. Parent-row shards are
    scanned once and only selected opaque groups are retained. Rows with
    missing or non-finite parent values are removed at this client boundary.
    A group split into several fixed preparation blocks is reassembled before
    it is returned to the HE runner.
    """
    root = prepared_population_dir.resolve()
    mapping_path = root / "client_private" / "group_mapping.csv"
    parent_dir = root / "client_private" / "parent_rows"
    if not mapping_path.is_file():
        raise FileNotFoundError(
            f"prepared population mapping is missing: {mapping_path}"
        )
    parent_paths = sorted(parent_dir.glob("parent_rows_*.csv"))
    if not parent_paths:
        raise FileNotFoundError(
            f"prepared population parent shards are missing: {parent_dir}"
        )

    mapping: dict[int, int] = {}
    with mapping_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            group_id = int(row["opaque_group_id"])
            if group_id in mapping:
                raise ValueError(f"duplicate opaque group ID: {group_id}")
            mapping[group_id] = int(row["source_rows"])
    if not mapping:
        raise ValueError(f"prepared population is empty: {root}")

    # Sample variance requires at least two source rows. Numeric sanitation
    # below may still reject an explicitly selected group if fewer than two
    # usable parent rows remain.
    candidates = [
        (group_id, source_rows)
        for group_id, source_rows in mapping.items()
        if source_rows >= 2
    ]
    explicit_ids = list(opaque_group_ids or [])
    if explicit_ids:
        if len(set(explicit_ids)) != len(explicit_ids):
            raise ValueError("opaque_group_ids must be unique")
        missing = [group_id for group_id in explicit_ids if group_id not in mapping]
        if missing:
            raise ValueError(f"unknown opaque group IDs: {missing}")
        selected = [(group_id, mapping[group_id]) for group_id in explicit_ids]
        policy = "explicit"
    else:
        if group_count < 0:
            raise ValueError("group_count must be zero (all) or positive")
        requested = len(candidates) if group_count == 0 else group_count
        if requested < 2:
            raise ValueError("select at least two prepared groups")
        if requested > len(candidates):
            raise ValueError(
                f"requested {requested} groups but only "
                f"{len(candidates)} have at least two source rows"
            )
        if selection_policy == "spread":
            selected = _spread_selection(candidates, requested)
        elif selection_policy == "largest":
            selected = sorted(
                candidates,
                key=lambda item: (-item[1], item[0]),
            )[:requested]
        elif selection_policy == "first":
            selected = candidates[:requested]
        else:
            raise ValueError(
                "selection_policy must be one of: spread, largest, first"
            )
        policy = selection_policy

    selected_ids = {group_id for group_id, _ in selected}
    accumulated: dict[int, list[tuple[int, int, float, float]]] = {
        group_id: [] for group_id in selected_ids
    }
    invalid_counts = {group_id: 0 for group_id in selected_ids}
    for path in parent_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                group_id = int(row["opaque_group_id"])
                if group_id not in selected_ids:
                    continue
                try:
                    paid = float(row["AMT_PAYMENT"])
                    due = float(row["AMT_INSTALMENT"])
                except (TypeError, ValueError):
                    invalid_counts[group_id] += 1
                    continue
                if not math.isfinite(paid) or not math.isfinite(due):
                    invalid_counts[group_id] += 1
                    continue
                accumulated[group_id].append(
                    (
                        int(row["group_block_index"]),
                        int(row["lane"]),
                        due,
                        paid,
                    )
                )

    groups: list[PreparedPaymentGroup] = []
    for group_id, source_rows in selected:
        ordered_rows = sorted(accumulated[group_id])
        if len(ordered_rows) < 2:
            raise ValueError(
                f"opaque group {group_id} has fewer than two valid parent rows "
                "after client numeric sanitation"
            )
        if len(ordered_rows) + invalid_counts[group_id] != source_rows:
            raise ValueError(
                f"opaque group {group_id} is incomplete: mapping records "
                f"{source_rows} source rows, loaded "
                f"{len(ordered_rows) + invalid_counts[group_id]}"
            )
        groups.append(
            PreparedPaymentGroup(
                applicant_id=f"opaque_{group_id}",
                slot_count=len(ordered_rows),
                installment=[row[2] for row in ordered_rows],
                payment=[row[3] for row in ordered_rows],
                source_row_count=source_rows,
                dropped_invalid_rows=invalid_counts[group_id],
            )
        )
    return PreparedGroupSelection(
        groups=groups,
        population_group_count=len(mapping),
        eligible_group_count=len(candidates),
        selection_policy=policy,
        source_directory=str(root),
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
