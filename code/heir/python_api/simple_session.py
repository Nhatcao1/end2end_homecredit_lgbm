"""Minimal ciphertext-in/ciphertext-out API over one OpenFHE context.

This module is intentionally small.  It exposes runtime operations instead of
compiler configuration.  All returned objects remain encrypted until an
explicit client-side decrypt call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from code.heir.python_api.official_openfhe_minmax import (
    EncryptedOpenFheColumn,
    OfficialOpenFheColumnOps,
)


@dataclass(frozen=True)
class EncryptedColumn:
    """Opaque encrypted vector belonging to exactly one CKKS session."""

    _payload: EncryptedOpenFheColumn = field(repr=False)
    _session_id: str = field(repr=False)

    @property
    def valid_count(self) -> int:
        return self._payload.valid_count

    @property
    def scale(self) -> float:
        return self._payload.scale


@dataclass(frozen=True)
class EncryptedScalar:
    """Opaque one-value ciphertext returned by an encrypted reduction."""

    _payload: EncryptedOpenFheColumn = field(repr=False)
    _session_id: str = field(repr=False)
    source_count: int

    @property
    def scale(self) -> float:
        return self._payload.scale


class CkksSession:
    """Simple same-context arithmetic, statistics, and MIN/MAX API."""

    def __init__(
        self,
        *,
        width: int,
        input_scale: float,
        ring_dimension: int = 16384,
    ) -> None:
        self.width = width
        self.input_scale = input_scale
        self.ring_dimension = ring_dimension
        self._session_id = uuid4().hex
        self._ops = OfficialOpenFheColumnOps(
            width=width,
            input_scale=input_scale,
            ring_dimension=ring_dimension,
        )
        self._is_setup = False

    @classmethod
    def create(
        cls,
        *,
        width: int,
        input_scale: float,
        ring_dimension: int = 16384,
    ) -> "CkksSession":
        """Create one context and all CKKS↔FHEW switching material."""
        session = cls(
            width=width,
            input_scale=input_scale,
            ring_dimension=ring_dimension,
        )
        session.setup()
        return session

    def setup(self) -> None:
        if self._is_setup:
            return
        self._ops.setup()
        self._is_setup = True

    def encrypt_column(self, values: Sequence[float]) -> EncryptedColumn:
        """Encrypt one column; duplicate padding preserves exact MIN/MAX."""
        self._require_setup()
        return self._column(
            self._ops.encrypt(values, padding="duplicate")
        )

    def add(
        self,
        left: EncryptedColumn,
        right: EncryptedColumn,
    ) -> EncryptedColumn:
        return self._column(
            self._ops.add(self._unwrap(left), self._unwrap(right))
        )

    def subtract(
        self,
        left: EncryptedColumn,
        right: EncryptedColumn,
    ) -> EncryptedColumn:
        return self._column(
            self._ops.subtract(self._unwrap(left), self._unwrap(right))
        )

    def multiply(
        self,
        left: EncryptedColumn,
        right: EncryptedColumn,
    ) -> EncryptedColumn:
        return self._column(
            self._ops.multiply(self._unwrap(left), self._unwrap(right))
        )

    def sum(self, column: EncryptedColumn) -> EncryptedScalar:
        payload = self._unwrap(column)
        return self._scalar(self._ops.sum(payload), payload.valid_count)

    def mean(self, column: EncryptedColumn) -> EncryptedScalar:
        payload = self._unwrap(column)
        return self._scalar(self._ops.mean(payload), payload.valid_count)

    def variance(self, column: EncryptedColumn) -> EncryptedScalar:
        payload = self._unwrap(column)
        return self._scalar(
            self._ops.variance(payload),
            payload.valid_count,
        )

    def minimum(self, column: EncryptedColumn) -> EncryptedScalar:
        payload = self._unwrap(column)
        return self._scalar(
            self._ops.minimum(payload),
            payload.valid_count,
        )

    def maximum(self, column: EncryptedColumn) -> EncryptedScalar:
        payload = self._unwrap(column)
        return self._scalar(
            self._ops.maximum(payload),
            payload.valid_count,
        )

    def decrypt_column(
        self,
        column: EncryptedColumn,
    ) -> tuple[float, ...]:
        return self._ops.decrypt(self._unwrap(column))

    def decrypt_scalar(self, scalar: EncryptedScalar) -> float:
        self._validate_session(scalar._session_id)
        return self._ops.decrypt_scalar(scalar._payload)

    def _column(self, payload: EncryptedOpenFheColumn) -> EncryptedColumn:
        return EncryptedColumn(payload, self._session_id)

    def _scalar(
        self,
        payload: EncryptedOpenFheColumn,
        source_count: int,
    ) -> EncryptedScalar:
        return EncryptedScalar(payload, self._session_id, source_count)

    def _unwrap(self, column: EncryptedColumn) -> EncryptedOpenFheColumn:
        if not isinstance(column, EncryptedColumn):
            raise TypeError("operation requires an EncryptedColumn")
        self._validate_session(column._session_id)
        return column._payload

    def _validate_session(self, session_id: str) -> None:
        self._require_setup()
        if session_id != self._session_id:
            raise ValueError(
                "ciphertext belongs to a different CKKS context/session"
            )

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() or use CkksSession.create()")
