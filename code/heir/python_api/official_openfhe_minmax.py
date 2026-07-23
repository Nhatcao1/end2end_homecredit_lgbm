"""Exact encrypted MIN/MAX through OpenFHE's official Python wrapper.

MIN/MAX are not ordinary CKKS polynomial operations and are not emitted by
HEIR's Python frontend.  This module uses OpenFHE's official CKKS↔FHEW
scheme-switching API directly, while keeping the same explicit application
lifecycle used by the HEIR wrapper:

``setup -> encrypt -> eval -> decrypt``.

The resulting ciphertexts belong to this OpenFHE context. They cannot be
passed to a separately compiled HEIR Python program.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
from typing import Any


def _load_openfhe() -> Any:
    try:
        import openfhe
    except ImportError as error:
        raise RuntimeError(
            "The official OpenFHE Python wrapper is required for exact "
            "MIN/MAX. Install it with: "
            "python3 -m pip install 'openfhe==1.5.1.0'"
        ) from error
    return openfhe


def _next_power_of_two(value: int) -> int:
    if value < 2:
        return 2
    return 1 << (value - 1).bit_length()


def public_power_of_two_scale(values: Sequence[float]) -> float:
    """Return a public scale placing all encoded values in ``(-0.5, 0.5]``."""
    materialized = [float(value) for value in values]
    if not materialized:
        raise ValueError("values must not be empty")
    if not all(math.isfinite(value) for value in materialized):
        raise ValueError("values must not contain NaN or infinity")
    maximum = max(abs(value) for value in materialized)
    required = max(2.0, 2.0 * maximum + 1.0)
    return float(1 << math.ceil(math.log2(required)))


@dataclass(frozen=True)
class EncryptedMinMax:
    """Two encrypted CKKS outputs returned from the FHEW comparison trees."""

    minimum: Any
    maximum: Any


class OfficialOpenFheMinMax:
    """One reusable official OpenFHE CKKS↔FHEW MIN/MAX context."""

    def __init__(
        self,
        *,
        valid_count: int,
        input_scale: float,
        ring_dimension: int = 16384,
    ) -> None:
        if valid_count < 2:
            raise ValueError("valid_count must be at least two")
        if input_scale <= 0 or not math.isfinite(input_scale):
            raise ValueError("input_scale must be finite and positive")
        if ring_dimension < 4 or ring_dimension & (ring_dimension - 1):
            raise ValueError("ring_dimension must be a power of two")

        self.valid_count = valid_count
        self.candidate_count = _next_power_of_two(valid_count)
        if self.candidate_count > ring_dimension // 2:
            raise ValueError(
                "padded candidate count exceeds the selected ring capacity"
            )
        self.input_scale = float(input_scale)
        self.ring_dimension = ring_dimension
        self.multiplicative_depth = (
            9 + 3 + 1 + int(math.log2(self.candidate_count))
        )
        self._openfhe: Any = None
        self._context: Any = None
        self._keys: Any = None

    def setup(self) -> None:
        """Create CKKS/FHEW contexts, keys, and comparison precomputation."""
        of = _load_openfhe()
        parameters = of.CCParamsCKKSRNS()
        parameters.SetMultiplicativeDepth(self.multiplicative_depth)
        parameters.SetFirstModSize(60)
        parameters.SetScalingModSize(50)
        parameters.SetScalingTechnique(of.FLEXIBLEAUTO)
        parameters.SetSecurityLevel(of.HEStd_NotSet)
        parameters.SetRingDim(self.ring_dimension)
        parameters.SetBatchSize(self.candidate_count)
        parameters.SetSecretKeyDist(of.UNIFORM_TERNARY)
        parameters.SetKeySwitchTechnique(of.HYBRID)
        parameters.SetNumLargeDigits(3)

        context = of.GenCryptoContext(parameters)
        for feature in (
            of.PKE,
            of.KEYSWITCH,
            of.LEVELEDSHE,
            of.ADVANCEDSHE,
            of.SCHEMESWITCH,
        ):
            context.Enable(feature)
        if hasattr(of, "FHE"):
            context.Enable(of.FHE)

        keys = context.KeyGen()
        switching = of.SchSwchParams()
        switching.SetSecurityLevelCKKS(of.HEStd_NotSet)
        switching.SetSecurityLevelFHEW(of.TOY)
        switching.SetCtxtModSizeFHEWLargePrec(25)
        switching.SetNumSlotsCKKS(self.candidate_count)
        switching.SetNumValues(self.candidate_count)
        switching.SetComputeArgmin(True)
        lwe_secret_key = context.EvalSchemeSwitchingSetup(switching)
        context.EvalSchemeSwitchingKeyGen(keys, lwe_secret_key)

        # Unit-circle route: values are normalized before encryption so
        # candidate differences are in (-1, 1].
        context.EvalCompareSwitchPrecompute(1, 1, True)
        self._openfhe = of
        self._context = context
        self._keys = keys

    def encrypt(self, values: Sequence[float]) -> Any:
        """Normalize, duplicate-pad, encode, and encrypt one candidate vector."""
        self._require_setup()
        materialized = [float(value) for value in values]
        if len(materialized) != self.valid_count:
            raise ValueError(
                f"this context expects {self.valid_count} values; "
                f"received {len(materialized)}"
            )
        if not all(math.isfinite(value) for value in materialized):
            raise ValueError("values must not contain NaN or infinity")
        normalized = [value / self.input_scale for value in materialized]
        if not all(-0.5 < value <= 0.5 for value in normalized):
            raise ValueError(
                "input violates (-0.5, 0.5]; increase the public input scale"
            )
        normalized.extend(
            [normalized[0]] * (self.candidate_count - self.valid_count)
        )
        plaintext = self._context.MakeCKKSPackedPlaintext(normalized)
        return self._context.Encrypt(self._keys.publicKey, plaintext)

    def eval(self, encrypted_values: Any) -> EncryptedMinMax:
        """Return encrypted minimum and maximum; discard encrypted indices."""
        return EncryptedMinMax(
            minimum=self.eval_min(encrypted_values),
            maximum=self.eval_max(encrypted_values),
        )

    def eval_min(self, encrypted_values: Any) -> Any:
        """Return only the encrypted minimum value."""
        self._require_setup()
        minimum_and_index = self._context.EvalMinSchemeSwitching(
            encrypted_values,
            self._keys.publicKey,
            self.candidate_count,
            self.candidate_count,
        )
        return minimum_and_index[0]

    def eval_max(self, encrypted_values: Any) -> Any:
        """Return only the encrypted maximum value."""
        self._require_setup()
        maximum_and_index = self._context.EvalMaxSchemeSwitching(
            encrypted_values,
            self._keys.publicKey,
            self.candidate_count,
            self.candidate_count,
        )
        return maximum_and_index[0]

    def decrypt(self, encrypted: EncryptedMinMax) -> tuple[float, float]:
        """Decrypt both outputs only at the client/audit boundary."""
        self._require_setup()
        minimum = self._context.Decrypt(
            self._keys.secretKey,
            encrypted.minimum,
        )
        maximum = self._context.Decrypt(
            self._keys.secretKey,
            encrypted.maximum,
        )
        minimum.SetLength(1)
        maximum.SetLength(1)
        return (
            float(minimum.GetRealPackedValue()[0]) * self.input_scale,
            float(maximum.GetRealPackedValue()[0]) * self.input_scale,
        )

    def _require_setup(self) -> None:
        if self._context is None or self._keys is None:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")


class OfficialOpenFhePaymentDiffMax:
    """Exact MAX where PAYMENT_DIFF is derived after parent encryption."""

    def __init__(
        self,
        *,
        width: int,
        input_scale: float,
        ring_dimension: int = 16384,
    ) -> None:
        if width < 2:
            raise ValueError("width must be at least two")
        if width & (width - 1):
            raise ValueError("width must be a power of two")
        self.width = width
        self.input_scale = float(input_scale)
        self._engine = OfficialOpenFheMinMax(
            valid_count=width,
            input_scale=input_scale,
            ring_dimension=ring_dimension,
        )

    def setup(self) -> None:
        self._engine.setup()

    def encrypt(
        self,
        payment: Sequence[float],
        installment: Sequence[float],
    ) -> tuple[Any, Any]:
        """Duplicate-pad genuine parent pairs, then encrypt both columns."""
        self._engine._require_setup()
        paid = [float(value) for value in payment]
        due = [float(value) for value in installment]
        if len(paid) != len(due) or not 2 <= len(paid) <= self.width:
            raise ValueError("parent columns must have equal length in [2, width]")
        if not all(math.isfinite(value) for value in [*paid, *due]):
            raise ValueError("parent columns must not contain NaN or infinity")
        paid.extend([paid[0]] * (self.width - len(paid)))
        due.extend([due[0]] * (self.width - len(due)))
        normalized_paid = [value / self.input_scale for value in paid]
        normalized_due = [value / self.input_scale for value in due]
        if not all(
            -0.5 < value <= 0.5
            for value in [*normalized_paid, *normalized_due]
        ):
            raise ValueError(
                "parent input violates (-0.5, 0.5]; increase input_scale"
            )
        context = self._engine._context
        key = self._engine._keys.publicKey
        return (
            context.Encrypt(
                key,
                context.MakeCKKSPackedPlaintext(normalized_due),
            ),
            context.Encrypt(
                key,
                context.MakeCKKSPackedPlaintext(normalized_paid),
            ),
        )

    def eval(self, encrypted_parents: tuple[Any, Any]) -> Any:
        """Calculate PAYMENT_DIFF as CT-CT, then exact scheme-switched MAX."""
        self._engine._require_setup()
        installment, payment = encrypted_parents
        difference = self._engine._context.EvalSub(installment, payment)
        return self._engine.eval_max(difference)

    def decrypt(self, encrypted_maximum: Any) -> float:
        """Decrypt only the final maximum at the audit boundary."""
        self._engine._require_setup()
        plaintext = self._engine._context.Decrypt(
            self._engine._keys.secretKey,
            encrypted_maximum,
        )
        plaintext.SetLength(1)
        return (
            float(plaintext.GetRealPackedValue()[0]) * self.input_scale
        )
