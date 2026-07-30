"""Canonical backend selection for Python-facing HE calculations.

Use HEIR whenever the calculation is expressible by the project's compiled
CKKS programs. Use the official OpenFHE Python wrapper only for operations
that require CKKS/FHEW scheme switching and are not exposed by the current
HEIR Python route.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


BackendName = Literal["heir", "openfhe-python"]


@dataclass(frozen=True)
class CalculationRoute:
    operation: str
    backend: BackendName
    reason: str


_ALIASES = {
    "ct+ct": "add",
    "ct-ct": "subtract",
    "ct*ct": "multiply",
    "ct×ct": "multiply",
    "var": "variance",
    "min": "minimum",
    "max": "maximum",
}

_ROUTES = {
    "add": CalculationRoute(
        "add",
        "heir",
        "native encrypted arithmetic",
    ),
    "subtract": CalculationRoute(
        "subtract",
        "heir",
        "native encrypted arithmetic",
    ),
    "multiply": CalculationRoute(
        "multiply",
        "heir",
        "native encrypted arithmetic",
    ),
    "sum": CalculationRoute(
        "sum",
        "heir",
        "explicit HEIR CKKS rotation/add reduction circuit",
    ),
    "count": CalculationRoute(
        "count",
        "heir",
        "HEIR SUM over an encrypted 1/0 validity mask",
    ),
    "mean": CalculationRoute(
        "mean",
        "heir",
        "HEIR SUM multiplied by public reciprocal count",
    ),
    "square_sum": CalculationRoute(
        "square_sum",
        "heir",
        "HEIR encrypted multiply followed by SUM",
    ),
    "variance": CalculationRoute(
        "variance",
        "heir",
        "explicit HEIR CKKS SUM/square-sum sample-variance circuit",
    ),
    "weighted_sum": CalculationRoute(
        "weighted_sum",
        "heir",
        "HEIR encrypted/plain multiplication followed by SUM",
    ),
    "dot_product": CalculationRoute(
        "dot_product",
        "heir",
        "HEIR encrypted multiplication followed by SUM",
    ),
    "polynomial": CalculationRoute(
        "polynomial",
        "heir",
        "HEIR arithmetic circuit",
    ),
    "minimum": CalculationRoute(
        "minimum",
        "openfhe-python",
        "requires OpenFHE CKKS-to-FHEW comparison/scheme switching",
    ),
    "maximum": CalculationRoute(
        "maximum",
        "openfhe-python",
        "requires OpenFHE CKKS-to-FHEW comparison/scheme switching",
    ),
    "comparison": CalculationRoute(
        "comparison",
        "openfhe-python",
        "requires OpenFHE CKKS-to-FHEW comparison/scheme switching",
    ),
}


def calculation_route(operation: str) -> CalculationRoute:
    """Return the required backend for one canonical calculation."""
    normalized = operation.strip().lower().replace(" ", "_")
    normalized = _ALIASES.get(normalized, normalized)
    try:
        return _ROUTES[normalized]
    except KeyError as error:
        raise ValueError(
            f"no approved HE backend route for operation {operation!r}"
        ) from error


def backend_manifest(*operations: str) -> dict[str, dict[str, str]]:
    """Return JSON-serializable routing records for a benchmark manifest."""
    return {
        route.operation: asdict(route)
        for route in (
            calculation_route(operation) for operation in operations
        )
    }


def require_backend(operation: str, backend: BackendName) -> None:
    """Fail if code attempts to use a backend contrary to the policy."""
    route = calculation_route(operation)
    if route.backend != backend:
        raise RuntimeError(
            f"{route.operation} must use {route.backend}, not {backend}: "
            f"{route.reason}"
        )
