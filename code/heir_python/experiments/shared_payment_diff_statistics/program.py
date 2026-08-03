"""Experimental single-program HEIR PAYMENT_DIFF statistics tensor."""

from __future__ import annotations

from collections.abc import Sequence
import csv
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaymentDiffStatistics:
    total: float
    mean: float
    sample_variance: float


def _validate(width: int, valid_count: int) -> None:
    if width < 2 or not 2 <= valid_count <= width:
        raise ValueError("valid_count must be in [2, width]")


def _balanced_reduce(values: list[str], result: str, prefix: str) -> list[str]:
    lines: list[str] = []
    current = list(values)
    operation = 0
    while len(current) > 1:
        next_level: list[str] = []
        for index in range(0, len(current), 2):
            if index + 1 == len(current):
                next_level.append(current[index])
                continue
            name = result if len(current) == 2 else f"%{prefix}_{operation}"
            lines.append(
                f"  {name} = arith.addf {current[index]}, "
                f"{current[index + 1]} : f64"
            )
            next_level.append(name)
            operation += 1
        current = next_level
    return lines


def payment_diff_statistics_mlir(width: int, valid_count: int) -> str:
    """Return one encrypted tensor containing SUM, MEAN, sample VARIANCE."""
    _validate(width, valid_count)
    tensor = f"tensor<{width}xf64>"
    lines = [
        "func.func @payment_diff_shared_statistics(",
        f"    %installment: {tensor} {{secret.secret}},",
        f"    %payment: {tensor} {{secret.secret}}",
        ") -> tensor<3xf64> {",
        f"  %payment_diff = arith.subf %installment, %payment : {tensor}",
        f"  %squares = arith.mulf %payment_diff, %payment_diff : {tensor}",
    ]
    differences: list[str] = []
    squares: list[str] = []
    for index in range(valid_count):
        lines.extend(
            [
                f"  %index_{index} = arith.constant {index} : index",
                f"  %diff_{index} = tensor.extract "
                f"%payment_diff[%index_{index}] : {tensor}",
                f"  %square_{index} = tensor.extract "
                f"%squares[%index_{index}] : {tensor}",
            ]
        )
        differences.append(f"%diff_{index}")
        squares.append(f"%square_{index}")
    lines.extend(_balanced_reduce(differences, "%sum_result", "sum_tree"))
    lines.extend(
        _balanced_reduce(
            squares,
            "%square_sum_result",
            "square_sum_tree",
        )
    )
    inverse_count = 1.0 / valid_count
    inverse_sample_count = 1.0 / (valid_count - 1)
    lines.extend(
        [
            f"  %inverse_count = arith.constant {inverse_count:.17g} : f64",
            "  %mean_result = arith.mulf %sum_result, %inverse_count : f64",
            "  %sum_squared = arith.mulf %sum_result, %sum_result : f64",
            "  %mean_square_correction = arith.mulf "
            "%sum_squared, %inverse_count : f64",
            "  %centered_square_sum = arith.subf "
            "%square_sum_result, %mean_square_correction : f64",
            f"  %inverse_sample_count = arith.constant "
            f"{inverse_sample_count:.17g} : f64",
            "  %variance_result = arith.mulf "
            "%centered_square_sum, %inverse_sample_count : f64",
            "  %statistics = tensor.from_elements "
            "%sum_result, %mean_result, %variance_result : tensor<3xf64>",
            "  return %statistics : tensor<3xf64>",
            "}",
        ]
    )
    return "\n".join(lines) + "\n"


def _load_heir_compile() -> Any:
    try:
        from heir import compile as heir_compile
    except ImportError as error:
        raise RuntimeError(
            "activate .venv-heir and install heir_py[python,openfhe]"
        ) from error
    return heir_compile


def _pack(values: Sequence[float], width: int, scale: float) -> Any:
    if not values or len(values) > width:
        raise ValueError("values must fit inside the public width")
    materialized = [float(value) / scale for value in values]
    if not all(math.isfinite(value) for value in materialized):
        raise ValueError("values must be finite")
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("NumPy is required by HEIR-Python") from error
    packed = np.zeros(width, dtype=np.float64)
    packed[:len(materialized)] = materialized
    return packed


def read_prepared_payment_group(path: Path) -> tuple[list[float], list[float]]:
    """Read real mask-one parent rows from the committed credit fixture."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    installment: list[float] = []
    payment: list[float] = []
    for row in rows:
        if int(row["VALID_MASK"]) != 1:
            continue
        installment.append(float(row["AMT_INSTALMENT"]))
        payment.append(float(row["AMT_PAYMENT"]))
    if len(installment) < 2:
        raise ValueError("prepared group must contain at least two real rows")
    return installment, payment


class SharedPaymentDiffStatisticsProgram:
    """One HEIR program, context, parent encryption, evaluation, and output."""

    def __init__(
        self,
        *,
        width: int,
        valid_count: int,
        input_scale: float = 2048.0,
        debug: bool = False,
    ) -> None:
        _validate(width, valid_count)
        if input_scale <= 0 or not math.isfinite(input_scale):
            raise ValueError("input_scale must be finite and positive")
        self.width = width
        self.valid_count = valid_count
        self.input_scale = input_scale
        self.mlir = payment_diff_statistics_mlir(width, valid_count)
        self._program = _load_heir_compile()(
            mlir_str=self.mlir,
            scheme="ckks",
            debug=debug,
        )
        self._is_setup = False

    def setup(self) -> None:
        self._program.setup()
        self._is_setup = True

    def encrypt_parents(
        self,
        installment: Sequence[float],
        payment: Sequence[float],
    ) -> tuple[Any, Any]:
        self._require_setup()
        if len(installment) != self.valid_count or len(payment) != self.valid_count:
            raise ValueError(
                f"program expects {self.valid_count} aligned parent rows"
            )
        packed_installment = _pack(installment, self.width, self.input_scale)
        packed_payment = _pack(payment, self.width, self.input_scale)
        encryptors = self._program.compilation_result.arg_enc_funcs or {}
        if len(encryptors) != 2:
            raise RuntimeError(
                f"expected two encrypted HEIR inputs; got {sorted(encryptors)}"
            )
        names = list(encryptors)
        return (
            getattr(self._program, f"encrypt_{names[0]}")(
                packed_installment
            ),
            getattr(self._program, f"encrypt_{names[1]}")(packed_payment),
        )

    def eval(self, encrypted_parents: tuple[Any, Any]) -> Any:
        """Return one encrypted tensor; do not decrypt intermediate values."""
        self._require_setup()
        return self._program.eval(*encrypted_parents)

    def decrypt(self, encrypted_statistics: Any) -> PaymentDiffStatistics:
        """Decrypt only the final [SUM, MEAN, VARIANCE] audit tensor."""
        self._require_setup()
        decoded = [
            float(value)
            for value in self._program.decrypt_result(encrypted_statistics)
        ]
        if len(decoded) != 3:
            raise RuntimeError("expected three decoded statistics")
        return PaymentDiffStatistics(
            total=decoded[0] * self.input_scale,
            mean=decoded[1] * self.input_scale,
            sample_variance=decoded[2] * self.input_scale * self.input_scale,
        )

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")
