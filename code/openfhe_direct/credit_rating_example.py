#!/usr/bin/env python3
"""Small planned OpenFHE-Python PAYMENT_DIFF example."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct import HEWorkflow
from code.openfhe_direct.prepared_data import load_prepared_group


def build_workflow() -> HEWorkflow:
    """Front end: describe business calculations without HE parameters."""
    workflow = HEWorkflow()
    installment = workflow.input("AMT_INSTALMENT")
    payment = workflow.input("AMT_PAYMENT")

    payment_diff = workflow.subtract(installment, payment)
    workflow.output("PAYMENT_DIFF", payment_diff)
    workflow.output("PAYMENT_DIFF_SUM", workflow.sum(payment_diff))
    workflow.output("PAYMENT_DIFF_MEAN", workflow.mean(payment_diff))
    workflow.output("PAYMENT_DIFF_VAR", workflow.variance(payment_diff))
    return workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-group", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    group = load_prepared_group(args.prepared_group.resolve())

    # Back end: inspect the complete DAG, choose depth/keys, then create one
    # OpenFHE context before any value is encrypted.
    runtime = build_workflow().compile(slot_count=group.slot_count)

    encrypted_inputs = runtime.encrypt_inputs(
        {
            "AMT_INSTALMENT": group.installment,
            "AMT_PAYMENT": group.payment,
        }
    )
    encrypted_outputs = runtime.evaluate(encrypted_inputs)

    # Explicit final audit boundary. No intermediate result was decrypted.
    result = {
        "applicant_id": group.applicant_id,
        "physical_plan": runtime.physical_plan.as_dict(),
        "audit": {
            name: runtime.decrypt(ciphertext)
            for name, ciphertext in encrypted_outputs.items()
        },
    }
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(output)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
