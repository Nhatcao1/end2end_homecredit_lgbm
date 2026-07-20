#!/usr/bin/env python3
"""Generate one function-agnostic HEIR CKKS binary-column operation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import sha256_file, write_json
from code.heir.operations.columns import binary_mlir


def _run(command: list[str], output: Path) -> float:
    started = time.perf_counter()
    with output.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{completed.stderr}")
    return time.perf_counter() - started


def generate(output_dir: Path, operation: str, vector_size: int, heir_opt: str, heir_translate: str) -> dict[str, object]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite generated directory: {output_dir}")
    output_dir.mkdir(parents=True)
    entry = f"encrypted_{operation}"
    source = output_dir / "source.mlir"
    lowered = output_dir / "lowered_openfhe.mlir"
    header = output_dir / "heir_output.h"
    cpp = output_dir / "heir_output.cpp"
    source.write_text(binary_mlir(vector_size, operation), encoding="utf-8")
    lower_seconds = _run(
        [heir_opt, f"--mlir-to-ckks=ciphertext-degree={vector_size}", f"--scheme-to-openfhe=entry-function={entry}", str(source)],
        lowered,
    )
    header_seconds = _run(
        [heir_translate, "--emit-openfhe-pke-header", "--openfhe-include-type=install-relative", str(lowered)], header
    )
    cpp_seconds = _run(
        [heir_translate, "--emit-openfhe-pke", "--openfhe-include-type=install-relative", str(lowered)], cpp
    )
    manifest: dict[str, object] = {
        "status": "heir_generated_ckks_sources_ready",
        "operation": operation,
        "entry_function": entry,
        "scheme": "CKKS",
        "vector_size": vector_size,
        "ciphertext_degree": vector_size,
        "source_sha256": sha256_file(source),
        "lowered_sha256": sha256_file(lowered),
        "header_sha256": sha256_file(header),
        "cpp_sha256": sha256_file(cpp),
        "timings_seconds": {"lower_to_openfhe": lower_seconds, "translate_header": header_seconds, "translate_cpp": cpp_seconds},
    }
    write_json(output_dir / "generation_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--operation", choices=("add", "subtract", "multiply"), required=True)
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    args = parser.parse_args()
    print(json.dumps(generate(args.output_dir, args.operation, args.vector_size, args.heir_opt, args.heir_translate), indent=2))


if __name__ == "__main__":
    main()
