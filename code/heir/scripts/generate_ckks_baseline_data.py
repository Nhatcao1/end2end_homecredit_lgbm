#!/usr/bin/env python3
"""Generate deterministic real-number pairs for the CKKS baseline benchmark."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import sha256_file, write_json


RANGES = {
    "add_sub": (-40_000, 40_000),
    "multiply": (-100, 100),
}


def seed_for(base_seed: int, workload: str, value_count: int, decimals: int) -> int:
    material = f"{base_seed}:{workload}:{value_count}:{decimals}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def generate_pair_file(
    path: Path,
    *,
    value_count: int,
    decimals: int,
    lower: int,
    upper: int,
    seed: int,
) -> dict[str, object]:
    """Write exact decimal-grid values as two aligned CSV columns."""
    scale = 10**decimals
    rng = random.Random(seed)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["left", "right"])
        for _ in range(value_count):
            left = rng.randint(lower * scale, upper * scale)
            right = rng.randint(lower * scale, upper * scale)
            writer.writerow([f"{left / scale:.{decimals}f}", f"{right / scale:.{decimals}f}"])
    return {
        "file": path.name,
        "value_count": value_count,
        "decimals": decimals,
        "range": [lower, upper],
        "seed": seed,
        "sha256": sha256_file(path),
    }


def generate(
    output_dir: Path,
    *,
    value_counts: tuple[int, ...],
    decimal_places: tuple[int, ...],
    base_seed: int,
) -> dict[str, object]:
    if not value_counts or any(value <= 0 for value in value_counts):
        raise ValueError("value counts must be positive")
    if not decimal_places or any(value < 0 for value in decimal_places):
        raise ValueError("decimal places must be non-negative")
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    output_dir.mkdir(parents=True)
    files: dict[str, list[dict[str, object]]] = {name: [] for name in RANGES}
    for workload, (lower, upper) in RANGES.items():
        for count in value_counts:
            for decimals in decimal_places:
                filename = f"{workload}_{count}_{decimals}dp.csv"
                files[workload].append(
                    generate_pair_file(
                        output_dir / filename,
                        value_count=count,
                        decimals=decimals,
                        lower=lower,
                        upper=upper,
                        seed=seed_for(base_seed, workload, count, decimals),
                    )
                )
    manifest = {
        "status": "deterministic_ckks_baseline_data_ready",
        "base_seed": base_seed,
        "value_counts": list(value_counts),
        "decimal_places": list(decimal_places),
        "datasets": files,
        "packing_note": "The later CKKS runner packs each aligned left/right pair into 8192-slot ciphertext chunks and zero-pads only the final chunk.",
    }
    write_json(output_dir / "dataset_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--value-counts", type=int, nargs="+", default=[1_000, 50_000, 1_000_000])
    parser.add_argument("--decimal-places", type=int, nargs="+", default=[1, 2, 3, 6])
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists() and args.overwrite:
        shutil.rmtree(root)
    result = generate(
        root,
        value_counts=tuple(args.value_counts),
        decimal_places=tuple(args.decimal_places),
        base_seed=args.seed,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
