"""Encrypted mean and sample-variance finalization over sufficient statistics."""

from __future__ import annotations

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="K04",
    name="mean_sample_variance",
    entry_function="mean_sample_variance",
    lane="source-derived",
    operation="mean=sum/count; sample_variance=(sum_squares-sum*mean)/(count-1)",
    inputs=(
        "count: encrypted scalar in a public valid-count range",
        "sum: encrypted scalar",
        "sum_squares: encrypted scalar",
    ),
    outputs=("mean: encrypted scalar", "sample_variance: encrypted scalar"),
    multiplicative_depth="two Newton steps for count; three for count-1; encrypted finalization",
    expected_evaluation_keys=("relinearization",),
)


def mean_sample_variance_mlir(
    *, min_count: int = 2, max_count: int = 4
) -> str:
    """Finalize encrypted moments under a public count-range contract.

    ``count`` remains encrypted. The public bounds are a packing policy, not
    the realised count. This initial review kernel supports groups containing
    two through four valid rows, which includes the three-row demo.
    """
    if min_count < 2 or max_count < min_count:
        raise ValueError("sample variance requires 2 <= min_count <= max_count")
    if (min_count, max_count) != (2, 4):
        raise ValueError("this initial CKKS finalizer is calibrated for count range [2, 4]")
    return """func.func @mean_sample_variance(
    %count: f64 {secret.secret},
    %sum: f64 {secret.secret},
    %sum_squares: f64 {secret.secret}
) -> (f64, f64) {
  // count / 4 is in [0.5, 1.0]. The linear seed is minimax-relative on that range.
  %quarter = arith.constant 0.25 : f64
  %seed_a_count = arith.constant 2.8235294117647058 : f64
  %seed_b_count = arith.constant 1.8823529411764706 : f64
  %two = arith.constant 2.0 : f64
  %count_normalized = arith.mulf %count, %quarter : f64
  %count_seed_product = arith.mulf %seed_b_count, %count_normalized : f64
  %count_x0 = arith.subf %seed_a_count, %count_seed_product : f64
  %count_step0_product = arith.mulf %count_normalized, %count_x0 : f64
  %count_step0_error = arith.subf %two, %count_step0_product : f64
  %count_x1 = arith.mulf %count_x0, %count_step0_error : f64
  %count_step1_product = arith.mulf %count_normalized, %count_x1 : f64
  %count_step1_error = arith.subf %two, %count_step1_product : f64
  %count_reciprocal_normalized = arith.mulf %count_x1, %count_step1_error : f64
  %count_reciprocal = arith.mulf %count_reciprocal_normalized, %quarter : f64
  %mean = arith.mulf %sum, %count_reciprocal : f64

  // (count - 1) / 3 is in [1/3, 1]. Three Newton steps tighten this wider range.
  %one = arith.constant 1.0 : f64
  %third = arith.constant 0.3333333333333333 : f64
  %seed_a_denom = arith.constant 3.4285714285714284 : f64
  %seed_b_denom = arith.constant 2.5714285714285716 : f64
  %count_minus_one = arith.subf %count, %one : f64
  %denom_normalized = arith.mulf %count_minus_one, %third : f64
  %denom_seed_product = arith.mulf %seed_b_denom, %denom_normalized : f64
  %denom_x0 = arith.subf %seed_a_denom, %denom_seed_product : f64
  %denom_step0_product = arith.mulf %denom_normalized, %denom_x0 : f64
  %denom_step0_error = arith.subf %two, %denom_step0_product : f64
  %denom_x1 = arith.mulf %denom_x0, %denom_step0_error : f64
  %denom_step1_product = arith.mulf %denom_normalized, %denom_x1 : f64
  %denom_step1_error = arith.subf %two, %denom_step1_product : f64
  %denom_x2 = arith.mulf %denom_x1, %denom_step1_error : f64
  %denom_step2_product = arith.mulf %denom_normalized, %denom_x2 : f64
  %denom_step2_error = arith.subf %two, %denom_step2_product : f64
  %denom_reciprocal_normalized = arith.mulf %denom_x2, %denom_step2_error : f64
  %denom_reciprocal = arith.mulf %denom_reciprocal_normalized, %third : f64

  %sum_times_mean = arith.mulf %sum, %mean : f64
  %variance_numerator = arith.subf %sum_squares, %sum_times_mean : f64
  %sample_variance = arith.mulf %variance_numerator, %denom_reciprocal : f64
  return %mean, %sample_variance : f64, f64
}
"""
