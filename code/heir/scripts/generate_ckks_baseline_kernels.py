#!/usr/bin/env python3
"""Generate the HEIR CKKS kernel set for the standalone baseline benchmark.

The CT+CT and CT-CT kernels use 8192 logical slots and are deliberately
generated once.  Runtime data sizes (1k, 50k, or 1m values) select how many
matching 8192-lane ciphertext chunks the benchmark runner processes; they do
not cause repeated HEIR compilation.

The extended kernels are intentionally smaller defaults for a weak benchmark
host.  They are independent experiments, not part of the credit pipeline.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import sha256_file, write_json
from code.heir.kernels.dot_product import dot_product_mlir
from code.heir.kernels.fixed_count_statistics import (
    fixed_count_mean_mlir,
    fixed_count_sum_mlir,
    fixed_count_sum_squares_mlir,
    fixed_count_variance_mlir,
)
from code.heir.kernels.linear_score import linear_score_mlir
from code.heir.kernels.sum import encrypted_sum_mlir
from code.heir.operations.columns import binary_mlir


@dataclass(frozen=True)
class KernelSpec:
    report_label: str
    entry_function: str
    logical_value_count: int
    source: str
    notes: str


def polynomial_vector_mlir(vector_size: int) -> str:
    """Simple cubic CKKS score over encrypted lanes, using public coefficients."""
    tensor = f"tensor<{vector_size}xf64>"
    return f'''func.func @polynomial_score(
    %values: {tensor} {{secret.secret}}
) -> {tensor} {{
  %c0 = arith.constant dense<0.125> : {tensor}
  %c1 = arith.constant dense<0.5> : {tensor}
  %c2 = arith.constant dense<-0.25> : {tensor}
  %c3 = arith.constant dense<0.1> : {tensor}
  %square = arith.mulf %values, %values : {tensor}
  %cube = arith.mulf %square, %values : {tensor}
  %term1 = arith.mulf %c1, %values : {tensor}
  %term2 = arith.mulf %c2, %square : {tensor}
  %term3 = arith.mulf %c3, %cube : {tensor}
  %first = arith.addf %c0, %term1 : {tensor}
  %second = arith.addf %term2, %term3 : {tensor}
  %result = arith.addf %first, %second : {tensor}
  return %result : {tensor}
}}
'''


def kernel_specs(slot_count: int) -> tuple[KernelSpec, ...]:
    """Return core and scaled-down extended benchmark kernel sources."""
    sum_count = min(slot_count, 1024)
    square_count = min(slot_count, 512)
    variance_count = min(slot_count, 256)
    weighted_count = min(slot_count, 512)
    dot_count = min(slot_count, 512)
    polynomial_count = min(slot_count, 256)
    return (
        KernelSpec("CT+CT", "encrypted_add", slot_count, binary_mlir(slot_count, "add"), "stream 1k/50k/1m aligned values in slot-sized chunks"),
        KernelSpec("CT-CT", "encrypted_subtract", slot_count, binary_mlir(slot_count, "subtract"), "stream 1k/50k/1m aligned values in slot-sized chunks"),
        KernelSpec("CT×CT", "encrypted_multiply", slot_count, binary_mlir(slot_count, "multiply"), "available for the basic arithmetic extension"),
        KernelSpec("CKKS-SUM-01", "encrypted_sum", sum_count, encrypted_sum_mlir(sum_count), "scaled to 1024 lanes by default"),
        KernelSpec("CKKS-MEAN-01", "fixed_count_mean", sum_count, fixed_count_mean_mlir(sum_count, sum_count), "scaled to 1024 lanes by default"),
        KernelSpec("CKKS-SQSUM-01", "fixed_count_sum_squares", square_count, fixed_count_sum_squares_mlir(square_count, square_count), "scaled to 512 lanes by default"),
        KernelSpec("CKKS-VAR-01", "fixed_count_variance", variance_count, fixed_count_variance_mlir(variance_count, variance_count), "scaled to 256 lanes by default"),
        KernelSpec("CKKS-WSUM-01", "linear_score_ct_pt", weighted_count, linear_score_mlir(weighted_count), "scaled to 512 encrypted values with plaintext weights"),
        KernelSpec("CKKS-DOT-01", "dot_product", dot_count, dot_product_mlir(dot_count), "scaled to 512 encrypted/encrypted pairs"),
        KernelSpec("CKKS-POLY-01", "polynomial_score", polynomial_count, polynomial_vector_mlir(polynomial_count), "scaled cubic polynomial over 256 encrypted lanes"),
    )


def markdown_report(manifest: dict[str, object]) -> str:
    """Return a concise generation-only report for the standalone benchmark."""
    kernels = manifest["kernels"]
    generated_labels = {kernel["report_label"] for kernel in kernels}
    def primitive_status(label: str) -> str:
        return "MLIR ready" if label in generated_labels else "not generated in this profile"
    extended = [kernel for kernel in kernels if str(kernel["report_label"]).startswith("CKKS-")]
    rows = "\n".join(
        f"| `{kernel['report_label']}` | `{kernel['logical_value_count']}` | {kernel['generation_status']} |"
        for kernel in extended
    ) or "| — | — | not generated in this profile |"
    return f"""# CKKS baseline generation report

This is a **generation report only**. It records real HEIR MLIR and, when
`--lower` is used, real translated OpenFHE source. It contains no fabricated
latency, accuracy, encryption, or decryption result. Those fields are written
by the later execution runner.

| Setting | Value |
|---|---:|
| Requested ring dimension | {manifest['requested_ring_dimension']} |
| Requested CKKS slots | {manifest['requested_slot_count']} |

## Primitive runtime matrix

Each primitive calculation will later run over 1,000, 50,000, and 1,000,000
aligned real values. A value count above the slot count is streamed in
8192-lane ciphertext chunks; it does not create a larger HEIR kernel.

| Calculation | Runtime value counts | Source status |
|---|---|---|
| `CT+CT` | 1k, 50k, 1m | {primitive_status('CT+CT')} |
| `CT-CT` | 1k, 50k, 1m | {primitive_status('CT-CT')} |
| `CT×CT` | 1k, 50k, 1m | {primitive_status('CT×CT')} |
| `CT+PT` | 1k, 50k, 1m | pending public-operand compatibility probe |
| `CT×PT` | 1k, 50k, 1m | pending public-operand compatibility probe |

## Scaled extended kernels

| Calculation | Logical values per run | Generation status |
|---|---:|---|
{rows}

The smaller defaults are intentional for the current weak server. Runtime
execution will compare Python/NumPy calculation latency with HEIR/OpenFHE
latency and apply the requested `1e-6` maximum-absolute-error acceptance rule.
"""


def run(command: list[str], output: Path) -> float:
    started = time.perf_counter()
    with output.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{completed.stderr}")
    return time.perf_counter() - started


def generate(
    output_dir: Path,
    *,
    slot_count: int,
    ciphertext_degree: int,
    lower: bool,
    heir_opt: str,
    heir_translate: str,
    profile: str,
    entries: tuple[str, ...],
) -> dict[str, object]:
    if slot_count <= 0 or ciphertext_degree < 2 * slot_count:
        raise ValueError("ciphertext_degree must be at least twice the CKKS slot_count")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    output_dir.mkdir(parents=True)
    all_specs = kernel_specs(slot_count)
    if profile == "primitives":
        specs = all_specs[:3]
    elif profile == "extended":
        specs = all_specs[3:]
    else:
        specs = all_specs
    if entries:
        requested = set(entries)
        specs = tuple(spec for spec in specs if spec.entry_function in requested)
        missing = requested - {spec.entry_function for spec in specs}
        if missing:
            raise ValueError(f"requested entries are unavailable for profile {profile!r}: {sorted(missing)}")
    kernels: list[dict[str, object]] = []
    for index, spec in enumerate(specs):
        directory = output_dir / f"{index:02d}_{spec.entry_function}"
        directory.mkdir()
        source = directory / "source.mlir"
        source.write_text(spec.source, encoding="utf-8")
        artifact: dict[str, object] = {
            "report_label": spec.report_label,
            "entry_function": spec.entry_function,
            "logical_value_count": spec.logical_value_count,
            "notes": spec.notes,
            "source": str(source.relative_to(output_dir)),
            "source_sha256": sha256_file(source),
            "generation_status": "MLIR ready",
        }
        if lower:
            lowered = directory / "lowered_openfhe.mlir"
            header = directory / "heir_output.h"
            cpp = directory / "heir_output.cpp"
            lower_seconds = run(
                [
                    heir_opt,
                    f"--mlir-to-ckks=ciphertext-degree={ciphertext_degree}",
                    f"--scheme-to-openfhe=entry-function={spec.entry_function}",
                    str(source.resolve()),
                ],
                lowered,
            )
            header_seconds = run(
                [heir_translate, "--emit-openfhe-pke-header", "--openfhe-include-type=install-relative", str(lowered.resolve())],
                header,
            )
            cpp_seconds = run(
                [heir_translate, "--emit-openfhe-pke", "--openfhe-include-type=install-relative", str(lowered.resolve())],
                cpp,
            )
            artifact.update(
                {
                    "generation_status": "HEIR lowered and translated",
                    "lowered": str(lowered.relative_to(output_dir)),
                    "header": str(header.relative_to(output_dir)),
                    "cpp": str(cpp.relative_to(output_dir)),
                    "lowered_sha256": sha256_file(lowered),
                    "header_sha256": sha256_file(header),
                    "cpp_sha256": sha256_file(cpp),
                    "generation_seconds": {
                        "lower": lower_seconds,
                        "translate_header": header_seconds,
                        "translate_cpp": cpp_seconds,
                    },
                }
            )
        kernels.append(artifact)
    manifest = {
        "status": "ckks_baseline_kernel_sources_ready",
        "scheme": "CKKS",
        "requested_ring_dimension": ciphertext_degree,
        "requested_slot_count": slot_count,
        "core_runtime_value_counts": [1_000, 50_000, 1_000_000],
        "core_note": "CT+CT and CT-CT reuse one 8192-lane kernel; value count determines streamed ciphertext chunks at runtime.",
        "extended_note": "Extended kernels use deliberately smaller one-ciphertext defaults for the weak server. Each default is recorded per kernel.",
        "generation_profile": profile,
        "kernels": kernels,
    }
    write_json(output_dir / "generation_manifest.json", manifest)
    (output_dir / "REPORT.md").write_text(markdown_report(manifest), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--slot-count", type=int, default=8192)
    parser.add_argument("--ciphertext-degree", type=int, default=16384)
    parser.add_argument("--lower", action="store_true", help="run heir-opt and heir-translate after writing MLIR")
    parser.add_argument("--profile", choices=("primitives", "extended", "all"), default="all")
    parser.add_argument("--entries", nargs="*", default=[], help="optional entry functions to generate, e.g. encrypted_sum")
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists() and args.overwrite:
        shutil.rmtree(root)
    report = generate(
        root,
        slot_count=args.slot_count,
        ciphertext_degree=args.ciphertext_degree,
        lower=args.lower,
        heir_opt=args.heir_opt,
        heir_translate=args.heir_translate,
        profile=args.profile,
        entries=tuple(args.entries),
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
