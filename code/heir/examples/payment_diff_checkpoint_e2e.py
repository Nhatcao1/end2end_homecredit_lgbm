#!/usr/bin/env python3
"""Runnable post-PSI PAYMENT_DIFF aggregates with an encrypted checkpoint.

This is application flow, not a benchmark/report generator:

``CSV -> post-PSI group -> encrypt parents -> PAYMENT_DIFF ->
encrypted SUM/MEAN/VAR -> save/reload -> encrypted MAX -> final decrypt``.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    OfficialOpenFheColumnOps,
    compile_checkpointable_binary_column_statistics,
    load_binary_column_statistics_checkpoint,
    prepare_post_psi_groups,
    public_power_of_two_scale,
    save_binary_column_statistics_checkpoint,
)


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
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(
            "benchmark_runs/payment_diff_checkpoint_example"
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    # Client-only post-PSI semi-join and grouping. The HE evaluator receives
    # neither the raw applicant key nor the join mapping.
    layout = prepare_post_psi_groups(
        args.installments.resolve(),
        args.bridge_dir.resolve(),
        group_count=1,
        bucket_size=args.bucket_size,
        minimum_group_size=2,
    )
    group = layout.groups[0]
    raw_applicant_id = layout.private_mapping[0][1]

    # A public scale is part of the CKKS representation contract.
    scale = public_power_of_two_scale(
        [
            abs(installment) + abs(payment)
            for installment, payment in zip(
                group.installment,
                group.payment,
            )
        ]
    )

    # One official HEIR circuit performs the original feature expression and
    # returns an encrypted [SUM, MEAN, sample VAR] tensor. There is no
    # PAYMENT_DIFF plaintext between these operations.
    statistics = compile_checkpointable_binary_column_statistics(
        operation="subtract",
        width=args.bucket_size,
        input_scale=scale,
    )
    statistics.setup()
    statistics_parents = statistics.encrypt(
        group.installment,
        group.payment,
    )
    statistics_ciphertext = statistics.eval(
        statistics_parents,
        valid_count=group.real_count,
    )

    # Persist the context, evaluation keys, encrypted parents, and encrypted
    # aggregate tensor. The audit key remains client-private.
    save_binary_column_statistics_checkpoint(
        statistics,
        encrypted_columns=statistics_parents,
        result_ciphertext=statistics_ciphertext,
        valid_count=group.real_count,
        checkpoint_dir=args.checkpoint_dir,
        overwrite=args.overwrite,
    )

    # Conceptual restart boundary: reconstruct the program only from files.
    del statistics, statistics_parents, statistics_ciphertext
    restored = load_binary_column_statistics_checkpoint(
        args.checkpoint_dir,
        for_audit=True,
    )

    # Exact MAX needs CKKS-to-FHEW switching. The current HEIR Python module
    # cannot attach switching to its module-local ciphertexts, so this follows
    # the benchmark's separate OpenFHE context without decrypting either path.
    maximum = OfficialOpenFheColumnOps(
        width=args.bucket_size,
        input_scale=scale,
        ring_dimension=args.max_ring_dimension,
    )
    maximum.setup()
    max_installment = maximum.encrypt(
        group.installment,
        padding="duplicate",
    )
    max_payment = maximum.encrypt(
        group.payment,
        padding="duplicate",
    )
    max_payment_diff = maximum.subtract(max_installment, max_payment)
    maximum_ciphertext = maximum.maximum(max_payment_diff)

    # Final client boundary only. All four values were encrypted until here.
    payment_diff_sum, payment_diff_mean, payment_diff_var = (
        restored.program.decrypt(restored.result_ciphertext)
    )
    payment_diff_max = maximum.decrypt_scalar(maximum_ciphertext)
    audit_csv = (
        args.checkpoint_dir.resolve()
        / "client_private"
        / "payment_diff_features.csv"
    )
    with audit_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "SK_ID_CURR",
                "PAYMENT_DIFF_MAX",
                "PAYMENT_DIFF_MEAN",
                "PAYMENT_DIFF_SUM",
                "PAYMENT_DIFF_VAR",
            ]
        )
        writer.writerow(
            [
                raw_applicant_id,
                payment_diff_max,
                payment_diff_mean,
                payment_diff_sum,
                payment_diff_var,
            ]
        )

    print(f"public checkpoint: {args.checkpoint_dir.resolve() / 'public'}")
    print(f"final client features: {audit_csv}")


if __name__ == "__main__":
    main()
