"""Official HEIR-Python compile wrappers owned by the new package."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import math
from typing import Any, Literal

from .kernels import (
    fixed_count_mean_mlir,
    fixed_count_sum_mlir,
    fixed_count_variance_mlir,
)


Operation = Literal["sum", "mean", "variance"]


def _load_heir_compile() -> Any:
    try:
        from heir import compile as heir_compile
    except ImportError as error:
        raise RuntimeError(
            "Install official HEIR-Python in the active Python 3.12 "
            "environment with: python3 -m pip install "
            "'heir_py[python,openfhe]==2026.7.1'"
        ) from error
    return heir_compile


def _pack(values: Sequence[float], *, width: int, valid_count: int) -> Any:
    materialized = [float(value) for value in values]
    if len(materialized) != valid_count:
        raise ValueError(
            f"program expects {valid_count} values; received {len(materialized)}"
        )
    if not all(math.isfinite(value) for value in materialized):
        raise ValueError("values must not contain NaN or infinity")
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "NumPy is required by the HEIR tensor interface"
        ) from error
    packed = np.zeros(width, dtype=np.float64)
    packed[:valid_count] = np.asarray(materialized, dtype=np.float64)
    return packed


@dataclass
class HeirCkksAggregateProgram:
    """One reusable single-output HEIR CKKS aggregate program."""

    operation: Operation
    width: int
    valid_count: int
    debug: bool = False
    backend: Any | None = field(default=None, repr=False)
    _program: Any = field(init=False, repr=False)
    _is_setup: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._source = self._mlir_source()
        options = {
            "mlir_str": self._source,
            "scheme": "ckks",
            "debug": self.debug,
        }
        if self.backend is not None:
            options["backend"] = self.backend
        self._program = _load_heir_compile()(**options)

    @property
    def mlir(self) -> str:
        return self._source

    def setup(self) -> None:
        self._program.setup()
        self._is_setup = True

    def encrypt(self, values: Sequence[float]) -> Any:
        self._require_setup()
        packed = _pack(
            values,
            width=self.width,
            valid_count=self.valid_count,
        )
        encryptors = self._program.compilation_result.arg_enc_funcs or {}
        if len(encryptors) != 1:
            raise RuntimeError(
                "expected one encrypted HEIR input; compiled names are "
                f"{sorted(encryptors)}"
            )
        argument_name = next(iter(encryptors))
        return getattr(self._program, f"encrypt_{argument_name}")(packed)

    def eval(self, encrypted_values: Any) -> Any:
        self._require_setup()
        return self._program.eval(encrypted_values)

    def decrypt(self, encrypted_result: Any) -> float:
        self._require_setup()
        return float(self._program.decrypt_result(encrypted_result))

    def _mlir_source(self) -> str:
        sources = {
            "sum": fixed_count_sum_mlir,
            "mean": fixed_count_mean_mlir,
            "variance": fixed_count_variance_mlir,
        }
        try:
            source = sources[self.operation]
        except KeyError as error:
            raise ValueError(
                f"unsupported HEIR aggregate: {self.operation}"
            ) from error
        return source(self.width, self.valid_count)

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")


def compile_sum(
    *, width: int, valid_count: int, debug: bool = False
) -> HeirCkksAggregateProgram:
    return HeirCkksAggregateProgram("sum", width, valid_count, debug)


def compile_mean(
    *, width: int, valid_count: int, debug: bool = False
) -> HeirCkksAggregateProgram:
    return HeirCkksAggregateProgram("mean", width, valid_count, debug)


def compile_variance(
    *, width: int, valid_count: int, debug: bool = False
) -> HeirCkksAggregateProgram:
    return HeirCkksAggregateProgram("variance", width, valid_count, debug)
