#!/usr/bin/env python3
"""Compatibility entry point for the direct OpenFHE-Python API benchmark."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.openfhe_direct.benchmark import main


if __name__ == "__main__":
    main()
