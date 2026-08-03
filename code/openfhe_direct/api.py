"""Simple front end for planning and running encrypted calculations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .planner import Expression, PhysicalPlan, build_physical_plan
from .session import EncryptedScalar, EncryptedVector, OpenFHECreditSession


EncryptedValue = EncryptedVector | EncryptedScalar


class HEWorkflow:
    """Build a lazy calculation DAG before creating an HE context."""

    def __init__(self) -> None:
        self._inputs: dict[str, Expression] = {}
        self._outputs: dict[str, Expression] = {}

    def input(self, name: str) -> Expression:
        if not name or not name.strip():
            raise ValueError("input name must not be empty")
        if name in self._inputs:
            return self._inputs[name]
        expression = Expression("input", name=name)
        self._inputs[name] = expression
        return expression

    def add(self, left: Expression, right: Expression) -> Expression:
        return Expression("add", (left, right))

    def subtract(self, left: Expression, right: Expression) -> Expression:
        return Expression("subtract", (left, right))

    def multiply(self, left: Expression, right: Expression) -> Expression:
        return Expression("multiply", (left, right))

    def square(self, encrypted: Expression) -> Expression:
        return Expression("square", (encrypted,))

    def sum(self, encrypted: Expression) -> Expression:
        return Expression("sum", (encrypted,))

    def mean(self, encrypted: Expression) -> Expression:
        return Expression("mean", (encrypted,))

    def variance(self, encrypted: Expression) -> Expression:
        return Expression("variance", (encrypted,))

    def output(self, name: str, expression: Expression) -> None:
        if not name or not name.strip():
            raise ValueError("output name must not be empty")
        if name in self._outputs:
            raise ValueError(f"duplicate output name: {name}")
        self._outputs[name] = expression

    def compile(
        self,
        *,
        slot_count: int,
        _openfhe_module: Any | None = None,
    ) -> "CompiledHEWorkflow":
        """Plan the whole DAG, then create one compatible CKKS session."""
        physical_plan = build_physical_plan(tuple(self._outputs.values()))
        session = OpenFHECreditSession(
            slot_count=slot_count,
            multiplicative_depth=physical_plan.context_depth,
            _needs_eval_mult_key=physical_plan.needs_eval_mult_key,
            _needs_eval_sum_key=physical_plan.needs_eval_sum_key,
            _openfhe_module=_openfhe_module,
        )
        return CompiledHEWorkflow(
            inputs=dict(self._inputs),
            outputs=dict(self._outputs),
            physical_plan=physical_plan,
            session=session,
        )


class CompiledHEWorkflow:
    """Execute one planned workflow using ciphertext-only calculations."""

    def __init__(
        self,
        *,
        inputs: dict[str, Expression],
        outputs: dict[str, Expression],
        physical_plan: PhysicalPlan,
        session: OpenFHECreditSession,
    ) -> None:
        self.inputs = inputs
        self.outputs = outputs
        self.physical_plan = physical_plan
        self._session = session

    def encrypt_inputs(
        self,
        clear_inputs: Mapping[str, Sequence[float]],
    ) -> dict[str, EncryptedVector]:
        missing = set(self.inputs) - set(clear_inputs)
        extra = set(clear_inputs) - set(self.inputs)
        if missing or extra:
            raise ValueError(
                f"input names do not match; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
        return {
            name: self._session.encrypt(clear_inputs[name])
            for name in self.inputs
        }

    def evaluate(
        self,
        encrypted_inputs: Mapping[str, EncryptedVector],
    ) -> dict[str, EncryptedValue]:
        """Evaluate the immutable DAG without intermediate decryption."""
        cache: dict[Expression, EncryptedValue] = {}

        def evaluate_one(expression: Expression) -> EncryptedValue:
            if expression in cache:
                return cache[expression]
            if expression.operation == "input":
                if expression.name not in encrypted_inputs:
                    raise ValueError(
                        f"missing encrypted input: {expression.name}"
                    )
                result: EncryptedValue = encrypted_inputs[expression.name]
            else:
                parents = tuple(evaluate_one(node) for node in expression.inputs)
                function = getattr(self, expression.operation)
                result = function(*parents)
            cache[expression] = result
            return result

        return {
            name: evaluate_one(expression)
            for name, expression in self.outputs.items()
        }

    # Public calculation functions accept only ciphertext operands. All HE
    # parameters and key decisions were fixed by HEWorkflow.compile().
    def add(self, left: EncryptedValue, right: EncryptedValue) -> EncryptedValue:
        return self._session.add(left, right)

    def subtract(
        self,
        left: EncryptedValue,
        right: EncryptedValue,
    ) -> EncryptedValue:
        return self._session.subtract(left, right)

    def multiply(
        self,
        left: EncryptedValue,
        right: EncryptedValue,
    ) -> EncryptedValue:
        return self._session.multiply(left, right)

    def square(self, encrypted: EncryptedValue) -> EncryptedValue:
        return self._session.square(encrypted)

    def sum(self, encrypted: EncryptedVector) -> EncryptedScalar:
        return self._session.sum(encrypted)

    def mean(self, encrypted: EncryptedVector) -> EncryptedScalar:
        return self._session.mean(encrypted)

    def variance(self, encrypted: EncryptedVector) -> EncryptedScalar:
        return self._session.variance(encrypted)

    def decrypt(self, encrypted: EncryptedValue) -> list[float] | float:
        return self._session.decrypt(encrypted)
