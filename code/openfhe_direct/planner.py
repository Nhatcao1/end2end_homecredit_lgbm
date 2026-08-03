"""Small physical planner for the supported OpenFHE functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Operation = Literal[
    "input",
    "add",
    "subtract",
    "multiply",
    "square",
    "sum",
    "mean",
    "variance",
]


@dataclass(frozen=True)
class Expression:
    """One immutable node in the user-visible calculation DAG."""

    operation: Operation
    inputs: tuple["Expression", ...] = ()
    name: str | None = None


@dataclass(frozen=True)
class OperationRule:
    """Conservative requirements for one supported operation."""

    depth_cost: int
    needs_eval_mult_key: bool = False
    needs_eval_sum_key: bool = False


# These are wrapper policies, not universal OpenFHE constants. They remain
# deliberately conservative until a tested profile proves a smaller budget.
OPERATION_RULES: dict[Operation, OperationRule] = {
    "input": OperationRule(0),
    "add": OperationRule(0),
    "subtract": OperationRule(0),
    "multiply": OperationRule(1, needs_eval_mult_key=True),
    "square": OperationRule(1, needs_eval_mult_key=True),
    "sum": OperationRule(0, needs_eval_sum_key=True),
    "mean": OperationRule(1, needs_eval_sum_key=True),
    "variance": OperationRule(
        2,
        needs_eval_mult_key=True,
        needs_eval_sum_key=True,
    ),
}


@dataclass(frozen=True)
class PhysicalPlan:
    """Reviewable OpenFHE requirements derived before encryption."""

    operations: tuple[str, ...]
    required_depth: int
    context_depth: int
    needs_eval_mult_key: bool
    needs_eval_sum_key: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "scheme": "CKKS",
            "operations": list(self.operations),
            "required_depth": self.required_depth,
            "context_depth": self.context_depth,
            "needs_eval_mult_key": self.needs_eval_mult_key,
            "needs_eval_sum_key": self.needs_eval_sum_key,
            "automatic_scaling": True,
            "bootstrapping": False,
        }


def build_physical_plan(outputs: tuple[Expression, ...]) -> PhysicalPlan:
    """Calculate depth and key requirements from the complete DAG."""
    if not outputs:
        raise ValueError("workflow must declare at least one output")

    depth_cache: dict[Expression, int] = {}
    operations: set[str] = set()
    needs_mult = False
    needs_sum = False

    def visit(expression: Expression) -> int:
        nonlocal needs_mult, needs_sum
        if expression in depth_cache:
            return depth_cache[expression]
        try:
            rule = OPERATION_RULES[expression.operation]
        except KeyError as error:
            raise ValueError(
                f"unsupported HE operation: {expression.operation}"
            ) from error

        if expression.operation == "input":
            if expression.inputs or not expression.name:
                raise ValueError("input expressions require only a name")
            parent_depth = 0
        else:
            if not expression.inputs:
                raise ValueError(
                    f"{expression.operation} requires an input expression"
                )
            parent_depth = max(visit(parent) for parent in expression.inputs)
            operations.add(expression.operation)

        needs_mult = needs_mult or rule.needs_eval_mult_key
        needs_sum = needs_sum or rule.needs_eval_sum_key
        depth_cache[expression] = parent_depth + rule.depth_cost
        return depth_cache[expression]

    required_depth = max(visit(output) for output in outputs)
    return PhysicalPlan(
        operations=tuple(sorted(operations)),
        required_depth=required_depth,
        # The existing, tested CKKS session requires a minimum depth of two.
        context_depth=max(2, required_depth),
        needs_eval_mult_key=needs_mult,
        needs_eval_sum_key=needs_sum,
    )
