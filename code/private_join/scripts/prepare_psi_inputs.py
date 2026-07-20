#!/usr/bin/env python3
"""Prepare receiver and sender identifier-only CSV files for SecretFlow PSI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.private_join.secretflow_adapter import prepare_secretflow_inputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver-source", type=Path, required=True)
    parser.add_argument("--sender-source", type=Path, required=True)
    parser.add_argument("--key", default="SK_ID_CURR")
    parser.add_argument(
        "--receiver-output",
        type=Path,
        default=Path("data/psi/receiver/psi_input.csv"),
    )
    parser.add_argument(
        "--sender-output",
        type=Path,
        default=Path("data/psi/sender/psi_input.csv"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/psi/psi_input_manifest.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = prepare_secretflow_inputs(
        args.receiver_source,
        args.sender_source,
        args.receiver_output,
        args.sender_output,
        args.manifest,
        args.key,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
