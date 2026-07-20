#!/usr/bin/env python3
"""Generate CKKS/OpenFHE C++ for the three source-derived DAG kernels."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import sha256_file, write_json
from code.heir.kernels.difference_moments import difference_moments_mlir
from code.heir.kernels.dot_product import dot_product_mlir
from code.heir.kernels.moments import moments_mlir


KERNELS = {
    "K01": ("dot_product", dot_product_mlir),
    "K02": ("moments", moments_mlir),
    "K03": ("difference_moments", difference_moments_mlir),
}


def _run(command: list[str], output_path: Path) -> float:
    started = time.perf_counter()
    with output_path.open("w", encoding="utf-8") as output:
        completed = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"command failed: {' '.join(command)}\n{completed.stderr}"
        )
    return time.perf_counter() - started


def generate(
    output_root: Path,
    vector_size: int,
    heir_opt: str,
    heir_translate: str,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite generated root: {output_root}")
    output_root.mkdir(parents=True)
    records = []
    for kernel_id, (entry, builder) in KERNELS.items():
        directory = output_root / kernel_id
        directory.mkdir()
        source = directory / "source.mlir"
        lowered = directory / "lowered_openfhe.mlir"
        header = directory / "heir_output.h"
        cpp = directory / "heir_output.cpp"
        source.write_text(builder(vector_size), encoding="utf-8")
        lower_seconds = _run(
            [
                heir_opt,
                # Keep every logical vector in one CKKS ciphertext.  HEIR's
                # default ciphertext degree is smaller than the 8,192-slot
                # workload and otherwise splits it into incompatible chunks
                # during a scalar reduction.
                f"--mlir-to-ckks=ciphertext-degree={vector_size}",
                f"--scheme-to-openfhe=entry-function={entry}",
                str(source),
            ],
            lowered,
        )
        header_seconds = _run(
            [
                heir_translate,
                "--emit-openfhe-pke-header",
                "--openfhe-include-type=install-relative",
                str(lowered),
            ],
            header,
        )
        cpp_seconds = _run(
            [
                heir_translate,
                "--emit-openfhe-pke",
                "--openfhe-include-type=install-relative",
                str(lowered),
            ],
            cpp,
        )
        generated_text = header.read_text(encoding="utf-8", errors="replace")
        generated_text += cpp.read_text(encoding="utf-8", errors="replace")
        if "CKKS" not in generated_text and "CryptoContextCKKSRNS" not in generated_text:
            raise ValueError(f"{kernel_id} generated source is not identifiable as CKKS")
        records.append(
            {
                "kernel_id": kernel_id,
                "entry_function": entry,
                "source_sha256": sha256_file(source),
                "lowered_sha256": sha256_file(lowered),
                "header_sha256": sha256_file(header),
                "cpp_sha256": sha256_file(cpp),
                "timings_seconds": {
                    "lower_to_openfhe": lower_seconds,
                    "translate_header": header_seconds,
                    "translate_cpp": cpp_seconds,
                },
            }
        )
    manifest: dict[str, object] = {
        "status": "heir_generated_ckks_sources_ready",
        "scheme": "CKKS",
        "vector_size": vector_size,
        "ciphertext_degree": vector_size,
        "kernels": records,
    }
    write_json(output_root / "generation_manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = generate(
        args.output_root, args.vector_size, args.heir_opt, args.heir_translate
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
