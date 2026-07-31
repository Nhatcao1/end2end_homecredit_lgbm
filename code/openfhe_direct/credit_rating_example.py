#!/usr/bin/env python3
"""Direct OpenFHE-Python calculations over prepared credit-rating data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import OpenFHECreditSession
from code.openfhe_direct.prepared_data import load_prepared_group


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-group", type=Path, required=True)
    parser.add_argument("--multiplicative-depth", type=int, default=4)
    parser.add_argument("--ring-dimension", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    group = load_prepared_group(args.prepared_group.resolve())
    session = OpenFHECreditSession(
        slot_count=group.slot_count,
        multiplicative_depth=args.multiplicative_depth,
        ring_dimension=args.ring_dimension,
    )

    # Encrypt the two parent DataFrame columns.
    installment_ct = session.encrypt(group.installment)
    payment_ct = session.encrypt(group.payment)

    # Original credit feature, calculated only after encryption.
    payment_diff_ct = session.subtract(installment_ct, payment_ct)

    # Reusable direct API calls.
    addition_ct = session.add(installment_ct, payment_ct)
    multiplication_ct = session.multiply(installment_ct, payment_ct)
    adjusted_payment_ct = session.add_public_vector(
        payment_ct,
        [10.0] * len(group.payment),
    )
    shifted_ct = session.add_public_scalar(payment_diff_ct, 1.5)
    half_diff_ct = session.multiply_public_scalar(payment_diff_ct, 0.5)
    squared_ct = session.square(payment_diff_ct)
    sum_ct = session.sum(payment_diff_ct)
    mean_ct = session.mean(payment_diff_ct)
    variance = session.variance_components(payment_diff_ct)
    covariance = session.covariance_components(
        installment_ct,
        payment_ct,
    )
    correlation = session.correlation_components(
        installment_ct,
        payment_ct,
    )
    weights = [1.0 / len(group.installment)] * len(group.installment)
    weighted_sum_ct = session.weighted_sum(payment_diff_ct, weights)
    risk_score_ct = session.risk_score(payment_diff_ct, weights, 1.5)

    # Explicit final decryption only.
    result = {
        "applicant_id": group.applicant_id,
        "addition": session.decrypt(addition_ct),
        "payment_diff": session.decrypt(payment_diff_ct),
        "multiplication": session.decrypt(multiplication_ct),
        "payment_plus_public_10": session.decrypt(adjusted_payment_ct),
        "payment_diff_plus_1_5": session.decrypt(shifted_ct),
        "payment_diff_times_public_0_5": session.decrypt(half_diff_ct),
        "payment_diff_squared": session.decrypt(squared_ct),
        "payment_diff_sum": session.decrypt(sum_ct),
        "payment_diff_mean": session.decrypt(mean_ct),
        "variance_components": {
            "sum_x": session.decrypt(variance.sum_x),
            "sum_x2": session.decrypt(variance.sum_x2),
        },
        "covariance_components": {
            "sum_x": session.decrypt(covariance.sum_x),
            "sum_y": session.decrypt(covariance.sum_y),
            "sum_xy": session.decrypt(covariance.sum_xy),
        },
        "correlation_components": {
            "sum_x": session.decrypt(correlation.sum_x),
            "sum_y": session.decrypt(correlation.sum_y),
            "sum_x2": session.decrypt(correlation.sum_x2),
            "sum_y2": session.decrypt(correlation.sum_y2),
            "sum_xy": session.decrypt(correlation.sum_xy),
        },
        "payment_diff_weighted_sum": session.decrypt(weighted_sum_ct),
        "payment_diff_risk_score": session.decrypt(risk_score_ct),
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(output)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
