#!/usr/bin/env python3
"""Minimal real-data PAYMENT_DIFF application using the simple HE API.

This is intentionally not a benchmark. It contains no timers, repetitions,
performance reports, or generated benchmark tables.

Flow:

```
installments CSV
→ client selects one complete allowed group
→ encrypt AMT_INSTALMENT and AMT_PAYMENT
→ PAYMENT_DIFF.ct = installment.ct - payment.ct
→ encrypted SUM / MEAN / VAR / MIN / MAX
→ final client decryption
```
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    SourceBuiltCkksSession,
    load_prepared_allowed_group,
    prepare_allowed_group_csv,
    public_power_of_two_scale,
)


def plaintext_reference(values: list[float]) -> dict[str, float]:
    """Return the equivalent non-HE result for the final correctness audit."""
    return {
        "sum": float(sum(values)),
        "mean": float(statistics.fmean(values)),
        "variance": float(statistics.variance(values)),
        "minimum": float(min(values)),
        "maximum": float(max(values)),
    }


def save_parent_checkpoint(args: argparse.Namespace) -> None:
    if not args.allowed_sk_id_curr:
        raise ValueError("--allowed-sk-id-curr is required for save")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(
                f"refusing to overwrite application output: {root}"
            )
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    client_private = root / "client_private"
    client_private.mkdir(parents=True)

    prepared = prepare_allowed_group_csv(
        args.installments.resolve(),
        allowed_sk_id_curr=args.allowed_sk_id_curr,
        bucket_size=0,
        maximum_width=args.ring_dimension // 2,
        output_csv=client_private / "prepared_group.csv",
        overwrite=False,
    )
    group = prepared.group
    if group.real_count < 2:
        raise ValueError("SUM/MEAN/VAR/MIN/MAX require at least two rows")

    # This is public representation metadata, not a plaintext feature
    # calculation. Doubling the parent-only scale guarantees that subtracting
    # two normalized parents still fits CKKS/FHEW's comparison interval.
    input_scale = 2.0 * public_power_of_two_scale(
        [*group.installment, *group.payment]
    )
    he = SourceBuiltCkksSession.create(
        checkpoint_dir=root / "encrypted_session",
        width=prepared.bucket_size,
        input_scale=input_scale,
        ring_dimension=args.ring_dimension,
        openfhe_dir=args.openfhe_dir,
        overwrite=False,
    )
    he.encrypt_column(group.installment, name="AMT_INSTALMENT")
    he.encrypt_column(group.payment, name="AMT_PAYMENT")
    application = {
        "status": "parent_ciphertext_checkpoint_ready",
        "allowed_sk_id_curr": str(args.allowed_sk_id_curr),
        "real_rows": group.real_count,
        "encrypted_width": prepared.bucket_size,
        "input_scale": input_scale,
        "session": "encrypted_session",
        "parent_ciphertexts": [
            "AMT_INSTALMENT",
            "AMT_PAYMENT",
        ],
    }
    (root / "application.json").write_text(
        json.dumps(application, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(application, indent=2))


def evaluate_loaded_checkpoint(args: argparse.Namespace) -> None:
    root = args.output_dir.resolve()
    he = SourceBuiltCkksSession.load(root / "encrypted_session")
    installment_ct = he.load_column("AMT_INSTALMENT")
    payment_ct = he.load_column("AMT_PAYMENT")

    # No plaintext feature is calculated here.
    payment_diff_ct = he.subtract(installment_ct, payment_ct)

    encrypted_outputs = {
        "sum": he.sum(payment_diff_ct),
        "mean": he.mean(payment_diff_ct),
        "variance": he.variance(payment_diff_ct),
        "minimum": he.minimum(payment_diff_ct),
        "maximum": he.maximum(payment_diff_ct),
    }

    # This is the only decryption boundary.
    final_audit = {
        name: he.decrypt_scalar(ciphertext)
        for name, ciphertext in encrypted_outputs.items()
    }

    # Only after every encrypted result has reached the final decrypt boundary
    # does the client reopen plaintext for a correctness comparison.
    client_private = root / "client_private"
    prepared = load_prepared_allowed_group(
        client_private / "prepared_group.csv"
    )
    expected_values = [
        installment - payment
        for installment, payment in zip(
            prepared.group.installment,
            prepared.group.payment,
        )
    ]
    reference = plaintext_reference(expected_values)
    comparison = {
        name: {
            "plaintext_reference": reference[name],
            "final_he_audit": final_audit[name],
            "absolute_error": abs(reference[name] - final_audit[name]),
        }
        for name in reference
    }
    if not all(
        math.isfinite(row["final_he_audit"])
        for row in comparison.values()
    ):
        raise RuntimeError("final HE audit contains a non-finite value")

    result = {
        "status": "payment_diff_simple_api_executed",
        "allowed_sk_id_curr": prepared.raw_applicant_id,
        "real_rows": prepared.group.real_count,
        "encrypted_width": prepared.bucket_size,
        "input_scale": he.input_scale,
        "backend": "source-built OpenFHE C++",
        "parent_ciphertexts_reloaded_in_fresh_process": True,
        "same_openfhe_context": True,
        "no_intermediate_decryption": True,
        "expression": "AMT_INSTALMENT.ct - AMT_PAYMENT.ct",
        "outputs": comparison,
    }
    result_path = client_private / "final_audit.json"
    result_path.write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    print(f"final client audit: {result_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("save", "evaluate", "roundtrip"),
        default="roundtrip",
    )
    parser.add_argument(
        "--installments",
        type=Path,
        default=Path("data/home_credit/installments_payments.csv"),
    )
    parser.add_argument("--allowed-sk-id-curr")
    parser.add_argument("--ring-dimension", type=int, default=16384)
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_runs/payment_diff_simple_api_e2e"),
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.stage == "save":
        save_parent_checkpoint(args)
        return
    if args.stage == "evaluate":
        evaluate_loaded_checkpoint(args)
        return
    if not args.allowed_sk_id_curr:
        parser.error("--allowed-sk-id-curr is required for roundtrip")

    common = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--output-dir",
        str(args.output_dir.resolve()),
        "--openfhe-dir",
        args.openfhe_dir,
    ]
    save_command = [
        *common,
        "--stage",
        "save",
        "--installments",
        str(args.installments.resolve()),
        "--allowed-sk-id-curr",
        str(args.allowed_sk_id_curr),
        "--ring-dimension",
        str(args.ring_dimension),
    ]
    if args.overwrite:
        save_command.append("--overwrite")
    subprocess.run(save_command, cwd=ROOT, check=True)

    # This is intentionally a second Python process. It receives no plaintext
    # parent arrays and reloads both ciphertexts from the session checkpoint.
    subprocess.run(
        [*common, "--stage", "evaluate"],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
