"""Prepare the exact POS_COUNT feature as anonymous fixed-shape tensors."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from code.heir.common import write_csv, write_json, write_values


REFERENCE_CODE = '''pos_count = (
    POS_CASH_balance.groupby("SK_ID_CURR").size().rename("POS_COUNT")
)'''


def _read_applications(path: Path, row_limit: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"SK_ID_CURR", "TARGET"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"application input is missing columns: {sorted(missing)}")
        for index, row in enumerate(reader):
            if row_limit > 0 and index >= row_limit:
                break
            rows.append(row)
    if not rows:
        raise ValueError("application input produced no benchmark rows")
    identifiers = [row["SK_ID_CURR"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("SK_ID_CURR must be unique in the selected application rows")
    return rows


def prepare_pos_count(
    application_path: Path,
    pos_path: Path,
    output_dir: Path,
    application_row_limit: int = 8,
    pos_row_limit: int = 0,
) -> dict[str, Any]:
    """Create one padded 0/1 POS-history row per anonymous applicant.

    Applicant IDs and TARGET remain under ``client_private``. The HE tensor
    exposes only row order, padding shape, and 0/1 history occupancy.
    """
    started = time.perf_counter()
    load_started = time.perf_counter()
    applications = _read_applications(application_path, application_row_limit)
    application_load_seconds = time.perf_counter() - load_started

    app_index = {row["SK_ID_CURR"]: index for index, row in enumerate(applications)}
    counts = [0] * len(applications)
    scanned_rows = 0
    pos_started = time.perf_counter()
    with pos_path.open("r", encoding="utf-8-sig", newline="") as source:
        # csv.reader avoids constructing a dictionary for every one of the
        # roughly ten million POS rows when only SK_ID_CURR is needed.
        reader = csv.reader(source)
        header = next(reader, [])
        if "SK_ID_CURR" not in header:
            raise ValueError("POS_CASH_balance input is missing SK_ID_CURR")
        id_column = header.index("SK_ID_CURR")
        for row_index, row in enumerate(reader):
            if pos_row_limit > 0 and row_index >= pos_row_limit:
                break
            scanned_rows += 1
            selected_index = app_index.get(row[id_column])
            if selected_index is not None:
                counts[selected_index] += 1
    pos_scan_seconds = time.perf_counter() - pos_started

    # A minimum width of one keeps the HE tensor well-formed for applicants
    # with no POS history in the selected input scope.
    slots_per_application = max(max(counts), 1)
    matched_rows = sum(counts)
    tensor_started = time.perf_counter()
    tensor_dir = output_dir / "tensors"

    def history_values():
        for count in counts:
            yield from (1.0 for _ in range(count))
            yield from (0.0 for _ in range(slots_per_application - count))

    mask_elements = write_values(tensor_dir / "history_mask_matrix.csv", history_values())
    write_values(tensor_dir / "unit_weights.csv", (1.0 for _ in range(slots_per_application)))

    reference_rows = [
        {"app_index": index, "POS_COUNT": count, "has_pos_history": int(count > 0)}
        for index, count in enumerate(counts)
    ]
    private_rows = [
        {
            "app_index": index,
            "SK_ID_CURR": row["SK_ID_CURR"],
            "TARGET": row["TARGET"],
        }
        for index, row in enumerate(applications)
    ]
    write_csv(
        output_dir / "plaintext_reference.csv",
        ["app_index", "POS_COUNT", "has_pos_history"],
        reference_rows,
    )
    write_csv(
        output_dir / "client_private" / "applicant_mapping.csv",
        ["app_index", "SK_ID_CURR", "TARGET"],
        private_rows,
    )
    manifest_rows = [
        {
            "name": "history_mask_matrix",
            "kind": "history_mask_matrix",
            "file": "tensors/history_mask_matrix.csv",
            "elements": mask_elements,
        },
        {
            "name": "unit_weights",
            "kind": "unit_weights",
            "file": "tensors/unit_weights.csv",
            "elements": slots_per_application,
        },
    ]
    write_csv(
        output_dir / "tensor_manifest.csv",
        ["name", "kind", "file", "elements"],
        manifest_rows,
    )
    tensor_seconds = time.perf_counter() - tensor_started

    summary: dict[str, Any] = {
        "workload": "pos_count",
        "title": "POS cash history count per applicant",
        "source_operation": REFERENCE_CODE,
        "application_input": str(application_path),
        "pos_input": str(pos_path),
        "application_rows": len(applications),
        "pos_rows_scanned": scanned_rows,
        "matched_pos_rows": matched_rows,
        "slots_per_application": slots_per_application,
        "padding_slots": len(applications) * slots_per_application - matched_rows,
        "kernel": "dot_product(history_mask, unit_weights)",
        "he_boundary": "trusted row alignment; encrypted fixed-shape dot product",
        "backend_status": "prepared_only",
        "timings_seconds": {
            "application_load_seconds": application_load_seconds,
            "pos_scan_seconds": pos_scan_seconds,
            "tensor_materialization_seconds": tensor_seconds,
            "prepare_wall_seconds": time.perf_counter() - started,
        },
    }
    write_json(output_dir / "workload_spec.json", summary)
    return summary
