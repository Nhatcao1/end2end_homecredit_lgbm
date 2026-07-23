#!/usr/bin/env python3
"""Post-PSI five-group PAYMENT_DIFF SUM using official HEIR Python."""

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
    OfficialPaymentDiffGroupSum,
    prepare_post_psi_groups,
)


def _timed(callable_, *args, **kwargs) -> tuple[Any, float]:
    started = time.perf_counter()
    result = callable_(*args, **kwargs)
    return result, time.perf_counter() - started


def _pandas_reference(groups) -> tuple[dict[int, float], dict[str, float]]:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError(
            "Pandas is required for the plaintext groupby reference: "
            "python3 -m pip install pandas"
        ) from error
    rows = [
        {
            "opaque_group_id": group.opaque_group_id,
            "AMT_PAYMENT": payment,
            "AMT_INSTALMENT": installment,
        }
        for group in groups
        for payment, installment in zip(group.payment, group.installment)
    ]
    frame = pd.DataFrame(rows)
    started = time.perf_counter()
    frame["PAYMENT_DIFF"] = (
        frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]
    )
    feature_seconds = time.perf_counter() - started
    started = time.perf_counter()
    grouped = frame.groupby("opaque_group_id")["PAYMENT_DIFF"].sum()
    groupby_seconds = time.perf_counter() - started
    return (
        {int(index): float(value) for index, value in grouped.items()},
        {
            "payment_diff_seconds": feature_seconds,
            "groupby_sum_seconds": groupby_seconds,
            "total_seconds": feature_seconds + groupby_seconds,
        },
    )


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


def _report(
    *,
    bridge_dir: Path,
    layout,
    bucket_size: int,
    compile_seconds: float,
    setup_seconds: float,
    pandas_timing: dict[str, float],
    rows: list[dict[str, Any]],
) -> str:
    total_encrypt = sum(float(row["encrypt_seconds"]) for row in rows)
    total_eval = sum(float(row["evaluation_seconds"]) for row in rows)
    total_decrypt = sum(float(row["audit_decrypt_seconds"]) for row in rows)
    lines = [
        "# Official Python post-PSI groupby trial",
        "",
        "SecretFlow PSI has already decided the eligible applicant intersection. "
        "This Python trial consumes that bridge, performs client-side grouping "
        "and opaque ordinal assignment, then uses one reusable official HEIR "
        "CKKS program for every group.",
        "",
        "Encrypted circuit:",
        "",
        "```text",
        "Enc(AMT_INSTALMENT block), Enc(AMT_PAYMENT block)",
        "    -> encrypted CT - CT",
        "    -> encrypted SUM over the fixed block",
        "    -> result ciphertext retained",
        "```",
        "",
        f"- PSI bridge: `{bridge_dir}`",
        f"- Post-PSI applicants available: `{layout.post_psi_applicants}`",
        f"- Selected opaque groups: `{len(layout.groups)}`",
        f"- Fixed lanes per group: `{bucket_size}`",
        f"- Real selected rows: `{sum(group.real_count for group in layout.groups)}`",
        "",
        "## Accuracy",
        "",
        "| Opaque group | Real rows | Pandas PAYMENT_DIFF SUM | "
        "HE final audit | Absolute error | Status |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['opaque_group_id']} | {row['real_count']} | "
            f"{row['python_sum']:.12g} | {row['he_sum']:.12g} | "
            f"{row['absolute_error']:.12g} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "## Timing",
            "",
            "| Stage | Seconds |",
            "|---|---:|",
            f"| Client post-PSI scan/group/pad | {layout.preparation_seconds:.9f} |",
            f"| HEIR compile once | {compile_seconds:.9f} |",
            f"| HEIR context/key setup once | {setup_seconds:.9f} |",
            f"| Encrypt all group parents | {total_encrypt:.9f} |",
            f"| Encrypted CT−CT + SUM, all groups | {total_eval:.9f} |",
            f"| Final audit decrypt, all groups | {total_decrypt:.9f} |",
            f"| Pandas PAYMENT_DIFF + groupby SUM | {pandas_timing['total_seconds']:.9f} |",
            "",
            "All group result ciphertexts are evaluated first and retained in "
            "memory. The audit loop decrypts only after every group has "
            "finished. PSI execution time is excluded; this trial starts from "
            "the already validated PSI bridge.",
            "",
            "`client_private/group_mapping.csv` contains raw applicant IDs and "
            "must remain with the data owner. `he_ready/group_blocks.csv` "
            "contains opaque group ordinals, parent amounts, zero padding, and "
            "a review mask; PAYMENT_DIFF is not prepared in plaintext.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installments",
        type=Path,
        default=Path("data/home_credit/installments_payments.csv"),
    )
    parser.add_argument("--bridge-dir", type=Path, required=True)
    parser.add_argument("--group-count", type=int, default=5)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--relative-tolerance", type=float, default=1e-6)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "benchmark_runs/official_python_post_psi_groupby_trial"
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    root = args.output_dir.resolve()
    known = [
        root / "REPORT.md",
        root / "results.csv",
        root / "summary.json",
        root / "payment_diff_sum.mlir",
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
    )
    _write_layout(root, layout, args.bucket_size)
    reference, pandas_timing = _pandas_reference(layout.groups)

    program, compile_seconds = _timed(
        OfficialPaymentDiffGroupSum,
        width=args.bucket_size,
        debug=args.debug,
    )
    _, setup_seconds = _timed(program.setup)

    pending: list[tuple[Any, Any, float, float]] = []
    for group in layout.groups:
        encrypted_parents, encrypt_seconds = _timed(program.encrypt, group)
        encrypted_sum, evaluation_seconds = _timed(
            program.eval,
            encrypted_parents,
        )
        pending.append(
            (
                group,
                encrypted_sum,
                encrypt_seconds,
                evaluation_seconds,
            )
        )

    rows: list[dict[str, Any]] = []
    for group, encrypted_sum, encrypt_seconds, evaluation_seconds in pending:
        he_sum, decrypt_seconds = _timed(program.decrypt, encrypted_sum)
        python_sum = reference[group.opaque_group_id]
        absolute_error = abs(he_sum - python_sum)
        status = (
            "PASS"
            if absolute_error
            <= args.relative_tolerance * max(1.0, abs(python_sum))
            else "FAIL"
        )
        rows.append(
            {
                "opaque_group_id": group.opaque_group_id,
                "real_count": group.real_count,
                "python_sum": python_sum,
                "he_sum": he_sum,
                "absolute_error": absolute_error,
                "status": status,
                "encrypt_seconds": encrypt_seconds,
                "evaluation_seconds": evaluation_seconds,
                "audit_decrypt_seconds": decrypt_seconds,
            }
        )

    with (root / "results.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "status": (
            "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
        ),
        "scope": "post-PSI client grouping plus official HEIR Python PAYMENT_DIFF SUM",
        "psi_execution": "not rerun; consumes validated bridge",
        "bridge_dir": str(args.bridge_dir.resolve()),
        "post_psi_applicants": layout.post_psi_applicants,
        "selected_groups": len(layout.groups),
        "bucket_size": args.bucket_size,
        "real_rows": sum(group.real_count for group in layout.groups),
        "one_heir_program_and_context_reused": True,
        "compile_seconds": compile_seconds,
        "setup_seconds": setup_seconds,
        "client_preparation_seconds": layout.preparation_seconds,
        "pandas_timing": pandas_timing,
        "results": rows,
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "payment_diff_sum.mlir").write_text(
        program.mlir,
        encoding="utf-8",
    )
    (root / "REPORT.md").write_text(
        _report(
            bridge_dir=args.bridge_dir.resolve(),
            layout=layout,
            bucket_size=args.bucket_size,
            compile_seconds=compile_seconds,
            setup_seconds=setup_seconds,
            pandas_timing=pandas_timing,
            rows=rows,
        ),
        encoding="utf-8",
    )
    print((root / "summary.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
