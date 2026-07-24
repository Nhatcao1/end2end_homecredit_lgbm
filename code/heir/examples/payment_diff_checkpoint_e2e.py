#!/usr/bin/env python3
"""Runnable post-PSI PAYMENT_DIFF aggregates with an encrypted checkpoint.

This is application flow, not a benchmark/report generator:

``CSV -> post-PSI group -> three encrypted PAYMENT_DIFF aggregate branches
-> save/reload -> encrypted MAX branch -> final decrypt``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    SourceBuiltOpenFheColumnMax,
    compile_checkpointable_binary_column_aggregate,
    load_binary_column_aggregate_checkpoint,
    prepare_post_psi_groups,
    public_power_of_two_scale,
    save_binary_column_aggregate_checkpoint,
)


def _audit_one_checkpoint(checkpoint_dir: Path) -> float:
    """Decrypt one branch in a fresh process to avoid OpenFHE key collisions."""
    output = checkpoint_dir / "client_private" / "audit_result.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--audit-aggregate-checkpoint",
            str(checkpoint_dir),
            "--audit-output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"isolated checkpoint audit failed for {checkpoint_dir}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return float(json.loads(output.read_text(encoding="utf-8"))["value"])


def _validate_resumed_branch(
    checkpoint_dir: Path,
    *,
    aggregate: str,
    width: int,
    valid_count: int,
    input_scale: float,
) -> None:
    manifest_path = checkpoint_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"cannot resume; checkpoint is missing: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "operation": "binary_column_aggregate",
        "binary_operation": "subtract",
        "aggregate": aggregate,
        "width": width,
        "valid_count": valid_count,
        "input_scale": input_scale,
    }
    mismatches = {
        key: (manifest.get(key), value)
        for key, value in expected.items()
        if manifest.get(key) != value
    }
    if mismatches:
        raise RuntimeError(
            f"checkpoint does not match this selected group: {mismatches}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installments",
        type=Path,
        default=Path("data/home_credit/installments_payments.csv"),
    )
    parser.add_argument("--bridge-dir", type=Path)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--max-ring-dimension", type=int, default=16384)
    parser.add_argument(
        "--openfhe-dir",
        default="/usr/local/lib/OpenFHE",
        help="CMake package directory for the source-built OpenFHE install",
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path(
            "benchmark_runs/payment_diff_checkpoint_example"
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--resume-checkpoints",
        action="store_true",
        help="reuse already-saved SUM/MEAN/VAR branches",
    )
    parser.add_argument(
        "--audit-aggregate-checkpoint",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    # Private child-process mode. OpenFHE stores evaluation keys in process-
    # global maps, so loading a checkpoint into the process that created it
    # produces duplicate-key-tag failures.
    if args.audit_aggregate_checkpoint is not None:
        if args.audit_output is None:
            parser.error("--audit-output is required for isolated audit")
        restored = load_binary_column_aggregate_checkpoint(
            args.audit_aggregate_checkpoint,
            for_audit=True,
        )
        value = restored.program.decrypt(restored.result_ciphertext)
        args.audit_output.parent.mkdir(parents=True, exist_ok=True)
        args.audit_output.write_text(
            json.dumps({"value": value}) + "\n",
            encoding="utf-8",
        )
        return
    if args.bridge_dir is None:
        parser.error("--bridge-dir is required")
    # Client-only post-PSI semi-join and grouping. The HE evaluator receives
    # neither the raw applicant key nor the join mapping.
    layout = prepare_post_psi_groups(
        args.installments.resolve(),
        args.bridge_dir.resolve(),
        group_count=1,
        bucket_size=args.bucket_size,
        minimum_group_size=2,
    )
    group = layout.groups[0]
    raw_applicant_id = layout.private_mapping[0][1]

    # A public scale is part of the CKKS representation contract.
    scale = public_power_of_two_scale(
        [
            abs(installment) + abs(payment)
            for installment, payment in zip(
                group.installment,
                group.payment,
            )
        ]
    )

    # HEIR Python 2026.7.1 is unreliable when one circuit returns a packed
    # SUM/MEAN/VAR tensor. Use one proven scalar-output circuit per branch.
    # Every branch still computes PAYMENT_DIFF after encryption; no plaintext
    # derived column is passed between branches.
    aggregate_root = args.checkpoint_dir.resolve() / "aggregates"
    branch_dirs: dict[str, Path] = {}
    for aggregate in ("sum", "mean", "variance"):
        branch_dir = aggregate_root / aggregate
        branch_dirs[aggregate] = branch_dir
        if args.resume_checkpoints:
            _validate_resumed_branch(
                branch_dir,
                aggregate=aggregate,
                width=args.bucket_size,
                valid_count=group.real_count,
                input_scale=scale,
            )
            print(f"[HEIR] reuse {aggregate} checkpoint: {branch_dir}", flush=True)
            continue
        print(f"[HEIR] compile {aggregate} branch", flush=True)
        ##cyphertext - cyperhtext
        branch = compile_checkpointable_binary_column_aggregate(
            operation="subtract",
            aggregate=aggregate,
            width=args.bucket_size,
            valid_count=group.real_count,
            input_scale=scale,
        )
        print(f"[HEIR] setup/encrypt/evaluate {aggregate} branch", flush=True)
        branch.setup()
        ##cyphertext - cyphertext
        encrypted_parents = branch.encrypt(
            group.installment,
            group.payment,
        )
        encrypted_result = branch.eval(encrypted_parents)
        save_binary_column_aggregate_checkpoint(
            branch,
            encrypted_columns=encrypted_parents,
            result_ciphertext=encrypted_result,
            checkpoint_dir=branch_dir,
            overwrite=args.overwrite,
        )
        print(f"[HEIR] saved {aggregate} checkpoint: {branch_dir}", flush=True)
        del branch, encrypted_parents, encrypted_result

    # Exact MAX needs CKKS-to-FHEW switching. Compile the small MAX runner
    # against the server's source-built OpenFHE installation; no pip
    # ``openfhe`` Python package is required.
    maximum = SourceBuiltOpenFheColumnMax(
        input_scale=scale,
        ring_dimension=args.max_ring_dimension,
        openfhe_dir=args.openfhe_dir,
    )
    maximum_dir = args.checkpoint_dir.resolve() / "maximum"
    if args.resume_checkpoints:
        print(f"[OpenFHE] reuse encrypted MAX checkpoint: {maximum_dir}", flush=True)
        max_result = maximum.load_completed(maximum_dir)
    else:
        print("[OpenFHE] build/run source-installed CKKS-to-FHEW MAX", flush=True)
        max_result = maximum.run_subtract_max(
            group.installment,
            group.payment,
            output_dir=maximum_dir,
            overwrite=args.overwrite,
        )
        print("[OpenFHE] encrypted MAX checkpoint ready", flush=True)

    # Final client audit boundary only. The source-built MAX child decrypted
    # only after maximum.ct was written; the aggregate children now do the
    # same independently so OpenFHE's process-global key maps begin empty.
    payment_diff_sum = _audit_one_checkpoint(branch_dirs["sum"])
    payment_diff_mean = _audit_one_checkpoint(branch_dirs["mean"])
    payment_diff_var = _audit_one_checkpoint(branch_dirs["variance"])
    payment_diff_max = float(max_result["maximum"])
    audit_csv = (
        args.checkpoint_dir.resolve()
        / "client_private"
        / "payment_diff_features.csv"
    )
    audit_csv.parent.mkdir(parents=True, exist_ok=True)
    with audit_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "SK_ID_CURR",
                "PAYMENT_DIFF_MAX",
                "PAYMENT_DIFF_MEAN",
                "PAYMENT_DIFF_SUM",
                "PAYMENT_DIFF_VAR",
            ]
        )
        writer.writerow(
            [
                raw_applicant_id,
                payment_diff_max,
                payment_diff_mean,
                payment_diff_sum,
                payment_diff_var,
            ]
        )
    print(f"aggregate checkpoints: {aggregate_root}")
    print(f"final client features: {audit_csv}")


if __name__ == "__main__":
    main()
