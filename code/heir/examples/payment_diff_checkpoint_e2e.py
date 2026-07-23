#!/usr/bin/env python3
"""Runnable post-PSI PAYMENT_DIFF aggregates with an encrypted checkpoint.

This is application flow, not a benchmark/report generator:

``CSV -> post-PSI group -> three encrypted PAYMENT_DIFF aggregate branches
-> save/reload -> encrypted MAX branch -> final decrypt``.
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
    compile_checkpointable_binary_column_aggregate,
    load_binary_column_aggregate_checkpoint,
    prepare_post_psi_groups,
    public_power_of_two_scale,
    save_binary_column_aggregate_checkpoint,
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

    # HEIR Python 2026.7.1 is unreliable when one circuit returns a packed
    # SUM/MEAN/VAR tensor. Use one proven scalar-output circuit per branch.
    # Every branch still computes PAYMENT_DIFF after encryption; no plaintext
    # derived column is passed between branches.
    aggregate_root = args.checkpoint_dir.resolve() / "aggregates"
    branch_dirs: dict[str, Path] = {}
    for aggregate in ("sum", "mean", "variance"):
        print(f"[HEIR] compile {aggregate} branch", flush=True)
        branch = compile_checkpointable_binary_column_aggregate(
            operation="subtract",
            aggregate=aggregate,
            width=args.bucket_size,
            valid_count=group.real_count,
            input_scale=scale,
        )
        print(f"[HEIR] setup/encrypt/evaluate {aggregate} branch", flush=True)
        branch.setup()
        encrypted_parents = branch.encrypt(
            group.installment,
            group.payment,
        )
        encrypted_result = branch.eval(encrypted_parents)
        branch_dir = aggregate_root / aggregate
        save_binary_column_aggregate_checkpoint(
            branch,
            encrypted_columns=encrypted_parents,
            result_ciphertext=encrypted_result,
            checkpoint_dir=branch_dir,
            overwrite=args.overwrite,
        )
        print(f"[HEIR] saved {aggregate} checkpoint: {branch_dir}", flush=True)
        branch_dirs[aggregate] = branch_dir
        del branch, encrypted_parents, encrypted_result

    # Conceptual restart boundary: rebuild all three programs from files.
    restored = {
        aggregate: load_binary_column_aggregate_checkpoint(
            checkpoint_dir,
            for_audit=True,
        )
        for aggregate, checkpoint_dir in branch_dirs.items()
    }

    # Exact MAX needs CKKS-to-FHEW switching. The current HEIR Python module
    # cannot attach switching to its module-local ciphertexts, so this follows
    # the benchmark's separate OpenFHE context without decrypting either path.
    maximum = OfficialOpenFheColumnOps(
        width=args.bucket_size,
        input_scale=scale,
        ring_dimension=args.max_ring_dimension,
    )
    print("[OpenFHE] setup CKKS-to-FHEW MAX branch", flush=True)
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
    print("[OpenFHE] encrypted MAX ready", flush=True)

    # Final client boundary only. All four values were encrypted until here.
    payment_diff_sum = restored["sum"].program.decrypt(
        restored["sum"].result_ciphertext
    )
    payment_diff_mean = restored["mean"].program.decrypt(
        restored["mean"].result_ciphertext
    )
    payment_diff_var = restored["variance"].program.decrypt(
        restored["variance"].result_ciphertext
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

    print(f"aggregate checkpoints: {aggregate_root}")
    print(f"final client features: {audit_csv}")


if __name__ == "__main__":
    main()
