"""Special non-source encrypted-feature/plaintext-weight linear score."""

from __future__ import annotations

from collections.abc import Sequence

from code.heir.kernels.contracts import KernelContract


CONTRACT = KernelContract(
    kernel_id="S01",
    name="linear_score_ct_pt",
    entry_function="linear_score_ct_pt",
    lane="special-non-source",
    operation="bias + sum(encrypted_features[i] * plaintext_weights[i])",
    inputs=(
        "features: encrypted vector",
        "weights: plaintext vector",
        "bias: plaintext scalar",
    ),
    outputs=("score: encrypted scalar",),
    multiplicative_depth="1 plaintext multiplication level",
    expected_evaluation_keys=("rotations for packed reduction",),
)


def linear_score_reference(
    features: Sequence[float], weights: Sequence[float], bias: float = 0.0
) -> float:
    if len(features) != len(weights):
        raise ValueError("features and weights must have equal length")
    if not features:
        raise ValueError("vectors must not be empty")
    return float(bias) + sum(
        float(feature) * float(weight)
        for feature, weight in zip(features, weights)
    )


def linear_score_mlir(feature_count: int) -> str:
    if feature_count <= 0:
        raise ValueError("feature_count must be positive")
    return f"""func.func @linear_score_ct_pt(
    %features: tensor<{feature_count}xf64> {{secret.secret}},
    %weights: tensor<{feature_count}xf64>,
    %bias: f64
) -> f64 {{
  %result = affine.for %i = 0 to {feature_count}
      iter_args(%score = %bias) -> (f64) {{
    %feature = tensor.extract %features[%i] : tensor<{feature_count}xf64>
    %weight = tensor.extract %weights[%i] : tensor<{feature_count}xf64>
    %term = arith.mulf %feature, %weight : f64
    %next = arith.addf %score, %term : f64
    affine.yield %next : f64
  }}
  return %result : f64
}}
"""
