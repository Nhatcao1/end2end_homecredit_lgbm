#!/usr/bin/env python3
"""Save and reload named encrypted columns, then calculate PAYMENT_DIFF.

The default ``roundtrip`` mode launches the load/evaluate stage in a fresh
process.  This is the intended checkpoint boundary and avoids OpenFHE global
key-registry collisions inside one long-lived Python interpreter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from code.heir.python_api import EncryptedDataset


INSTALLMENT = (800.0, 500.0, 1000.0)
PAYMENT = (640.0, 600.0, 1000.0)


def save(checkpoint_dir: Path, overwrite: bool) -> None:
    dataset = EncryptedDataset.encrypt(
        {
            "AMT_INSTALMENT": INSTALLMENT,
            "AMT_PAYMENT": PAYMENT,
        },
        operation="subtract",
        width=8,
        input_scale=2048.0,
    )
    manifest = dataset.save(
        checkpoint_dir,
        include_audit_key=True,
        overwrite=overwrite,
    )
    print(
        json.dumps(
            {
                "status": "encrypted_dataset_saved",
                "columns": manifest["column_order"],
                "operation": manifest["binary_operation"],
                "valid_count": manifest["valid_count"],
                "checkpoint_dir": str(checkpoint_dir.resolve()),
            },
            indent=2,
        )
    )


def load_and_evaluate(checkpoint_dir: Path) -> None:
    dataset = EncryptedDataset.load(checkpoint_dir, for_audit=True)
    payment_diff = dataset.evaluate("AMT_INSTALMENT", "AMT_PAYMENT")
    audited = dataset.decrypt_result(payment_diff)
    expected = tuple(
        installment - payment
        for installment, payment in zip(INSTALLMENT, PAYMENT)
    )
    print(
        json.dumps(
            {
                "status": "encrypted_dataset_loaded_and_evaluated",
                "operation": dataset.operation,
                "expected": expected,
                "final_audit": audited,
                "maximum_absolute_error": max(
                    abs(left - right)
                    for left, right in zip(expected, audited)
                ),
                "no_intermediate_decryption": True,
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("save", "load", "roundtrip"),
        default="roundtrip",
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("benchmark_runs/encrypted_dataset_trial"),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.stage == "save":
        save(args.checkpoint_dir, args.overwrite)
        return
    if args.stage == "load":
        load_and_evaluate(args.checkpoint_dir)
        return

    save(args.checkpoint_dir, args.overwrite)
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--stage",
            "load",
            "--checkpoint-dir",
            str(args.checkpoint_dir.resolve()),
        ],
        cwd=ROOT,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"fresh-process checkpoint load failed with exit code "
            f"{completed.returncode}"
        )


if __name__ == "__main__":
    main()
