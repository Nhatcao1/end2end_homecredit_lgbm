"""Generic encrypted-column arithmetic through the official HEIR Python API."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import math
from typing import Any, Literal

from code.heir.python_api.official_ckks_aggregates import (
    _load_official_heir_compile,
)


BinaryOperation = Literal["add", "subtract", "multiply"]
_ARITHMETIC = {
    "add": "arith.addf",
    "subtract": "arith.subf",
    "multiply": "arith.mulf",
}


def _validate_operation(operation: str) -> BinaryOperation:
    if operation not in _ARITHMETIC:
        raise ValueError(
            f"unsupported binary operation {operation!r}; "
            "choose add, subtract, or multiply"
        )
    return operation  # type: ignore[return-value]


def _pack(values: Sequence[float], width: int) -> Any:
    materialized = [float(value) for value in values]
    if not 1 <= len(materialized) <= width:
        raise ValueError(f"column length must be in [1, {width}]")
    if not all(math.isfinite(value) for value in materialized):
        raise ValueError("column must not contain NaN or infinity")
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "NumPy is required by HEIR's tensor interface"
        ) from error
    result = np.zeros(width, dtype=np.float64)
    result[: len(materialized)] = materialized
    return result


def _balanced_reduce(names: list[str], result: str, prefix: str) -> list[str]:
    current = list(names)
    lines: list[str] = []
    operation = 0
    while len(current) > 1:
        next_level: list[str] = []
        for index in range(0, len(current), 2):
            if index + 1 == len(current):
                next_level.append(current[index])
                continue
            name = (
                result
                if len(current) == 2
                else f"%{prefix}_{operation}"
            )
            lines.append(
                f"  {name} = arith.addf {current[index]}, "
                f"{current[index + 1]} : f64"
            )
            next_level.append(name)
            operation += 1
        current = next_level
    return lines


def binary_column_mlir(width: int, operation: BinaryOperation) -> str:
    """Emit an element-wise encrypted DataFrame-column-style operation."""
    if width < 2:
        raise ValueError("width must be at least two")
    operation = _validate_operation(operation)
    tensor = f"tensor<{width}xf64>"
    return (
        f"func.func @encrypted_column_{operation}_{width}(\n"
        f"    %left: {tensor} {{secret.secret}},\n"
        f"    %right: {tensor} {{secret.secret}}\n"
        f") -> {tensor} {{\n"
        f"  %result = {_ARITHMETIC[operation]} %left, %right : {tensor}\n"
        f"  return %result : {tensor}\n"
        "}\n"
    )


def binary_column_statistics_mlir(
    width: int,
    operation: BinaryOperation,
) -> str:
    """Emit encrypted SUM/MEAN/sample-VAR over a generic binary column."""
    if width < 2:
        raise ValueError("width must be at least two")
    operation = _validate_operation(operation)
    tensor = f"tensor<{width}xf64>"
    lines = [
        f"func.func @encrypted_column_statistics_{operation}_{width}(",
        f"    %left: {tensor} {{secret.secret}},",
        f"    %right: {tensor} {{secret.secret}},",
        "    %inverse_count: f64,",
        "    %inverse_sample_count: f64",
        ") -> tensor<3xf64> {",
        f"  %derived = {_ARITHMETIC[operation]} "
        f"%left, %right : {tensor}",
        f"  %squares = arith.mulf %derived, %derived : {tensor}",
    ]
    derived: list[str] = []
    squares: list[str] = []
    for index in range(width):
        lines.extend(
            [
                f"  %index_{index} = arith.constant {index} : index",
                f"  %derived_{index} = tensor.extract "
                f"%derived[%index_{index}] : {tensor}",
                f"  %square_{index} = tensor.extract "
                f"%squares[%index_{index}] : {tensor}",
            ]
        )
        derived.append(f"%derived_{index}")
        squares.append(f"%square_{index}")
    lines.extend(_balanced_reduce(derived, "%sum_result", "sum_tree"))
    lines.extend(
        _balanced_reduce(
            squares,
            "%square_sum_result",
            "square_sum_tree",
        )
    )
    lines.extend(
        [
            "  %mean_result = arith.mulf %sum_result, "
            "%inverse_count : f64",
            "  %sum_squared = arith.mulf %sum_result, "
            "%sum_result : f64",
            "  %mean_square_correction = arith.mulf %sum_squared, "
            "%inverse_count : f64",
            "  %centered_square_sum = arith.subf %square_sum_result, "
            "%mean_square_correction : f64",
            "  %variance_result = arith.mulf %centered_square_sum, "
            "%inverse_sample_count : f64",
            "  %result = tensor.from_elements %sum_result, %mean_result, "
            "%variance_result : tensor<3xf64>",
            "  return %result : tensor<3xf64>",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class OfficialCkksBinaryColumn:
    """Reusable HEIR program equivalent to ``df[left] op df[right]``."""

    operation: BinaryOperation
    width: int
    input_scale: float = 1.0
    debug: bool = False
    backend: Any | None = field(default=None, repr=False)
    _program: Any = field(init=False, repr=False)
    _is_setup: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.operation = _validate_operation(self.operation)
        if self.input_scale <= 0 or not math.isfinite(self.input_scale):
            raise ValueError("input_scale must be finite and positive")
        options = {
            "mlir_str": binary_column_mlir(self.width, self.operation),
            "scheme": "ckks",
            "debug": self.debug,
        }
        if self.backend is not None:
            options["backend"] = self.backend
        self._program = _load_official_heir_compile()(**options)

    @property
    def mlir(self) -> str:
        return binary_column_mlir(self.width, self.operation)

    @property
    def output_scale(self) -> float:
        if self.operation == "multiply":
            return self.input_scale * self.input_scale
        return self.input_scale

    def setup(self) -> None:
        self._program.setup()
        self._is_setup = True

    def encrypt(
        self,
        left: Sequence[float],
        right: Sequence[float],
    ) -> tuple[Any, Any]:
        self._require_setup()
        if len(left) != len(right):
            raise ValueError("left and right columns must have equal length")
        packed_left = _pack(
            [float(value) / self.input_scale for value in left],
            self.width,
        )
        packed_right = _pack(
            [float(value) / self.input_scale for value in right],
            self.width,
        )
        encryptors = self._program.compilation_result.arg_enc_funcs or {}
        if len(encryptors) != 2:
            raise RuntimeError("expected two encrypted column inputs")
        names = list(encryptors)
        return (
            getattr(self._program, f"encrypt_{names[0]}")(packed_left),
            getattr(self._program, f"encrypt_{names[1]}")(packed_right),
        )

    def eval(self, encrypted_columns: tuple[Any, Any]) -> Any:
        self._require_setup()
        return self._program.eval(*encrypted_columns)

    def decrypt(
        self,
        encrypted_column: Any,
        *,
        valid_count: int,
    ) -> tuple[float, ...]:
        self._require_setup()
        if not 1 <= valid_count <= self.width:
            raise ValueError("valid_count must be in [1, width]")
        decoded = self._program.decrypt_result(encrypted_column)
        return tuple(
            float(value) * self.output_scale
            for value in decoded[:valid_count]
        )

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")


@dataclass
class OfficialCkksBinaryColumnStatistics:
    """Encrypted SUM/MEAN/VAR for any generic binary column operation."""

    operation: BinaryOperation
    width: int
    input_scale: float = 1.0
    debug: bool = False
    backend: Any | None = field(default=None, repr=False)
    _program: Any = field(init=False, repr=False)
    _is_setup: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self.operation = _validate_operation(self.operation)
        if self.input_scale <= 0 or not math.isfinite(self.input_scale):
            raise ValueError("input_scale must be finite and positive")
        options = {
            "mlir_str": binary_column_statistics_mlir(
                self.width,
                self.operation,
            ),
            "scheme": "ckks",
            "debug": self.debug,
        }
        if self.backend is not None:
            options["backend"] = self.backend
        self._program = _load_official_heir_compile()(**options)

    @property
    def mlir(self) -> str:
        return binary_column_statistics_mlir(self.width, self.operation)

    @property
    def derived_scale(self) -> float:
        if self.operation == "multiply":
            return self.input_scale * self.input_scale
        return self.input_scale

    def setup(self) -> None:
        self._program.setup()
        self._is_setup = True

    def encrypt(
        self,
        left: Sequence[float],
        right: Sequence[float],
    ) -> tuple[Any, Any]:
        self._require_setup()
        if len(left) != len(right) or not 2 <= len(left) <= self.width:
            raise ValueError(
                "columns must have equal length in [2, width]"
            )
        packed_left = _pack(
            [float(value) / self.input_scale for value in left],
            self.width,
        )
        packed_right = _pack(
            [float(value) / self.input_scale for value in right],
            self.width,
        )
        encryptors = self._program.compilation_result.arg_enc_funcs or {}
        if len(encryptors) != 2:
            raise RuntimeError("expected two encrypted column inputs")
        names = list(encryptors)
        return (
            getattr(self._program, f"encrypt_{names[0]}")(packed_left),
            getattr(self._program, f"encrypt_{names[1]}")(packed_right),
        )

    def eval(
        self,
        encrypted_columns: tuple[Any, Any],
        *,
        valid_count: int,
    ) -> Any:
        self._require_setup()
        if not 2 <= valid_count <= self.width:
            raise ValueError("valid_count must be in [2, width]")
        return self._program.eval(
            *encrypted_columns,
            1.0 / valid_count,
            1.0 / (valid_count - 1),
        )

    def decrypt(self, encrypted_statistics: Any) -> tuple[float, float, float]:
        self._require_setup()
        decoded = [float(value) for value in self._program.decrypt_result(
            encrypted_statistics
        )]
        if len(decoded) != 3:
            raise RuntimeError("expected encrypted [SUM, MEAN, VAR]")
        scale = self.derived_scale
        return (
            decoded[0] * scale,
            decoded[1] * scale,
            decoded[2] * scale * scale,
        )

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")
