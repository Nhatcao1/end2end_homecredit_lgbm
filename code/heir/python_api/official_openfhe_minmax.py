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
from typing import Any, Literal


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


@dataclass(frozen=True)
class EncryptedOpenFheColumn:
    """One encrypted column plus the public scale needed at decryption."""

    ciphertext: Any
    scale: float
    valid_count: int


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


class OfficialOpenFheColumnOps:
    """Generic encrypted-column arithmetic and exact MIN/MAX in one context."""

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
        self._multiplication_ready = False
        self._sum_ready = False

    def setup(self) -> None:
        self._engine.setup()

    def encrypt(
        self,
        values: Sequence[float],
        *,
        padding: Literal["zero", "duplicate"] = "zero",
    ) -> EncryptedOpenFheColumn:
        """Encrypt a numeric column using zero or genuine-value padding."""
        self._engine._require_setup()
        materialized = [float(value) for value in values]
        if not 2 <= len(materialized) <= self.width:
            raise ValueError("column length must be in [2, width]")
        if not all(math.isfinite(value) for value in materialized):
            raise ValueError("column must not contain NaN or infinity")
        if padding == "zero":
            pad_value = 0.0
        elif padding == "duplicate":
            pad_value = materialized[0]
        else:
            raise ValueError("padding must be zero or duplicate")
        padded = materialized + [pad_value] * (
            self.width - len(materialized)
        )
        return EncryptedOpenFheColumn(
            ciphertext=self._engine.encrypt(padded),
            scale=self.input_scale,
            valid_count=len(materialized),
        )

    def add(
        self,
        left: EncryptedOpenFheColumn,
        right: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Element-wise encrypted column addition."""
        self._validate_pair(left, right)
        return EncryptedOpenFheColumn(
            self._engine._context.EvalAdd(
                left.ciphertext,
                right.ciphertext,
            ),
            left.scale,
            left.valid_count,
        )

    def subtract(
        self,
        left: EncryptedOpenFheColumn,
        right: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Element-wise encrypted column subtraction."""
        self._validate_pair(left, right)
        return EncryptedOpenFheColumn(
            self._engine._context.EvalSub(
                left.ciphertext,
                right.ciphertext,
            ),
            left.scale,
            left.valid_count,
        )

    def multiply(
        self,
        left: EncryptedOpenFheColumn,
        right: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Element-wise encrypted column multiplication."""
        self._validate_pair(left, right)
        self._ensure_multiplication_key()
        return EncryptedOpenFheColumn(
            self._engine._context.EvalMult(
                left.ciphertext,
                right.ciphertext,
            ),
            left.scale * right.scale,
            left.valid_count,
        )

    def sum(
        self,
        column: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Return an encrypted sum while excluding public padding lanes."""
        masked = self._masked(column)
        self._ensure_sum_keys()
        return EncryptedOpenFheColumn(
            self._engine._context.EvalSum(masked, self.width),
            column.scale,
            1,
        )

    def mean(
        self,
        column: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Return encrypted SUM(x) / public valid_count."""
        encrypted_sum = self.sum(column)
        return EncryptedOpenFheColumn(
            self._engine._context.EvalMult(
                encrypted_sum.ciphertext,
                1.0 / float(column.valid_count),
            ),
            column.scale,
            1,
        )

    def variance(
        self,
        column: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Return encrypted ddof=1 sample variance from one ciphertext."""
        if column.valid_count < 2:
            raise ValueError("sample variance requires at least two values")
        self._ensure_multiplication_key()
        self._ensure_sum_keys()
        masked = self._masked(column)
        encrypted_sum = self._engine._context.EvalSum(masked, self.width)
        encrypted_squares = self._engine._context.EvalMult(masked, masked)
        encrypted_square_sum = self._engine._context.EvalSum(
            encrypted_squares,
            self.width,
        )
        encrypted_mean = self._engine._context.EvalMult(
            encrypted_sum,
            1.0 / float(column.valid_count),
        )
        centered_square_sum = self._engine._context.EvalSub(
            encrypted_square_sum,
            self._engine._context.EvalMult(
                encrypted_sum,
                encrypted_mean,
            ),
        )
        sample_variance = self._engine._context.EvalMult(
            centered_square_sum,
            1.0 / float(column.valid_count - 1),
        )
        return EncryptedOpenFheColumn(
            sample_variance,
            column.scale * column.scale,
            1,
        )

    def minimum(
        self,
        column: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Return one encrypted exact minimum value."""
        return EncryptedOpenFheColumn(
            self._engine.eval_min(column.ciphertext),
            column.scale,
            1,
        )

    def maximum(
        self,
        column: EncryptedOpenFheColumn,
    ) -> EncryptedOpenFheColumn:
        """Return one encrypted exact maximum value."""
        return EncryptedOpenFheColumn(
            self._engine.eval_max(column.ciphertext),
            column.scale,
            1,
        )

    def decrypt(
        self,
        column: EncryptedOpenFheColumn,
    ) -> tuple[float, ...]:
        """Decrypt a finished column only at the key-owner boundary."""
        self._engine._require_setup()
        plaintext = self._engine._context.Decrypt(
            self._engine._keys.secretKey,
            column.ciphertext,
        )
        plaintext.SetLength(column.valid_count)
        return tuple(
            float(value) * column.scale
            for value in plaintext.GetRealPackedValue()[
                : column.valid_count
            ]
        )

    def decrypt_scalar(self, column: EncryptedOpenFheColumn) -> float:
        """Decrypt a one-value reduction result."""
        if column.valid_count != 1:
            raise ValueError("decrypt_scalar requires a reduced column")
        return self.decrypt(column)[0]

    def _masked(self, column: EncryptedOpenFheColumn) -> Any:
        """Zero public padding without revealing or decrypting real values."""
        if not 1 <= column.valid_count <= self.width:
            raise ValueError("column valid_count is outside this context")
        mask = [1.0] * column.valid_count + [0.0] * (
            self.width - column.valid_count
        )
        plaintext_mask = self._engine._context.MakeCKKSPackedPlaintext(mask)
        return self._engine._context.EvalMult(
            column.ciphertext,
            plaintext_mask,
        )

    def _ensure_multiplication_key(self) -> None:
        if not self._multiplication_ready:
            self._engine._context.EvalMultKeyGen(
                self._engine._keys.secretKey
            )
            self._multiplication_ready = True

    def _ensure_sum_keys(self) -> None:
        if not self._sum_ready:
            self._engine._context.EvalSumKeyGen(
                self._engine._keys.secretKey
            )
            self._sum_ready = True

    @staticmethod
    def _validate_pair(
        left: EncryptedOpenFheColumn,
        right: EncryptedOpenFheColumn,
    ) -> None:
        if left.valid_count != right.valid_count:
            raise ValueError("encrypted columns have different valid counts")
        if left.scale != right.scale:
            raise ValueError("encrypted columns have different scales")
