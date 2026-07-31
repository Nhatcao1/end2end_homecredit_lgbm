"""Reviewable direct wrapper around verified OpenFHE-Python primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import importlib
import math
from typing import Any


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
        _openfhe_module: Any | None = None,
    ) -> None:
        if slot_count < 2:
            raise ValueError("slot_count must be at least two")
        if multiplicative_depth < 2:
            raise ValueError("multiplicative_depth must be at least two")

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

        self.slot_count = slot_count
        self._context = context
        self._public_key = keys.publicKey
        self._secret_key = keys.secretKey
        self._session_id = id(self)

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
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> EncryptedVector:
        """Ciphertext + ciphertext via OpenFHE ``EvalAdd``."""
        self._require_binary(left, right)
        return self._vector(
            self._context.EvalAdd(left.ciphertext, right.ciphertext),
            left.length,
        )

    def subtract(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> EncryptedVector:
        """Ciphertext - ciphertext via OpenFHE ``EvalSub``."""
        self._require_binary(left, right)
        return self._vector(
            self._context.EvalSub(left.ciphertext, right.ciphertext),
            left.length,
        )

    def multiply(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> EncryptedVector:
        """Ciphertext × ciphertext via OpenFHE ``EvalMult``."""
        self._require_binary(left, right)
        return self._vector(
            self._context.EvalMult(left.ciphertext, right.ciphertext),
            left.length,
        )

    def add_public_scalar(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
        scalar: float,
    ) -> EncryptedVector | EncryptedScalar:
        """Ciphertext + visible scalar via OpenFHE ``EvalAdd``."""
        self._require_session(encrypted)
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
        plaintext = self._public_plaintext(encrypted, public_values)
        return self._vector(
            self._context.EvalMult(encrypted.ciphertext, plaintext),
            encrypted.length,
        )

    def square(self, encrypted: EncryptedVector) -> EncryptedVector:
        """Ciphertext square implemented as ``EvalMult(x, x)``."""
        self._require_session(encrypted)
        return self._vector(
            self._context.EvalMult(
                encrypted.ciphertext,
                encrypted.ciphertext,
            ),
            encrypted.length,
        )

    def sum(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted packed sum via OpenFHE ``EvalSum``."""
        self._require_session(encrypted)
        return self._scalar(
            self._context.EvalSum(
                encrypted.ciphertext,
                self.slot_count,
            )
        )

    def mean(self, encrypted: EncryptedVector) -> EncryptedScalar:
        """Encrypted mean: ``EvalSum(x) × public (1/n)``."""
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
        values = [float(value) for value in public_values]
        if len(values) != encrypted.length:
            raise ValueError("public vector length does not match ciphertext")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("public values must not contain NaN or infinity")
        return self._context.MakeCKKSPackedPlaintext(values)

    def _require_session(
        self,
        encrypted: EncryptedVector | EncryptedScalar,
    ) -> None:
        if encrypted.session_id != self._session_id:
            raise ValueError("ciphertext belongs to another OpenFHE session")

    def _require_binary(
        self,
        left: EncryptedVector,
        right: EncryptedVector,
    ) -> None:
        self._require_session(left)
        self._require_session(right)
        if left.length != right.length:
            raise ValueError("encrypted vector lengths do not match")
