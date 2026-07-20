"""Explicit capability contracts for expressions evaluated after encryption."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class OperationContract:
    """Describe one generic column operation before a workload uses it."""

    operation_id: str
    python_shape: str
    execution_route: str
    status: str
    accuracy_kind: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CONTRACTS: tuple[OperationContract, ...] = (
    OperationContract(
        "add", "left + right", "exact_ckks", "implemented", "CKKS tolerance",
        "Element-wise ciphertext addition.",
    ),
    OperationContract(
        "subtract", "left - right", "exact_ckks", "implemented", "CKKS tolerance",
        "Element-wise ciphertext subtraction; use for DPD/DBD-style expressions.",
    ),
    OperationContract(
        "multiply", "left * right", "exact_ckks", "implemented", "CKKS tolerance",
        "Element-wise ciphertext multiplication followed by compiler-managed rescaling.",
    ),
    OperationContract(
        "count_sum_squares", "count(x), sum(x), sum(x * x)", "exact_ckks", "implemented", "CKKS tolerance",
        "Masked reductions return encrypted sufficient statistics.",
    ),
    OperationContract(
        "divide", "left / right", "approximate_ckks", "planned", "declared approximation error",
        "Requires an encrypted reciprocal polynomial and a non-zero bounded denominator domain.",
    ),
    OperationContract(
        "mean", "sum / count", "approximate_ckks", "planned", "declared approximation error",
        "Build only on the encrypted reciprocal/count contract; no client-side finalization.",
    ),
    OperationContract(
        "variance", "sum_squares / count - mean * mean", "approximate_ckks", "planned", "declared approximation error",
        "Build only after encrypted mean is validated.",
    ),
    OperationContract(
        "threshold", "value > public_threshold", "approximate_ckks", "planned", "threshold error band",
        "CKKS polynomial sign approximation; a scheme-switch route is a separate benchmark.",
    ),
    OperationContract(
        "min_max", "min(values), max(values)", "ckks_fhew_switch", "planned", "comparison correctness",
        "Requires an OpenFHE CKKS-to-FHEW switching wrapper; not emitted by the current HEIR pipeline.",
    ),
)


def operation_contracts() -> tuple[OperationContract, ...]:
    return CONTRACTS


def operation_contract(operation_id: str) -> OperationContract:
    normalized = operation_id.strip().lower()
    for contract in CONTRACTS:
        if contract.operation_id == normalized:
            return contract
    raise ValueError(f"unknown encrypted operation: {operation_id}")


def require_implemented(operation_id: str) -> OperationContract:
    contract = operation_contract(operation_id)
    if contract.status != "implemented":
        raise NotImplementedError(
            f"{contract.operation_id} is {contract.status} via "
            f"{contract.execution_route}: {contract.notes}"
        )
    return contract
