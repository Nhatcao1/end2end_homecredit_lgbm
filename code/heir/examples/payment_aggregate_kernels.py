"""HEIR graphs that keep payment features encrypted through aggregation."""

from __future__ import annotations


def _float(value: float) -> str:
    literal = repr(float(value))
    return literal if "." in literal or "e" in literal.lower() else literal + ".0"


def payment_perc_aggregate_mlir(
    vector_size: int, group_count: int, amount_scale: float = 1000.0
) -> str:
    """Return encrypted PAYMENT_PERC rows plus count/sum/mean/sample-var."""
    if vector_size <= 0 or group_count <= 1 or group_count > vector_size:
        raise ValueError("invalid vector size or public group count")
    tensor = f"tensor<{vector_size}xf64>"
    return f'''func.func @payment_perc_aggregate(
    %payment: {tensor} {{secret.secret}},
    %installment: {tensor} {{secret.secret}},
    %mask: {tensor} {{secret.secret}}
) -> ({tensor}, f64, f64, f64, f64) {{
  %zero = arith.constant 0.0 : f64
  %zero_tensor = arith.constant dense<0.0> : {tensor}
  %inv_scale = arith.constant {_float(1.0 / amount_scale)} : f64
  %seed_a = arith.constant 2.8235294117647058 : f64
  %seed_b = arith.constant 1.8823529411764706 : f64
  %two = arith.constant 2.0 : f64
  %feature, %count, %sum, %sum_squares = affine.for %i = 0 to {vector_size}
      iter_args(%out = %zero_tensor, %n = %zero, %total = %zero, %squares = %zero)
      -> ({tensor}, f64, f64, f64) {{
    %paid = tensor.extract %payment[%i] : {tensor}
    %due_raw = tensor.extract %installment[%i] : {tensor}
    %weight = tensor.extract %mask[%i] : {tensor}
    %due = arith.mulf %due_raw, %inv_scale : f64
    %seed_product = arith.mulf %seed_b, %due : f64
    %x0 = arith.subf %seed_a, %seed_product : f64
    %product0 = arith.mulf %due, %x0 : f64
    %error0 = arith.subf %two, %product0 : f64
    %x1 = arith.mulf %x0, %error0 : f64
    %product1 = arith.mulf %due, %x1 : f64
    %error1 = arith.subf %two, %product1 : f64
    %inverse_normalized = arith.mulf %x1, %error1 : f64
    %unscaled_ratio = arith.mulf %paid, %inverse_normalized : f64
    %ratio = arith.mulf %unscaled_ratio, %inv_scale : f64
    %masked_ratio = arith.mulf %ratio, %weight : f64
    %square = arith.mulf %masked_ratio, %masked_ratio : f64
    %next_out = tensor.insert %masked_ratio into %out[%i] : {tensor}
    %next_count = arith.addf %n, %weight : f64
    %next_sum = arith.addf %total, %masked_ratio : f64
    %next_squares = arith.addf %squares, %square : f64
    affine.yield %next_out, %next_count, %next_sum, %next_squares : {tensor}, f64, f64, f64
  }}
  %inv_count = arith.constant {_float(1.0 / group_count)} : f64
  %inv_count_minus_one = arith.constant {_float(1.0 / (group_count - 1))} : f64
  %mean = arith.mulf %sum, %inv_count : f64
  %sum_times_mean = arith.mulf %sum, %mean : f64
  %variance_numerator = arith.subf %sum_squares, %sum_times_mean : f64
  %variance = arith.mulf %variance_numerator, %inv_count_minus_one : f64
  return %feature, %count, %sum, %mean, %variance : {tensor}, f64, f64, f64, f64
}}
'''


def payment_diff_aggregate_mlir(vector_size: int, group_count: int) -> str:
    """Return encrypted PAYMENT_DIFF rows plus count/sum/mean/sample-var."""
    if vector_size <= 0 or group_count <= 1 or group_count > vector_size:
        raise ValueError("invalid vector size or public group count")
    tensor = f"tensor<{vector_size}xf64>"
    return f'''func.func @payment_diff_aggregate(
    %installment: {tensor} {{secret.secret}},
    %payment: {tensor} {{secret.secret}},
    %mask: {tensor} {{secret.secret}}
) -> ({tensor}, f64, f64, f64, f64) {{
  %zero = arith.constant 0.0 : f64
  %zero_tensor = arith.constant dense<0.0> : {tensor}
  %feature, %count, %sum, %sum_squares = affine.for %i = 0 to {vector_size}
      iter_args(%out = %zero_tensor, %n = %zero, %total = %zero, %squares = %zero)
      -> ({tensor}, f64, f64, f64) {{
    %due = tensor.extract %installment[%i] : {tensor}
    %paid = tensor.extract %payment[%i] : {tensor}
    %weight = tensor.extract %mask[%i] : {tensor}
    %difference = arith.subf %due, %paid : f64
    %masked_difference = arith.mulf %difference, %weight : f64
    %square = arith.mulf %masked_difference, %masked_difference : f64
    %next_out = tensor.insert %masked_difference into %out[%i] : {tensor}
    %next_count = arith.addf %n, %weight : f64
    %next_sum = arith.addf %total, %masked_difference : f64
    %next_squares = arith.addf %squares, %square : f64
    affine.yield %next_out, %next_count, %next_sum, %next_squares : {tensor}, f64, f64, f64
  }}
  %inv_count = arith.constant {_float(1.0 / group_count)} : f64
  %inv_count_minus_one = arith.constant {_float(1.0 / (group_count - 1))} : f64
  %mean = arith.mulf %sum, %inv_count : f64
  %sum_times_mean = arith.mulf %sum, %mean : f64
  %variance_numerator = arith.subf %sum_squares, %sum_times_mean : f64
  %variance = arith.mulf %variance_numerator, %inv_count_minus_one : f64
  return %feature, %count, %sum, %mean, %variance : {tensor}, f64, f64, f64, f64
}}
'''
