"""Reviewable direct wrapper around verified OpenFHE-Python primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import importlib
import math
from typing import Any

from code.heir.python_api.official_openfhe_minmax import (
    EncryptedOpenFheColumn,
)
from code.heir.python_api.simple_session import CkksSession


@dataclass(frozen=True)
class EncryptedVector:
    """One encrypted packed vector owned by one session."""

    ciphertext: Any
    length: int
    session_id: int


@dataclass(frozen=True)
class EncryptedScalar:
    """One encrypted scalar owned by one session."""

    ciphertext: Any
    session_id: int


@dataclass(frozen=True)
class VarianceComponents:
    """Encrypted components needed for variance: Σx and Σx²."""

    sum_x: EncryptedScalar
    sum_x2: EncryptedScalar


@dataclass(frozen=True)
class CovarianceComponents:
    """Encrypted components needed for covariance: Σx, Σy, and Σxy."""

    sum_x: EncryptedScalar
    sum_y: EncryptedScalar
    sum_xy: EncryptedScalar


@dataclass(frozen=True)
class CorrelationComponents:
    """Encrypted components needed for correlation."""

    sum_x: EncryptedScalar
    sum_y: EncryptedScalar
    sum_x2: EncryptedScalar
    sum_y2: EncryptedScalar
    sum_xy: EncryptedScalar


class OpenFHEBgvSession:
    """One exact-integer BGV context for packed ciphertext arithmetic."""

    def __init__(
        self,
        *,
        slot_count: int,
        plaintext_modulus: int = 100_000_038_913,
        multiplicative_depth: int = 1,
        ring_dimension: int = 16_384,
        _openfhe_module: Any | None = None,
    ) -> None:
        if slot_count < 2:
            raise ValueError("slot_count must be at least two")
        if plaintext_modulus < 3:
            raise ValueError("plaintext_modulus must be at least three")
        if multiplicative_depth < 1:
            raise ValueError("multiplicative_depth must be positive")

        if _openfhe_module is not None:
            of = _openfhe_module
        else:
            try:
                of = importlib.import_module("openfhe")
            except ModuleNotFoundError as error:
                raise RuntimeError(
                    "the official OpenFHE Python binding is not installed in "
                    "this interpreter"
                ) from error

        parameters = of.CCParamsBGVRNS()
        parameters.SetPlaintextModulus(plaintext_modulus)
        parameters.SetMultiplicativeDepth(multiplicative_depth)
        parameters.SetBatchSize(slot_count)
        if ring_dimension:
            parameters.SetRingDim(ring_dimension)

        context = of.GenCryptoContext(parameters)
        for feature in (
            of.PKE,
            of.KEYSWITCH,
            of.LEVELEDSHE,
            of.ADVANCEDSHE,
        ):
            context.Enable(feature)

        required_methods = (
            "MakePackedPlaintext",
            "Encrypt",
            "Decrypt",
            "EvalAdd",
            "EvalSum",
            "EvalSumKeyGen",
        )
        missing = [
            name for name in required_methods if not hasattr(context, name)
        ]
        if missing:
            raise RuntimeError(
                f"installed OpenFHE Python is missing BGV methods: {missing}"
            )

        keys = context.KeyGen()
        context.EvalSumKeyGen(keys.secretKey)
        self.slot_count = slot_count
        self.plaintext_modulus = plaintext_modulus
        self.centered_capacity = plaintext_modulus // 2
        self._session_id = id(self)
        self._context = context
        self._public_key = keys.publicKey
        self._secret_key = keys.secretKey

    def encrypt(self, values: Sequence[int]) -> EncryptedVector:
        """Encode and encrypt one packed integer vector."""
        materialized = []
        for value in values:
            integer = int(value)
            if integer != value:
                raise ValueError("BGV values must be integers")
            if abs(integer) >= self.centered_capacity:
                raise ValueError(
                    "BGV value exceeds the centered plaintext-modulus range"
                )
            materialized.append(integer)
        if not 1 <= len(materialized) <= self.slot_count:
            raise ValueError(
                f"expected 1..{self.slot_count} values; "
                f"received {len(materialized)}"
            )
        plaintext = self._context.MakePackedPlaintext(materialized)
        return EncryptedVector(
            self._context.Encrypt(self._public_key, plaintext),
            len(materialized),
            self._session_id,
        )

    def add(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> EncryptedVector:
        """Exact packed ciphertext + ciphertext modulo plaintext modulus."""
        self._require_vector_pair(left, right)
        return EncryptedVector(
            self._context.EvalAdd(left.ciphertext, right.ciphertext),
            left.length,
            self._session_id,
        )

    def sum(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Reduce one encrypted integer vector to an encrypted total."""
        self._require_vector(encrypted)
        return EncryptedScalar(
            self._context.EvalSum(
                encrypted.ciphertext,
                encrypted.length,
            ),
            self._session_id,
        )

    def decrypt(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> list[int] | int:
        """Decrypt one final integer result for the audit boundary."""
        self._require_encrypted(encrypted)
        plaintext = self._context.Decrypt(
            self._secret_key,
            encrypted.ciphertext,
        )
        length = encrypted.length if isinstance(encrypted, EncryptedVector) else 1
        plaintext.SetLength(length)
        values = [
            int(value)
            for value in plaintext.GetPackedValue()[:length]
        ]
        return values if isinstance(encrypted, EncryptedVector) else values[0]

    def _require_encrypted(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> None:
        if not isinstance(encrypted, (EncryptedVector, EncryptedScalar)):
            raise TypeError("BGV session expects an encrypted value")
        if encrypted.session_id != self._session_id:
            raise ValueError("ciphertext belongs to another OpenFHE session")

    def _require_vector(self, encrypted: EncryptedVector) -> None:
        if not isinstance(encrypted, EncryptedVector):
            raise TypeError("BGV session expects an encrypted vector")
        self._require_encrypted(encrypted)

    def _require_vector_pair(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> None:
        self._require_vector(left)
        self._require_vector(right)
        if left.length != right.length:
            raise ValueError("encrypted vector lengths do not match")


class OpenFHECreditSession:
    """One local CKKS context exposing small ciphertext operations.

    The class calls the official OpenFHE Python binding directly. There is no
    HTTP client, gateway, HEIR compiler, generated MLIR, or CMake build.
    """

    def __init__(
        self,
        *,
        slot_count: int,
        multiplicative_depth: int = 4,
        scaling_mod_size: int = 50,
        first_mod_size: int = 60,
        ring_dimension: int = 0,
        input_scale: float = 1.0,
        enable_minmax: bool = False,
        _openfhe_module: Any | None = None,
        _switching_session: Any | None = None,
    ) -> None:
        if slot_count < 2:
            raise ValueError("slot_count must be at least two")
        if multiplicative_depth < 2:
            raise ValueError("multiplicative_depth must be at least two")

        self.slot_count = slot_count
        self._session_id = id(self)
        self._switching_session: Any | None = None
        if enable_minmax or _switching_session is not None:
            if slot_count & (slot_count - 1):
                raise ValueError(
                    "scheme-switching slot_count must be a power of two"
                )
            if input_scale <= 0 or not math.isfinite(input_scale):
                raise ValueError("input_scale must be finite and positive")
            selected_ring = ring_dimension or max(16384, 2 * slot_count)
            self._switching_session = (
                _switching_session
                if _switching_session is not None
                else CkksSession.create(
                    width=slot_count,
                    input_scale=input_scale,
                    ring_dimension=selected_ring,
                )
            )
            self._context = None
            self._public_key = None
            self._secret_key = None
            return

        if _openfhe_module is not None:
            of = _openfhe_module
        else:
            try:
                of = importlib.import_module("openfhe")
            except ModuleNotFoundError as error:
                raise RuntimeError(
                    "the official OpenFHE Python binding is not installed in "
                    "this interpreter; install a wrapper version matching the "
                    "local OpenFHE C++ runtime"
                ) from error
        parameters = of.CCParamsCKKSRNS()
        parameters.SetMultiplicativeDepth(multiplicative_depth)
        parameters.SetScalingModSize(scaling_mod_size)
        parameters.SetFirstModSize(first_mod_size)
        parameters.SetBatchSize(slot_count)
        if ring_dimension:
            parameters.SetRingDim(ring_dimension)

        context = of.GenCryptoContext(parameters)
        for feature in (
            of.PKE,
            of.KEYSWITCH,
            of.LEVELEDSHE,
            of.ADVANCEDSHE,
        ):
            context.Enable(feature)

        required_methods = (
            "MakeCKKSPackedPlaintext",
            "Encrypt",
            "Decrypt",
            "EvalAdd",
            "EvalSub",
            "EvalMult",
            "EvalSum",
            "EvalMultKeyGen",
            "EvalSumKeyGen",
        )
        missing = [
            name for name in required_methods if not hasattr(context, name)
        ]
        if missing:
            raise RuntimeError(
                f"installed OpenFHE Python is missing methods: {missing}"
            )

        keys = context.KeyGen()
        context.EvalMultKeyGen(keys.secretKey)
        context.EvalSumKeyGen(keys.secretKey)

        self._context = context
        self._public_key = keys.publicKey
        self._secret_key = keys.secretKey

    @property
    def scheme_switching_enabled(self) -> bool:
        """Whether this session supports encrypted MIN/MAX."""
        return self._switching_session is not None

    def encrypt(self, values: Sequence[float]) -> EncryptedVector:
        """Encode and encrypt one numeric vector."""
        materialized = [float(value) for value in values]
        if not 1 <= len(materialized) <= self.slot_count:
            raise ValueError(
                f"expected 1..{self.slot_count} values; "
                f"received {len(materialized)}"
            )
        if not all(math.isfinite(value) for value in materialized):
            raise ValueError("values must not contain NaN or infinity")
        if self._switching_session is not None:
            return self._vector(
                self._switching_session.encrypt_column(materialized),
                len(materialized),
            )
        plaintext = self._context.MakeCKKSPackedPlaintext(materialized)
        return EncryptedVector(
            ciphertext=self._context.Encrypt(
                self._public_key,
                plaintext,
            ),
            length=len(materialized),
            session_id=self._session_id,
        )

    def decrypt(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> list[float] | float:
        """Decrypt only at the application-controlled final boundary."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            if isinstance(encrypted, EncryptedVector):
                return list(
                    self._switching_session.decrypt_column(
                        encrypted.ciphertext
                    )
                )
            return float(
                self._switching_session.decrypt_scalar(
                    encrypted.ciphertext
                )
            )
        plaintext = self._context.Decrypt(
            self._secret_key,
            encrypted.ciphertext,
        )
        length = (
            encrypted.length
            if isinstance(encrypted, EncryptedVector)
            else 1
        )
        plaintext.SetLength(length)
        values = [
            float(value)
            for value in plaintext.GetRealPackedValue()[:length]
        ]
        return values if isinstance(encrypted, EncryptedVector) else values[0]

    def add(
        self,
        left: EncryptedVector | EncryptedScalar,
        right: EncryptedVector | EncryptedScalar,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext + ciphertext via OpenFHE ``EvalAdd``."""
        self._require_binary(left, right)
        if self._switching_session is not None:
            return self._switching_binary("add", left, right)
        return self._same_shape(
            left,
            self._context.EvalAdd(left.ciphertext, right.ciphertext),
        )

    def subtract(
        self,
        left: EncryptedVector | EncryptedScalar,
        right: EncryptedVector | EncryptedScalar,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext - ciphertext via OpenFHE ``EvalSub``."""
        self._require_binary(left, right)
        if self._switching_session is not None:
            return self._switching_binary("subtract", left, right)
        return self._same_shape(
            left,
            self._context.EvalSub(left.ciphertext, right.ciphertext),
        )

    def multiply(
        self,
        left: EncryptedVector | EncryptedScalar,
        right: EncryptedVector | EncryptedScalar,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext × ciphertext via OpenFHE ``EvalMult``."""
        self._require_binary(left, right)
        if self._switching_session is not None:
            return self._switching_binary("multiply", left, right)
        return self._same_shape(
            left,
            self._context.EvalMult(left.ciphertext, right.ciphertext),
        )

    def add_public_scalar(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
        scalar: float,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext + visible scalar via OpenFHE ``EvalAdd``."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            payload = self._switching_payload(encrypted)
            result = self._switching_context().EvalAdd(
                payload.ciphertext,
                float(scalar) / payload.scale,
            )
            return self._switching_result(
                encrypted,
                EncryptedOpenFheColumn(
                    result,
                    payload.scale,
                    payload.valid_count,
                ),
            )
        return self._same_shape(
            encrypted,
            self._context.EvalAdd(encrypted.ciphertext, float(scalar)),
        )

    def add_public_vector(
        self,
        encrypted: EncryptedVector,
        public_values: Sequence[float],
    ) -> EncryptedVector:
        """Ciphertext + visible vector via plaintext ``EvalAdd``."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            payload = self._switching_payload(encrypted)
            values = self._public_values(encrypted, public_values)
            padded = values + [values[0]] * (
                self.slot_count - len(values)
            )
            plaintext = self._switching_context().MakeCKKSPackedPlaintext(
                [value / payload.scale for value in padded]
            )
            result = self._switching_context().EvalAdd(
                payload.ciphertext,
                plaintext,
            )
            wrapped = self._switching_result(
                encrypted,
                EncryptedOpenFheColumn(
                    result,
                    payload.scale,
                    payload.valid_count,
                ),
            )
            assert isinstance(wrapped, EncryptedVector)
            return wrapped
        plaintext = self._public_plaintext(encrypted, public_values)
        return self._vector(
            self._context.EvalAdd(encrypted.ciphertext, plaintext),
            encrypted.length,
        )

    def multiply_public_scalar(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
        scalar: float,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext × visible scalar via OpenFHE ``EvalMult``."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            self._switching_session._ops._ensure_multiplication_key()
            payload = self._switching_payload(encrypted)
            result = self._switching_context().EvalMult(
                payload.ciphertext,
                float(scalar),
            )
            return self._switching_result(
                encrypted,
                EncryptedOpenFheColumn(
                    result,
                    payload.scale,
                    payload.valid_count,
                ),
            )
        return self._same_shape(
            encrypted,
            self._context.EvalMult(encrypted.ciphertext, float(scalar)),
        )

    def multiply_public_vector(
        self,
        encrypted: EncryptedVector,
        public_values: Sequence[float],
    ) -> EncryptedVector:
        """Ciphertext × visible vector via plaintext ``EvalMult``."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            self._switching_session._ops._ensure_multiplication_key()
            payload = self._switching_payload(encrypted)
            values = self._public_values(encrypted, public_values)
            padded = values + [values[0]] * (
                self.slot_count - len(values)
            )
            plaintext = self._switching_context().MakeCKKSPackedPlaintext(
                padded
            )
            result = self._switching_context().EvalMult(
                payload.ciphertext,
                plaintext,
            )
            wrapped = self._switching_result(
                encrypted,
                EncryptedOpenFheColumn(
                    result,
                    payload.scale,
                    payload.valid_count,
                ),
            )
            assert isinstance(wrapped, EncryptedVector)
            return wrapped
        plaintext = self._public_plaintext(encrypted, public_values)
        return self._vector(
            self._context.EvalMult(encrypted.ciphertext, plaintext),
            encrypted.length,
        )

    def square(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext square implemented as ``EvalMult(x, x)``."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            return self.multiply(encrypted, encrypted)
        return self._same_shape(
            encrypted,
            self._context.EvalMult(
                encrypted.ciphertext,
                encrypted.ciphertext,
            ),
        )

    def sum(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted packed sum via OpenFHE ``EvalSum``."""
        self._require_session(encrypted)
        if self._switching_session is not None:
            return self._scalar(
                self._switching_session.sum(encrypted.ciphertext)
            )
        return self._scalar(
            self._context.EvalSum(
                encrypted.ciphertext,
                self.slot_count,
            )
        )

    def mean(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted mean: ``EvalSum(x) × public (1/n)``."""
        if self._switching_session is not None:
            self._require_session(encrypted)
            return self._scalar(
                self._switching_session.mean(encrypted.ciphertext)
            )
        encrypted_sum = self.sum(encrypted)
        mean = self.multiply_public_scalar(
            encrypted_sum,
            1.0 / encrypted.length,
        )
        assert isinstance(mean, EncryptedScalar)
        return mean

    def variance_components(
        self,
        encrypted: EncryptedVector,
    ) -> VarianceComponents:
        """Return encrypted Σx and Σx² without intermediate decryption."""
        return VarianceComponents(
            sum_x=self.sum(encrypted),
            sum_x2=self.sum(self.square(encrypted)),
        )

    def variance(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted sample variance using public count ``n``.

        Formula: ``(Σx² - (Σx)² / n) / (n - 1)``.
        """
        if encrypted.length < 2:
            raise ValueError("sample variance requires at least two values")
        if self._switching_session is not None:
            self._require_session(encrypted)
            return self._scalar(
                self._switching_session.variance(encrypted.ciphertext)
            )
        components = self.variance_components(encrypted)
        sum_x_squared = self.square(components.sum_x)
        scaled_sum_x_squared = self.multiply_public_scalar(
            sum_x_squared,
            1.0 / encrypted.length,
        )
        numerator = self.subtract(
            components.sum_x2,
            scaled_sum_x_squared,
        )
        result = self.multiply_public_scalar(
            numerator,
            1.0 / (encrypted.length - 1),
        )
        assert isinstance(result, EncryptedScalar)
        return result

    def minimum(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted minimum through OpenFHE CKKS↔FHEW switching."""
        self._require_session(encrypted)
        if self._switching_session is None:
            raise RuntimeError(
                "minimum requires enable_minmax=True when creating session"
            )
        return self._scalar(
            self._switching_session.minimum(encrypted.ciphertext)
        )

    def maximum(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted maximum through OpenFHE CKKS↔FHEW switching."""
        self._require_session(encrypted)
        if self._switching_session is None:
            raise RuntimeError(
                "maximum requires enable_minmax=True when creating session"
            )
        return self._scalar(
            self._switching_session.maximum(encrypted.ciphertext)
        )

    def covariance_components(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> CovarianceComponents:
        """Return encrypted Σx, Σy, and Σxy."""
        self._require_binary(left, right)
        return CovarianceComponents(
            sum_x=self.sum(left),
            sum_y=self.sum(right),
            sum_xy=self.sum(self.multiply(left, right)),
        )

    def correlation_components(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> CorrelationComponents:
        """Return all encrypted sums required for correlation."""
        self._require_binary(left, right)
        return CorrelationComponents(
            sum_x=self.sum(left),
            sum_y=self.sum(right),
            sum_x2=self.sum(self.square(left)),
            sum_y2=self.sum(self.square(right)),
            sum_xy=self.sum(self.multiply(left, right)),
        )

    def weighted_sum(
        self,
        encrypted: EncryptedVector,
        public_weights: Sequence[float],
    ) -> EncryptedScalar:
        """Return encrypted Σ(x[i] × visible weight[i])."""
        return self.sum(
            self.multiply_public_vector(encrypted, public_weights)
        )

    def risk_score(
        self,
        encrypted: EncryptedVector,
        public_weights: Sequence[float],
        public_bias: float,
    ) -> EncryptedScalar:
        """Return encrypted linear score ``weighted_sum + public_bias``."""
        weighted = self.weighted_sum(encrypted, public_weights)
        score = self.add_public_scalar(weighted, public_bias)
        assert isinstance(score, EncryptedScalar)
        return score

    def _vector(self, ciphertext: Any, length: int) -> EncryptedVector:
        return EncryptedVector(ciphertext, length, self._session_id)

    def _scalar(self, ciphertext: Any) -> EncryptedScalar:
        return EncryptedScalar(ciphertext, self._session_id)

    def _same_shape(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
        ciphertext: Any,
    ) -> EncryptedVector | EncryptedScalar:
        if isinstance(encrypted, EncryptedVector):
            return self._vector(ciphertext, encrypted.length)
        return self._scalar(ciphertext)

    def _public_plaintext(
        self,
        encrypted: EncryptedVector,
        public_values: Sequence[float],
    ) -> Any:
        values = self._public_values(encrypted, public_values)
        return self._context.MakeCKKSPackedPlaintext(values)

    @staticmethod
    def _public_values(
        encrypted: EncryptedVector,
        public_values: Sequence[float],
    ) -> list[float]:
        values = [float(value) for value in public_values]
        if len(values) != encrypted.length:
            raise ValueError("public vector length does not match ciphertext")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("public values must not contain NaN or infinity")
        return values

    def _switching_context(self) -> Any:
        if self._switching_session is None:
            raise RuntimeError("scheme-switching session is not enabled")
        return self._switching_session._ops._engine._context

    @staticmethod
    def _switching_payload(
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> EncryptedOpenFheColumn:
        return encrypted.ciphertext._payload

    def _switching_result(
        self,
        template: EncryptedVector | EncryptedScalar,
        payload: EncryptedOpenFheColumn,
    ) -> EncryptedVector | EncryptedScalar:
        if self._switching_session is None:
            raise RuntimeError("scheme-switching session is not enabled")
        if isinstance(template, EncryptedVector):
            return self._vector(
                self._switching_session._column(payload),
                template.length,
            )
        return self._scalar(
            self._switching_session._scalar(
                payload,
                template.ciphertext.source_count,
            )
        )

    def _switching_binary(
        self,
        operation: str,
        left: EncryptedVector | EncryptedScalar,
        right: EncryptedVector | EncryptedScalar,
    ) -> EncryptedVector | EncryptedScalar:
        if self._switching_session is None:
            raise RuntimeError("scheme-switching session is not enabled")
        left_payload = self._switching_payload(left)
        right_payload = self._switching_payload(right)
        if left_payload.valid_count != right_payload.valid_count:
            raise ValueError("encrypted columns have different valid counts")
        if operation in {"add", "subtract"} and (
            left_payload.scale != right_payload.scale
        ):
            raise ValueError("encrypted columns have different scales")
        context = self._switching_context()
        if operation == "add":
            ciphertext = context.EvalAdd(
                left_payload.ciphertext,
                right_payload.ciphertext,
            )
            scale = left_payload.scale
        elif operation == "subtract":
            ciphertext = context.EvalSub(
                left_payload.ciphertext,
                right_payload.ciphertext,
            )
            scale = left_payload.scale
        elif operation == "multiply":
            self._switching_session._ops._ensure_multiplication_key()
            ciphertext = context.EvalMult(
                left_payload.ciphertext,
                right_payload.ciphertext,
            )
            scale = left_payload.scale * right_payload.scale
        else:
            raise ValueError(f"unsupported switching operation: {operation}")
        return self._switching_result(
            left,
            EncryptedOpenFheColumn(
                ciphertext,
                scale,
                left_payload.valid_count,
            ),
        )

    def _require_session(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> None:
        if encrypted.session_id != self._session_id:
            raise ValueError("ciphertext belongs to another OpenFHE session")

    def _require_binary(
        self,
        left: EncryptedVector | EncryptedScalar,
        right: EncryptedVector | EncryptedScalar,
    ) -> None:
        self._require_session(left)
        self._require_session(right)
        if type(left) is not type(right):
            raise ValueError("encrypted operand shapes do not match")
        if (
            isinstance(left, EncryptedVector)
            and isinstance(right, EncryptedVector)
            and left.length != right.length
        ):
            raise ValueError("encrypted vector lengths do not match")
