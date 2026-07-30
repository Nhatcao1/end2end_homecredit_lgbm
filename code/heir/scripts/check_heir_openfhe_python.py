#!/usr/bin/env python3
"""Verify the isolated HEIR plus OpenFHE Python benchmark environment."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.python_api.backend_policy import backend_manifest


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError as error:
        raise RuntimeError(f"required package is missing: {name}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-heir", default="2026.7.1")
    parser.add_argument("--expected-openfhe", required=True)
    args = parser.parse_args()

    from heir import compile as heir_compile
    import openfhe

    heir_version = package_version("heir_py")
    openfhe_version = package_version("openfhe")
    if heir_version != args.expected_heir:
        raise RuntimeError(
            f"HEIR version mismatch: {heir_version} != "
            f"{args.expected_heir}"
        )
    if openfhe_version != args.expected_openfhe:
        raise RuntimeError(
            f"OpenFHE Python version mismatch: {openfhe_version} != "
            f"{args.expected_openfhe}"
        )

    result = {
        "status": "heir_openfhe_python_ready",
        "python": sys.version.split()[0],
        "heir_py": heir_version,
        "openfhe_python": openfhe_version,
        "imports": {
            "heir.compile": callable(heir_compile),
            "openfhe": bool(openfhe),
        },
        "calculation_backends": backend_manifest(
            "add",
            "subtract",
            "multiply",
            "sum",
            "mean",
            "variance",
            "minimum",
            "maximum",
        ),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
