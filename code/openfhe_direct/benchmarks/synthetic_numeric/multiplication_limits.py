#!/usr/bin/env python3
"""Probe large-number multiplication in BGV and CKKS without HE CLI tuning."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import (
    BGV_MULTIPLY_PROBE_PROFILE,
    CKKS_MULTIPLY_PROBE_PROFILE,
    OpenFHEBgvSession,
    OpenFHECreditSession,
)
from code.openfhe_direct.benchmarks.synthetic_vnd.sum import (
    packed_plaintext_modulus,
)


MAGNITUDES = tuple(10**exponent for exponent in range(1, 16))


def _cases() -> tuple[dict[str, Any], ...]:
    """Return independent multiplications; no result feeds another case."""
    times_decimal = tuple(
        {
            "case": "times_0_1",
            "magnitude": magnitude,
            "left": float(magnitude),
            "right": 0.1,
            "bgv_left": magnitude,
            # BGV is integer-only: 0.1 is encoded as 1 with scale 10.
            "bgv_right": 1,
            "bgv_fixed_point_scale": 10,
        }
        for magnitude in MAGNITUDES
    )
    squares = tuple(
        {
            "case": "square",
            "magnitude": magnitude,
            "left": float(magnitude),
            "right": float(magnitude),
            "bgv_left": magnitude,
            "bgv_right": magnitude,
            "bgv_fixed_point_scale": 1,
        }
        for magnitude in MAGNITUDES
    )
    return times_decimal + squares


CASES = _cases()


def _timed(
    function: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> tuple[Any, float]:
    started = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - started


def _centered_modulo(value: int, modulus: int) -> int:
    residue = value % modulus
    return residue - modulus if residue > modulus // 2 else residue


def _errors(observed: float, expected: float) -> tuple[float, float]:
    absolute = abs(observed - expected)
    return absolute, absolute / max(1.0, abs(expected))


def _ckks_case(
    *,
    client: OpenFHECreditSession,
    evaluator: OpenFHECreditSession,
    case: dict[str, Any],
    repetition: int,
) -> dict[str, Any]:
    left = float(case["left"])
    right = float(case["right"])
    expected = left * right
    try:
        # HE API call: OpenFHECreditSession.encrypt(left)
        left_ct, left_encrypt = _timed(client.encrypt, [left])
        # HE API call: OpenFHECreditSession.encrypt(right)
        right_ct, right_encrypt = _timed(client.encrypt, [right])
        # HE API call: OpenFHECreditSession.multiply(left_ct, right_ct)
        product_ct, evaluation = _timed(evaluator.multiply, left_ct, right_ct)
        # HE API call: OpenFHECreditSession.decrypt(product_ct)
        observed_encoded, decrypt = _timed(client.decrypt, product_ct)
        observed = float(observed_encoded[0])
        absolute, relative = _errors(observed, expected)
        assessment = (
            "ABSOLUTE_AND_RELATIVE_PASS"
            if absolute <= 1.0 and relative <= 1e-6
            else "RELATIVE_ONLY"
            if relative <= 1e-6
            else "FAIL"
        )
        return {
            "scheme": "CKKS",
            "case": case["case"],
            "magnitude": case["magnitude"],
            "route": "one_fresh_ct_x_ct",
            "repetition": repetition,
            "left": left,
            "right": right,
            "expected_python": expected,
            "observed": observed,
            "modular_expected": "",
            "absolute_error": absolute,
            "relative_error": relative,
            "setup_or_input_status": "EXECUTED",
            "accuracy_assessment": assessment,
            "encrypt_seconds": left_encrypt + right_encrypt,
            "evaluation_seconds": evaluation,
            "decrypt_seconds": decrypt,
            "note": "fresh encryption; no chained multiplication",
        }
    except Exception as error:  # server probe must report, not hide, rejection
        return {
            "scheme": "CKKS",
            "case": case["case"],
            "magnitude": case["magnitude"],
            "route": "one_fresh_ct_x_ct",
            "repetition": repetition,
            "left": left,
            "right": right,
            "expected_python": expected,
            "observed": "",
            "modular_expected": "",
            "absolute_error": "",
            "relative_error": "",
            "setup_or_input_status": "EVALUATION_REJECTED",
            "accuracy_assessment": "NOT_EXECUTED",
            "encrypt_seconds": "",
            "evaluation_seconds": "",
            "decrypt_seconds": "",
            "note": f"{type(error).__name__}: {error}",
        }


def _bgv_case(
    *,
    client: OpenFHEBgvSession,
    evaluator: OpenFHEBgvSession,
    modulus: int,
    case: dict[str, Any],
    repetition: int,
) -> dict[str, Any]:
    left = int(case["bgv_left"])
    right = int(case["bgv_right"])
    fixed_point_scale = int(case["bgv_fixed_point_scale"])
    expected = float(case["left"]) * float(case["right"])
    encoded_product = left * right
    modular_encoded = _centered_modulo(encoded_product, modulus)
    modular_expected = modular_encoded / fixed_point_scale
    try:
        # HE API call: OpenFHEBgvSession.encrypt(left)
        left_ct, left_encrypt = _timed(client.encrypt, [left])
        # HE API call: OpenFHEBgvSession.encrypt(right)
        right_ct, right_encrypt = _timed(client.encrypt, [right])
        # HE API call: OpenFHEBgvSession.multiply(left_ct, right_ct)
        product_ct, evaluation = _timed(evaluator.multiply, left_ct, right_ct)
        # HE API call: OpenFHEBgvSession.decrypt(product_ct)
        observed_encoded, decrypt = _timed(client.decrypt, product_ct)
        observed_centered = _centered_modulo(
            int(observed_encoded[0]),
            modulus,
        )
        observed = observed_centered / fixed_point_scale
        absolute, relative = _errors(observed, expected)
        modular_exact = observed_centered == modular_encoded
        ordinary_exact = observed == expected
        return {
            "scheme": "BGV",
            "case": case["case"],
            "magnitude": case["magnitude"],
            "route": "integer_or_fixed_point",
            "repetition": repetition,
            "left": case["left"],
            "right": case["right"],
            "expected_python": expected,
            "observed": observed,
            "modular_expected": modular_expected,
            "absolute_error": absolute,
            "relative_error": relative,
            "setup_or_input_status": "EXECUTED",
            "accuracy_assessment": (
                "ORDINARY_EXACT"
                if ordinary_exact
                else "MODULAR_WRAP" if modular_exact else "NOISE_FAILURE"
            ),
            "encrypt_seconds": left_encrypt + right_encrypt,
            "evaluation_seconds": evaluation,
            "decrypt_seconds": decrypt,
            "note": (
                f"plaintext_modulus={modulus}; "
                f"fixed_point_scale={fixed_point_scale}"
            ),
        }
    except Exception as error:  # input/context limits are part of this probe
        return {
            "scheme": "BGV",
            "case": case["case"],
            "magnitude": case["magnitude"],
            "route": "integer_or_fixed_point",
            "repetition": repetition,
            "left": case["left"],
            "right": case["right"],
            "expected_python": expected,
            "observed": "",
            "modular_expected": modular_expected,
            "absolute_error": "",
            "relative_error": "",
            "setup_or_input_status": "INPUT_OR_EVALUATION_REJECTED",
            "accuracy_assessment": "NOT_EXECUTED",
            "encrypt_seconds": "",
            "evaluation_seconds": "",
            "decrypt_seconds": "",
            "note": f"{type(error).__name__}: {error}",
        }


def run_probe(
    *,
    output_dir: Path,
    repetitions: int,
    overwrite: bool,
    _ckks_factory: Callable[..., Any] = OpenFHECreditSession,
    _bgv_factory: Callable[..., Any] = OpenFHEBgvSession,
) -> dict[str, Any]:
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

    rows: list[dict[str, Any]] = []
    setup: dict[str, Any] = {}

    try:
        ckks_client, setup["ckks_seconds"] = _timed(
            _ckks_factory,
            slot_count=CKKS_MULTIPLY_PROBE_PROFILE.slot_count,
            multiplicative_depth=CKKS_MULTIPLY_PROBE_PROFILE.benchmark_depth,
            scaling_mod_size=CKKS_MULTIPLY_PROBE_PROFILE.scaling_mod_size,
            first_mod_size=CKKS_MULTIPLY_PROBE_PROFILE.first_mod_size,
            ring_dimension=CKKS_MULTIPLY_PROBE_PROFILE.ring_dimension,
        )
        ckks_evaluator = ckks_client.evaluator_view()
        for repetition in range(1, repetitions + 1):
            for case in CASES:
                rows.append(
                    _ckks_case(
                        client=ckks_client,
                        evaluator=ckks_evaluator,
                        case=case,
                        repetition=repetition,
                    )
                )
    except Exception as error:
        setup["ckks_error"] = f"{type(error).__name__}: {error}"

    bgv_profile = BGV_MULTIPLY_PROBE_PROFILE
    bgv_modulus = packed_plaintext_modulus(
        bgv_profile.plaintext_modulus_bits,
        bgv_profile.ring_dimension,
    )
    try:
        bgv_client, setup["bgv_seconds"] = _timed(
            _bgv_factory,
            slot_count=bgv_profile.slot_count,
            plaintext_modulus=bgv_modulus,
            multiplicative_depth=bgv_profile.multiplicative_depth,
            ring_dimension=bgv_profile.ring_dimension,
        )
        bgv_evaluator = bgv_client.evaluator_view()
        for repetition in range(1, repetitions + 1):
            for case in CASES:
                rows.append(
                    _bgv_case(
                        client=bgv_client,
                        evaluator=bgv_evaluator,
                        modulus=bgv_modulus,
                        case=case,
                        repetition=repetition,
                    )
                )
    except Exception as error:
        setup["bgv_error"] = f"{type(error).__name__}: {error}"

    if not rows:
        status = "NO_SCHEME_EXECUTED"
    elif any(row["setup_or_input_status"] == "EXECUTED" for row in rows):
        status = "PROBE_EXECUTED"
    else:
        status = "ALL_CASES_REJECTED"

    fieldnames = list(rows[0]) if rows else ["scheme", "note"]
    with (root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    first_rows = [row for row in rows if row["repetition"] == 1]

    def first_magnitude(predicate: Callable[[dict[str, Any]], bool]) -> int | None:
        matches = [int(row["magnitude"]) for row in first_rows if predicate(row)]
        return min(matches) if matches else None

    boundaries = {
        case_name: {
            "bgv_first_not_ordinary_exact": first_magnitude(
                lambda row, name=case_name: row["scheme"] == "BGV"
                and row["case"] == name
                and row["accuracy_assessment"] != "ORDINARY_EXACT"
            ),
            "ckks_first_absolute_error_over_1": first_magnitude(
                lambda row, name=case_name: row["scheme"] == "CKKS"
                and row["case"] == name
                and row["absolute_error"] != ""
                and float(row["absolute_error"]) > 1.0
            ),
            "ckks_first_relative_error_over_1e_6": first_magnitude(
                lambda row, name=case_name: row["scheme"] == "CKKS"
                and row["case"] == name
                and row["relative_error"] != ""
                and float(row["relative_error"]) > 1e-6
            ),
            "ckks_first_rejected": first_magnitude(
                lambda row, name=case_name: row["scheme"] == "CKKS"
                and row["case"] == name
                and row["setup_or_input_status"] != "EXECUTED"
            ),
        }
        for case_name in ("times_0_1", "square")
    }

    summary = {
        "status": status,
        "purpose": "distinguish BGV modular limits from CKKS approximation",
        "independent_multiplications": True,
        "magnitude_sweep": list(MAGNITUDES),
        "cases": [
            {
                key: case[key]
                for key in ("case", "magnitude", "left", "right")
            }
            for case in CASES
        ],
        "setup": setup,
        "bgv_plaintext_modulus": bgv_modulus,
        "bgv_centered_capacity": bgv_modulus // 2,
        "first_observed_boundaries": boundaries,
        "profiles": {
            "CKKS": CKKS_MULTIPLY_PROBE_PROFILE.__dict__,
            "BGV": BGV_MULTIPLY_PROBE_PROFILE.__dict__,
        },
        "result_rows": len(rows),
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )

    report_rows = [
        "| Scheme | Case | Magnitude | Route | Python product | HE result | Modular result | Absolute error | Relative error | Assessment |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        report_rows.append(
            f"| {row['scheme']} | {row['case']} | {row['magnitude']} | "
            f"{row['route']} | "
            f"{row['expected_python']} | {row['observed']} | "
            f"{row['modular_expected']} | {row['absolute_error']} | "
            f"{row['relative_error']} | {row['accuracy_assessment']} |"
        )
    (root / "REPORT.md").write_text(
        "# BGV and CKKS multiplication-limit probe\n\n"
        "This is not a claim that every large multiplication fails. BGV is "
        "audited against both ordinary and modular arithmetic. CKKS is "
        "audited with both absolute and relative error. Every row uses fresh "
        "ciphertexts and exactly one multiplication; results are never "
        "chained.\n\n"
        f"Setup status: `{json.dumps(setup, sort_keys=True)}`\n\n"
        f"First observed boundaries: `{json.dumps(boundaries, sort_keys=True)}`\n\n"
        + "\n".join(report_rows)
        + "\n\nSee `summary.json` for fixed backend profiles and setup errors.\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = run_probe(
        output_dir=args.output_dir,
        repetitions=args.repetitions,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
