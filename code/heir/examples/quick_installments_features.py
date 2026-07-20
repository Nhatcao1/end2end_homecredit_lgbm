"""A deliberately small, inspectable HEIR sketch for installments features.

It demonstrates the *post-encryption* expressions behind PAYMENT_PERC and
DPD/DBD.  The JSON file is plaintext expected output for review only. The MLIR
files are the operations intended to be lowered by HEIR, not a claim that
division/comparison are exact CKKS operations.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEMO_ROWS = (
    {"AMT_INSTALMENT": 800.0, "AMT_PAYMENT": 640.0, "DAYS_ENTRY_PAYMENT": -10.0, "DAYS_INSTALMENT": -20.0},
    {"AMT_INSTALMENT": 500.0, "AMT_PAYMENT": 600.0, "DAYS_ENTRY_PAYMENT": -20.0, "DAYS_INSTALMENT": -10.0},
    {"AMT_INSTALMENT": 1000.0, "AMT_PAYMENT": 1000.0, "DAYS_ENTRY_PAYMENT": -15.0, "DAYS_INSTALMENT": -15.0},
)


def payment_perc_newton_mlir(vector_size: int, amount_scale: float = 1000.0) -> str:
    """Approximate payment / installment with two encrypted Newton steps.

    ``amount_scale`` is public representation/range policy. The kernel first
    normalizes an encrypted raw installment by it, then computes 1/x twice by
    ``x <- x * (2 - d*x)``. This is approximate and requires valid positive
    installments to lie in the documented normalized range.
    """
    if vector_size <= 0 or amount_scale <= 0:
        raise ValueError("vector size and amount scale must be positive")
    tensor = f"tensor<{vector_size}xf64>"
    inverse_scale = 1.0 / amount_scale
    return f'''func.func @payment_perc_newton(
    %payment: {tensor} {{secret.secret}},
    %installment: {tensor} {{secret.secret}},
    %valid: {tensor} {{secret.secret}}
) -> {tensor} {{
  %zero = arith.constant dense<0.0> : {tensor}
  %inv_scale = arith.constant {inverse_scale:.17g} : f64
  %a = arith.constant 2.8235294117647058 : f64
  %b = arith.constant 1.8823529411764706 : f64
  %two = arith.constant 2.0 : f64
  %result = affine.for %i = 0 to {vector_size} iter_args(%out = %zero) -> ({tensor}) {{
    %p = tensor.extract %payment[%i] : {tensor}
    %d_raw = tensor.extract %installment[%i] : {tensor}
    %m = tensor.extract %valid[%i] : {tensor}
    %d = arith.mulf %d_raw, %inv_scale : f64
    // Linear seed is fitted for normalized installment d in [0.5, 1.0].
    %seed_product = arith.mulf %b, %d : f64
    %x0 = arith.subf %a, %seed_product : f64
    %step0_product = arith.mulf %d, %x0 : f64
    %step0_error = arith.subf %two, %step0_product : f64
    %x1 = arith.mulf %x0, %step0_error : f64
    %step1_product = arith.mulf %d, %x1 : f64
    %step1_error = arith.subf %two, %step1_product : f64
    %inverse_normalized = arith.mulf %x1, %step1_error : f64
    %unscaled = arith.mulf %p, %inverse_normalized : f64
    %ratio = arith.mulf %unscaled, %inv_scale : f64
    // Missing/invalid lanes are zeroed only after encrypted calculation.
    %masked_ratio = arith.mulf %ratio, %m : f64
    %next = tensor.insert %masked_ratio into %out[%i] : {tensor}
    affine.yield %next : {tensor}
  }}
  return %result : {tensor}
}}
'''


def positive_difference_mlir(vector_size: int, days_range: float = 10.0) -> str:
    """Approximate ``max(left - right, 0)`` with a CKKS sign polynomial.

    This one generic kernel represents DPD when left is DAYS_ENTRY_PAYMENT and
    DBD when the input order is reversed.  It is necessarily approximate near
    zero; exact comparison belongs to the separate CKKS-to-FHEW route.
    """
    if vector_size <= 0 or days_range <= 0:
        raise ValueError("vector size and day range must be positive")
    tensor = f"tensor<{vector_size}xf64>"
    inverse_range = 1.0 / days_range
    return f'''func.func @positive_difference_smoothstep(
    %left: {tensor} {{secret.secret}},
    %right: {tensor} {{secret.secret}},
    %valid: {tensor} {{secret.secret}}
) -> {tensor} {{
  %zero = arith.constant dense<0.0> : {tensor}
  %inv_range = arith.constant {inverse_range:.17g} : f64
  %range = arith.constant {days_range:.17g} : f64
  %three = arith.constant 3.0 : f64
  %ten = arith.constant 10.0 : f64
  %fifteen = arith.constant 15.0 : f64
  %one_eighth = arith.constant 0.125 : f64
  %one_half = arith.constant 0.5 : f64
  %result = affine.for %i = 0 to {vector_size} iter_args(%out = %zero) -> ({tensor}) {{
    %l = tensor.extract %left[%i] : {tensor}
    %r = tensor.extract %right[%i] : {tensor}
    %m = tensor.extract %valid[%i] : {tensor}
    %raw_difference = arith.subf %l, %r : f64
    %x = arith.mulf %raw_difference, %inv_range : f64
    %x2 = arith.mulf %x, %x : f64
    // smoothstep sign approximation: (15x - 10x^3 + 3x^5) / 8
    %three_x2 = arith.mulf %three, %x2 : f64
    %three_x2_minus_ten = arith.subf %three_x2, %ten : f64
    %middle = arith.mulf %three_x2_minus_ten, %x2 : f64
    %poly = arith.addf %middle, %fifteen : f64
    %sign_numerator = arith.mulf %poly, %x : f64
    %sign = arith.mulf %sign_numerator, %one_eighth : f64
    %abs_approx = arith.mulf %x, %sign : f64
    %sum = arith.addf %x, %abs_approx : f64
    %positive_normalized = arith.mulf %sum, %one_half : f64
    %positive = arith.mulf %positive_normalized, %range : f64
    %masked_positive = arith.mulf %positive, %m : f64
    %next = tensor.insert %masked_positive into %out[%i] : {tensor}
    affine.yield %next : {tensor}
  }}
  return %result : {tensor}
}}
'''


def expected_plaintext() -> list[dict[str, float]]:
    """Notebook-equivalent output for the tiny review data only."""
    output = []
    for row in DEMO_ROWS:
        payment = row["AMT_PAYMENT"]
        installment = row["AMT_INSTALMENT"]
        entry = row["DAYS_ENTRY_PAYMENT"]
        due = row["DAYS_INSTALMENT"]
        output.append({
            "PAYMENT_PERC": payment / installment,
            "PAYMENT_DIFF": installment - payment,
            "DPD": max(entry - due, 0.0),
            "DBD": max(due - entry, 0.0),
        })
    return output


def emit(output_dir: Path, vector_size: int) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "payment_perc_newton.mlir").write_text(
        payment_perc_newton_mlir(vector_size), encoding="utf-8"
    )
    (output_dir / "positive_difference_smoothstep.mlir").write_text(
        positive_difference_mlir(vector_size), encoding="utf-8"
    )
    manifest = {
        "status": "inspectable_heir_sketch_not_benchmark",
        "vector_size": vector_size,
        "input_rows": list(DEMO_ROWS),
        "expected_plaintext_output": expected_plaintext(),
        "payment_perc": {
            "route": "CKKS reciprocal polynomial + multiply",
            "accuracy": "approximate; range-dependent",
            "range_requirement": "AMT_INSTALMENT / 1000 must be in [0.5, 1.0] for the displayed seed",
        },
        "dpd_dbd": {
            "route": "CKKS smoothstep sign polynomial",
            "accuracy": "approximate near zero; not an exact comparison",
            "input_order": {"DPD": "DAYS_ENTRY_PAYMENT - DAYS_INSTALMENT", "DBD": "DAYS_INSTALMENT - DAYS_ENTRY_PAYMENT"},
            "range_requirement": "the tiny demo uses absolute day differences at most 10; production needs a separately validated public range policy",
        },
    }
    (output_dir / "expected_plaintext.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_runs/quick_installments_features"))
    parser.add_argument("--vector-size", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(emit(args.output_dir, args.vector_size), indent=2))


if __name__ == "__main__":
    main()
