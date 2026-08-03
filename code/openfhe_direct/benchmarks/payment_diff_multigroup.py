#!/usr/bin/env python3
"""Run PAYMENT_DIFF statistics for several groups in one OpenFHE session."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
from statistics import median, variance
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import CKKS_CREDIT_PROFILE, OpenFHECreditSession
from code.openfhe_direct.prepared_data import (
    PreparedPaymentGroup,
    load_prepared_group,
    load_prepared_group_population,
    public_power_of_two_scale,
)


OUTPUTS = ("sum", "mean", "variance", "minimum", "maximum")


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _python_reference(group: PreparedPaymentGroup) -> dict[str, float]:
    difference = [
        due - paid
        for due, paid in zip(group.installment, group.payment)
    ]
    total = sum(difference)
    return {
        "sum": total,
        "mean": total / len(difference),
        "variance": variance(difference),
        "minimum": min(difference),
        "maximum": max(difference),
    }


def _next_power_of_two(value: int) -> int:
    return 1 << (value - 1).bit_length()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_report(
    root: Path,
    *,
    groups: list[PreparedPaymentGroup],
    slot_count: int,
    input_scale: float,
    repetitions: int,
    setup_seconds: float,
    timing: dict[str, float],
    final_audits: list[dict[str, Any]],
    status: str,
    selection: dict[str, Any],
) -> None:
    lines = [
        "# OpenFHE-Python multi-group PAYMENT_DIFF",
        "",
        "One shared `OpenFHECreditSession` processes several client-prepared "
        "groups selected from one run set. Each selected group is evaluated "
        "separately; values from different groups are never mixed. For each "
        "group it encrypts both parent columns, calculates "
        "`PAYMENT_DIFF`, then encrypted SUM, MEAN, sample VARIANCE, MINIMUM, "
        "and MAXIMUM. No ciphertext is decrypted between operations.",
        "",
        f"- Groups: `{len(groups)}`",
        f"- Input mode: `{selection['input_mode']}`",
        f"- Selection policy: `{selection['selection_policy']}`",
        f"- Population groups available: "
        f"`{selection['population_group_count']}`",
        f"- Real rows: `{sum(len(group.installment) for group in groups)}`",
        f"- Client-invalid rows removed: "
        f"`{sum(group.dropped_invalid_rows for group in groups)}`",
        f"- Shared CKKS width: `{slot_count}`",
        f"- Public input scale: `{input_scale:g}`",
        f"- Repetitions: `{repetitions}`",
        f"- CKKS/FHEW context and key setup: `{setup_seconds:.9f}` seconds",
        "- Client/evaluator roles: `same-process scheme-switching exception`",
        "- Reason: the current Python route cannot transport all live "
        "CKKS/FHEW switching material",
        "",
        "## Final accuracy audit",
        "",
        "| Group | Rows | Output | Python | HE audit | Absolute error | "
        "Status |",
        "|---|---:|---|---:|---:|---:|---|",
    ]
    for row in final_audits:
        for output in OUTPUTS:
            lines.append(
                f"| `{row['group']}` | {row['rows']} | `{output}` | "
                f"{float(row[f'python_{output}']):.12g} | "
                f"{float(row[f'he_{output}']):.12g} | "
                f"{float(row[f'{output}_abs_error']):.12g} | "
                f"{row[f'{output}_status']} |"
            )
    lines.extend(
        [
            "",
            "## Median latency across all groups",
            "",
            "| Python reference | Parent encryption | PAYMENT_DIFF | SUM | "
            "MEAN | VARIANCE | MIN | MAX | HE online | Audit decrypt |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {timing['python_seconds']:.9f} | "
            f"{timing['encrypt_seconds']:.9f} | "
            f"{timing['subtract_seconds']:.9f} | "
            f"{timing['sum_seconds']:.9f} | "
            f"{timing['mean_seconds']:.9f} | "
            f"{timing['variance_seconds']:.9f} | "
            f"{timing['minimum_seconds']:.9f} | "
            f"{timing['maximum_seconds']:.9f} | "
            f"{timing['he_online_seconds']:.9f} | "
            f"{timing['audit_decrypt_seconds']:.9f} |",
            "",
            "Equivalent plaintext calculation per prepared group:",
            "",
            "```python",
            "payment_diff = [due - paid for due, paid in zip(due, paid)]",
            "result = {",
            "    'sum': sum(payment_diff),",
            "    'mean': sum(payment_diff) / len(payment_diff),",
            "    'variance': statistics.variance(payment_diff),",
            "    'minimum': min(payment_diff),",
            "    'maximum': max(payment_diff),",
            "}",
            "```",
            "",
            "`HE online` includes parent encryption through every final "
            "encrypted aggregate. It excludes setup and audit decryption. "
            "MIN/MAX use the session's OpenFHE CKKS↔FHEW scheme-switching "
            "comparison trees and are expected to dominate latency.",
            "",
            f"Overall status: **{status}**.",
        ]
    )
    (root / "REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def run_multigroup_benchmark(
    *,
    prepared_groups: list[Path] | None = None,
    prepared_population_dir: Path | None = None,
    group_count: int = 5,
    opaque_group_ids: list[int] | None = None,
    selection_policy: str = "spread",
    output_dir: Path,
    repetitions: int,
    ring_dimension: int,
    slot_count: int,
    input_scale: float,
    absolute_tolerance: float,
    relative_tolerance: float,
    overwrite: bool,
    _session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run all five encrypted reductions over at least two prepared groups."""
    if (prepared_groups is None) == (prepared_population_dir is None):
        raise ValueError(
            "provide exactly one of prepared_groups or "
            "prepared_population_dir"
        )
    if repetitions < 1:
        raise ValueError("repetitions must be positive")

    root = output_dir.resolve()
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    preparation_started = time.perf_counter()
    if prepared_population_dir is not None:
        population = load_prepared_group_population(
            prepared_population_dir,
            group_count=group_count,
            opaque_group_ids=opaque_group_ids,
            selection_policy=selection_policy,
        )
        groups = population.groups
        selection = {
            "input_mode": "prepared_population",
            "selection_policy": population.selection_policy,
            "population_group_count": population.population_group_count,
            "eligible_group_count": population.eligible_group_count,
            "source_directory": population.source_directory,
        }
    else:
        groups = [
            load_prepared_group(path.resolve())
            for path in (prepared_groups or [])
        ]
        selection = {
            "input_mode": "individual_smoke_fixtures",
            "selection_policy": "explicit_files",
            "population_group_count": len(groups),
            "eligible_group_count": len(groups),
            "source_directory": None,
        }
    client_load_seconds = time.perf_counter() - preparation_started
    if len(groups) < 2:
        raise ValueError("provide or select at least two prepared groups")
    identifiers = [group.applicant_id for group in groups]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("prepared group identifiers must be unique")

    required_slots = max(group.slot_count for group in groups)
    selected_slots = slot_count or _next_power_of_two(required_slots)
    if selected_slots < required_slots or selected_slots & (selected_slots - 1):
        raise ValueError(
            "slot_count must be a power of two covering the largest group"
        )
    if ring_dimension < 2 * selected_slots:
        raise ValueError("ring_dimension cannot hold the selected slot_count")

    scale_candidates: list[float] = []
    for group in groups:
        scale_candidates.extend(group.installment)
        scale_candidates.extend(group.payment)
        scale_candidates.extend(
            due - paid
            for due, paid in zip(group.installment, group.payment)
        )
    selected_scale = input_scale or public_power_of_two_scale(
        scale_candidates
    )
    if selected_scale <= 0 or not all(
        -0.5 < value / selected_scale <= 0.5
        for value in scale_candidates
    ):
        raise ValueError(
            "input_scale must place parents and PAYMENT_DIFF in (-0.5, 0.5]"
        )
    factory = _session_factory or OpenFHECreditSession
    # HE API call: OpenFHECreditSession(...) creates CKKS/FHEW context and keys.
    session, setup_seconds = _timed(
        factory,
        slot_count=selected_slots,
        input_scale=selected_scale,
        ring_dimension=ring_dimension,
        enable_minmax=True,
    )
    references = {
        group.applicant_id: _python_reference(group)
        for group in groups
    }

    timing_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    all_passed = True
    for repetition in range(1, repetitions + 1):
        python_started = time.perf_counter()
        for group in groups:
            _python_reference(group)
        python_seconds = time.perf_counter() - python_started

        stage = {
            "encrypt_seconds": 0.0,
            "subtract_seconds": 0.0,
            "sum_seconds": 0.0,
            "mean_seconds": 0.0,
            "variance_seconds": 0.0,
            "minimum_seconds": 0.0,
            "maximum_seconds": 0.0,
            "audit_decrypt_seconds": 0.0,
        }
        for group in groups:
            # HE API call: OpenFHECreditSession.encrypt(AMT_INSTALMENT)
            installment_ct, elapsed = _timed(
                session.encrypt,
                group.installment,
            )
            stage["encrypt_seconds"] += elapsed
            # HE API call: OpenFHECreditSession.encrypt(AMT_PAYMENT)
            payment_ct, elapsed = _timed(
                session.encrypt,
                group.payment,
            )
            stage["encrypt_seconds"] += elapsed
            # HE API call: OpenFHECreditSession.subtract(parent ciphertexts)
            difference_ct, elapsed = _timed(
                session.subtract,
                installment_ct,
                payment_ct,
            )
            stage["subtract_seconds"] += elapsed

            encrypted_outputs: dict[str, Any] = {}
            for name, method in (
                # HE API call: OpenFHECreditSession.sum(difference_ct)
                ("sum", session.sum),
                # HE API call: OpenFHECreditSession.mean(difference_ct)
                ("mean", session.mean),
                # HE API call: OpenFHECreditSession.variance(difference_ct)
                ("variance", session.variance),
                # HE API call: OpenFHECreditSession.minimum(difference_ct)
                ("minimum", session.minimum),
                # HE API call: OpenFHECreditSession.maximum(difference_ct)
                ("maximum", session.maximum),
            ):
                encrypted_outputs[name], elapsed = _timed(
                    method,
                    difference_ct,
                )
                stage[f"{name}_seconds"] += elapsed

            observed: dict[str, float] = {}
            for name, encrypted in encrypted_outputs.items():
                # HE API call: OpenFHECreditSession.decrypt(final scalar)
                observed[name], elapsed = _timed(session.decrypt, encrypted)
                stage["audit_decrypt_seconds"] += elapsed

            reference = references[group.applicant_id]
            row: dict[str, Any] = {
                "repetition": repetition,
                "group": group.applicant_id,
                "rows": len(group.installment),
            }
            for name in OUTPUTS:
                absolute = abs(observed[name] - reference[name])
                relative = absolute / max(1.0, abs(reference[name]))
                passed = (
                    absolute <= absolute_tolerance
                    or relative <= relative_tolerance
                )
                all_passed = all_passed and passed
                row.update(
                    {
                        f"python_{name}": reference[name],
                        f"he_{name}": observed[name],
                        f"{name}_abs_error": absolute,
                        f"{name}_relative_error": relative,
                        f"{name}_status": "PASS" if passed else "FAIL",
                    }
                )
            audit_rows.append(row)

        he_online = sum(
            stage[name]
            for name in (
                "encrypt_seconds",
                "subtract_seconds",
                "sum_seconds",
                "mean_seconds",
                "variance_seconds",
                "minimum_seconds",
                "maximum_seconds",
            )
        )
        timing_rows.append(
            {
                "repetition": repetition,
                "python_seconds": python_seconds,
                **stage,
                "he_online_seconds": he_online,
            }
        )

    timing_names = (
        "python_seconds",
        "encrypt_seconds",
        "subtract_seconds",
        "sum_seconds",
        "mean_seconds",
        "variance_seconds",
        "minimum_seconds",
        "maximum_seconds",
        "he_online_seconds",
        "audit_decrypt_seconds",
    )
    median_timing = {
        name: median(float(row[name]) for row in timing_rows)
        for name in timing_names
    }
    status = "PASS" if all_passed else "FAIL"
    summary = {
        "status": status,
        "backend": "official OpenFHE Python",
        "one_shared_context": True,
        "independent_group_execution": True,
        "scheme_switching_minmax": True,
        "client_evaluator_separated": False,
        "separation_blocker": (
            "current OpenFHE Python path cannot transport all live "
            "CKKS/FHEW switching material"
        ),
        "group_count": len(groups),
        "selected_opaque_groups": identifiers,
        "selection": selection,
        "real_rows": sum(len(group.installment) for group in groups),
        "dropped_invalid_rows": sum(
            group.dropped_invalid_rows for group in groups
        ),
        "client_population_load_seconds": client_load_seconds,
        "slot_count": selected_slots,
        "ring_dimension": ring_dimension,
        "input_scale": selected_scale,
        "repetitions": repetitions,
        "setup_seconds": setup_seconds,
        "median_timings": median_timing,
        "outputs": [
            "PAYMENT_DIFF_SUM",
            "PAYMENT_DIFF_MEAN",
            "PAYMENT_DIFF_VAR",
            "PAYMENT_DIFF_MIN",
            "PAYMENT_DIFF_MAX",
        ],
    }
    _write_csv(root / "timings.csv", timing_rows)
    _write_csv(root / "accuracy.csv", audit_rows)
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(
        root,
        groups=groups,
        slot_count=selected_slots,
        input_scale=selected_scale,
        repetitions=repetitions,
        setup_seconds=setup_seconds,
        timing=median_timing,
        final_audits=[
            row
            for row in audit_rows
            if int(row["repetition"]) == repetitions
        ],
        status=status,
        selection=selection,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument(
        "--prepared-group",
        nargs="+",
        type=Path,
        help="individual prepared CSVs; intended only for smoke fixtures",
    )
    inputs.add_argument(
        "--prepared-population-dir",
        type=Path,
        help=(
            "population-wide output from "
            "prepare_installments_group_blocks.py"
        ),
    )
    parser.add_argument(
        "--group-count",
        type=int,
        default=5,
        help="number selected from the full population; 0 selects all",
    )
    parser.add_argument(
        "--opaque-group-id",
        nargs="+",
        type=int,
        help="explicit opaque IDs; overrides --group-count and --selection",
    )
    parser.add_argument(
        "--selection",
        choices=("spread", "largest", "first"),
        default="spread",
        help="deterministic selection from the complete group population",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = run_multigroup_benchmark(
        prepared_groups=args.prepared_group,
        prepared_population_dir=args.prepared_population_dir,
        group_count=args.group_count,
        opaque_group_ids=args.opaque_group_id,
        selection_policy=args.selection,
        output_dir=args.output_dir,
        repetitions=args.repetitions,
        ring_dimension=CKKS_CREDIT_PROFILE.ring_dimension,
        slot_count=0,
        input_scale=0.0,
        absolute_tolerance=CKKS_CREDIT_PROFILE.absolute_tolerance,
        relative_tolerance=CKKS_CREDIT_PROFILE.relative_tolerance,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
