#!/usr/bin/env python3
"""Generate deterministic synthetic VND pairs for an isolated CT-CT test."""

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
    row_counts: list[int],
    minimum_value: int,
    maximum_value: int,
    seed: int,
    overwrite: bool,
) -> dict[str, object]:
    """Write one aligned LEFT/RIGHT CSV for every requested row count."""
    if not row_counts or any(count < 1 for count in row_counts):
        raise ValueError("row_counts must be positive")
    if len(set(row_counts)) != len(row_counts):
        raise ValueError("row_counts must be unique")
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
    # of larger files, so row-count scaling does not change the first rows.
    maximum_count = max(row_counts)
    generator = random.Random(seed)
    pairs = [
        (
            generator.randint(minimum_value, maximum_value),
            generator.randint(minimum_value, maximum_value),
        )
        for _ in range(maximum_count)
    ]

    files: list[dict[str, object]] = []
    for count in row_counts:
        path = root / f"vnd_pairs_{count}.csv"
        selected = pairs[:count]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ROW_ID", "LEFT_VALUE", "RIGHT_VALUE"])
            writer.writerows(
                (row_id, left, right)
                for row_id, (left, right) in enumerate(selected)
            )
        files.append(
            {
                "file": path.name,
                "row_count": count,
                "sha256": _sha256(path),
                "observed_left_range": [
                    min(left for left, _ in selected),
                    max(left for left, _ in selected),
                ],
                "observed_right_range": [
                    min(right for _, right in selected),
                    max(right for _, right in selected),
                ],
            }
        )

    manifest = {
        "status": "synthetic_vnd_pairs_ready",
        "purpose": "isolated CT-CT latency and accuracy benchmark",
        "seed": seed,
        "configured_value_range_inclusive": [
            minimum_value,
            maximum_value,
        ],
        "row_counts": row_counts,
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
        "--row-count",
        dest="row_counts",
        nargs="+",
        type=int,
        required=True,
    )
    parser.add_argument("--minimum-value", type=int, default=1_000_000)
    parser.add_argument("--maximum-value", type=int, default=100_000_000)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    result = generate_dataset(
        output_dir=args.output_dir,
        row_counts=args.row_counts,
        minimum_value=args.minimum_value,
        maximum_value=args.maximum_value,
        seed=args.seed,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
