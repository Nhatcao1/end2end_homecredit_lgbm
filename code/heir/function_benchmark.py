"""Streaming preparation for function-specific reusable-kernel benchmarks."""

from __future__ import annotations

import csv
import math
import time
from pathlib import Path
from typing import Any

from code.heir.common import path_size, write_csv, write_json, write_values
from code.heir.kernels.registry import kernel_contracts
from code.heir.workloads.grouped import FeatureSpec, TaskSpec


def _read_applications(path: Path, row_limit: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"SK_ID_CURR", "TARGET"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"application input is missing columns: {sorted(missing)}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader):
            if row_limit > 0 and index >= row_limit:
                break
            rows.append(row)
    if not rows:
        raise ValueError("application input produced no benchmark rows")
    identifiers = [row["SK_ID_CURR"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("SK_ID_CURR must be unique in selected applications")
    return rows


def _required_columns(task: TaskSpec) -> set[str]:
    required = {"SK_ID_CURR"}
    if task.branch_column:
        required.add(task.branch_column)
    for feature in task.features:
        required.update(feature.source_columns or (feature.name,))
    return required


def _read_selected_rows(
    path: Path,
    task: TaskSpec,
    app_index: dict[str, int],
    row_limit: int,
) -> tuple[list[list[dict[str, str]]], int, int]:
    rows_by_app: list[list[dict[str, str]]] = [[] for _ in app_index]
    scanned = matched = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = _required_columns(task).difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
        for row_index, row in enumerate(reader):
            if row_limit > 0 and row_index >= row_limit:
                break
            scanned += 1
            selected_index = app_index.get(row["SK_ID_CURR"])
            if selected_index is not None:
                rows_by_app[selected_index].append(row)
                matched += 1
    return rows_by_app, scanned, matched


def _bureau_balance_sizes(
    data_dir: Path, rows_by_app: list[list[dict[str, str]]], row_limit: int
) -> tuple[dict[str, int], int]:
    selected_ids = {
        row["SK_ID_BUREAU"]
        for applicant_rows in rows_by_app
        for row in applicant_rows
    }
    counts = {identifier: 0 for identifier in selected_ids}
    scanned = 0
    path = data_dir / "bureau_balance.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if "SK_ID_BUREAU" not in (reader.fieldnames or []):
            raise ValueError("bureau_balance.csv is missing SK_ID_BUREAU")
        for row_index, row in enumerate(reader):
            if row_limit > 0 and row_index >= row_limit:
                break
            scanned += 1
            identifier = row["SK_ID_BUREAU"]
            if identifier in counts:
                counts[identifier] += 1
    return counts, scanned


def _number(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _feature_components(
    feature: FeatureSpec,
    row: dict[str, str],
    bureau_sizes: dict[str, int],
) -> tuple[float, ...] | None:
    columns = feature.source_columns or (feature.name,)
    if feature.transform == "bureau_balance_size":
        count = bureau_sizes.get(row[columns[0]])
        return None if count is None or count == 0 else (float(count),)
    values = tuple(_number(row.get(column)) for column in columns)
    if any(value is None for value in values):
        return None
    numeric = tuple(value for value in values if value is not None)
    if feature.transform == "identity":
        return (numeric[0],)
    if feature.transform == "ratio":
        if numeric[1] == 0:
            return None
        result = numeric[0] / numeric[1]
        return (result,) if math.isfinite(result) else None
    if feature.transform == "positive_difference":
        return (max(numeric[0] - numeric[1], 0.0),)
    if feature.transform == "difference":
        return numeric[0], numeric[1]
    raise ValueError(f"unsupported feature transform: {feature.transform}")


def _statistics(values: list[float], masks: list[float]) -> dict[str, float | None]:
    selected = [value for value, mask in zip(values, masks) if mask == 1.0]
    count = len(selected)
    total = sum(selected)
    sum_squares = sum(value * value for value in selected)
    mean = total / count if count else None
    if count > 1:
        numerator = sum_squares - total * total / count
        variance = max(numerator, 0.0) / (count - 1)
    else:
        variance = None
    return {
        "count": float(count),
        "sum": total,
        "sum_squares": sum_squares,
        "mean": mean,
        "var": variance,
    }


def _padded(values: list[float], width: int) -> list[float]:
    return values + [0.0] * (width - len(values))


def _prepare_count(
    task: TaskSpec,
    rows_by_app: list[list[dict[str, str]]],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, list[dict[str, Any]]]:
    width = max(max((len(rows) for rows in rows_by_app), default=0), 1)
    masks = [
        value
        for rows in rows_by_app
        for value in ([1.0] * len(rows) + [0.0] * (width - len(rows)))
    ]
    write_values(output_dir / "tensors" / "history_mask.csv", masks)
    write_values(output_dir / "tensors" / "unit_weights.csv", [1.0] * width)
    reference = []
    oracle = []
    for app_index, rows in enumerate(rows_by_app):
        count = float(len(rows))
        reference.append(
            {"app_index": app_index, "feature": "ROW_COUNT", "operation": "count", "value": count}
        )
        oracle.append(
            {
                "app_index": app_index,
                "feature": "ROW_COUNT",
                "count": count,
                "sum": count,
                "sum_squares": count,
            }
        )
    manifest = [
        {
            "feature": "ROW_COUNT",
            "kind": "encrypted_mask_matrix",
            "file": "tensors/history_mask.csv",
            "elements": len(masks),
        },
        {
            "feature": "ROW_COUNT",
            "kind": "encrypted_unit_weights",
            "file": "tensors/unit_weights.csv",
            "elements": width,
        },
    ]
    return reference, oracle, width, manifest


def _prepare_features(
    task: TaskSpec,
    rows_by_app: list[list[dict[str, str]]],
    bureau_sizes: dict[str, int],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, list[dict[str, Any]], int]:
    payloads: dict[str, list[tuple[list[float], list[float], list[float]]]] = {}
    dropped_missing = 0
    width = 1
    for feature in task.features:
        applicant_payloads: list[tuple[list[float], list[float], list[float]]] = []
        for rows in rows_by_app:
            left: list[float] = []
            right: list[float] = []
            masks: list[float] = []
            for row in rows:
                components = _feature_components(feature, row, bureau_sizes)
                if components is None:
                    dropped_missing += 1
                    continue
                left.append(components[0])
                if len(components) == 2:
                    right.append(components[1])
                masks.append(
                    1.0
                    if not task.branch_column or row[task.branch_column] == task.branch_value
                    else 0.0
                )
            width = max(width, len(left))
            applicant_payloads.append((left, right, masks))
        payloads[feature.name] = applicant_payloads

    reference: list[dict[str, Any]] = []
    oracle: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for feature in task.features:
        values_flat: list[float] = []
        right_flat: list[float] = []
        masks_flat: list[float] = []
        for app_index, (left, right, masks) in enumerate(payloads[feature.name]):
            calculation_values = (
                [left_value - right_value for left_value, right_value in zip(left, right)]
                if feature.transform == "difference"
                else left
            )
            stats = _statistics(calculation_values, masks)
            oracle.append(
                {
                    "app_index": app_index,
                    "feature": feature.name,
                    "count": stats["count"],
                    "sum": stats["sum"],
                    "sum_squares": stats["sum_squares"],
                }
            )
            for operation in feature.operations:
                reference.append(
                    {
                        "app_index": app_index,
                        "feature": feature.name,
                        "operation": operation,
                        "value": stats[operation],
                    }
                )
            values_flat.extend(_padded(left, width))
            masks_flat.extend(_padded(masks, width))
            if feature.transform == "difference":
                right_flat.extend(_padded(right, width))

        base = output_dir / "tensors" / feature.name.lower()
        value_path = base.with_name(base.name + "_values.csv")
        mask_path = base.with_name(base.name + "_mask.csv")
        write_values(value_path, values_flat)
        write_values(mask_path, masks_flat)
        manifest.extend(
            [
                {
                    "feature": feature.name,
                    "kind": "encrypted_values",
                    "file": str(value_path.relative_to(output_dir)),
                    "elements": len(values_flat),
                },
                {
                    "feature": feature.name,
                    "kind": "encrypted_occupancy_or_branch_mask",
                    "file": str(mask_path.relative_to(output_dir)),
                    "elements": len(masks_flat),
                },
            ]
        )
        if feature.transform == "difference":
            right_path = base.with_name(base.name + "_right_values.csv")
            write_values(right_path, right_flat)
            manifest.append(
                {
                    "feature": feature.name,
                    "kind": "encrypted_right_values",
                    "file": str(right_path.relative_to(output_dir)),
                    "elements": len(right_flat),
                }
            )
    return reference, oracle, width, manifest, dropped_missing


def prepare_function_task(
    task: TaskSpec,
    data_dir: Path,
    application_path: Path,
    output_dir: Path,
    application_row_limit: int = 8,
    source_row_limit: int = 0,
) -> dict[str, Any]:
    """Prepare one task without claiming HEIR-generated CKKS execution."""
    started = time.perf_counter()
    applications = _read_applications(application_path, application_row_limit)
    app_index = {row["SK_ID_CURR"]: index for index, row in enumerate(applications)}
    source_path = data_dir / task.input_file
    rows_by_app, scanned, matched = _read_selected_rows(
        source_path, task, app_index, source_row_limit
    )

    bureau_sizes: dict[str, int] = {}
    auxiliary_rows_scanned = 0
    if any(feature.transform == "bureau_balance_size" for feature in task.features):
        bureau_sizes, auxiliary_rows_scanned = _bureau_balance_sizes(
            data_dir, rows_by_app, source_row_limit
        )

    if task.kind == "count":
        reference, oracle, width, tensor_manifest = _prepare_count(
            task, rows_by_app, output_dir
        )
        dropped_missing = 0
    else:
        reference, oracle, width, tensor_manifest, dropped_missing = _prepare_features(
            task, rows_by_app, bureau_sizes, output_dir
        )

    write_csv(
        output_dir / "plaintext_reference.csv",
        ["app_index", "feature", "operation", "value"],
        reference,
    )
    write_csv(
        output_dir / "kernel_oracle.csv",
        ["app_index", "feature", "count", "sum", "sum_squares"],
        oracle,
    )
    write_csv(
        output_dir / "tensor_manifest.csv",
        ["feature", "kind", "file", "elements"],
        tensor_manifest,
    )
    write_csv(
        output_dir / "client_private" / "applicant_mapping.csv",
        ["app_index", "SK_ID_CURR", "TARGET"],
        [
            {
                "app_index": index,
                "SK_ID_CURR": row["SK_ID_CURR"],
                "TARGET": row["TARGET"],
            }
            for index, row in enumerate(applications)
        ],
    )

    contracts_by_id = {contract.kernel_id: contract for contract in kernel_contracts()}
    summary: dict[str, Any] = {
        "task": task.to_dict(),
        "backend_status": "prepared_only",
        "heir_scheme": "CKKS",
        "application_rows": len(applications),
        "source_rows_scanned": scanned,
        "source_rows_matched": matched,
        "auxiliary_rows_scanned": auxiliary_rows_scanned,
        "slots_per_application": width,
        "client_compacted_missing_values": dropped_missing,
        "kernel_contracts": [contracts_by_id[kernel_id].to_dict() for kernel_id in task.kernel_ids],
        "privacy_boundary": (
            "client groups, derives features/masks, compacts missing values, pads, and encrypts; "
            "server evaluates only fixed-shape arithmetic"
        ),
        "timings_seconds": {"prepare_wall_seconds": time.perf_counter() - started},
    }
    summary["artifact_sizes_bytes"] = {
        "run_directory": path_size(output_dir),
        "tensors": path_size(output_dir / "tensors"),
        "plaintext_reference": path_size(output_dir / "plaintext_reference.csv"),
        "kernel_oracle": path_size(output_dir / "kernel_oracle.csv"),
        "client_private_mapping": path_size(output_dir / "client_private" / "applicant_mapping.csv"),
    }
    write_json(output_dir / "benchmark_summary.json", summary)
    return summary
