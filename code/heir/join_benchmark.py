"""Benchmarks for assembling pre-aligned function feature bundles by app_index."""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from code.heir.common import path_size, write_json
from code.heir.workloads.grouped import FunctionSpec


REFERENCE_FIELDS = (
    "component_id",
    "component",
    "app_index",
    "feature",
    "operation",
    "value",
)


@dataclass(frozen=True)
class FunctionBundle:
    """Validated metadata and audit paths for one function output bundle."""

    function: FunctionSpec
    run_dir: Path
    bundle_manifest: dict[str, Any]
    applicant_count: int
    layout_sha256: str
    presence_by_app: tuple[bool, ...]
    reference_rows: int


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing required join input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _validate_reference(
    path: Path,
    intended_outputs: list[dict[str, str]],
    applicant_count: int,
) -> int:
    """Stream the audit reference and require one row per output/applicant."""
    expected = {
        (item["component_id"], item["feature"], item["operation"]): 0
        for item in intended_outputs
    }
    if len(expected) != len(intended_outputs):
        raise ValueError(f"{path} bundle schema contains duplicate outputs")
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != REFERENCE_FIELDS:
            raise ValueError(f"{path} has an unexpected audit-reference schema")
        for row_number, row in enumerate(reader, start=2):
            schema_key = (row["component_id"], row["feature"], row["operation"])
            if schema_key not in expected:
                raise ValueError(f"{path}:{row_number} is absent from bundle schema")
            try:
                app_index = int(row["app_index"])
            except ValueError as error:
                raise ValueError(f"{path}:{row_number} has invalid app_index") from error
            if app_index != expected[schema_key]:
                raise ValueError(
                    f"{path}:{row_number} expected app_index "
                    f"{expected[schema_key]} for {schema_key}, got {app_index}"
                )
            expected[schema_key] += 1
            row_count += 1
    incomplete = {
        key: count for key, count in expected.items() if count != applicant_count
    }
    if incomplete:
        raise ValueError(
            f"{path} does not contain every applicant for {len(incomplete)} outputs"
        )
    return row_count


def _presence_from_oracle(path: Path, applicant_count: int) -> tuple[bool, ...]:
    """Derive an audit-only table-presence flag from grouped source row counts."""
    presence = [False] * applicant_count
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"app_index", "count"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path} is missing oracle columns: {sorted(required)}")
        for row_number, row in enumerate(reader, start=2):
            try:
                app_index = int(row["app_index"])
                count = float(row["count"])
            except ValueError as error:
                raise ValueError(f"{path}:{row_number} has invalid oracle values") from error
            if app_index < 0 or app_index >= applicant_count:
                raise ValueError(f"{path}:{row_number} app_index is outside the layout")
            if count > 0:
                presence[app_index] = True
    return tuple(presence)


def load_function_bundle(function: FunctionSpec, run_dir: Path) -> FunctionBundle:
    """Load and validate one prepared or encrypted function bundle."""
    manifest = _load_json(run_dir / "feature_bundle_manifest.json")
    layout = _load_json(run_dir / "client_private" / "applicant_layout.json")
    if manifest.get("function_benchmark") != function.name:
        raise ValueError(
            f"{run_dir} contains {manifest.get('function_benchmark')!r}, "
            f"expected {function.name!r}"
        )
    if manifest.get("scheme") != "CKKS":
        raise ValueError(f"{run_dir} is not a CKKS feature bundle")
    applicant_count = int(manifest.get("applicant_count", -1))
    if applicant_count <= 0 or applicant_count != int(layout.get("applicant_count", -2)):
        raise ValueError(f"{run_dir} has inconsistent applicant counts")
    layout_sha256 = str(layout.get("private_applicant_order_sha256", ""))
    if not layout_sha256:
        raise ValueError(f"{run_dir} is missing its applicant-layout fingerprint")
    intended_outputs = manifest.get("intended_outputs")
    if not isinstance(intended_outputs, list) or not intended_outputs:
        raise ValueError(f"{run_dir} contains no intended feature outputs")
    reference_path = run_dir / "plaintext_reference.csv"
    oracle_path = run_dir / "kernel_oracle.csv"
    reference_rows = _validate_reference(
        reference_path, intended_outputs, applicant_count
    )
    presence = _presence_from_oracle(oracle_path, applicant_count)
    return FunctionBundle(
        function=function,
        run_dir=run_dir,
        bundle_manifest=manifest,
        applicant_count=applicant_count,
        layout_sha256=layout_sha256,
        presence_by_app=presence,
        reference_rows=reference_rows,
    )


def _compatibility(bundles: Iterable[FunctionBundle]) -> dict[str, Any]:
    bundle_list = list(bundles)
    if not bundle_list:
        raise ValueError("at least one function bundle is required")
    applicant_counts = {bundle.applicant_count for bundle in bundle_list}
    layout_hashes = {bundle.layout_sha256 for bundle in bundle_list}
    schemes = {bundle.bundle_manifest["scheme"] for bundle in bundle_list}
    if len(applicant_counts) != 1:
        raise ValueError("function bundles have different applicant counts")
    if len(layout_hashes) != 1:
        raise ValueError("function bundles have different app_index layouts")
    if schemes != {"CKKS"}:
        raise ValueError(f"function bundles have incompatible schemes: {schemes}")

    contexts = {
        str(bundle.bundle_manifest.get("crypto_context_id", ""))
        for bundle in bundle_list
    }
    key_sets = {
        str(bundle.bundle_manifest.get("key_set_id", "")) for bundle in bundle_list
    }
    ciphertext_ready = all(
        bundle.bundle_manifest.get("bundle_status") != "plaintext_staging_only"
        and bool(bundle.bundle_manifest.get("ciphertext_files"))
        for bundle in bundle_list
    )
    if ciphertext_ready and (len(contexts) != 1 or len(key_sets) != 1):
        raise ValueError("encrypted bundles do not share one CKKS context and key set")
    return {
        "applicant_count": applicant_counts.pop(),
        "applicant_layout_sha256": layout_hashes.pop(),
        "scheme": "CKKS",
        "crypto_context_compatibility": (
            "verified" if ciphertext_ready else "pending_ciphertext_execution"
        ),
        "key_set_compatibility": (
            "verified" if ciphertext_ready else "pending_ciphertext_execution"
        ),
        "ciphertext_ready": ciphertext_ready,
    }


def _bundle_entry(bundle: FunctionBundle) -> dict[str, Any]:
    manifest = bundle.bundle_manifest
    return {
        "function": bundle.function.name,
        "function_name": bundle.function.function_name,
        "source_manifest": str(bundle.run_dir / "feature_bundle_manifest.json"),
        "bundle_status": manifest.get("bundle_status"),
        "feature_schema_sha256": manifest.get("feature_schema_sha256"),
        "feature_columns": len(manifest["intended_outputs"]),
        "ciphertext_files": manifest.get("ciphertext_files", []),
        "applicants_with_source_rows": sum(bundle.presence_by_app),
    }


def _write_joined_reference(path: Path, bundles: Iterable[FunctionBundle]) -> int:
    """Materialize a long audit reference; never use this as an HE pipeline input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["function", *REFERENCE_FIELDS, "source_present"]
    row_count = 0
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for bundle in bundles:
            with (bundle.run_dir / "plaintext_reference.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as source:
                reader = csv.DictReader(source)
                for row in reader:
                    app_index = int(row["app_index"])
                    writer.writerow(
                        {
                            "function": bundle.function.name,
                            **row,
                            "source_present": int(bundle.presence_by_app[app_index]),
                        }
                    )
                    row_count += 1
    return row_count


def _report(summary: dict[str, Any]) -> str:
    function_rows = "\n".join(
        "| {function} | {feature_columns} | {applicants_with_source_rows} | "
        "{bundle_status} |".format(**item)
        for item in summary["function_bundles"]
    )
    timing_rows = "\n".join(
        f"| {name} | {seconds:.6f} |"
        for name, seconds in summary["timings_seconds"].items()
    )
    return f"""# Pre-aligned feature join benchmark: {summary['scope']}

## Result

`{summary['status']}`

This benchmark assembles function feature bundles that already share the same
anonymous `app_index` ordering. PSI execution is outside this benchmark. Raw
identifiers are not compared again.

## Acceptance criteria

| Criterion | Result |
|---|---|
| All bundles use one dense applicant layout | Pass |
| Function schema and audit-row coverage validated | Pass |
| Raw identifiers read by join benchmark | No |
| PSI execution included in timing | No |
| Ciphertexts decrypted to perform join | No |
| HE rotations/multiplications/additions used for join | 0 / 0 / 0 |
| CKKS context and key compatibility | {summary['compatibility']['crypto_context_compatibility']} |

## What the join does

```python
# Simplified production behavior: zero-copy ciphertext bundle assembly.
assert every_bundle_has_same_app_index_layout()
assert every_encrypted_bundle_has_same_ckks_context_and_key()

joined_bundle = []
for function_bundle in function_bundles:
    joined_bundle.extend(function_bundle.ciphertext_files)
```

No ciphertext values are numerically combined. The join appends feature
columns that already use identical applicant slots. Therefore the normal join
has multiplicative depth 0 and needs no evaluation keys.

`joined_reference.csv` is a plaintext correctness artifact only. Its writing
time is reported separately and is not an encrypted-pipeline join cost.

## Function bundles

| Function | Feature outputs | Applicants with source rows | Input status |
|---|---:|---:|---|
{function_rows}

## Join measurements

| Measure | Value |
|---|---:|
| Applicant slots | {summary['compatibility']['applicant_count']} |
| Function bundles | {len(summary['function_bundles'])} |
| Joined feature outputs | {summary['joined_feature_columns']} |
| Bundle-index HE operations | 0 |
| Audit reference rows | {summary['audit_reference_rows']} |

## Timings

| Stage | Seconds |
|---|---:|
{timing_rows}

## HE boundary

- Individual mode validates and attaches one function bundle to the application
  slot layout.
- End-to-end mode validates and attaches all five function bundles.
- When ciphertext files exist, the output index references those ciphertexts
  without decrypting or copying their values.
- Current function bundles are plaintext staging, so this run validates the
  join contract and audit oracle but does not claim HEIR/OpenFHE execution.
- A rotation-and-mask benchmark is needed only for a separate case where
  ciphertext bundles were encrypted in different slot orders.
"""


def _write_one_join(
    scope: str,
    bundles: list[FunctionBundle],
    output_dir: Path,
    validation_seconds: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    compatibility = _compatibility(bundles)
    compatibility_seconds = time.perf_counter() - started

    index_started = time.perf_counter()
    entries = [_bundle_entry(bundle) for bundle in bundles]
    joined_columns = sum(item["feature_columns"] for item in entries)
    status = (
        "ciphertext_bundle_index_assembled"
        if compatibility["ciphertext_ready"]
        else "prepared_join_contract_only"
    )
    bundle_index = {
        "status": status,
        "scope": scope,
        "join_semantics": "prealigned_app_index_column_concatenation",
        "he_operation_counts": {
            "rotations": 0,
            "ciphertext_plaintext_multiplications": 0,
            "ciphertext_additions": 0,
        },
        "multiplicative_depth": 0,
        "evaluation_keys": [],
        "compatibility": compatibility,
        "function_bundles": entries,
        "pipeline_rule": (
            "ciphertext files remain encrypted; joined_reference.csv is audit-only"
        ),
    }
    write_json(output_dir / "bundle_index.json", bundle_index)
    index_seconds = time.perf_counter() - index_started

    reference_started = time.perf_counter()
    reference_rows = _write_joined_reference(
        output_dir / "joined_reference.csv", bundles
    )
    reference_seconds = time.perf_counter() - reference_started
    summary: dict[str, Any] = {
        **bundle_index,
        "joined_feature_columns": joined_columns,
        "audit_reference_rows": reference_rows,
        "timings_seconds": {
            "input_bundle_validation_seconds": validation_seconds,
            "cross_bundle_compatibility_seconds": compatibility_seconds,
            "bundle_index_assembly_seconds": index_seconds,
            "audit_reference_materialization_seconds": reference_seconds,
        },
    }
    summary["timings_seconds"]["join_contract_total_seconds"] = (
        validation_seconds + compatibility_seconds + index_seconds
    )
    summary["artifact_sizes_bytes"] = {
        "bundle_index": path_size(output_dir / "bundle_index.json"),
        "joined_reference": path_size(output_dir / "joined_reference.csv"),
    }
    write_json(output_dir / "benchmark_summary.json", summary)
    (output_dir / "benchmark_report.md").write_text(
        _report(summary), encoding="utf-8"
    )
    return summary


def run_join_benchmarks(
    function_runs: list[tuple[FunctionSpec, Path]], output_dir: Path
) -> dict[str, Any]:
    """Run five individual joins and their combined end-to-end bundle join."""
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite join run: {output_dir}")
    loaded: list[tuple[FunctionBundle, float]] = []
    for function, run_dir in function_runs:
        started = time.perf_counter()
        bundle = load_function_bundle(function, run_dir)
        validation_seconds = time.perf_counter() - started
        loaded.append((bundle, validation_seconds))

    bundles = [bundle for bundle, _ in loaded]
    end_started = time.perf_counter()
    _compatibility(bundles)
    end_validation_seconds = time.perf_counter() - end_started

    # Create output only after every input and the cross-bundle layout have
    # passed preflight, preventing a failed end-to-end run from looking complete.
    output_dir.mkdir(parents=True)
    individual: list[dict[str, Any]] = []
    for bundle, validation_seconds in loaded:
        function = bundle.function
        summary = _write_one_join(
            f"individual/{function.name}",
            [bundle],
            output_dir / "individual" / function.function_name,
            validation_seconds,
        )
        individual.append(
            {
                "function": function.name,
                "status": summary["status"],
                "report": str(
                    output_dir
                    / "individual"
                    / function.function_name
                    / "benchmark_report.md"
                ),
            }
        )

    end_summary = _write_one_join(
        "end_to_end/all_functions",
        bundles,
        output_dir / "end_to_end",
        end_validation_seconds,
    )
    run_summary = {
        "status": "join_benchmarks_completed",
        "psi_timing_included": False,
        "individual": individual,
        "end_to_end": {
            "status": end_summary["status"],
            "report": str(output_dir / "end_to_end" / "benchmark_report.md"),
        },
    }
    write_json(output_dir / "run_summary.json", run_summary)
    return run_summary
