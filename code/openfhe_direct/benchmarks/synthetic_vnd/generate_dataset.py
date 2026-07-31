#!/usr/bin/env python3
"""Generate deterministic synthetic VND vectors for an encrypted SUM test."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_dataset(
    *,
    output_dir: Path,
    value_counts: list[int],
    minimum_value: int,
    maximum_value: int,
    seed: int,
    overwrite: bool,
) -> dict[str, object]:
    """Write one deterministic integer vector per requested length."""
    if not value_counts or any(count < 1 for count in value_counts):
        raise ValueError("value_counts must be positive")
    if len(set(value_counts)) != len(value_counts):
        raise ValueError("value_counts must be unique")
    if minimum_value >= maximum_value:
        raise ValueError("minimum_value must be below maximum_value")

    root = output_dir.resolve()
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(f"refusing to remove broad path: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    # Generate one deterministic master sequence. Smaller files are prefixes
    # of larger files, so changing vector length does not change its prefix.
    maximum_count = max(value_counts)
    generator = random.Random(seed)
    values = [
        generator.randint(minimum_value, maximum_value)
        for _ in range(maximum_count)
    ]

    files: list[dict[str, object]] = []
    for count in value_counts:
        path = root / f"vnd_values_{count}.csv"
        selected = values[:count]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["INDEX", "VALUE"])
            writer.writerows(
                (index, value)
                for index, value in enumerate(selected)
            )
        files.append(
            {
                "file": path.name,
                "value_count": count,
                "sha256": _sha256(path),
                "observed_value_range": [min(selected), max(selected)],
                "plaintext_vector_sum": sum(selected),
            }
        )

    manifest = {
        "status": "synthetic_vnd_vectors_ready",
        "purpose": "shared synthetic input for BGV and CKKS SUM benchmarks",
        "seed": seed,
        "configured_value_range_inclusive": [
            minimum_value,
            maximum_value,
        ],
        "value_counts": value_counts,
        "prefix_consistent": True,
        "files": files,
    }
    (root / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--value-count",
        dest="value_counts",
        nargs="+",
        type=int,
        required=True,
    )
    parser.add_argument("--minimum-value", type=int, default=10_000_000)
    parser.add_argument("--maximum-value", type=int, default=200_000_000)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = generate_dataset(
        output_dir=args.output_dir,
        value_counts=args.value_counts,
        minimum_value=args.minimum_value,
        maximum_value=args.maximum_value,
        seed=args.seed,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
