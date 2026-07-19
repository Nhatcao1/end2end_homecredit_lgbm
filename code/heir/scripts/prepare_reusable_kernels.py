"""Create reviewable artifacts for every reusable non-tree HEIR kernel."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.kernel_artifacts import build_kernel_artifacts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_runs/reusable_kernels/review_v1"),
    )
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--polynomial-degree", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_kernel_artifacts(
        args.output_dir, args.vector_size, args.polynomial_degree
    )
    print(f"wrote {len(manifest['kernels'])} reusable kernels to {args.output_dir}")
    print("status: MLIR and plaintext oracle only; generated CKKS was not executed")


if __name__ == "__main__":
    main()
