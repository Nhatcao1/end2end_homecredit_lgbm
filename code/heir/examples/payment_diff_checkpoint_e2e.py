#!/usr/bin/env python3
"""Conceptual CSV → PSI join → HE subtract → checkpoint → reload example."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    compile_checkpointable_binary_column,
    load_binary_column_checkpoint,
    prepare_post_psi_groups,
    public_power_of_two_scale,
    save_binary_column_checkpoint,
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

    # Generic equivalent of:
    # df["derived"] = df["left"] - df["right"]
    subtract = compile_checkpointable_binary_column(
        operation="subtract",
        width=args.bucket_size,
        input_scale=scale,
    )
    subtract.setup()
    parent_ciphertexts = subtract.encrypt(
        group.installment,
        group.payment,
    )
    derived_ciphertext = subtract.eval(parent_ciphertexts)

    # This writes context, public key, encrypted parents and encrypted result.
    # The secret key is written separately under client_private/.
    save_binary_column_checkpoint(
        subtract,
        encrypted_columns=parent_ciphertexts,
        result_ciphertext=derived_ciphertext,
        valid_count=group.real_count,
        checkpoint_dir=args.checkpoint_dir,
        overwrite=args.overwrite,
    )

    # Conceptual restart boundary: reconstruct the program only from files.
    del subtract, parent_ciphertexts, derived_ciphertext
    restored = load_binary_column_checkpoint(
        args.checkpoint_dir,
        for_audit=True,
    )

    # Final client boundary. No plaintext derived column existed before here.
    payment_diff = restored.program.decrypt(
        restored.result_ciphertext,
        valid_count=int(restored.manifest["valid_count"]),
    )
    audit_csv = (
        args.checkpoint_dir.resolve()
        / "client_private"
        / "payment_diff_audit.csv"
    )
    with audit_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["SK_ID_CURR", "lane", "PAYMENT_DIFF"]
        )
        for lane, value in enumerate(payment_diff):
            writer.writerow([raw_applicant_id, lane, value])

    print(f"public checkpoint: {args.checkpoint_dir.resolve() / 'public'}")
    print(f"client audit: {audit_csv}")


if __name__ == "__main__":
    main()
