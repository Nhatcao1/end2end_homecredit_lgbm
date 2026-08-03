"""Session-style SUM and MEAN API using official HEIR-Python compilation.

This module deliberately does not import OpenFHE directly. HEIR compiles each
aggregate for its OpenFHE backend and owns that program's context and keys.

Current HEIR-Python exposes SUM and MEAN as separate compiled programs. Their
ciphertexts are not interchangeable. ``encrypt()`` therefore creates one
encrypted branch per program and keeps that fact visible in the data type.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from typing import Any, Literal

from code.heir.python_api.official_ckks_aggregates import (
    compile_mean,
    compile_sum,
)


Aggregate = Literal["sum", "mean"]
ProgramFactory = Callable[..., Any]


@dataclass(frozen=True)
class EncryptedColumn:
    """Encrypted branches of one logical column for one HEIR session."""

    sum_branch: Any
    mean_branch: Any
    length: int
    session_id: int


@dataclass(frozen=True)
class EncryptedScalar:
    """One encrypted aggregate and the program that owns it."""

    ciphertext: Any
    operation: Aggregate
    session_id: int


class HeirCkksSession:
    """Compile and reuse official HEIR CKKS SUM and MEAN programs.

    ``width`` is the public packed width and ``valid_count`` is the public
    number of real values. These are compile-time circuit properties.
    """

    def __init__(
        self,
        *,
        width: int,
        valid_count: int,
        debug: bool = False,
        _program_factory: ProgramFactory | None = None,
    ) -> None:
        if width < 2:
            raise ValueError("width must be at least two")
        if not 2 <= valid_count <= width:
            raise ValueError("valid_count must be in [2, width]")
        self.width = width
        self.valid_count = valid_count
        self.debug = debug
        self._session_id = id(self)
        self._program_factory = _program_factory
        self._programs: dict[Aggregate, Any] = {}
        self._is_setup = False

    @property
    def uses_one_ciphertext_for_both_aggregates(self) -> bool:
        """False because current HEIR programs own separate contexts."""
        return False

    def setup(self) -> None:
        """Compile both HEIR programs and create both contexts/key sets."""
        if self._is_setup:
            return
        self._programs = {
            "sum": self._create_program("sum"),
            "mean": self._create_program("mean"),
        }
        for program in self._programs.values():
            program.setup()
        self._is_setup = True

    def encrypt(self, values: Sequence[float]) -> EncryptedColumn:
        """Encrypt one logical column into its SUM and MEAN branches."""
        self._require_setup()
        materialized = [float(value) for value in values]
        if len(materialized) != self.valid_count:
            raise ValueError(
                f"session expects {self.valid_count} values; "
                f"received {len(materialized)}"
            )
        if not all(math.isfinite(value) for value in materialized):
            raise ValueError("values must not contain NaN or infinity")
        return EncryptedColumn(
            sum_branch=self._programs["sum"].encrypt(materialized),
            mean_branch=self._programs["mean"].encrypt(materialized),
            length=len(materialized),
            session_id=self._session_id,
        )

    def sum(self, encrypted: EncryptedColumn) -> EncryptedScalar:
        """Return an encrypted SUM from the SUM-owned branch."""
        self._require_column(encrypted)
        return EncryptedScalar(
            ciphertext=self._programs["sum"].eval(encrypted.sum_branch),
            operation="sum",
            session_id=self._session_id,
        )

    def mean(self, encrypted: EncryptedColumn) -> EncryptedScalar:
        """Return an encrypted fixed-count MEAN from the MEAN-owned branch."""
        self._require_column(encrypted)
        return EncryptedScalar(
            ciphertext=self._programs["mean"].eval(encrypted.mean_branch),
            operation="mean",
            session_id=self._session_id,
        )

    def decrypt(self, encrypted: EncryptedScalar) -> float:
        """Decrypt one final scalar with its owning HEIR program."""
        self._require_scalar(encrypted)
        return float(
            self._programs[encrypted.operation].decrypt(
                encrypted.ciphertext
            )
        )

    def run(self, values: Sequence[float]) -> dict[str, float]:
        """Convenience audit path; normal applications may retain outputs."""
        encrypted = self.encrypt(values)
        encrypted_sum = self.sum(encrypted)
        encrypted_mean = self.mean(encrypted)
        return {
            "sum": self.decrypt(encrypted_sum),
            "mean": self.decrypt(encrypted_mean),
        }

    def _create_program(self, operation: Aggregate) -> Any:
        if self._program_factory is not None:
            return self._program_factory(
                operation=operation,
                width=self.width,
                valid_count=self.valid_count,
                debug=self.debug,
            )
        compiler = compile_sum if operation == "sum" else compile_mean
        return compiler(
            width=self.width,
            valid_count=self.valid_count,
            debug=self.debug,
        )

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/sum/mean/decrypt")

    def _require_column(self, encrypted: EncryptedColumn) -> None:
        self._require_setup()
        if not isinstance(encrypted, EncryptedColumn):
            raise TypeError("expected an HEIR encrypted column")
        if encrypted.session_id != self._session_id:
            raise ValueError("encrypted column belongs to another session")

    def _require_scalar(self, encrypted: EncryptedScalar) -> None:
        self._require_setup()
        if not isinstance(encrypted, EncryptedScalar):
            raise TypeError("expected an HEIR encrypted scalar")
        if encrypted.session_id != self._session_id:
            raise ValueError("encrypted scalar belongs to another session")
