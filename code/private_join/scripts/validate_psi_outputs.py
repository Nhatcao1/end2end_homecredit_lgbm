#!/usr/bin/env python3
"""Validate matching ordered outputs produced by both SecretFlow PSI parties."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.private_join.secretflow_adapter import validate_secretflow_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receiver-output",
        type=Path,
        default=Path("data/psi/receiver/psi_output.csv"),
    )
    parser.add_argument(
        "--sender-output",
        type=Path,
        default=Path("data/psi/sender/psi_output.csv"),
    )
    parser.add_argument("--key", default="SK_ID_CURR")
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("data/psi/psi_output_audit.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = validate_secretflow_outputs(
        args.receiver_output, args.sender_output, args.audit, args.key
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
