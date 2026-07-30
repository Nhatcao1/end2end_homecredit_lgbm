"""Lightweight OpenFHE-Python adapter for primitive CKKS arithmetic.

This adapter deliberately enables no CKKS/FHEW scheme switching. It exists so
ADD, SUBTRACT, and MULTIPLY can be compared with their HEIR-compiled
equivalents without charging the primitive benchmark for MIN/MAX setup.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import math
from typing import Any, Literal


BinaryOperation = Literal["add", "subtract", "multiply"]


def _load_openfhe() -> Any:
    try:
        import openfhe
    except ImportError as error:
        raise RuntimeError(
            "OpenFHE Python is required for this comparison backend. Run "
            "./scripts/setup_heir_openfhe_python.sh"
        ) from error
    return openfhe


@dataclass
class OpenFhePythonBinaryColumn:
    """Reusable primitive OpenFHE-Python program matching the HEIR adapter."""

    operation: BinaryOperation
    width: int
    input_scale: float
    ring_dimension: int = 16384
    _openfhe: Any = field(init=False, default=None, repr=False)
    _context: Any = field(init=False, default=None, repr=False)
    _keys: Any = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        if self.operation not in {"add", "subtract", "multiply"}:
            raise ValueError(f"unsupported binary operation: {self.operation}")
        if self.width < 2:
            raise ValueError("width must be at least two")
        if self.ring_dimension < 2 * self.width:
            raise ValueError("ring dimension does not fit the packed width")
        if self.ring_dimension & (self.ring_dimension - 1):
            raise ValueError("ring dimension must be a power of two")
        if self.input_scale <= 0 or not math.isfinite(self.input_scale):
            raise ValueError("input_scale must be finite and positive")

    @property
    def output_scale(self) -> float:
        if self.operation == "multiply":
            return self.input_scale * self.input_scale
        return self.input_scale

    def setup(self) -> None:
        of = _load_openfhe()
        parameters = of.CCParamsCKKSRNS()
        parameters.SetMultiplicativeDepth(
            2 if self.operation == "multiply" else 1
        )
        parameters.SetFirstModSize(60)
        parameters.SetScalingModSize(50)
        parameters.SetScalingTechnique(of.FLEXIBLEAUTO)
        parameters.SetSecurityLevel(of.HEStd_128_classic)
        parameters.SetRingDim(self.ring_dimension)
        parameters.SetBatchSize(self.width)
        context = of.GenCryptoContext(parameters)
        context.Enable(of.PKE)
        context.Enable(of.KEYSWITCH)
        context.Enable(of.LEVELEDSHE)
        keys = context.KeyGen()
        if self.operation == "multiply":
            context.EvalMultKeyGen(keys.secretKey)
        self._openfhe = of
        self._context = context
        self._keys = keys

    def encrypt(
        self,
        left: Sequence[float],
        right: Sequence[float],
    ) -> tuple[Any, Any]:
        self._require_setup()
        if len(left) != len(right):
            raise ValueError("left and right columns must have equal length")
        if not 1 <= len(left) <= self.width:
            raise ValueError("column length must be in [1, width]")
        normalized: list[list[float]] = []
        for values in (left, right):
            materialized = [float(value) for value in values]
            if not all(math.isfinite(value) for value in materialized):
                raise ValueError("column must not contain NaN or infinity")
            encoded = [value / self.input_scale for value in materialized]
            if not all(-0.5 < value <= 0.5 for value in encoded):
                raise ValueError(
                    "input violates the normalized range; raise input_scale"
                )
            encoded.extend([0.0] * (self.width - len(encoded)))
            normalized.append(encoded)
        left_plain = self._context.MakeCKKSPackedPlaintext(normalized[0])
        right_plain = self._context.MakeCKKSPackedPlaintext(normalized[1])
        return (
            self._context.Encrypt(self._keys.publicKey, left_plain),
            self._context.Encrypt(self._keys.publicKey, right_plain),
        )

    def eval(self, encrypted_columns: tuple[Any, Any]) -> Any:
        self._require_setup()
        left, right = encrypted_columns
        if self.operation == "add":
            return self._context.EvalAdd(left, right)
        if self.operation == "subtract":
            return self._context.EvalSub(left, right)
        return self._context.EvalMult(left, right)

    def decrypt(
        self,
        encrypted_column: Any,
        *,
        valid_count: int,
    ) -> tuple[float, ...]:
        self._require_setup()
        if not 1 <= valid_count <= self.width:
            raise ValueError("valid_count must be in [1, width]")
        plaintext = self._context.Decrypt(
            self._keys.secretKey,
            encrypted_column,
        )
        plaintext.SetLength(valid_count)
        return tuple(
            float(value) * self.output_scale
            for value in plaintext.GetRealPackedValue()[:valid_count]
        )

    def _require_setup(self) -> None:
        if self._context is None or self._keys is None:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")
