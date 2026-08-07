"""Reviewed cryptographic defaults hidden behind the public API and CLIs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CkksBackendProfile:
    """One conservative CKKS deployment profile for credit analytics."""

    slot_count: int = 8192
    ring_dimension: int = 16384
    minimum_depth: int = 2
    benchmark_depth: int = 4
    scaling_mod_size: int = 50
    first_mod_size: int = 60
    absolute_tolerance: float = 1e-6
    relative_tolerance: float = 1e-5
    vnd_normalization_divisor: float = 1_000_000.0
    vnd_absolute_tolerance: float = 1.0
    vnd_relative_tolerance: float = 1e-6


@dataclass(frozen=True)
class BgvBackendProfile:
    """Exact-integer defaults for the isolated synthetic VND SUM."""

    slot_count: int = 8192
    ring_dimension: int = 16384
    multiplicative_depth: int = 0
    plaintext_modulus_bits: int = 50


CKKS_CREDIT_PROFILE = CkksBackendProfile()
BGV_VND_SUM_PROFILE = BgvBackendProfile()

# Small, fixed profiles for the multiplication-limit probe. They are not
# command-line knobs: changing either profile requires code review.
CKKS_MULTIPLY_PROBE_PROFILE = CkksBackendProfile(
    slot_count=8,
    ring_dimension=16_384,
    benchmark_depth=2,
)
BGV_MULTIPLY_PROBE_PROFILE = BgvBackendProfile(
    slot_count=8,
    ring_dimension=16_384,
    multiplicative_depth=1,
    plaintext_modulus_bits=30,
)
