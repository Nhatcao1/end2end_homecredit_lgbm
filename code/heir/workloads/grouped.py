"""Shared contracts for function-specific grouped HEIR benchmark workloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureSpec:
    """One source feature and the outputs retained from its pandas aggregation."""

    name: str
    operations: tuple[str, ...]
    transform: str = "identity"
    source_columns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["operations"] = list(self.operations)
        value["source_columns"] = list(self.source_columns)
        return value


@dataclass(frozen=True)
class TaskSpec:
    """Function-specific interpretation of one or more reusable kernels."""

    task_id: str
    slug: str
    function_name: str
    title: str
    input_file: str
    source_lines: str
    kind: str
    kernel_ids: tuple[str, ...]
    features: tuple[FeatureSpec, ...] = ()
    branch_column: str = ""
    branch_value: str = ""
    client_preparation: tuple[str, ...] = ()
    excluded_outputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("kernel_ids", "client_preparation", "excluded_outputs"):
            value[key] = list(value[key])
        value["features"] = [feature.to_dict() for feature in self.features]
        return value
