#!/usr/bin/env python3
"""One-context ciphertext-in/ciphertext-out PAYMENT_DIFF example."""

from __future__ import annotations

from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from code.heir.python_api import CkksSession


def main() -> None:
    installment = [800.0, 500.0, 1000.0]
    payment = [640.0, 600.0, 1000.0]

    he = CkksSession.create(
        width=4,
        input_scale=4096.0,
        ring_dimension=16384,
    )

    installment_ct = he.encrypt_column(installment)
    payment_ct = he.encrypt_column(payment)

    added_ct = he.add(installment_ct, payment_ct)
    payment_diff_ct = he.subtract(installment_ct, payment_ct)
    multiplied_ct = he.multiply(installment_ct, payment_ct)

    sum_ct = he.sum(payment_diff_ct)
    mean_ct = he.mean(payment_diff_ct)
    variance_ct = he.variance(payment_diff_ct)
    minimum_ct = he.minimum(payment_diff_ct)
    maximum_ct = he.maximum(payment_diff_ct)

    # This is the only decryption boundary in the example.
    audited = {
        "add": he.decrypt_column(added_ct),
        "payment_diff": he.decrypt_column(payment_diff_ct),
        "multiply": he.decrypt_column(multiplied_ct),
        "sum": he.decrypt_scalar(sum_ct),
        "mean": he.decrypt_scalar(mean_ct),
        "variance": he.decrypt_scalar(variance_ct),
        "minimum": he.decrypt_scalar(minimum_ct),
        "maximum": he.decrypt_scalar(maximum_ct),
    }
    print(
        json.dumps(
            {
                "status": "simple_ciphertext_api_executed",
                "same_context": True,
                "no_intermediate_decryption": True,
                "final_audit": audited,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
