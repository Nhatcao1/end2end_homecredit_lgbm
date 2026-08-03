"""Simple front end for planning and running encrypted calculations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .planner import Expression, PhysicalPlan, build_physical_plan
from .session import EncryptedScalar, EncryptedVector, OpenFHECreditSession


EncryptedValue = EncryptedVector | EncryptedScalar


@dataclass(frozen=True)
class EncryptedInputBundle:
    """Ciphertext parents sent from the data owner to the evaluator."""

    ciphertexts: dict[str, EncryptedVector]


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
        # Context/key setup and parent encryption belong to the data owner.
        client_session = OpenFHECreditSession(
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
            client_session=client_session,
        )


class HEEvaluator:
    """Calculation-only receiver of ciphertexts and evaluation components.

    This object has a compatible CKKS context and evaluation keys, but it has
    neither the public encryption key nor the client secret key.
    """

    def __init__(
        self,
        *,
        inputs: dict[str, Expression],
        outputs: dict[str, Expression],
        session: OpenFHECreditSession,
    ) -> None:
        self.inputs = inputs
        self.outputs = outputs
        self._session = session

    def evaluate(
        self,
        received: EncryptedInputBundle,
    ) -> dict[str, EncryptedValue]:
        """Evaluate received ciphertexts without plaintext or a secret key."""
        encrypted_inputs = received.ciphertexts
        missing = set(self.inputs) - set(encrypted_inputs)
        extra = set(encrypted_inputs) - set(self.inputs)
        if missing or extra:
            raise ValueError(
                f"ciphertext names do not match; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
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


class CompiledHEWorkflow:
    """Client cryptography plus a logically separate evaluator view."""

    def __init__(
        self,
        *,
        inputs: dict[str, Expression],
        outputs: dict[str, Expression],
        physical_plan: PhysicalPlan,
        client_session: OpenFHECreditSession,
    ) -> None:
        self.inputs = inputs
        self.outputs = outputs
        self.physical_plan = physical_plan
        self._client_session = client_session
        self.evaluator = HEEvaluator(
            inputs=inputs,
            outputs=outputs,
            session=client_session.evaluator_view(),
        )

    def encrypt_inputs(
        self,
        clear_inputs: Mapping[str, Sequence[float]],
    ) -> EncryptedInputBundle:
        """Client-only: encrypt parents for transport to the evaluator."""
        missing = set(self.inputs) - set(clear_inputs)
        extra = set(clear_inputs) - set(self.inputs)
        if missing or extra:
            raise ValueError(
                f"input names do not match; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
        return EncryptedInputBundle(
            {
                name: self._client_session.encrypt(clear_inputs[name])
                for name in self.inputs
            }
        )

    def decrypt(self, encrypted: EncryptedValue) -> list[float] | float:
        """Client-only: decrypt a final ciphertext returned by evaluator."""
        return self._client_session.decrypt(encrypted)
