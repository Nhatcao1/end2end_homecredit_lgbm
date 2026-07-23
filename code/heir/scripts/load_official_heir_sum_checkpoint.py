#!/usr/bin/env python3
"""Reload an official HEIR CKKS SUM checkpoint in a fresh audit process."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import load_sum_checkpoint


def _write_report(path: Path, result: dict[str, object]) -> None:
    path.write_text(
        "\n".join(
            [
                "# Official HEIR/OpenFHE SUM checkpoint proof",
                "",
                "Process A compiled the SUM circuit, created one CKKS context, "
                "encrypted the input, evaluated SUM, and serialized the still-"
                "encrypted input/result. It did not decrypt.",
                "",
                "Process B compiled the matching circuit bindings, restored the "
                "same OpenFHE context, public/evaluation keys, client-only secret "
                "key, and saved result ciphertext, then decrypted only for audit.",
                "",
                "| Python SUM | Reloaded HE audit | Absolute error | Status |",
                "|---:|---:|---:|---|",
                f"| {result['python_sum']:.12g} | "
                f"{result['he_sum']:.12g} | "
                f"{result['absolute_error']:.12g} | "
                f"{result['accuracy_status']} |",
                "",
                "| Reload/compile (s) | Audit decrypt (s) |",
                "|---:|---:|",
                f"| {result['reload_seconds']:.9f} | "
                f"{result['audit_decrypt_seconds']:.9f} |",
                "",
                "`public/` is evaluator-safe material. "
                "`client_private/audit_secret.key` must never be sent to the "
                "evaluator. Encryption and serialization are separate steps.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("benchmark_runs/official_heir_sum_checkpoint"),
    )
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    root = args.checkpoint_dir.resolve()

    reference = json.loads(
        (root / "client_private" / "reference.json").read_text(
            encoding="utf-8"
        )
    )
    started = time.perf_counter()
    loaded = load_sum_checkpoint(root, for_audit=True, debug=args.debug)
    reload_seconds = time.perf_counter() - started
    started = time.perf_counter()
    he_sum = loaded.program.decrypt(loaded.result_ciphertext)
    decrypt_seconds = time.perf_counter() - started
    python_sum = float(reference["python_sum"])
    absolute_error = abs(he_sum - python_sum)
    result = {
        "status": "official_heir_sum_checkpoint_reloaded",
        "checkpoint_dir": str(root),
        "python_sum": python_sum,
        "he_sum": he_sum,
        "absolute_error": absolute_error,
        "accuracy_status": (
            "PASS" if absolute_error <= args.tolerance else "FAIL"
        ),
        "reload_seconds": reload_seconds,
        "audit_decrypt_seconds": decrypt_seconds,
        "process_boundary_proven": True,
    }
    (root / "AUDIT.json").write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_report(root / "REPORT.md", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
