"""Serial, resumable feature DAG that persists encrypted HEIR outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from code.heir.common import path_size, read_csv, sha256_file, write_json
from code.heir.dag.contracts import (
    STAGE_ORDER,
    artifact_records,
    combined_sha256,
    validate_completion,
    validate_encrypted_bundle,
    write_completion,
)
from code.heir.dag.generated_backend import GeneratedCkksBackend
from code.heir.function_benchmark import prepare_complete_function
from code.heir.report import write_complete_function_report
from code.heir.workloads.catalog import get_function


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _application_layout(path: Path, row_limit: int) -> dict[str, Any]:
    identifiers: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = set(reader.fieldnames or [])
        if "SK_ID_CURR" not in fields:
            raise ValueError(f"{path} is missing SK_ID_CURR")
        for index, row in enumerate(reader):
            if row_limit > 0 and index >= row_limit:
                break
            if "app_index" in fields and (row.get("app_index") or "") != str(index):
                raise ValueError(f"{path} app_index must be dense and match row order")
            identifiers.append((row.get("SK_ID_CURR") or "").strip())
    if not identifiers:
        raise ValueError(f"{path} produced no applicant slots")
    nonempty = [value for value in identifiers if value]
    if len(nonempty) != len(set(nonempty)):
        raise ValueError(f"{path} contains duplicate non-empty applicant identifiers")
    return {
        "applicant_count": len(identifiers),
        "private_applicant_order_sha256": hashlib.sha256(
            "\n".join(identifiers).encode("utf-8")
        ).hexdigest(),
    }


def _session_files(session_dir: Path) -> list[Path]:
    return [
        session_dir / "public" / "crypto_context.bin",
        session_dir / "public" / "public_key.bin",
        session_dir / "public" / "evaluation_mult_keys.bin",
        session_dir / "public" / "evaluation_rotation_keys.bin",
        session_dir / "client_private" / "secret_key.bin",
    ]


def initialize_dag(
    run_root: Path,
    *,
    application_path: Path,
    data_dir: Path,
    generated_root: Path,
    openfhe_dir: str,
    vector_size: int,
    application_row_limit: int,
    source_row_limit: int,
    provider_kernel: str = "K03",
    psi_manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Create one CKKS session and immutable applicant layout for the full DAG."""
    if run_root.exists():
        raise FileExistsError(f"refusing to overwrite DAG run: {run_root}")
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    generation_manifest_path = generated_root / "generation_manifest.json"
    if not generation_manifest_path.is_file():
        raise FileNotFoundError(
            f"generated CKKS root is missing {generation_manifest_path.name}"
        )
    generation_manifest = _load_json(generation_manifest_path)
    if generation_manifest.get("status") != "heir_generated_ckks_sources_ready":
        raise ValueError("generated CKKS manifest is not complete")
    if generation_manifest.get("scheme") != "CKKS":
        raise ValueError("generated DAG kernels must use CKKS")
    if int(generation_manifest.get("vector_size", -1)) != vector_size:
        raise ValueError("DAG vector size differs from generated kernel vector size")
    generated_ids = {
        item.get("kernel_id") for item in generation_manifest.get("kernels", [])
    }
    if generated_ids != {"K01", "K02", "K03"}:
        raise ValueError("generated root must contain exactly K01, K02, and K03")
    layout = _application_layout(application_path, application_row_limit)
    psi_provenance: dict[str, Any] = {"status": "not_provided"}
    if psi_manifest_path is not None:
        psi_manifest = _load_json(psi_manifest_path)
        dense_slots = int(psi_manifest.get("counts", {}).get("dense_slots", -1))
        if application_row_limit == 0 and dense_slots != layout["applicant_count"]:
            raise ValueError("PSI manifest dense-slot count differs from DAG layout")
        if psi_manifest.get("status") != "psi_outputs_validated_bridge_prepared":
            raise ValueError("PSI manifest is not a completed bridge artifact")
        psi_provenance = {
            "status": "referenced",
            "manifest": str(psi_manifest_path.resolve()),
            "manifest_sha256": sha256_file(psi_manifest_path),
            "layout_id": psi_manifest.get("layout_id"),
            "alignment_schema_sha256": psi_manifest.get(
                "alignment_schema_sha256"
            ),
            "psi_timing_included": False,
        }
    run_root.mkdir(parents=True)
    session_dir = run_root / "session"
    backend = GeneratedCkksBackend(
        generated_root.resolve(), run_root / "build_cache", openfhe_dir
    )
    backend_result = backend.initialize_session(session_dir, provider_kernel)
    files = _session_files(session_dir)
    for path in files:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"session initializer did not create {path}")
    files[4].chmod(0o600)
    session_id = secrets.token_hex(16)
    context_id = sha256_file(files[0])
    key_set_id = combined_sha256(files[1:4])
    session_manifest: dict[str, Any] = {
        "status": "ckks_session_ready",
        "scheme": "CKKS",
        "session_id": session_id,
        "crypto_context_id": context_id,
        "key_set_id": key_set_id,
        "provider_kernel": provider_kernel,
        "applicant_count": layout["applicant_count"],
        "applicant_layout_sha256": layout["private_applicant_order_sha256"],
        "vector_size": vector_size,
        "public_artifacts": artifact_records(session_dir, files[:4]),
        "client_private_secret_key": str(files[4].relative_to(session_dir)),
        "backend": backend_result,
        "security_rule": (
            "feature evaluation stages load public/evaluation keys only; "
            "the secret key is reserved for separate audit validation"
        ),
    }
    write_json(session_dir / "session_manifest.json", session_manifest)
    write_completion(
        session_dir,
        stage="session",
        session_id=session_id,
        layout_sha256=layout["private_applicant_order_sha256"],
        required_paths=[session_dir / "session_manifest.json", *files[:4]],
    )
    dag_manifest: dict[str, Any] = {
        "status": "active",
        "run_id": run_root.name,
        "session_id": session_id,
        "crypto_context_id": context_id,
        "key_set_id": key_set_id,
        "applicant_count": layout["applicant_count"],
        "applicant_layout_sha256": layout["private_applicant_order_sha256"],
        "application_path": str(application_path.resolve()),
        "data_dir": str(data_dir.resolve()),
        "generated_root": str(generated_root.resolve()),
        "openfhe_dir": openfhe_dir,
        "vector_size": vector_size,
        "application_row_limit": application_row_limit,
        "source_row_limit": source_row_limit,
        "stage_order": list(STAGE_ORDER),
        "scope": (
            "HE-compatible feature subset; no LightGBM/rules/min/max and "
            "K02/K03 persist encrypted sufficient statistics"
        ),
        "psi_provenance": psi_provenance,
    }
    write_json(run_root / "dag_manifest.json", dag_manifest)
    base_checkpoint = {
        "status": "layout_ready",
        "checkpoint": 0,
        "session_id": session_id,
        "crypto_context_id": context_id,
        "key_set_id": key_set_id,
        "applicant_layout_sha256": layout["private_applicant_order_sha256"],
        "function_bundles": [],
        "ciphertext_files": [],
        "note": "application layout anchor; application feature encoding is client-only",
    }
    write_json(run_root / "checkpoints" / "00_layout" / "bundle_index.json", base_checkpoint)
    return dag_manifest


def _load_run(run_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    dag = _load_json(run_root / "dag_manifest.json")
    session = _load_json(run_root / "session" / "session_manifest.json")
    expected = (
        "session_id",
        "crypto_context_id",
        "key_set_id",
        "applicant_layout_sha256",
        "applicant_count",
        "vector_size",
    )
    for key in expected:
        if dag.get(key) != session.get(key):
            raise ValueError(f"DAG and session disagree on {key}")
    validate_completion(
        run_root / "session",
        expected_stage="session",
        session_id=dag["session_id"],
        layout_sha256=dag["applicant_layout_sha256"],
    )
    return dag, session


def _stage_number(stage: str) -> int:
    return STAGE_ORDER.index(stage) + 1


def _stage_dir(run_root: Path, stage: str) -> Path:
    return run_root / "stages" / f"{_stage_number(stage):02d}_{stage}"


def _checkpoint_dir(run_root: Path, stage: str) -> Path:
    return run_root / "checkpoints" / f"{_stage_number(stage):02d}_{stage}"


def _previous_checkpoint(run_root: Path, stage: str) -> Path:
    number = _stage_number(stage)
    if number == 1:
        return run_root / "checkpoints" / "00_layout" / "bundle_index.json"
    previous = STAGE_ORDER[number - 2]
    return _checkpoint_dir(run_root, previous) / "bundle_index.json"


def _assert_predecessors(run_root: Path, stage: str, dag: dict[str, Any]) -> None:
    number = _stage_number(stage)
    for predecessor in STAGE_ORDER[: number - 1]:
        directory = _stage_dir(run_root, predecessor)
        validate_completion(
            directory,
            expected_stage=predecessor,
            session_id=dag["session_id"],
            layout_sha256=dag["applicant_layout_sha256"],
        )
        validate_encrypted_bundle(
            directory / "feature_bundle_manifest.json",
            session_id=dag["session_id"],
            context_id=dag["crypto_context_id"],
            key_set_id=dag["key_set_id"],
            layout_sha256=dag["applicant_layout_sha256"],
        )
    _validate_checkpoint(
        _previous_checkpoint(run_root, stage), dag, expected_number=number - 1
    )


def _validate_checkpoint(
    path: Path, dag: dict[str, Any], *, expected_number: int
) -> dict[str, Any]:
    checkpoint = _load_json(path)
    expected = {
        "checkpoint": expected_number,
        "session_id": dag["session_id"],
        "crypto_context_id": dag["crypto_context_id"],
        "key_set_id": dag["key_set_id"],
        "applicant_layout_sha256": dag["applicant_layout_sha256"],
    }
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(f"{path} has incompatible {key}")
    if len(checkpoint.get("function_bundles", [])) != expected_number:
        raise ValueError(f"{path} has an incomplete function-bundle chain")
    if expected_number > 0 and not checkpoint.get("ciphertext_files"):
        raise ValueError(f"{path} contains no ciphertext references")
    for record in checkpoint.get("ciphertext_files", []):
        ciphertext = Path(record["file"])
        if not ciphertext.is_file():
            raise FileNotFoundError(f"checkpoint ciphertext disappeared: {ciphertext}")
        if sha256_file(ciphertext) != record["sha256"]:
            raise ValueError(f"checkpoint ciphertext hash changed: {ciphertext}")
    return checkpoint


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return result or "feature"


def _jobs(preparation_dir: Path, summary: dict[str, Any]) -> list[dict[str, Any]]:
    widths = {
        component["component_id"]: int(component["slots_per_application"])
        for component in summary["components"]
    }
    grouped: dict[tuple[str, str], dict[str, Path]] = {}
    for row in read_csv(preparation_dir / "tensor_manifest.csv"):
        key = (row["component_id"], row["feature"])
        grouped.setdefault(key, {})[row["kind"]] = preparation_dir / row["file"]
    jobs = []
    for (component_id, feature), tensors in grouped.items():
        if "encrypted_mask_matrix" in tensors:
            kernel_id = "K01"
            inputs = [
                tensors["encrypted_mask_matrix"],
                tensors["encrypted_unit_weights"],
            ]
            semantics = ["count"]
        elif "encrypted_right_values" in tensors:
            kernel_id = "K03"
            inputs = [
                tensors["encrypted_values"],
                tensors["encrypted_right_values"],
                tensors["encrypted_occupancy_or_branch_mask"],
            ]
            semantics = ["count", "difference_sum", "difference_sum_squares"]
        else:
            kernel_id = "K02"
            inputs = [
                tensors["encrypted_values"],
                tensors["encrypted_occupancy_or_branch_mask"],
            ]
            semantics = ["count", "sum", "sum_squares"]
        jobs.append(
            {
                "component_id": component_id,
                "feature": feature,
                "kernel_id": kernel_id,
                "width": widths[component_id],
                "inputs": inputs,
                "semantics": semantics,
                "slug": f"{component_id.lower()}_{_slug(feature)}",
            }
        )
    if not jobs:
        raise ValueError(f"{preparation_dir} produced no encrypted jobs")
    return jobs


def _job_ciphertexts(
    stage_dir: Path, job: dict[str, Any], result: dict[str, Any]
) -> list[dict[str, Any]]:
    index_rows = read_csv(Path(result["ciphertext_index"]))
    records = []
    for row in index_rows:
        ordinal = int(row["result_ordinal"])
        if ordinal >= len(job["semantics"]):
            raise ValueError(
                f"{job['kernel_id']} returned unexpected result ordinal {ordinal}"
            )
        path = Path(result["ciphertext_index"]).parent / row["file"]
        records.append(
            {
                "file": str(path.relative_to(stage_dir)),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "component_id": job["component_id"],
                "feature": job["feature"],
                "kernel_id": job["kernel_id"],
                "app_index": int(row["app_index"]),
                "statistic": job["semantics"][ordinal],
                "level": int(row["level"]),
                "scaling_factor": float(row["scaling_factor"]),
            }
        )
    expected = len(job["semantics"])
    applicants = len({record["app_index"] for record in records})
    if applicants == 0 or len(records) != applicants * expected:
        raise ValueError(
            f"{job['kernel_id']} output does not contain {expected} results per applicant"
        )
    return records


def _stage_report(summary: dict[str, Any]) -> str:
    timing_rows = "\n".join(
        f"| {name} | {seconds:.6f} |"
        for name, seconds in summary["timings_seconds"].items()
    )
    return f"""# Encrypted DAG stage: {summary['stage']}

## Status

`encrypted_complete`

This stage used HEIR-generated CKKS/OpenFHE code, serialized every retained
encrypted output, and proved that a fresh process can deserialize and
reserialize one result. The pipeline output was not decrypted.

## Downstream contract

| Measure | Value |
|---|---|
| Session ID | `{summary['session_id']}` |
| Applicant slots | {summary['applicant_count']} |
| Encrypted jobs | {summary['job_count']} |
| Serialized ciphertexts | {summary['ciphertext_count']} |
| CKKS context | `{summary['crypto_context_id']}` |
| Key set | `{summary['key_set_id']}` |
| Layout | `{summary['applicant_layout_sha256']}` |
| Continuity probe | {summary['continuity_probe']['status']} |

K02 and K03 outputs are encrypted `count`, `sum`, and `sum_squares`
sufficient statistics. Exact encrypted mean/variance finalization is not
claimed. Plaintext preparation/oracle files remain under `preparation/` and
are audit-only.

## Timings

| Stage | Seconds |
|---|---:|
{timing_rows}

## Resource evidence

| Measure | Value |
|---|---:|
| Maximum observed child RSS (KiB on Linux) | {summary['peak_child_rss_kib']} |
| Ciphertext bytes | {summary['ciphertext_bytes']} |
"""


def run_function_stage(
    run_root: Path, stage: str, *, resume: bool = False
) -> dict[str, Any]:
    if stage not in STAGE_ORDER:
        raise ValueError(f"unknown DAG stage: {stage}")
    dag, _ = _load_run(run_root)
    final_dir = _stage_dir(run_root, stage)
    if final_dir.exists():
        if not resume:
            raise FileExistsError(f"stage already exists: {final_dir}")
        validate_completion(
            final_dir,
            expected_stage=stage,
            session_id=dag["session_id"],
            layout_sha256=dag["applicant_layout_sha256"],
        )
        manifest = validate_encrypted_bundle(
            final_dir / "feature_bundle_manifest.json",
            session_id=dag["session_id"],
            context_id=dag["crypto_context_id"],
            key_set_id=dag["key_set_id"],
            layout_sha256=dag["applicant_layout_sha256"],
        )
        checkpoint_path = _checkpoint_dir(run_root, stage) / "bundle_index.json"
        if checkpoint_path.is_file():
            _validate_checkpoint(
                checkpoint_path, dag, expected_number=_stage_number(stage)
            )
        elif not checkpoint_path.parent.exists():
            _assert_predecessors(run_root, stage, dag)
            _write_checkpoint(run_root, stage, dag, final_dir)
        else:
            raise ValueError(
                f"incomplete checkpoint directory requires inspection: "
                f"{checkpoint_path.parent}"
            )
        return manifest
    _assert_predecessors(run_root, stage, dag)
    stages_root = run_root / "stages"
    stages_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{stage}_inprogress_", dir=stages_root))
    started = time.perf_counter()
    function = get_function(stage)
    preparation = temp_dir / "preparation"
    preparation_started = time.perf_counter()
    preparation_summary = prepare_complete_function(
        function,
        Path(dag["data_dir"]),
        Path(dag["application_path"]),
        preparation,
        int(dag["application_row_limit"]),
        int(dag["source_row_limit"]),
    )
    preview = read_csv(preparation / "plaintext_reference.csv")[:16]
    write_complete_function_report(
        preparation / "benchmark_report.md", preparation_summary, preview
    )
    preparation_seconds = time.perf_counter() - preparation_started
    if int(preparation_summary["application_rows"]) != int(dag["applicant_count"]):
        raise ValueError("function preparation changed the applicant slot count")

    backend = GeneratedCkksBackend(
        Path(dag["generated_root"]), run_root / "build_cache", dag["openfhe_dir"]
    )
    job_results = []
    ciphertext_records: list[dict[str, Any]] = []
    total_backend_seconds = 0.0
    peak_rss = 0
    for job in _jobs(preparation, preparation_summary):
        output_dir = temp_dir / "ciphertexts" / job["slug"]
        result = backend.execute_job(
            kernel_id=job["kernel_id"],
            session_dir=run_root / "session",
            vector_size=int(dag["vector_size"]),
            applicant_count=int(dag["applicant_count"]),
            width=int(job["width"]),
            input_paths=job["inputs"],
            output_dir=output_dir,
        )
        records = _job_ciphertexts(temp_dir, job, result)
        ciphertext_records.extend(records)
        total_backend_seconds += float(
            result["timings_seconds"]["subprocess_wall_seconds"]
        )
        peak_rss = max(peak_rss, int(result["peak_child_rss_kib"]))
        job_results.append(
            {
                "component_id": job["component_id"],
                "feature": job["feature"],
                "kernel_id": job["kernel_id"],
                "slots_per_applicant": job["width"],
                "output_statistics": job["semantics"],
                "generated_proof": result["generated_proof"],
                "timings_seconds": result["timings_seconds"],
            }
        )

    first_ciphertext = temp_dir / ciphertext_records[0]["file"]
    probe_path = temp_dir / "continuity_probe" / "reloaded.ct"
    probe_path.parent.mkdir(parents=True)
    continuity = backend.continuity_probe(
        run_root / "session", first_ciphertext, probe_path
    )
    # The in-progress directory is atomically renamed after completion, so
    # persist stage-relative paths rather than stale temporary absolute paths.
    continuity["input_file"] = str(first_ciphertext.relative_to(temp_dir))
    continuity["output_file"] = str(probe_path.relative_to(temp_dir))
    bundle_manifest: dict[str, Any] = {
        "bundle_status": "encrypted_complete",
        "scheme": "CKKS",
        "stage": stage,
        "function_benchmark": function.name,
        "session_id": dag["session_id"],
        "crypto_context_id": dag["crypto_context_id"],
        "key_set_id": dag["key_set_id"],
        "applicant_count": dag["applicant_count"],
        "applicant_layout_sha256": dag["applicant_layout_sha256"],
        "vector_size": dag["vector_size"],
        "output_kind": "encrypted_sufficient_statistics",
        "ciphertext_files": ciphertext_records,
        "jobs": job_results,
        "continuity_probe": continuity,
        "pipeline_rule": (
            "ciphertexts remain encrypted; preparation plaintext is audit-only"
        ),
    }
    write_json(temp_dir / "feature_bundle_manifest.json", bundle_manifest)
    timings = {
        "client_preparation_seconds": preparation_seconds,
        "generated_backend_subprocess_seconds": total_backend_seconds,
        "continuity_probe_seconds": float(continuity["seconds"]),
        "stage_wall_seconds": time.perf_counter() - started,
    }
    summary: dict[str, Any] = {
        "status": "encrypted_complete",
        "stage": stage,
        "session_id": dag["session_id"],
        "crypto_context_id": dag["crypto_context_id"],
        "key_set_id": dag["key_set_id"],
        "applicant_count": dag["applicant_count"],
        "applicant_layout_sha256": dag["applicant_layout_sha256"],
        "job_count": len(job_results),
        "ciphertext_count": len(ciphertext_records),
        "ciphertext_bytes": sum(record["bytes"] for record in ciphertext_records),
        "peak_child_rss_kib": peak_rss,
        "continuity_probe": continuity,
        "timings_seconds": timings,
        "preparation_status": preparation_summary["backend_status"],
    }
    write_json(temp_dir / "benchmark_summary.json", summary)
    (temp_dir / "benchmark_report.md").write_text(
        _stage_report(summary), encoding="utf-8"
    )
    required = [
        temp_dir / "feature_bundle_manifest.json",
        temp_dir / "benchmark_summary.json",
        temp_dir / "benchmark_report.md",
        probe_path,
        *(temp_dir / record["file"] for record in ciphertext_records),
    ]
    write_completion(
        temp_dir,
        stage=stage,
        session_id=dag["session_id"],
        layout_sha256=dag["applicant_layout_sha256"],
        required_paths=required,
    )
    temp_dir.replace(final_dir)
    validate_encrypted_bundle(
        final_dir / "feature_bundle_manifest.json",
        session_id=dag["session_id"],
        context_id=dag["crypto_context_id"],
        key_set_id=dag["key_set_id"],
        layout_sha256=dag["applicant_layout_sha256"],
    )
    _write_checkpoint(run_root, stage, dag, final_dir)
    return bundle_manifest


def _write_checkpoint(
    run_root: Path, stage: str, dag: dict[str, Any], stage_dir: Path
) -> dict[str, Any]:
    previous = _load_json(_previous_checkpoint(run_root, stage))
    manifest_path = stage_dir / "feature_bundle_manifest.json"
    manifest = _load_json(manifest_path)
    checkpoint_dir = _checkpoint_dir(run_root, stage)
    if checkpoint_dir.exists():
        raise FileExistsError(f"checkpoint already exists: {checkpoint_dir}")
    checkpoint = {
        "status": "encrypted_complete",
        "checkpoint": _stage_number(stage),
        "latest_stage": stage,
        "session_id": dag["session_id"],
        "crypto_context_id": dag["crypto_context_id"],
        "key_set_id": dag["key_set_id"],
        "applicant_layout_sha256": dag["applicant_layout_sha256"],
        "function_bundles": [
            *previous["function_bundles"],
            {
                "stage": stage,
                "manifest": str(manifest_path),
                "manifest_sha256": sha256_file(manifest_path),
                "ciphertext_count": len(manifest["ciphertext_files"]),
            },
        ],
        "ciphertext_files": [
            *previous["ciphertext_files"],
            *(
                {
                    "stage": stage,
                    "file": str(stage_dir / record["file"]),
                    "sha256": record["sha256"],
                }
                for record in manifest["ciphertext_files"]
            ),
        ],
        "join_semantics": "zero_copy_app_index_aligned_bundle_index",
        "he_operations_for_join": 0,
    }
    checkpoints_root = run_root / "checkpoints"
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{stage}_checkpoint_", dir=checkpoints_root)
    )
    write_json(temp_dir / "bundle_index.json", checkpoint)
    temp_dir.replace(checkpoint_dir)
    return checkpoint


def finalize_dag(run_root: Path) -> dict[str, Any]:
    dag, _ = _load_run(run_root)
    _assert_predecessors(run_root, "credit_card", dag)
    credit_dir = _stage_dir(run_root, "credit_card")
    validate_completion(
        credit_dir,
        expected_stage="credit_card",
        session_id=dag["session_id"],
        layout_sha256=dag["applicant_layout_sha256"],
    )
    validate_encrypted_bundle(
        credit_dir / "feature_bundle_manifest.json",
        session_id=dag["session_id"],
        context_id=dag["crypto_context_id"],
        key_set_id=dag["key_set_id"],
        layout_sha256=dag["applicant_layout_sha256"],
    )
    final_dir = run_root / "final"
    if final_dir.exists():
        raise FileExistsError(f"final DAG output already exists: {final_dir}")
    last = _validate_checkpoint(
        _checkpoint_dir(run_root, "credit_card") / "bundle_index.json",
        dag,
        expected_number=len(STAGE_ORDER),
    )
    final_manifest = {
        **last,
        "status": "encrypted_end_to_end_complete",
        "scope": dag["scope"],
        "stage_count": len(STAGE_ORDER),
        "psi_timing_included": False,
    }
    write_json(final_dir / "encrypted_feature_bundle.json", final_manifest)
    report = f"""# End-to-end encrypted feature DAG

## Result

`encrypted_end_to_end_complete`

All five feature stages ran one process at a time under CKKS session
`{dag['session_id']}`. The final bundle references
{len(last['ciphertext_files'])} serialized ciphertext files without decrypting
or copying them. PSI timing is excluded.

## Scope

{dag['scope']}.

## Stage order

{' -> '.join(STAGE_ORDER)}

Each stage has its own `benchmark_report.md`, integrity-checked completion
marker, and ciphertext reload probe. This final file is a zero-copy index; it
performs no HE arithmetic.
"""
    (final_dir / "benchmark_report.md").write_text(report, encoding="utf-8")
    write_json(
        final_dir / "benchmark_summary.json",
        {
            "status": "encrypted_end_to_end_complete",
            "stage_count": len(STAGE_ORDER),
            "ciphertext_count": len(last["ciphertext_files"]),
            "artifact_bytes": path_size(final_dir),
            "psi_timing_included": False,
        },
    )
    return final_manifest


def dag_status(run_root: Path) -> dict[str, Any]:
    dag, _ = _load_run(run_root)
    stages = []
    for stage in STAGE_ORDER:
        directory = _stage_dir(run_root, stage)
        status = "pending"
        if directory.is_dir():
            try:
                validate_completion(
                    directory,
                    expected_stage=stage,
                    session_id=dag["session_id"],
                    layout_sha256=dag["applicant_layout_sha256"],
                )
                status = "encrypted_complete"
            except (ValueError, FileNotFoundError):
                status = "invalid_or_incomplete"
        stages.append({"stage": stage, "status": status})
    return {
        "run_id": dag["run_id"],
        "session_id": dag["session_id"],
        "stages": stages,
        "finalized": (run_root / "final" / "encrypted_feature_bundle.json").is_file(),
    }
