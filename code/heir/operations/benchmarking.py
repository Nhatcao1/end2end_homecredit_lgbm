"""Timing and accuracy boundaries for one encrypted expression benchmark."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ExpressionBenchmark:
    python_calculation_seconds: float
    encryption_seconds: float
    encrypted_evaluation_seconds: float
    decryption_seconds: float
    max_absolute_error: float
    max_relative_error: float

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["headline_timing"] = {
            "python_calculation_seconds": self.python_calculation_seconds,
            "encrypted_evaluation_seconds": self.encrypted_evaluation_seconds,
        }
        return value


def _timed(function: Callable[[], T]) -> tuple[T, float]:
    started = time.perf_counter()
    return function(), time.perf_counter() - started


def _errors(expected: Sequence[float], actual: Sequence[float]) -> tuple[float, float]:
    if len(expected) != len(actual):
        raise ValueError("decrypted and plaintext results have different lengths")
    absolute = [abs(float(a) - float(b)) for a, b in zip(expected, actual)]
    relative = [error / max(1.0, abs(float(reference))) for error, reference in zip(absolute, expected)]
    return max(absolute, default=0.0), max(relative, default=0.0)


def run_expression_benchmark(
    *,
    python_calculation: Callable[[], Sequence[float]],
    encrypt: Callable[[], T],
    encrypted_calculation: Callable[[T], T],
    decrypt: Callable[[T], Sequence[float]],
) -> ExpressionBenchmark:
    """Measure calculation separately from encryption/decryption and audit accuracy."""
    expected, python_seconds = _timed(python_calculation)
    ciphertext, encryption_seconds = _timed(encrypt)
    evaluated, evaluation_seconds = _timed(lambda: encrypted_calculation(ciphertext))
    actual, decryption_seconds = _timed(lambda: decrypt(evaluated))
    absolute, relative = _errors(expected, actual)
    if not all(math.isfinite(item) for item in (absolute, relative)):
        raise ValueError("accuracy metrics are not finite")
    return ExpressionBenchmark(
        python_calculation_seconds=python_seconds,
        encryption_seconds=encryption_seconds,
        encrypted_evaluation_seconds=evaluation_seconds,
        decryption_seconds=decryption_seconds,
        max_absolute_error=absolute,
        max_relative_error=relative,
    )
