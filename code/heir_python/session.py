"""Session-style CKKS aggregates using official HEIR-Python compilation.

This module deliberately does not import OpenFHE directly. HEIR compiles each
aggregate for its OpenFHE backend and owns that program's context and keys.

Current HEIR-Python exposes SUM, MEAN, and VARIANCE as separate compiled
programs. Their ciphertexts are not interchangeable. ``encrypt()`` therefore
creates one encrypted branch per program and keeps that fact visible.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from typing import Any, Literal

from .aggregates import (
    compile_mean,
    compile_sum,
    compile_variance,
)


Aggregate = Literal["sum", "mean", "variance"]
ProgramFactory = Callable[..., Any]


@dataclass(frozen=True)
class EncryptedColumn:
    """Encrypted branches of one logical column for one HEIR session."""

    sum_branch: Any
    mean_branch: Any
    variance_branch: Any
    length: int
    session_id: int


@dataclass(frozen=True)
class EncryptedScalar:
    """One encrypted aggregate and the program that owns it."""

    ciphertext: Any
    operation: Aggregate
    session_id: int


class HeirCkksSession:
    """Compile and reuse official HEIR CKKS aggregate programs.

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
    def uses_one_ciphertext_for_all_aggregates(self) -> bool:
        """False because current HEIR programs own separate contexts."""
        return False

    def setup(self) -> None:
        """Compile HEIR programs and create their contexts/key sets."""
        if self._is_setup:
            return
        self._programs = {
            "sum": self._create_program("sum"),
            "mean": self._create_program("mean"),
            "variance": self._create_program("variance"),
        }
        for program in self._programs.values():
            program.setup()
        self._is_setup = True

    def encrypt(self, values: Sequence[float]) -> EncryptedColumn:
        """Encrypt one logical column into aggregate-owned branches."""
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
            variance_branch=self._programs["variance"].encrypt(materialized),
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

    def variance(self, encrypted: EncryptedColumn) -> EncryptedScalar:
        """Return encrypted sample variance from its HEIR-owned branch."""
        self._require_column(encrypted)
        return EncryptedScalar(
            ciphertext=self._programs["variance"].eval(
                encrypted.variance_branch
            ),
            operation="variance",
            session_id=self._session_id,
        )

    def minimum(self, encrypted: EncryptedColumn) -> EncryptedScalar:
        """Explain the required OpenFHE-Python scheme-switching route."""
        self._require_column(encrypted)
        raise NotImplementedError(
            "HEIR-Python CKKS does not currently expose the CKKS-to-FHEW "
            "minimum route; select OpenFHECreditSession before encryption"
        )

    def maximum(self, encrypted: EncryptedColumn) -> EncryptedScalar:
        """Explain the required OpenFHE-Python scheme-switching route."""
        self._require_column(encrypted)
        raise NotImplementedError(
            "HEIR-Python CKKS does not currently expose the CKKS-to-FHEW "
            "maximum route; select OpenFHECreditSession before encryption"
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
        encrypted_variance = self.variance(encrypted)
        return {
            "sum": self.decrypt(encrypted_sum),
            "mean": self.decrypt(encrypted_mean),
            "variance": self.decrypt(encrypted_variance),
        }

    def _create_program(self, operation: Aggregate) -> Any:
        if self._program_factory is not None:
            return self._program_factory(
                operation=operation,
                width=self.width,
                valid_count=self.valid_count,
                debug=self.debug,
            )
        compilers = {
            "sum": compile_sum,
            "mean": compile_mean,
            "variance": compile_variance,
        }
        compiler = compilers[operation]
        return compiler(
            width=self.width,
            valid_count=self.valid_count,
            debug=self.debug,
        )

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError(
                "call setup() before encrypt/aggregate/decrypt"
            )

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
