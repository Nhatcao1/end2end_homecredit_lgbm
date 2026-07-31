#!/usr/bin/env python3
"""Simple credit-data example using the remote OpenFHE HTTP service.

The caller only needs ``he_client``. OpenFHE runs inside the gateway Pod.
There is no HEIR, MLIR, CMake, or local OpenFHE setup in this file.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PreparedPaymentGroup:
    """Two clean, position-aligned parent columns for one applicant."""

    applicant_id: str
    installment: list[float]
    payment: list[float]


def load_prepared_group(path: Path) -> PreparedPaymentGroup:
    """Read real lanes from one client-prepared group and skip zero padding."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"prepared group is empty: {path}")

    installment: list[float] = []
    payment: list[float] = []
    padding_started = False
    for expected_lane, row in enumerate(rows):
        if int(row["lane"]) != expected_lane:
            raise ValueError(f"lanes are not contiguous in {path}")

        mask = int(row["VALID_MASK"])
        due = float(row["AMT_INSTALMENT"])
        paid = float(row["AMT_PAYMENT"])
        if mask == 0:
            padding_started = True
            if due != 0.0 or paid != 0.0:
                raise ValueError(f"padding must be zero in {path}")
            continue
        if mask != 1 or padding_started:
            raise ValueError(f"invalid VALID_MASK layout in {path}")

        installment.append(due)
        payment.append(paid)

    if len(installment) < 2:
        raise ValueError("at least two real rows are required")
    applicant_ids = {row["SK_ID_CURR"] for row in rows}
    if len(applicant_ids) != 1:
        raise ValueError("one prepared file must contain exactly one group")
    return PreparedPaymentGroup(
        applicant_id=applicant_ids.pop(),
        installment=installment,
        payment=payment,
    )


def decrypt_components(component: Any) -> dict[str, Any]:
    """Decrypt every named encrypted scalar returned by a component API."""
    result: dict[str, Any] = {}
    for name in (
        "sum_x",
        "sum_y",
        "sum_x2",
        "sum_y2",
        "sum_xy",
    ):
        encrypted_value = getattr(component, name, None)
        if encrypted_value is not None:
            result[name] = encrypted_value.decrypt()
    return result


def calculate_credit_group(
    he: Any,
    PublicScalar: type,
    PublicVector: type,
    group: PreparedPaymentGroup,
) -> dict[str, Any]:
    """Call every implemented gateway calculation for one credit-data group."""
    row_count = len(group.installment)

    # Public values do not need encryption. Uniform weights turn a weighted
    # sum into the average contribution across this applicant's rows.
    weights = PublicVector([1.0 / row_count] * row_count)
    bias = PublicScalar(1.5)

    # ENCRYPT: creates ciphertext handles in the gateway session.
    installment_ct = he.encrypt(group.installment)
    payment_ct = he.encrypt(group.payment)

    # ADD: encrypted AMT_INSTALMENT + encrypted AMT_PAYMENT, lane by lane.
    add_ct = he.add(installment_ct, payment_ct)

    # SUBTRACT: implements the original PAYMENT_DIFF feature after encryption.
    payment_diff_ct = he.subtract(installment_ct, payment_ct)

    # MULTIPLY: encrypted parent-column product, lane by lane.
    multiply_ct = he.multiply(installment_ct, payment_ct)

    # PUBLIC SCALAR: add a visible constant without encrypting the constant.
    public_scalar_ct = he.add(payment_diff_ct, bias)

    # PUBLIC VECTOR: multiply encrypted values by visible per-lane weights.
    public_vector_ct = he.multiply(payment_diff_ct, weights)

    # SQUARE: encrypted PAYMENT_DIFF × PAYMENT_DIFF.
    square_ct = he.square(payment_diff_ct)

    # SUM: one encrypted scalar containing Σ PAYMENT_DIFF.
    sum_ct = he.sum(payment_diff_ct)

    # MEAN: one encrypted scalar containing average PAYMENT_DIFF.
    mean_ct = he.mean(payment_diff_ct)

    # VARIANCE COMPONENTS: encrypted Σx and Σx². The caller may derive
    # population or sample variance from these final decrypted components.
    variance = he.variance_components(payment_diff_ct)

    # COVARIANCE COMPONENTS: encrypted Σx, Σy, and Σxy for the two parents.
    covariance = he.covariance_components(
        installment_ct,
        payment_ct,
    )

    # CORRELATION COMPONENTS: encrypted sums, squared sums, and product sum.
    correlation = he.correlation_components(
        installment_ct,
        payment_ct,
    )

    # WEIGHTED SUM: encrypted Σ(PAYMENT_DIFF[i] × public_weight[i]).
    weighted_sum_ct = he.weighted_sum(payment_diff_ct, weights)

    # RISK SCORE: encrypted linear score from values, public weights, and bias.
    # This demonstrates the service API; these are not trained LGBM weights.
    risk_score_ct = he.risk_score(
        payment_diff_ct,
        weights,
        bias,
    )

    # DECRYPT: the only plaintext boundary in this calculation flow.
    return {
        "applicant_id": group.applicant_id,
        "row_count": row_count,
        "add": add_ct.decrypt(),
        "payment_diff": payment_diff_ct.decrypt(),
        "multiply": multiply_ct.decrypt(),
        "add_public_scalar": public_scalar_ct.decrypt(),
        "multiply_public_vector": public_vector_ct.decrypt(),
        "square": square_ct.decrypt(),
        "sum": sum_ct.decrypt(),
        "mean": mean_ct.decrypt(),
        "variance_components": decrypt_components(variance),
        "covariance_components": decrypt_components(covariance),
        "correlation_components": decrypt_components(correlation),
        "weighted_sum": weighted_sum_ct.decrypt(),
        "risk_score": risk_score_ct.decrypt(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gateway-url",
        default="http://127.0.0.1:18082",
    )
    parser.add_argument(
        "--prepared-group",
        type=Path,
        nargs="+",
        required=True,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        he_client = importlib.import_module("he_client")
    except ImportError as error:
        raise RuntimeError(
            "he_client is required locally; HEIR and OpenFHE are not. "
            "Install the gateway client package or add it to PYTHONPATH."
        ) from error

    groups = [
        load_prepared_group(path.resolve())
        for path in args.prepared_group
    ]

    # One block means one in-memory gateway session reused by every group.
    with he_client.HEClient(args.gateway_url) as he:
        results = [
            calculate_credit_group(
                he,
                he_client.PublicScalar,
                he_client.PublicVector,
                group,
            )
            for group in groups
        ]

    output = json.dumps(
        {
            "gateway_url": args.gateway_url,
            "groups": results,
        },
        indent=2,
    ) + "\n"
    if args.output:
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output, encoding="utf-8")
        print(destination)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
