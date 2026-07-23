"""Official HEIR Python API for fixed-width CKKS SUM and MEAN.

This module is application code, not a benchmark runner.  It uses HEIR's
documented ``compile(mlir_str=..., scheme="ckks")`` entry point and the
official OpenFHE client interface:

``setup -> encrypt_values -> eval -> decrypt_result``.

HEIR 2026.7.1 supports only one function result in this Python interface.
Consequently SUM and MEAN are separate compiled programs.  Each ``eval`` call
still returns a ciphertext that can remain encrypted until the caller chooses
the audit/client decryption boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import math
from typing import Any, Literal

from code.heir.kernels.fixed_count_statistics import (
    fixed_count_mean_mlir,
    fixed_count_sum_mlir,
    fixed_count_variance_mlir,
)


Operation = Literal["sum", "mean", "variance"]


def _load_official_heir_compile() -> Any:
    try:
        from heir import compile as heir_compile
    except ImportError as error:
        raise RuntimeError(
            "The official HEIR Python package is required. Install it in the "
            "active Python 3.12 environment with: "
            "python3 -m pip install 'heir_py[python,openfhe]==2026.7.1'"
        ) from error
    return heir_compile


def _pack(values: Sequence[float], *, width: int, valid_count: int) -> Any:
    materialized = [float(value) for value in values]
    if len(materialized) != valid_count:
        raise ValueError(
            f"this circuit was compiled for {valid_count} real values; "
            f"received {len(materialized)}"
        )
    if not all(math.isfinite(value) for value in materialized):
        raise ValueError("values must not contain NaN or infinity")

    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "NumPy is required by HEIR's tensor interface. Install the "
            "'heir_py[python,openfhe]' extra."
        ) from error

    array = np.asarray(materialized, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError("values must be a one-dimensional numeric sequence")

    packed = np.zeros(width, dtype=np.float64)
    packed[:valid_count] = array
    return packed


@dataclass
class OfficialCkksAggregate:
    """One reusable, compiled HEIR/OpenFHE aggregate program.

    Compile and setup are intentionally separate: an application normally
    performs both once, then reuses the live program for multiple same-shaped
    inputs.  ``eval`` returns the encrypted aggregate; decryption is explicit.
    """

    operation: Operation
    width: int
    valid_count: int
    debug: bool = False
    backend: Any | None = field(default=None, repr=False)
    _program: Any = field(init=False, repr=False)
    _is_setup: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        if self.width < 2:
            raise ValueError("width must be at least two")
        if not 2 <= self.valid_count <= self.width:
            raise ValueError("valid_count must be in [2, width]")

        if self.operation == "sum":
            source = fixed_count_sum_mlir(self.width, self.valid_count)
        elif self.operation == "mean":
            source = fixed_count_mean_mlir(self.width, self.valid_count)
        elif self.operation == "variance":
            source = fixed_count_variance_mlir(self.width, self.valid_count)
        else:
            raise ValueError(f"unsupported aggregate: {self.operation}")

        heir_compile = _load_official_heir_compile()
        compile_options = {
            "mlir_str": source,
            "scheme": "ckks",
            "debug": self.debug,
        }
        if self.backend is not None:
            compile_options["backend"] = self.backend
        self._program = heir_compile(**compile_options)

    @property
    def mlir(self) -> str:
        """Return the exact source compiled by official HEIR."""
        if self.operation == "sum":
            return fixed_count_sum_mlir(self.width, self.valid_count)
        if self.operation == "mean":
            return fixed_count_mean_mlir(self.width, self.valid_count)
        return fixed_count_variance_mlir(self.width, self.valid_count)

    def setup(self) -> None:
        """Create the OpenFHE context and key material once."""
        self._program.setup()
        self._is_setup = True

    def encrypt(self, values: Sequence[float]) -> Any:
        """Pack and encrypt one fixed-count column."""
        self._require_setup()
        packed = _pack(
            values,
            width=self.width,
            valid_count=self.valid_count,
        )
        # For raw MLIR, HEIR's function-info pass may canonicalize ``%values``
        # to a generated name such as ``arg0``. The official client interface
        # registers encryptors by that compiled name, so discover it instead
        # of assuming the source-level SSA name survives.
        compilation_result = self._program.compilation_result
        encryptors = compilation_result.arg_enc_funcs or {}
        if len(encryptors) != 1:
            raise RuntimeError(
                "expected exactly one encrypted HEIR input; compiled encryptor "
                f"names are {sorted(encryptors)}"
            )
        argument_name = next(iter(encryptors))
        encryptor = getattr(self._program, f"encrypt_{argument_name}")
        return encryptor(packed)

    def eval(self, encrypted_values: Any) -> Any:
        """Evaluate SUM or MEAN and return the encrypted scalar."""
        self._require_setup()
        return self._program.eval(encrypted_values)

    def decrypt(self, encrypted_result: Any) -> float:
        """Decrypt only at the client/audit boundary."""
        self._require_setup()
        return float(self._program.decrypt_result(encrypted_result))

    def run(self, values: Sequence[float]) -> float:
        """Convenience path for a final audited result."""
        encrypted_values = self.encrypt(values)
        encrypted_result = self.eval(encrypted_values)
        return self.decrypt(encrypted_result)

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")


def compile_sum(
    *,
    width: int,
    valid_count: int,
    debug: bool = False,
) -> OfficialCkksAggregate:
    """Compile a reusable official HEIR CKKS SUM program."""
    return OfficialCkksAggregate("sum", width, valid_count, debug)


def compile_mean(
    *,
    width: int,
    valid_count: int,
    debug: bool = False,
) -> OfficialCkksAggregate:
    """Compile a reusable official HEIR CKKS MEAN program."""
    return OfficialCkksAggregate("mean", width, valid_count, debug)


def compile_variance(
    *,
    width: int,
    valid_count: int,
    debug: bool = False,
) -> OfficialCkksAggregate:
    """Compile a reusable official HEIR CKKS sample-variance program."""
    return OfficialCkksAggregate("variance", width, valid_count, debug)


def compile_max(*, width: int, valid_count: int, debug: bool = False) -> None:
    """Reject an operation that the official HEIR Python CKKS API cannot express."""
    del width, valid_count, debug
    raise NotImplementedError(
        "Exact encrypted MAX is not a normal CKKS arithmetic circuit in the "
        "official HEIR Python frontend. It requires the separate OpenFHE "
        "CKKS-to-FHEW scheme-switching implementation."
    )
