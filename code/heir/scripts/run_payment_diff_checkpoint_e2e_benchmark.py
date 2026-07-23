#!/usr/bin/env python3
"""Benchmark the exact ``payment_diff_checkpoint_e2e.py`` application flow.

The benchmark does not reimplement HE. It launches the example as a child
process with its optional timing trace enabled, then evaluates the same
post-PSI applicant group with the original Pandas PAYMENT_DIFF/groupby logic.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.common import write_csv, write_json
from code.heir.python_api import prepare_post_psi_groups


EXAMPLE = ROOT / "code/heir/examples/payment_diff_checkpoint_e2e.py"
OUTPUT_COLUMNS = {
    "max": "PAYMENT_DIFF_MAX",
    "mean": "PAYMENT_DIFF_MEAN",
    "sum": "PAYMENT_DIFF_SUM",
    "var": "PAYMENT_DIFF_VAR",
}


def _run_exact_example(
    *,
    installments: Path,
    bridge_dir: Path,
    checkpoint_dir: Path,
    execution_json: Path,
    bucket_size: int,
    max_ring_dimension: int,
    openfhe_dir: str,
    log_path: Path,
) -> tuple[float, list[str]]:
    command = [
        sys.executable,
        str(EXAMPLE),
        "--installments",
        str(installments),
        "--bridge-dir",
        str(bridge_dir),
        "--bucket-size",
        str(bucket_size),
        "--max-ring-dimension",
        str(max_ring_dimension),
        "--openfhe-dir",
        openfhe_dir,
        "--checkpoint-dir",
        str(checkpoint_dir),
        "--execution-json",
        str(execution_json),
        "--overwrite",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    wall_seconds = time.perf_counter() - started
    log_path.write_text(
        completed.stdout + completed.stderr,
        encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError(
            "exact checkpoint E2E example failed; inspect "
            f"{log_path}\n{completed.stdout}{completed.stderr}"
        )
    return wall_seconds, command


def _pandas_reference(
    installments: Path,
    bridge_dir: Path,
    bucket_size: int,
) -> tuple[dict[str, float], dict[str, float], dict[str, object]]:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError(
            "this benchmark requires pandas for the original-code-equivalent "
            "reference: python3 -m pip install pandas"
        ) from error

    total_started = time.perf_counter()
    started = time.perf_counter()
    layout = prepare_post_psi_groups(
        installments,
        bridge_dir,
        group_count=1,
        bucket_size=bucket_size,
        minimum_group_size=2,
    )
    preparation_seconds = time.perf_counter() - started
    group = layout.groups[0]

    started = time.perf_counter()
    frame = pd.DataFrame(
        {
            "opaque_group_id": [group.opaque_group_id] * group.real_count,
            "AMT_INSTALMENT": group.installment,
            "AMT_PAYMENT": group.payment,
        }
    )
    dataframe_seconds = time.perf_counter() - started

    started = time.perf_counter()
    frame["PAYMENT_DIFF"] = (
        frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]
    )
    expression_seconds = time.perf_counter() - started

    grouped = frame.groupby("opaque_group_id")["PAYMENT_DIFF"]
    started = time.perf_counter()
    aggregate = grouped.agg(["max", "mean", "sum", "var"]).iloc[0]
    combined_groupby_seconds = time.perf_counter() - started
    total_seconds = time.perf_counter() - total_started

    # These probes are diagnostic only and occur after the fair one-workload
    # total has stopped. They do not inflate the Pandas comparison.
    term_seconds: dict[str, float] = {}
    for term in OUTPUT_COLUMNS:
        started = time.perf_counter()
        grouped.agg(term)
        term_seconds[term] = time.perf_counter() - started
    values = {
        OUTPUT_COLUMNS[term]: float(aggregate[term])
        for term in OUTPUT_COLUMNS
    }
    timings = {
        "post_psi_prepare": preparation_seconds,
        "dataframe_construct": dataframe_seconds,
        "payment_diff_expression": expression_seconds,
        "combined_groupby": combined_groupby_seconds,
        "total": total_seconds,
        **{
            f"{term}_groupby_probe": seconds
            for term, seconds in term_seconds.items()
        },
    }
    input_info: dict[str, object] = {
        "post_psi_applicants": layout.post_psi_applicants,
        "source_rows_scanned": layout.source_rows_scanned,
        "invalid_parent_rows": layout.invalid_parent_rows,
        "selected_groups": 1,
        "real_rows": group.real_count,
        "bucket_size": bucket_size,
    }
    return values, timings, input_info


def _read_he_outputs(path: Path) -> dict[str, float]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise RuntimeError(
            f"expected one selected applicant in {path}; found {len(rows)}"
        )
    return {
        column: float(rows[0][column])
        for column in OUTPUT_COLUMNS.values()
    }


def _accuracy_rows(
    reference: dict[str, float],
    encrypted: dict[str, float],
    relative_tolerance: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for column in OUTPUT_COLUMNS.values():
        expected = reference[column]
        observed = encrypted[column]
        absolute_error = abs(expected - observed)
        relative_error = absolute_error / max(1.0, abs(expected))
        rows.append(
            {
                "output": column,
                "pandas": expected,
                "he_final_audit": observed,
                "absolute_error": absolute_error,
                "relative_error": relative_error,
                "tolerance": relative_tolerance,
                "status": (
                    "PASS"
                    if relative_error <= relative_tolerance
                    else "FAIL"
                ),
            }
        )
    return rows


def _seconds(mapping: dict[str, object], key: str) -> float:
    return float(mapping.get(key, 0.0))


def _report(result: dict[str, object]) -> str:
    exact = result["exact_execution"]
    branches = exact["aggregate_branches"]
    audits = exact["final_audit"]
    maximum = exact["maximum_branch"]
    pandas = result["pandas_timings_seconds"]
    accuracy = result["accuracy"]

    branch_rows = []
    for term in ("sum", "mean", "variance"):
        branch = branches[term]
        branch_rows.append(
            "| {term} | {compile:.9f} | {setup:.9f} | {encrypt:.9f} | "
            "{evaluate:.9f} | {save:.9f} | {audit:.9f} | {total:.9f} |".format(
                term=term.upper(),
                compile=_seconds(branch, "compile_seconds"),
                setup=_seconds(branch, "setup_seconds"),
                encrypt=_seconds(branch, "parent_encrypt_seconds"),
                evaluate=_seconds(branch, "evaluate_seconds"),
                save=_seconds(branch, "checkpoint_save_seconds"),
                audit=_seconds(audits, f"{term}_seconds"),
                total=(
                    _seconds(branch, "branch_total_seconds")
                    + _seconds(audits, f"{term}_seconds")
                ),
            )
        )

    accuracy_rows = [
        "| {output} | {pandas:.12g} | {he:.12g} | {absolute:.6g} | "
        "{relative:.6g} | {status} |".format(
            output=row["output"],
            pandas=row["pandas"],
            he=row["he_final_audit"],
            absolute=row["absolute_error"],
            relative=row["relative_error"],
            status=row["status"],
        )
        for row in accuracy
    ]

    return f"""# Exact checkpoint PAYMENT_DIFF end-to-end benchmark

This benchmark launches
`code/heir/examples/payment_diff_checkpoint_e2e.py` directly. It does not copy
or replace its HE logic. The measured cold path is:

`post-PSI layout → HEIR SUM/MEAN/VAR branches → encrypted checkpoints →`
`source-built OpenFHE CKKS↔FHEW MAX → final audit CSV`.

The existing PSI protocol run is outside this measurement. Reading the
post-PSI bridge and selecting/padding the applicant group are included.

## Input

| Source rows scanned | Post-PSI applicants | Selected groups | Real rows | Bucket size |
|---:|---:|---:|---:|---:|
| {exact["input"]["source_rows_scanned"]} | {exact["input"]["post_psi_applicants"]} | 1 | {exact["input"]["real_rows"]} | {exact["input"]["bucket_size"]} |

## Accuracy

| Output | Pandas | Final HE audit | Absolute error | Relative error | Status |
|---|---:|---:|---:|---:|---|
{chr(10).join(accuracy_rows)}

Acceptance uses relative tolerance `{result["relative_tolerance"]}`.

## HEIR aggregate branch latency

Each branch independently computes encrypted
`AMT_INSTALMENT - AMT_PAYMENT` from the same parent values. Decryption occurs
only in the final isolated audit process.

| Output branch | Compile | Setup/keygen | Parent encrypt | HE evaluate | Checkpoint save | Final audit | Branch through audit |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(branch_rows)}

## MAX branch latency

| CMake configure | CMake build | Context/switch-key setup | Parent encrypt | PAYMENT_DIFF | MAX switch | CT serialize | Final audit | MAX branch total |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| {_seconds(maximum, "cmake_configure"):.9f} | {_seconds(maximum, "cmake_build"):.9f} | {_seconds(maximum, "context_and_switching_key_setup"):.9f} | {_seconds(maximum, "parent_encrypt"):.9f} | {_seconds(maximum, "derived_subtraction"):.9f} | {_seconds(maximum, "maximum_switch"):.9f} | {_seconds(maximum, "ciphertext_serialize"):.9f} | {_seconds(maximum, "audit_decrypt"):.9f} | {_seconds(maximum, "branch_total_seconds"):.9f} |

## Complete workload latency

| Exact E2E orchestration stage | Seconds |
|---|---:|
| Client post-PSI scan/select/pad | {_seconds(exact, "client_post_psi_prepare_seconds"):.9f} |
| Public CKKS scale calibration | {_seconds(exact, "public_scale_seconds"):.9f} |
| Final feature CSV write | {_seconds(exact, "final_output_write_seconds"):.9f} |

| Workload | Seconds | HE ÷ Pandas |
|---|---:|---:|
| Pandas post-PSI preparation + DataFrame + expression + combined groupby | {_seconds(pandas, "total"):.9f} | 1.00× |
| Exact HE workflow, internal start-to-final-CSV | {_seconds(exact, "total_workflow_seconds"):.9f} | {_seconds(exact, "total_workflow_seconds") / _seconds(pandas, "total"):.2f}× |
| Exact example process wall time, including Python startup | {result["exact_process_wall_seconds"]:.9f} | {result["exact_process_wall_seconds"] / _seconds(pandas, "total"):.2f}× |

## Pandas term latency

The fair Pandas total uses one combined groupby. The individual probes below
time each requested aggregation separately only for per-term review.

| PAYMENT_DIFF expression | MAX probe | MEAN probe | SUM probe | VAR probe | Combined groupby |
|---:|---:|---:|---:|---:|---:|
| {_seconds(pandas, "payment_diff_expression"):.9f} | {_seconds(pandas, "max_groupby_probe"):.9f} | {_seconds(pandas, "mean_groupby_probe"):.9f} | {_seconds(pandas, "sum_groupby_probe"):.9f} | {_seconds(pandas, "var_groupby_probe"):.9f} | {_seconds(pandas, "combined_groupby"):.9f} |

Raw artifacts: `exact_execution.json`, `benchmark_result.json`,
`accuracy.csv`, `pandas_reference.json`, and `exact_example.log`.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installments",
        type=Path,
        default=Path("data/home_credit/installments_payments.csv"),
    )
    parser.add_argument("--bridge-dir", type=Path, required=True)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--max-ring-dimension", type=int, default=16384)
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--relative-tolerance", type=float, default=1e-5)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(
                f"refusing to overwrite benchmark output: {root}"
            )
        shutil.rmtree(root)
    root.mkdir(parents=True)

    installments = args.installments.resolve()
    bridge_dir = args.bridge_dir.resolve()
    exact_execution_path = root / "exact_execution.json"
    checkpoint_dir = root / "exact_checkpoint"
    process_wall, command = _run_exact_example(
        installments=installments,
        bridge_dir=bridge_dir,
        checkpoint_dir=checkpoint_dir,
        execution_json=exact_execution_path,
        bucket_size=args.bucket_size,
        max_ring_dimension=args.max_ring_dimension,
        openfhe_dir=args.openfhe_dir,
        log_path=root / "exact_example.log",
    )
    exact_execution = json.loads(
        exact_execution_path.read_text(encoding="utf-8")
    )
    pandas_values, pandas_timings, reference_input = _pandas_reference(
        installments,
        bridge_dir,
        args.bucket_size,
    )
    if reference_input["real_rows"] != exact_execution["input"]["real_rows"]:
        raise RuntimeError("exact HE and Pandas reference selected different groups")
    he_values = _read_he_outputs(
        checkpoint_dir / "client_private" / "payment_diff_features.csv"
    )
    accuracy = _accuracy_rows(
        pandas_values,
        he_values,
        args.relative_tolerance,
    )

    write_csv(root / "accuracy.csv", list(accuracy[0]), accuracy)
    write_json(
        root / "pandas_reference.json",
        {
            "values": pandas_values,
            "timings_seconds": pandas_timings,
            "input": reference_input,
        },
    )
    result: dict[str, object] = {
        "status": (
            "PASS"
            if all(row["status"] == "PASS" for row in accuracy)
            else "FAIL"
        ),
        "exact_example": str(EXAMPLE),
        "exact_command": command,
        "exact_process_wall_seconds": process_wall,
        "exact_execution": exact_execution,
        "pandas_values": pandas_values,
        "pandas_timings_seconds": pandas_timings,
        "accuracy": accuracy,
        "relative_tolerance": args.relative_tolerance,
    }
    write_json(root / "benchmark_result.json", result)
    (root / "REPORT.md").write_text(_report(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "exact_process_wall_seconds": process_wall,
                "exact_workflow_seconds": exact_execution[
                    "total_workflow_seconds"
                ],
                "pandas_total_seconds": pandas_timings["total"],
                "output_dir": str(root),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
