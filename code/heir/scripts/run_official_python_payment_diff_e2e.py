#!/usr/bin/env python3
"""Clean post-PSI PAYMENT_DIFF E2E proof through exposed Python HE APIs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    OfficialCkksBinaryColumnStatistics,
    OfficialOpenFheColumnOps,
    prepare_post_psi_groups,
    public_power_of_two_scale,
)


def _timed(callable_, *args, **kwargs) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_(*args, **kwargs)
    return result, time.perf_counter() - started


def _write_layout(root: Path, layout, bucket_size: int) -> None:
    private = root / "client_private"
    ready = root / "he_ready"
    private.mkdir(parents=True, exist_ok=True)
    ready.mkdir(parents=True, exist_ok=True)
    with (private / "group_mapping.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["opaque_group_id", "SK_ID_CURR", "real_count"])
        writer.writerows(layout.private_mapping)
    with (ready / "group_blocks.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "opaque_group_id",
                "lane",
                "AMT_PAYMENT",
                "AMT_INSTALMENT",
                "validity_mask",
            ]
        )
        for group in layout.groups:
            for lane in range(bucket_size):
                if lane < group.real_count:
                    writer.writerow(
                        [
                            group.opaque_group_id,
                            lane,
                            format(group.payment[lane], ".17g"),
                            format(group.installment[lane], ".17g"),
                            1,
                        ]
                    )
                else:
                    writer.writerow([group.opaque_group_id, lane, 0, 0, 0])


def _pandas_reference(groups) -> tuple[dict[int, dict[str, float]], dict[str, float]]:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError(
            "Pandas is required for the equivalent plaintext workload: "
            "python3 -m pip install pandas"
        ) from error
    rows = [
        {
            "opaque_group_id": group.opaque_group_id,
            "AMT_PAYMENT": payment,
            "AMT_INSTALMENT": installment,
        }
        for group in groups
        for payment, installment in zip(
            group.payment,
            group.installment,
        )
    ]
    frame = pd.DataFrame(rows)
    started = time.perf_counter()
    frame["PAYMENT_DIFF"] = (
        frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]
    )
    feature_seconds = time.perf_counter() - started
    started = time.perf_counter()
    grouped = frame.groupby("opaque_group_id")["PAYMENT_DIFF"].agg(
        ["max", "mean", "sum", "var"]
    )
    groupby_seconds = time.perf_counter() - started
    return (
        {
            int(index): {
                name: float(value)
                for name, value in row.items()
            }
            for index, row in grouped.iterrows()
        },
        {
            "payment_diff_seconds": feature_seconds,
            "groupby_seconds": groupby_seconds,
            "total_seconds": feature_seconds + groupby_seconds,
        },
    )


def _input_scale(groups) -> float:
    parent_bounds = [
        abs(payment) + abs(installment)
        for group in groups
        for payment, installment in zip(
            group.payment,
            group.installment,
        )
    ]
    return public_power_of_two_scale(parent_bounds)


def _status(
    observed: float,
    expected: float,
    relative_tolerance: float,
) -> tuple[float, str]:
    error = abs(observed - expected)
    threshold = relative_tolerance * max(1.0, abs(expected))
    return error, "PASS" if error <= threshold else "FAIL"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _report(
    *,
    root: Path,
    layout,
    bucket_size: int,
    input_scale: float,
    reference_timing: dict[str, float],
    compile_seconds: float,
    statistics_setup_seconds: float,
    maximum_setup_seconds: float,
    rows: list[dict[str, Any]],
    tolerance: float,
) -> None:
    def total(field: str) -> float:
        return sum(float(row[field]) for row in rows)

    statistics_online = total("statistics_encrypt_seconds") + total(
        "statistics_evaluation_seconds"
    )
    maximum_online = total("maximum_encrypt_seconds") + total(
        "maximum_evaluation_seconds"
    )
    online = statistics_online + maximum_online
    audit = total("audit_decrypt_seconds")
    pandas = reference_timing["total_seconds"]
    lines = [
        "# Official Python post-PSI PAYMENT_DIFF end-to-end proof",
        "",
        "This is the clean Python-API version of the small end-to-end "
        "`installments_payments()` proof. The benchmark contains no generated "
        "C++, CMake project, or inline cryptographic runner.",
        "",
        "```text",
        "validated PSI bridge",
        "  -> client opaque group blocks",
        "  -> HEIR Python: Enc(installment), Enc(payment)",
        "       -> PAYMENT_DIFF = CT - CT",
        "       -> encrypted [SUM, MEAN, sample VAR]",
        "  -> OpenFHE Python: Enc(installment), Enc(payment)",
        "       -> PAYMENT_DIFF = CT - CT",
        "       -> CKKS-to-FHEW encrypted MAX",
        "  -> final audit decrypt only",
        "```",
        "",
        "SUM, MEAN, and VAR share one official HEIR-generated CKKS context and "
        "one encrypted three-lane result per group. Exact MAX uses a second "
        "OpenFHE CKKS↔FHEW context because HEIR Python cannot attach scheme-"
        "switching operations to its module-local CKKS objects. Parent "
        "encryption for MAX is therefore measured separately.",
        "",
        "| Post-PSI applicants | Selected opaque groups | Real rows | "
        "Public lanes/group | Shared public scale |",
        "|---:|---:|---:|---:|---:|",
        f"| {layout.post_psi_applicants} | {len(layout.groups)} | "
        f"{sum(group.real_count for group in layout.groups)} | "
        f"{bucket_size} | {input_scale:g} |",
        "",
        "## Final aggregate accuracy",
        "",
        "| Opaque group | Output | Pandas | Final HE audit | "
        "Absolute error | Status |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for row in rows:
        for label, expected_field, actual_field in (
            ("MAX", "python_max", "he_max"),
            ("MEAN", "python_mean", "he_mean"),
            ("SUM", "python_sum", "he_sum"),
            ("VAR", "python_var", "he_var"),
        ):
            lines.append(
                f"| {row['opaque_group_id']} | `PAYMENT_DIFF_{label}` | "
                f"{row[expected_field]:.12g} | {row[actual_field]:.12g} | "
                f"{row[label.lower() + '_absolute_error']:.12g} | "
                f"{row[label.lower() + '_status']} |"
            )
    lines.extend(
        [
            "",
            "## Equivalent Pandas workload",
            "",
            "```python",
            "ins['PAYMENT_DIFF'] = "
            "ins['AMT_INSTALMENT'] - ins['AMT_PAYMENT']",
            "ins.groupby('opaque_group_id')['PAYMENT_DIFF']"
            ".agg(['max', 'mean', 'sum', 'var'])",
            "```",
            "",
            "CSV reading, PSI execution, and client grouping/padding are not "
            "inside the Pandas-versus-HE calculation comparison.",
            "",
            "## Latency",
            "",
            "| Stage | Seconds |",
            "|---|---:|",
            f"| Client post-PSI scan/group/pad | "
            f"{layout.preparation_seconds:.9f} |",
            f"| HEIR statistics compile once | {compile_seconds:.9f} |",
            f"| HEIR statistics setup once | "
            f"{statistics_setup_seconds:.9f} |",
            f"| OpenFHE MAX setup once | {maximum_setup_seconds:.9f} |",
            f"| HEIR parent encryption, all groups | "
            f"{total('statistics_encrypt_seconds'):.9f} |",
            f"| HEIR PAYMENT_DIFF + SUM/MEAN/VAR, all groups | "
            f"{total('statistics_evaluation_seconds'):.9f} |",
            f"| MAX parent encryption, all groups | "
            f"{total('maximum_encrypt_seconds'):.9f} |",
            f"| MAX PAYMENT_DIFF + scheme switching, all groups | "
            f"{total('maximum_evaluation_seconds'):.9f} |",
            f"| HE online total | {online:.9f} |",
            f"| Final audit decrypt, all outputs | {audit:.9f} |",
            f"| Pandas expression + groupby | {pandas:.9f} |",
            "",
            "| Fair workload comparison | Seconds | HE ÷ Pandas |",
            "|---|---:|---:|",
            f"| Pandas PAYMENT_DIFF + four aggregates | {pandas:.9f} | 1.00× |",
            f"| HE encryption through final encrypted outputs | "
            f"{online:.9f} | {online / pandas:.2f}× |",
            "",
            "Every group's encrypted outputs are retained before the audit "
            "loop starts. There is no decrypt/re-encrypt hand-off between "
            "feature calculation and aggregation.",
            "",
            f"Acceptance uses relative tolerance `{tolerance:g}`. Raw values "
            "and timing are in `results.csv` and `summary.json`.",
        ]
    )
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installments",
        type=Path,
        default=Path("data/home_credit/installments_payments.csv"),
    )
    parser.add_argument("--bridge-dir", type=Path, required=True)
    parser.add_argument("--group-count", type=int, default=2)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--max-ring-dimension", type=int, default=16384)
    parser.add_argument("--relative-tolerance", type=float, default=1e-5)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "benchmark_runs/official_python_payment_diff_e2e"
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.bucket_size < 2:
        parser.error("--bucket-size must be at least two")
    if args.group_count < 1:
        parser.error("--group-count must be positive")

    root = args.output_dir.resolve()
    known = [
        root / "REPORT.md",
        root / "results.csv",
        root / "summary.json",
        root / "payment_diff_statistics.mlir",
        root / "client_private" / "group_mapping.csv",
        root / "he_ready" / "group_blocks.csv",
    ]
    existing = [path for path in known if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(
            f"refusing to overwrite {existing[0]}; pass --overwrite"
        )
    root.mkdir(parents=True, exist_ok=True)

    layout = prepare_post_psi_groups(
        args.installments.resolve(),
        args.bridge_dir.resolve(),
        group_count=args.group_count,
        bucket_size=args.bucket_size,
        minimum_group_size=2,
    )
    _write_layout(root, layout, args.bucket_size)
    reference, reference_timing = _pandas_reference(layout.groups)
    input_scale = _input_scale(layout.groups)

    statistics, compile_seconds = _timed(
        OfficialCkksBinaryColumnStatistics,
        operation="subtract",
        width=args.bucket_size,
        input_scale=input_scale,
        debug=args.debug,
    )
    _, statistics_setup_seconds = _timed(statistics.setup)
    comparison_ops = OfficialOpenFheColumnOps(
        width=args.bucket_size,
        input_scale=input_scale,
        ring_dimension=args.max_ring_dimension,
    )
    _, maximum_setup_seconds = _timed(comparison_ops.setup)
    (root / "payment_diff_statistics.mlir").write_text(
        statistics.mlir,
        encoding="utf-8",
    )

    pending: list[dict[str, Any]] = []
    for group in layout.groups:
        statistics_parents, statistics_encrypt = _timed(
            statistics.encrypt,
            group.installment,
            group.payment,
        )
        statistics_ct, statistics_evaluation = _timed(
            statistics.eval,
            statistics_parents,
            valid_count=group.real_count,
        )
        started = time.perf_counter()
        max_installment_ct = comparison_ops.encrypt(
            group.installment,
            padding="duplicate",
        )
        max_payment_ct = comparison_ops.encrypt(
            group.payment,
            padding="duplicate",
        )
        maximum_encrypt = time.perf_counter() - started
        started = time.perf_counter()
        max_difference_ct = comparison_ops.subtract(
            max_installment_ct,
            max_payment_ct,
        )
        maximum_ct = comparison_ops.maximum(max_difference_ct)
        maximum_evaluation = time.perf_counter() - started
        pending.append(
            {
                "group": group,
                "statistics_ct": statistics_ct,
                "maximum_ct": maximum_ct,
                "statistics_encrypt_seconds": statistics_encrypt,
                "statistics_evaluation_seconds": statistics_evaluation,
                "maximum_encrypt_seconds": maximum_encrypt,
                "maximum_evaluation_seconds": maximum_evaluation,
            }
        )

    rows: list[dict[str, Any]] = []
    for item in pending:
        group = item["group"]
        started = time.perf_counter()
        he_sum, he_mean, he_var = statistics.decrypt(
            item["statistics_ct"]
        )
        he_max = comparison_ops.decrypt_scalar(item["maximum_ct"])
        audit_seconds = time.perf_counter() - started
        expected = reference[group.opaque_group_id]
        row: dict[str, Any] = {
            "opaque_group_id": group.opaque_group_id,
            "python_max": expected["max"],
            "he_max": he_max,
            "python_mean": expected["mean"],
            "he_mean": he_mean,
            "python_sum": expected["sum"],
            "he_sum": he_sum,
            "python_var": expected["var"],
            "he_var": he_var,
        }
        for name in ("max", "mean", "sum", "var"):
            error, status = _status(
                float(row[f"he_{name}"]),
                float(row[f"python_{name}"]),
                args.relative_tolerance,
            )
            row[f"{name}_absolute_error"] = error
            row[f"{name}_status"] = status
        row.update(
            {
                "statistics_encrypt_seconds": item[
                    "statistics_encrypt_seconds"
                ],
                "statistics_evaluation_seconds": item[
                    "statistics_evaluation_seconds"
                ],
                "maximum_encrypt_seconds": item["maximum_encrypt_seconds"],
                "maximum_evaluation_seconds": item[
                    "maximum_evaluation_seconds"
                ],
                "audit_decrypt_seconds": audit_seconds,
            }
        )
        rows.append(row)

    _write_csv(root / "results.csv", rows)
    passed = all(
        row[f"{name}_status"] == "PASS"
        for row in rows
        for name in ("max", "mean", "sum", "var")
    )
    summary = {
        "status": "PASS" if passed else "FAIL",
        "scope": "post-PSI PAYMENT_DIFF MAX/MEAN/SUM/VAR",
        "implementation": "exposed official Python HE APIs",
        "selected_groups": len(layout.groups),
        "real_rows": sum(group.real_count for group in layout.groups),
        "heir_contexts": 1,
        "scheme_switching_contexts": 1,
        "no_intermediate_decryption": True,
        "statistics_ciphertext_result": "[SUM, MEAN, sample VAR]",
        "maximum_route": "separate parent encryption; CT-CT then CKKS-to-FHEW",
        "input_scale": input_scale,
        "timing_seconds": {
            "client_prepare": layout.preparation_seconds,
            "statistics_compile": compile_seconds,
            "statistics_setup": statistics_setup_seconds,
            "maximum_setup": maximum_setup_seconds,
            "pandas": reference_timing,
        },
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _report(
        root=root,
        layout=layout,
        bucket_size=args.bucket_size,
        input_scale=input_scale,
        reference_timing=reference_timing,
        compile_seconds=compile_seconds,
        statistics_setup_seconds=statistics_setup_seconds,
        maximum_setup_seconds=maximum_setup_seconds,
        rows=rows,
        tolerance=args.relative_tolerance,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
