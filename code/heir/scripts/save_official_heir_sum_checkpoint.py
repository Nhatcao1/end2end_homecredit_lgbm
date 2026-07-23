#!/usr/bin/env python3
"""Create an official HEIR CKKS SUM checkpoint without decrypting it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api import (
    compile_checkpointable_sum,
    save_sum_checkpoint,
)


DEFAULT_VALUES = [160.0, -100.0, 0.0, 60.0, 250.0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--values", nargs="+", type=float, default=DEFAULT_VALUES)
    parser.add_argument("--width", type=int, default=8)
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("benchmark_runs/official_heir_sum_checkpoint"),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    values = [float(value) for value in args.values]
    if not 2 <= len(values) <= args.width:
        parser.error("provide between two and --width values")

    started = time.perf_counter()
    program = compile_checkpointable_sum(
        width=args.width,
        valid_count=len(values),
        debug=args.debug,
    )
    compile_seconds = time.perf_counter() - started
    started = time.perf_counter()
    program.setup()
    setup_seconds = time.perf_counter() - started
    started = time.perf_counter()
    input_ct = program.encrypt(values)
    encrypt_seconds = time.perf_counter() - started
    started = time.perf_counter()
    result_ct = program.eval(input_ct)
    evaluation_seconds = time.perf_counter() - started
    started = time.perf_counter()
    manifest = save_sum_checkpoint(
        program,
        input_ciphertext=input_ct,
        result_ciphertext=result_ct,
        checkpoint_dir=args.checkpoint_dir,
        overwrite=args.overwrite,
    )
    save_seconds = time.perf_counter() - started

    private = args.checkpoint_dir.resolve() / "client_private"
    private.mkdir(parents=True, exist_ok=True)
    reference = {"values": values, "python_sum": sum(values)}
    (private / "reference.json").write_text(
        json.dumps(reference, indent=2) + "\n",
        encoding="utf-8",
    )
    (private / "reference.json").chmod(0o600)
    result = {
        "status": "official_heir_sum_checkpoint_saved",
        "checkpoint_dir": str(args.checkpoint_dir.resolve()),
        "compile_seconds": compile_seconds,
        "setup_seconds": setup_seconds,
        "encrypt_seconds": encrypt_seconds,
        "evaluation_seconds": evaluation_seconds,
        "checkpoint_save_seconds": save_seconds,
        "decryption_performed": False,
        "format": manifest["format"],
    }
    (args.checkpoint_dir.resolve() / "SAVE.json").write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
