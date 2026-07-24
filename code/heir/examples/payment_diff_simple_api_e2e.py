#!/usr/bin/env python3
"""Minimal real-data PAYMENT_DIFF application using the simple HE API.

This is intentionally not a benchmark. It contains no timers, repetitions,
performance reports, or generated benchmark tables.

Flow:

```
installments CSV
→ client selects one complete allowed group
→ encrypt AMT_INSTALMENT and AMT_PAYMENT
→ ADD.ct = installment.ct + payment.ct
→ PAYMENT_DIFF.ct = installment.ct - payment.ct
→ MULTIPLY.ct = installment.ct × payment.ct
→ encrypted SUM / MEAN / VAR / MIN / MAX
→ final client decryption
```
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    SourceBuiltCkksSession,
    prepare_allowed_group_csv,
    public_power_of_two_scale,
)


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

    # Each API call receives two ciphertext handles and returns another
    # ciphertext handle. No parent plaintext is loaded in this process.
    added_ct = he.add(installment_ct, payment_ct)
    payment_diff_ct = he.subtract(installment_ct, payment_ct)
    multiplied_ct = he.multiply(installment_ct, payment_ct)

    aggregate_ct = {
        "sum": he.sum(payment_diff_ct),
        "mean": he.mean(payment_diff_ct),
        "variance": he.variance(payment_diff_ct),
        "minimum": he.minimum(payment_diff_ct),
        "maximum": he.maximum(payment_diff_ct),
    }

    # This is the only decryption boundary.
    decrypted_outputs = {
        "columns": {
            "AMT_INSTALMENT_PLUS_AMT_PAYMENT": he.decrypt_column(added_ct),
            "PAYMENT_DIFF": he.decrypt_column(payment_diff_ct),
            "AMT_INSTALMENT_TIMES_AMT_PAYMENT": he.decrypt_column(
                multiplied_ct
            ),
        },
        "payment_diff_aggregates": {
            name: he.decrypt_scalar(ciphertext)
            for name, ciphertext in aggregate_ct.items()
        },
    }

    application = json.loads(
        (root / "application.json").read_text(encoding="utf-8")
    )

    result = {
        "status": "payment_diff_simple_api_executed",
        "allowed_sk_id_curr": application["allowed_sk_id_curr"],
        "real_rows": application["real_rows"],
        "encrypted_width": application["encrypted_width"],
        "input_scale": he.input_scale,
        "backend": "source-built OpenFHE C++",
        "parent_ciphertexts_reloaded_in_fresh_process": True,
        "same_openfhe_context": True,
        "no_intermediate_decryption": True,
        "ciphertext_operations": {
            "add": "AMT_INSTALMENT.ct + AMT_PAYMENT.ct",
            "subtract": "AMT_INSTALMENT.ct - AMT_PAYMENT.ct",
            "multiply": "AMT_INSTALMENT.ct * AMT_PAYMENT.ct",
            "aggregates": "SUM/MEAN/VAR/MIN/MAX(PAYMENT_DIFF.ct)",
        },
        "decrypted_outputs": decrypted_outputs,
    }
    result_path = root / "client_private/decrypted_outputs.json"
    result_path.write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    print(f"final decrypted output: {result_path}")


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
