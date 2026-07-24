#!/usr/bin/env python3
"""Minimal real-data PAYMENT_DIFF application using the simple HE API.

This is intentionally not a benchmark. It contains no timers, repetitions,
performance reports, or generated benchmark tables.

Flow:

```
installments CSV
→ client selects one complete allowed group
→ encrypt AMT_INSTALMENT and AMT_PAYMENT
→ PAYMENT_DIFF.ct = installment.ct - payment.ct
→ encrypted SUM / MEAN / VAR / MIN / MAX
→ final client decryption
```
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import statistics
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    CkksSession,
    prepare_allowed_group_csv,
    public_power_of_two_scale,
)


def plaintext_reference(values: list[float]) -> dict[str, float]:
    """Return the equivalent non-HE result for the final correctness audit."""
    return {
        "sum": float(sum(values)),
        "mean": float(statistics.fmean(values)),
        "variance": float(statistics.variance(values)),
        "minimum": float(min(values)),
        "maximum": float(max(values)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installments",
        type=Path,
        default=Path("data/home_credit/installments_payments.csv"),
    )
    parser.add_argument("--allowed-sk-id-curr", required=True)
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_runs/payment_diff_simple_api_e2e"),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(
                f"refusing to overwrite application output: {root}"
            )
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    client_private = root / "client_private"
    client_private.mkdir(parents=True)

    prepared = prepare_allowed_group_csv(
        args.installments.resolve(),
        allowed_sk_id_curr=args.allowed_sk_id_curr,
        bucket_size=0,
        maximum_width=args.ring_dimension // 2,
        output_csv=client_private / "prepared_group.csv",
        overwrite=False,
    )
    group = prepared.group
    if group.real_count < 2:
        raise ValueError("SUM/MEAN/VAR/MIN/MAX require at least two rows")

    # The scale is public representation metadata. Parent values are placed
    # inside CKKS's comparison interval before encryption.
    input_scale = public_power_of_two_scale(
        [*group.installment, *group.payment]
    )
    he = CkksSession.create(
        width=prepared.bucket_size,
        input_scale=input_scale,
        ring_dimension=args.ring_dimension,
    )

    installment_ct = he.encrypt_column(group.installment)
    payment_ct = he.encrypt_column(group.payment)

    # No plaintext feature is calculated here.
    payment_diff_ct = he.subtract(installment_ct, payment_ct)

    encrypted_outputs = {
        "sum": he.sum(payment_diff_ct),
        "mean": he.mean(payment_diff_ct),
        "variance": he.variance(payment_diff_ct),
        "minimum": he.minimum(payment_diff_ct),
        "maximum": he.maximum(payment_diff_ct),
    }

    # This is the only decryption boundary.
    final_audit = {
        name: he.decrypt_scalar(ciphertext)
        for name, ciphertext in encrypted_outputs.items()
    }
    expected_values = [
        installment - payment
        for installment, payment in zip(
            group.installment,
            group.payment,
        )
    ]
    reference = plaintext_reference(expected_values)
    comparison = {
        name: {
            "plaintext_reference": reference[name],
            "final_he_audit": final_audit[name],
            "absolute_error": abs(reference[name] - final_audit[name]),
        }
        for name in reference
    }
    if not all(
        math.isfinite(row["final_he_audit"])
        for row in comparison.values()
    ):
        raise RuntimeError("final HE audit contains a non-finite value")

    result = {
        "status": "payment_diff_simple_api_executed",
        "allowed_sk_id_curr": str(args.allowed_sk_id_curr),
        "real_rows": group.real_count,
        "encrypted_width": prepared.bucket_size,
        "input_scale": input_scale,
        "same_openfhe_context": True,
        "no_intermediate_decryption": True,
        "expression": "AMT_INSTALMENT.ct - AMT_PAYMENT.ct",
        "outputs": comparison,
    }
    result_path = client_private / "final_audit.json"
    result_path.write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    print(f"final client audit: {result_path}")


if __name__ == "__main__":
    main()
