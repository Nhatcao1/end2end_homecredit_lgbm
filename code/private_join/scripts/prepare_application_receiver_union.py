#!/usr/bin/env python3
"""Create the receiver application universe used by the original notebook.

The original Home Credit feature code reads ``application_train`` and
``application_test`` together before it left-joins history features.  PSI must
therefore receive that same receiver key universe, not train alone.

This is deliberately client-side preparation: it writes only ``SK_ID_CURR``
and ``TARGET`` (blank for test rows).  It is an input to PSI, not an HE step.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import sha256_file, write_json


KEY = "SK_ID_CURR"
TARGET = "TARGET"


def _read_rows(path: Path, *, is_train: bool) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if KEY not in (reader.fieldnames or []):
            raise ValueError(f"{path} is missing {KEY}")
        if is_train and TARGET not in (reader.fieldnames or []):
            raise ValueError(f"{path} is missing {TARGET}")
        for line, row in enumerate(reader, start=2):
            key = (row.get(KEY) or "").strip()
            if not key:
                raise ValueError(f"{path}:{line} has empty {KEY}")
            rows.append({KEY: key, TARGET: (row.get(TARGET) or "").strip() if is_train else ""})
    return rows


def prepare(application_train: Path, application_test: Path, output: Path) -> dict[str, object]:
    """Write a unique train/test receiver universe with blank test TARGET."""
    train_rows = _read_rows(application_train, is_train=True)
    test_rows = _read_rows(application_test, is_train=False)
    seen: set[str] = set()
    for source_name, rows in (("application_train", train_rows), ("application_test", test_rows)):
        for row in rows:
            if row[KEY] in seen:
                raise ValueError(f"{source_name} duplicates/collides on {KEY}={row[KEY]}")
            seen.add(row[KEY])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[KEY, TARGET])
        writer.writeheader()
        writer.writerows([*train_rows, *test_rows])
    return {
        "status": "client_receiver_union_ready",
        "rows": {"application_train": len(train_rows), "application_test": len(test_rows), "union": len(seen)},
        "output": str(output),
        "sha256": sha256_file(output),
        "privacy_note": "client-side PSI input; TARGET must not be included in the PSI input or sender exchange",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-train", type=Path, required=True)
    parser.add_argument("--application-test", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/psi/receiver/application_train_test_union.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("data/psi/receiver/application_train_test_union_manifest.json"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    output, manifest = args.output.resolve(), args.manifest.resolve()
    if output.exists() or manifest.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite {output} or {manifest}; pass --overwrite")
        for path in (output, manifest):
            if path.exists():
                path.unlink()
    result = prepare(args.application_train.resolve(), args.application_test.resolve(), output)
    write_json(manifest, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
