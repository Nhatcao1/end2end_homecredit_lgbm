"""Small, serializable contracts shared by reusable HEIR kernels."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class KernelContract:
    """Describe a reusable kernel without tying it to a business workload."""

    kernel_id: str
    name: str
    entry_function: str
    lane: str
    operation: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    multiplicative_depth: str
    expected_evaluation_keys: tuple[str, ...]
    ckks_parameters_status: str = "pending HEIR compilation"
    generated_ckks_status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["inputs"] = list(self.inputs)
        value["outputs"] = list(self.outputs)
        value["expected_evaluation_keys"] = list(self.expected_evaluation_keys)
        return value
