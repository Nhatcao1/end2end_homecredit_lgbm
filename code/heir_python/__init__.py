"""Self-contained application package built on official HEIR-Python."""

from .aggregates import (
    HeirCkksAggregateProgram,
    compile_mean,
    compile_sum,
    compile_variance,
)

from .session import (
    EncryptedColumn,
    EncryptedScalar,
    HeirCkksSession,
)

__all__ = [
    "EncryptedColumn",
    "EncryptedScalar",
    "HeirCkksAggregateProgram",
    "HeirCkksSession",
    "compile_mean",
    "compile_sum",
    "compile_variance",
]
