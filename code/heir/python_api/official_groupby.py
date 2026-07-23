"""Post-PSI client grouping and official HEIR grouped PAYMENT_DIFF SUM."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
import csv
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import time
from typing import Any

from code.heir.python_api.official_ckks_aggregates import (
    _load_official_heir_compile,
)


KEY = "SK_ID_CURR"
PARENT_COLUMNS = ("AMT_PAYMENT", "AMT_INSTALMENT")


def _finite(value: str | None) -> float | None:
    try:
        parsed = float(value or "")
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def payment_diff_sum_mlir(width: int) -> str:
    """Emit one-result CKKS: ``SUM(installment - payment)``."""
    if width < 2:
        raise ValueError("width must be at least two")
    tensor = f"tensor<{width}xf64>"
    lines = [
        "func.func @payment_diff_sum(",
        f"    %installment: {tensor} {{secret.secret}},",
        f"    %payment: {tensor} {{secret.secret}}",
        ") -> f64 {",
        f"  %difference = arith.subf %installment, %payment : {tensor}",
    ]
    current: list[str] = []
    for index in range(width):
        index_name = f"%index_{index}"
        value_name = f"%difference_{index}"
        lines.append(f"  {index_name} = arith.constant {index} : index")
        lines.append(
            f"  {value_name} = tensor.extract "
            f"%difference[{index_name}] : {tensor}"
        )
        current.append(value_name)
    operation = 0
    while len(current) > 1:
        next_level: list[str] = []
        for index in range(0, len(current), 2):
            if index + 1 == len(current):
                next_level.append(current[index])
                continue
            result_name = (
                "%sum_result"
                if len(current) == 2
                else f"%sum_tree_{operation}"
            )
            lines.append(
                f"  {result_name} = arith.addf {current[index]}, "
                f"{current[index + 1]} : f64"
            )
            next_level.append(result_name)
            operation += 1
        current = next_level
    lines.extend(["  return %sum_result : f64", "}"])
    return "\n".join(lines) + "\n"


def _pack_column(values: Sequence[float], width: int) -> Any:
    materialized = [float(value) for value in values]
    if not 1 <= len(materialized) <= width:
        raise ValueError(f"column length must be in [1, {width}]")
    if not all(math.isfinite(value) for value in materialized):
        raise ValueError("column must not contain NaN or infinity")
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError(
            "NumPy is required by HEIR's tensor interface. Install "
            "'heir_py[python,openfhe]'."
        ) from error
    packed = np.zeros(width, dtype=np.float64)
    packed[: len(materialized)] = materialized
    return packed


@dataclass(frozen=True)
class OpaquePaymentGroup:
    """Client-prepared group without its raw applicant identifier."""

    opaque_group_id: int
    payment: tuple[float, ...]
    installment: tuple[float, ...]

    @property
    def real_count(self) -> int:
        return len(self.payment)


@dataclass(frozen=True)
class PostPsiGroupLayout:
    """Private client result plus anonymous HE-ready blocks."""

    groups: tuple[OpaquePaymentGroup, ...]
    private_mapping: tuple[tuple[int, str, int], ...] = field(repr=False)
    post_psi_applicants: int
    source_rows_scanned: int
    invalid_parent_rows: int
    preparation_seconds: float


def _read_bridge_keys(bridge_dir: Path) -> set[str]:
    path = bridge_dir / "private_exchange" / "sender_application_layout.csv"
    if not path.is_file():
        raise FileNotFoundError(
            f"post-PSI bridge layout is missing: {path}; build it with "
            "code/bridge/psi_to_heir.py first"
        )
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != {"app_index", KEY}:
            raise ValueError("unexpected sender bridge layout schema")
        return {
            (row.get(KEY) or "").strip()
            for row in reader
            if (row.get(KEY) or "").strip()
        }


def prepare_post_psi_groups(
    installments: Path,
    bridge_dir: Path,
    *,
    group_count: int,
    bucket_size: int,
    minimum_group_size: int = 1,
) -> PostPsiGroupLayout:
    """Select complete PSI-approved groups and replace keys with ordinals."""
    if group_count < 1:
        raise ValueError("group_count must be positive")
    if bucket_size < 2:
        raise ValueError("bucket_size must be at least two")
    if not 1 <= minimum_group_size <= bucket_size:
        raise ValueError("minimum_group_size must be in [1, bucket_size]")
    if not installments.is_file():
        raise FileNotFoundError(f"installments CSV is missing: {installments}")

    started = time.perf_counter()
    eligible_keys = _read_bridge_keys(bridge_dir)
    counts: Counter[str] = Counter()
    source_rows = invalid_rows = 0
    with installments.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {KEY, *PARENT_COLUMNS}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"installments CSV is missing columns: {sorted(missing)}"
            )
        for row in reader:
            source_rows += 1
            key = (row.get(KEY) or "").strip()
            if key not in eligible_keys:
                continue
            if (
                _finite(row.get("AMT_PAYMENT")) is None
                or _finite(row.get("AMT_INSTALMENT")) is None
            ):
                invalid_rows += 1
                continue
            counts[key] += 1

    fitting = [
        key
        for key, count in counts.items()
        if minimum_group_size <= count <= bucket_size
    ]
    selected = sorted(
        fitting,
        key=lambda key: (
            -counts[key],
            hashlib.blake2b(key.encode(), digest_size=8).digest(),
        ),
    )[:group_count]
    if len(selected) != group_count:
        raise ValueError(
            f"only {len(selected)} post-PSI groups fit bucket "
            f"{bucket_size}; requested {group_count}"
        )

    parents: dict[str, list[tuple[float, float]]] = defaultdict(list)
    selected_set = set(selected)
    with installments.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get(KEY) or "").strip()
            if key not in selected_set:
                continue
            payment = _finite(row.get("AMT_PAYMENT"))
            installment = _finite(row.get("AMT_INSTALMENT"))
            if payment is not None and installment is not None:
                parents[key].append((payment, installment))
    if any(len(parents[key]) != counts[key] for key in selected):
        raise RuntimeError("source passes disagree for a selected group")

    groups: list[OpaquePaymentGroup] = []
    mapping: list[tuple[int, str, int]] = []
    for opaque, key in enumerate(selected):
        rows = parents[key]
        groups.append(
            OpaquePaymentGroup(
                opaque_group_id=opaque,
                payment=tuple(row[0] for row in rows),
                installment=tuple(row[1] for row in rows),
            )
        )
        mapping.append((opaque, key, len(rows)))
    return PostPsiGroupLayout(
        groups=tuple(groups),
        private_mapping=tuple(mapping),
        post_psi_applicants=len(eligible_keys),
        source_rows_scanned=source_rows,
        invalid_parent_rows=invalid_rows,
        preparation_seconds=time.perf_counter() - started,
    )


@dataclass
class OfficialPaymentDiffGroupSum:
    """Reusable official HEIR program for one zero-padded group block."""

    width: int
    debug: bool = False
    _program: Any = field(init=False, repr=False)
    _is_setup: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        source = payment_diff_sum_mlir(self.width)
        self._program = _load_official_heir_compile()(
            mlir_str=source,
            scheme="ckks",
            debug=self.debug,
        )

    @property
    def mlir(self) -> str:
        return payment_diff_sum_mlir(self.width)

    def setup(self) -> None:
        self._program.setup()
        self._is_setup = True

    def encrypt(
        self,
        group: OpaquePaymentGroup,
    ) -> tuple[Any, Any]:
        self._require_setup()
        if group.real_count > self.width:
            raise ValueError("group does not fit the compiled width")
        installment = _pack_column(group.installment, self.width)
        payment = _pack_column(group.payment, self.width)
        compilation = self._program.compilation_result
        encryptors = compilation.arg_enc_funcs or {}
        if len(encryptors) != 2:
            raise RuntimeError(
                "expected two encrypted HEIR inputs; compiled encryptor names "
                f"are {sorted(encryptors)}"
            )
        names = list(encryptors)
        return (
            getattr(self._program, f"encrypt_{names[0]}")(installment),
            getattr(self._program, f"encrypt_{names[1]}")(payment),
        )

    def eval(self, encrypted_parents: tuple[Any, Any]) -> Any:
        self._require_setup()
        return self._program.eval(*encrypted_parents)

    def decrypt(self, encrypted_sum: Any) -> float:
        self._require_setup()
        return float(self._program.decrypt_result(encrypted_sum))

    def _require_setup(self) -> None:
        if not self._is_setup:
            raise RuntimeError("call setup() before encrypt/eval/decrypt")
