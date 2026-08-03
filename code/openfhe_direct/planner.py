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
    """One immutable node in the user-visible calculation DAG.

    For example, ``subtract(installment, payment)`` becomes one Expression
    whose two inputs point at the parent-column expressions. No encryption or
    OpenFHE call happens while this graph is being constructed.
    """

    operation: Operation
    inputs: tuple["Expression", ...] = ()
    name: str | None = None


@dataclass(frozen=True)
class OperationRule:
    """Conservative HE cost assigned to one supported operation.

    ``depth_cost`` is the number of multiplication levels consumed along a
    sequential path. The two booleans tell session setup which expensive
    evaluation keys must be generated before encrypted evaluation starts.
    """

    depth_cost: int
    needs_eval_mult_key: bool = False
    needs_eval_sum_key: bool = False


# These are wrapper policies, not universal OpenFHE constants. They remain
# deliberately conservative until a tested profile proves a smaller budget.
#
# Add/subtract do not consume a multiplication level. SUM needs rotations but
# no multiplication level. MEAN is SUM followed by multiplication by public
# 1/n. Sample variance contains squares and therefore receives the largest
# depth allowance in this first supported operation set.
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
    """Reviewable OpenFHE requirements derived before encryption.

    ``required_depth`` describes the business DAG. ``context_depth`` is the
    actual depth passed to OpenFHE after applying backend safety minimums.
    """

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
    """Calculate depth and key requirements from the complete DAG.

    Planning is intentionally completed before any input is encrypted. That
    lets the backend reject unsupported work and create the right context and
    evaluation keys once, instead of discovering missing levels halfway
    through an encrypted calculation.
    """
    if not outputs:
        raise ValueError("workflow must declare at least one output")

    # A shared expression may feed several outputs. Cache its calculated depth
    # so the planner visits that DAG node once rather than treating it as
    # several copied calculations.
    depth_cache: dict[Expression, int] = {}
    operations: set[str] = set()
    needs_mult = False
    needs_sum = False

    def visit(expression: Expression) -> int:
        nonlocal needs_mult, needs_sum
        if expression in depth_cache:
            return depth_cache[expression]

        # Each logical operation has exactly one reviewed backend rule. An
        # unknown operation must fail planning rather than silently receive an
        # unsafe default depth or incomplete key set.
        try:
            rule = OPERATION_RULES[expression.operation]
        except KeyError as error:
            raise ValueError(
                f"unsupported HE operation: {expression.operation}"
            ) from error

        if expression.operation == "input":
            # Inputs begin at depth zero because no HE calculation has yet
            # been applied to the freshly encrypted parent column.
            if expression.inputs or not expression.name:
                raise ValueError("input expressions require only a name")
            parent_depth = 0
        else:
            if not expression.inputs:
                raise ValueError(
                    f"{expression.operation} requires an input expression"
                )
            # Parallel branches do not add their depths together. Only the
            # deepest parent controls how many levels this node needs, which
            # is why the planner follows the longest sequential path.
            parent_depth = max(visit(parent) for parent in expression.inputs)
            operations.add(expression.operation)

        # Key requirements are global to the context: if any node needs a key,
        # generate it once during session setup and reuse it for the whole DAG.
        needs_mult = needs_mult or rule.needs_eval_mult_key
        needs_sum = needs_sum or rule.needs_eval_sum_key
        depth_cache[expression] = parent_depth + rule.depth_cost
        return depth_cache[expression]

    # Multiple requested outputs share one context, so it must support the
    # deepest output path among all of them.
    required_depth = max(visit(output) for output in outputs)
    return PhysicalPlan(
        operations=tuple(sorted(operations)),
        required_depth=required_depth,
        # The existing, tested CKKS session requires a minimum depth of two.
        context_depth=max(2, required_depth),
        needs_eval_mult_key=needs_mult,
        needs_eval_sum_key=needs_sum,
    )
