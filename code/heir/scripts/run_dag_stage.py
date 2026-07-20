#!/usr/bin/env python3
"""Run exactly one resumable stage of the persistent encrypted feature DAG."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.dag.contracts import STAGE_ORDER
from code.heir.dag.pipeline import (
    dag_status,
    finalize_dag,
    initialize_dag,
    run_function_stage,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--run-root", type=Path, default=Path("benchmark_runs/dag")
    )
    parser.add_argument(
        "--stage", required=True, choices=("init", *STAGE_ORDER, "finalize", "status")
    )
    parser.add_argument("--application", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data/home_credit"))
    parser.add_argument("--generated-root", type=Path)
    parser.add_argument("--psi-manifest", type=Path)
    parser.add_argument("--openfhe-dir", default="")
    parser.add_argument("--vector-size", type=int, default=8192)
    parser.add_argument("--application-row-limit", type=int, default=8)
    parser.add_argument("--source-row-limit", type=int, default=500000)
    parser.add_argument("--provider-kernel", choices=("K01", "K02", "K03"), default="K03")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.run_root / args.run_id
    if args.stage == "init":
        if args.application is None or args.generated_root is None:
            raise SystemExit("init requires --application and --generated-root")
        result = initialize_dag(
            root,
            application_path=args.application,
            data_dir=args.data_dir,
            generated_root=args.generated_root,
            openfhe_dir=args.openfhe_dir,
            vector_size=args.vector_size,
            application_row_limit=args.application_row_limit,
            source_row_limit=args.source_row_limit,
            provider_kernel=args.provider_kernel,
            psi_manifest_path=args.psi_manifest,
        )
    elif args.stage == "finalize":
        result = finalize_dag(root)
    elif args.stage == "status":
        result = dag_status(root)
    else:
        result = run_function_stage(root, args.stage, resume=args.resume)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
