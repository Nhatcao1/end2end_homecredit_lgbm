"""Emit review and benchmark-preparation artifacts for reusable HEIR kernels."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from code.heir.common import sha256_file, write_json
from code.heir.kernels.difference_moments import difference_moments_reference
from code.heir.kernels.dot_product import dot_product_reference
from code.heir.kernels.linear_score import linear_score_reference
from code.heir.kernels.moments import moments_reference
from code.heir.kernels.polynomial_score import polynomial_score_reference
from code.heir.kernels.registry import build_all_mlir, kernel_contracts


def _fit(values: list[float], vector_size: int) -> list[float]:
    """Repeat deterministic values to exactly fill a fixed benchmark tensor."""
    return [values[index % len(values)] for index in range(vector_size)]


def build_kernel_artifacts(
    output_dir: Path, vector_size: int = 8, polynomial_degree: int = 3
) -> dict[str, Any]:
    """Write MLIR, contracts, and plaintext oracles without claiming HE execution."""
    if vector_size <= 0:
        raise ValueError("vector_size must be positive")
    if polynomial_degree <= 0:
        raise ValueError("polynomial_degree must be positive")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")

    started = time.perf_counter()
    mlir_dir = output_dir / "mlir"
    mlir_dir.mkdir(parents=True)
    sources = build_all_mlir(vector_size, polynomial_degree)
    source_manifest: dict[str, dict[str, Any]] = {}
    for name, source in sources.items():
        path = mlir_dir / f"{name}.mlir"
        path.write_text(source, encoding="utf-8")
        source_manifest[name] = {
            "file": str(path.relative_to(output_dir)),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }

    left = _fit([1.0, -2.0, 3.0, 0.0], vector_size)
    right = _fit([0.5, -1.0, 0.25, 99.0], vector_size)
    mask = _fit([1.0, 1.0, 1.0, 0.0], vector_size)
    weights = _fit([0.5, -1.0, 0.25, 0.0], vector_size)
    coefficients = [1.0 / (index + 1) for index in range(polynomial_degree + 1)]
    bias = 0.25
    polynomial_input = 0.5

    oracle = {
        "inputs": {
            "left": left,
            "right": right,
            "mask": mask,
            "plaintext_weights": weights,
            "bias": bias,
            "polynomial_input": polynomial_input,
            "polynomial_coefficients_ascending": coefficients,
        },
        "expected_outputs": {
            "dot_product_ct_ct": dot_product_reference(left, right),
            "moments": list(moments_reference(left, mask)),
            "difference_moments": list(
                difference_moments_reference(left, right, mask)
            ),
            "linear_score_ct_pt": linear_score_reference(left, weights, bias),
            "polynomial_score": polynomial_score_reference(
                polynomial_input, coefficients
            ),
        },
    }
    write_json(output_dir / "plaintext_oracle.json", oracle)

    manifest: dict[str, Any] = {
        "artifact_kind": "reusable_heir_kernel_review",
        "execution_status": "mlir_and_plaintext_oracle_only",
        "vector_size": vector_size,
        "polynomial_degree": polynomial_degree,
        "kernels": [contract.to_dict() for contract in kernel_contracts()],
        "mlir_sources": source_manifest,
        "excluded": [
            "rule-based scoring",
            "threshold comparison",
            "tree traversal",
            "LightGBM training or inference",
        ],
        "preparation_seconds": time.perf_counter() - started,
    }
    write_json(output_dir / "kernel_manifest.json", manifest)
    return manifest
