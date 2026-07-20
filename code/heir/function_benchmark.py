"""Streaming preparation for function-specific reusable-kernel benchmarks."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

from code.heir.common import path_size, write_csv, write_json, write_values
from code.heir.kernels.registry import kernel_contracts
from code.heir.workloads.grouped import FeatureSpec, FunctionSpec, TaskSpec


def _read_applications(path: Path, row_limit: int) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = set(reader.fieldnames or [])
        required = {"SK_ID_CURR"}
        missing = required.difference(fields)
        if missing:
            raise ValueError(f"application input is missing columns: {sorted(missing)}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader):
            if row_limit > 0 and index >= row_limit:
                break
            if (
                "app_index" in fields
                and (row.get("app_index") or "").strip() != str(index)
            ):
                raise ValueError(
                    "application layout app_index must be dense and match CSV row order"
                )
            normalized = dict(row)
            normalized["SK_ID_CURR"] = (row.get("SK_ID_CURR") or "").strip()
            normalized["TARGET"] = (row.get("TARGET") or "").strip()
            rows.append(normalized)
    if not rows:
        raise ValueError("application input produced no benchmark rows")
    identifiers = [row["SK_ID_CURR"] for row in rows if row["SK_ID_CURR"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(
            "non-empty SK_ID_CURR values must be unique in selected applications"
        )
    return rows


def _required_columns(task: TaskSpec) -> set[str]:
    required = {"SK_ID_CURR"}
    if task.branch_column:
        required.add(task.branch_column)
    for feature in task.features:
        required.update(feature.source_columns or (feature.name,))
    return required


def _read_function_rows(
    path: Path,
    function: FunctionSpec,
    app_index: dict[str, int],
    applicant_count: int,
    row_limit: int,
) -> tuple[list[list[dict[str, str]]], int, int]:
    """Read the union of columns once for all components in one function."""
    required = {"SK_ID_CURR"}
    for component in function.components:
        required.update(_required_columns(component))
    rows_by_app: list[list[dict[str, str]]] = [[] for _ in range(applicant_count)]
    scanned = matched = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = required.difference(reader.fieldnames or [])
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


def _prefix_component_rows(
    rows: list[dict[str, Any]], component: TaskSpec
) -> list[dict[str, Any]]:
    return [
        {
            "component_id": component.task_id,
            "component": component.title,
            **row,
        }
        for row in rows
    ]


def _execution_parts(function: FunctionSpec) -> list[dict[str, str]]:
    parts = [
        {
            "part": "CSV loading, applicant selection, grouping, and row alignment",
            "owner": "Client only",
            "status": "Executed in prepare-only run",
            "output": "anonymous grouped rows",
        }
    ]
    seen_client: set[str] = set()
    seen_excluded: set[str] = set()
    needs_finalized_statistics = False
    for component in function.components:
        for preparation in component.client_preparation:
            if preparation not in seen_client:
                parts.append(
                    {
                        "part": preparation,
                        "owner": "Client only",
                        "status": "Executed in prepare-only run",
                        "output": "plaintext staging values/masks",
                    }
                )
                seen_client.add(preparation)
        parts.append(
            {
                "part": component.title,
                "owner": "HEIR " + "/".join(component.kernel_ids),
                "status": "Intended HE arithmetic; not executed",
                "output": "encrypted sufficient statistics",
            }
        )
        needs_finalized_statistics = needs_finalized_statistics or any(
            operation in {"mean", "var"}
            for feature in component.features
            for operation in feature.operations
        )
        for excluded in component.excluded_outputs:
            if excluded not in seen_excluded:
                parts.append(
                    {
                        "part": excluded,
                        "owner": "Excluded from CKKS V1",
                        "status": "Not implemented",
                        "output": "none",
                    }
                )
                seen_excluded.add(excluded)
    if needs_finalized_statistics:
        parts.append(
            {
                "part": "Mean and sample-variance finalization",
                "owner": "Future HEIR continuation kernel",
                "status": "Not implemented; plaintext audit computes it today",
                "output": "future encrypted source feature values",
            }
        )
    parts.extend(
        [
            {
                "part": "Plaintext source reference and kernel oracle",
                "owner": "Audit only",
                "status": "Executed; must not feed encrypted pipeline",
                "output": "CSV correctness oracle",
            },
            {
                "part": "Encrypted feature-bundle persistence",
                "owner": "HE pipeline",
                "status": "Manifest prepared; ciphertext files pending",
                "output": "future serialized ciphertext bundle",
            },
        ]
    )
    return parts


def prepare_complete_function(
    function: FunctionSpec,
    data_dir: Path,
    application_path: Path,
    output_dir: Path,
    application_row_limit: int = 8,
    source_row_limit: int = 0,
) -> dict[str, Any]:
    """Prepare all components of one source function with a single main-table scan."""
    started = time.perf_counter()
    applications = _read_applications(application_path, application_row_limit)
    # A PSI sender layout is dense but intentionally leaves non-matching IDs
    # blank. Those rows remain valid zero-history applicant slots.
    app_index = {
        row["SK_ID_CURR"]: index
        for index, row in enumerate(applications)
        if row["SK_ID_CURR"]
    }
    rows_by_app, scanned, matched = _read_function_rows(
        data_dir / function.input_file,
        function,
        app_index,
        len(applications),
        source_row_limit,
    )

    bureau_sizes: dict[str, int] = {}
    auxiliary_rows_scanned = 0
    if any(
        feature.transform == "bureau_balance_size"
        for component in function.components
        for feature in component.features
    ):
        bureau_sizes, auxiliary_rows_scanned = _bureau_balance_sizes(
            data_dir, rows_by_app, source_row_limit
        )

    reference: list[dict[str, Any]] = []
    oracle: list[dict[str, Any]] = []
    tensor_manifest: list[dict[str, Any]] = []
    component_summaries: list[dict[str, Any]] = []
    for component in function.components:
        component_dir = output_dir / "components" / component.slug
        if component.kind == "count":
            part_reference, part_oracle, width, part_manifest = _prepare_count(
                component, rows_by_app, component_dir
            )
            dropped_missing = 0
        else:
            (
                part_reference,
                part_oracle,
                width,
                part_manifest,
                dropped_missing,
            ) = _prepare_features(
                component, rows_by_app, bureau_sizes, component_dir
            )
        reference.extend(_prefix_component_rows(part_reference, component))
        oracle.extend(_prefix_component_rows(part_oracle, component))
        for item in part_manifest:
            source_path = component_dir / item["file"]
            tensor_manifest.append(
                {
                    "component_id": component.task_id,
                    "component": component.title,
                    **item,
                    "file": str(source_path.relative_to(output_dir)),
                }
            )
        component_summaries.append(
            {
                "component_id": component.task_id,
                "name": component.title,
                "kind": component.kind,
                "kernel_ids": list(component.kernel_ids),
                "slots_per_application": width,
                "client_compacted_missing_values": dropped_missing,
                "feature_count": len(component.features) if component.features else 1,
            }
        )

    write_csv(
        output_dir / "plaintext_reference.csv",
        ["component_id", "component", "app_index", "feature", "operation", "value"],
        reference,
    )
    write_csv(
        output_dir / "kernel_oracle.csv",
        [
            "component_id",
            "component",
            "app_index",
            "feature",
            "count",
            "sum",
            "sum_squares",
        ],
        oracle,
    )
    write_csv(
        output_dir / "tensor_manifest.csv",
        ["component_id", "component", "feature", "kind", "file", "elements"],
        tensor_manifest,
    )
    private_mapping = [
        {
            "app_index": index,
            "SK_ID_CURR": row["SK_ID_CURR"],
            "TARGET": row["TARGET"],
        }
        for index, row in enumerate(applications)
    ]
    write_csv(
        output_dir / "client_private" / "applicant_mapping.csv",
        ["app_index", "SK_ID_CURR", "TARGET"],
        private_mapping,
    )
    layout_hash = hashlib.sha256(
        "\n".join(row["SK_ID_CURR"] for row in applications).encode("utf-8")
    ).hexdigest()
    write_json(
        output_dir / "client_private" / "applicant_layout.json",
        {
            "applicant_count": len(applications),
            "private_applicant_order_sha256": layout_hash,
            "rule": "application selection order",
        },
    )

    schema = [
        {
            "component_id": row["component_id"],
            "feature": row["feature"],
            "operation": row["operation"],
        }
        for row in reference
        if row["app_index"] == 0
    ]
    schema_hash = hashlib.sha256(
        json.dumps(schema, sort_keys=True).encode("utf-8")
    ).hexdigest()
    bundle_manifest = {
        "bundle_status": "plaintext_staging_only",
        "function_benchmark": function.name,
        "scheme": "CKKS",
        "crypto_context_id": "pending HEIR execution",
        "key_set_id": "pending HEIR execution",
        "applicant_count": len(applications),
        "applicant_layout": "client_private/applicant_layout.json",
        "feature_schema_sha256": schema_hash,
        "ciphertext_files": [],
        "intended_outputs": schema,
        "pipeline_rule": (
            "future ciphertext outputs remain encrypted; plaintext_reference.csv "
            "and kernel_oracle.csv are audit-only"
        ),
    }
    write_json(output_dir / "feature_bundle_manifest.json", bundle_manifest)

    contracts_by_id = {contract.kernel_id: contract for contract in kernel_contracts()}
    kernel_ids = list(
        dict.fromkeys(
            kernel_id
            for component in function.components
            for kernel_id in component.kernel_ids
        )
    )
    summary: dict[str, Any] = {
        "function": function.to_dict(),
        "backend_status": "prepared_only",
        "bundle_status": bundle_manifest["bundle_status"],
        "heir_scheme": "CKKS",
        "main_source_scan_count": 1,
        "application_rows": len(applications),
        "source_rows_scanned": scanned,
        "source_rows_matched": matched,
        "auxiliary_rows_scanned": auxiliary_rows_scanned,
        "components": component_summaries,
        "execution_parts": _execution_parts(function),
        "kernel_contracts": [contracts_by_id[kernel_id].to_dict() for kernel_id in kernel_ids],
        "feature_schema_sha256": schema_hash,
        "privacy_boundary": (
            "client groups, derives features/masks, compacts missing values, and pads; "
            "future server execution receives ciphertexts and returns ciphertexts"
        ),
        "timings_seconds": {"combined_prepare_wall_seconds": time.perf_counter() - started},
    }
    summary["artifact_sizes_bytes"] = {
        "run_directory": path_size(output_dir),
        "tensors": path_size(output_dir / "components"),
        "plaintext_reference": path_size(output_dir / "plaintext_reference.csv"),
        "kernel_oracle": path_size(output_dir / "kernel_oracle.csv"),
        "feature_bundle_manifest": path_size(output_dir / "feature_bundle_manifest.json"),
        "client_private": path_size(output_dir / "client_private"),
    }
    write_json(output_dir / "benchmark_summary.json", summary)
    return summary
