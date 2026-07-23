#!/usr/bin/env python3
"""Small complete PAYMENT_DIFF example using the exposed Python HE APIs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    OfficialOpenFhePaymentDiffMax,
    OfficialPaymentDiffGroupStatistics,
    prepare_post_psi_groups,
    public_power_of_two_scale,
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
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("benchmark_runs/payment_diff_features.csv"),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.output_csv.exists() and not args.overwrite:
        raise FileExistsError(
            f"refusing to overwrite {args.output_csv}; pass --overwrite"
        )

    # Client side: use the completed PSI bridge as a private semi-join.
    # Raw SK_ID_CURR values are replaced with opaque group ordinals.
    layout = prepare_post_psi_groups(
        args.installments.resolve(),
        args.bridge_dir.resolve(),
        group_count=args.group_count,
        bucket_size=args.bucket_size,
        minimum_group_size=2,
    )

    # One public power-of-two scale keeps both parent columns in CKKS range.
    parent_bounds = [
        abs(payment) + abs(installment)
        for group in layout.groups
        for payment, installment in zip(
            group.payment,
            group.installment,
        )
    ]
    scale = public_power_of_two_scale(parent_bounds)

    # SUM, MEAN and sample VAR share one official HEIR-generated CKKS context.
    statistics = OfficialPaymentDiffGroupStatistics(
        width=args.bucket_size,
        input_scale=scale,
    )
    statistics.setup()

    # Exact MAX needs OpenFHE CKKS-to-FHEW scheme switching, so it has a
    # separate context. PAYMENT_DIFF is still calculated after encryption.
    maximum = OfficialOpenFhePaymentDiffMax(
        width=args.bucket_size,
        input_scale=scale,
        ring_dimension=args.max_ring_dimension,
    )
    maximum.setup()

    # Evaluate every group first. Nothing is decrypted inside this loop.
    encrypted_outputs = []
    for group in layout.groups:
        statistics_parents = statistics.encrypt(group)
        statistics_ct = statistics.eval(
            statistics_parents,
            valid_count=group.real_count,
        )
        maximum_parents = maximum.encrypt(
            group.payment,
            group.installment,
        )
        maximum_ct = maximum.eval(maximum_parents)
        encrypted_outputs.append(
            (group.opaque_group_id, statistics_ct, maximum_ct)
        )

    # Final key-owner boundary: decrypt only the finished feature columns.
    features = []
    for opaque_group_id, statistics_ct, maximum_ct in encrypted_outputs:
        total, mean, variance = statistics.decrypt(statistics_ct)
        max_value = maximum.decrypt(maximum_ct)
        features.append(
            {
                "opaque_group_id": opaque_group_id,
                "PAYMENT_DIFF_MAX": max_value,
                "PAYMENT_DIFF_MEAN": mean,
                "PAYMENT_DIFF_SUM": total,
                "PAYMENT_DIFF_VAR": variance,
            }
        )

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(features[0]))
        writer.writeheader()
        writer.writerows(features)

    print(f"wrote {len(features)} encrypted-derived groups to {args.output_csv}")


if __name__ == "__main__":
    main()
