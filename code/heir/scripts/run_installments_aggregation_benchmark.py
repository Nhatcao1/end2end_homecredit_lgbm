#!/usr/bin/env python3
"""Run one honest benchmark report for installment payment aggregations.

It composes only independently reviewable lanes. A failed/deferred lane remains
failed/deferred in the report; no Python aggregate is substituted for HE.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.examples.quick_installments_features import DEMO_ROWS
from code.heir.operations.generic_api import encrypted_aggregation, encrypted_column


REPO_ROOT = Path(__file__).resolve().parents[3]


def python_baseline() -> dict[str, Any]:
    """Time the original feature expressions and pandas-compatible statistics."""
    paid = [row["AMT_PAYMENT"] for row in DEMO_ROWS]
    due = [row["AMT_INSTALMENT"] for row in DEMO_ROWS]
    started = time.perf_counter()
    perc = [payment / installment for payment, installment in zip(paid, due)]
    ratio_seconds = time.perf_counter() - started
    started = time.perf_counter()
    diff = [installment - payment for payment, installment in zip(paid, due)]
    diff_seconds = time.perf_counter() - started

    def stats(values: list[float]) -> dict[str, float]:
        total = sum(values)
        mean = total / len(values)
        return {
            "max": max(values),
            "mean": mean,
            "sum": total,
            "var": sum((value - mean) ** 2 for value in values) / (len(values) - 1),
        }

    return {
        "payment_perc": {"values": perc, "stats": stats(perc), "feature_seconds": ratio_seconds},
        "payment_diff": {"values": diff, "stats": stats(diff), "feature_seconds": diff_seconds},
    }


def run_lane(label: str, command: list[str], output: Path) -> dict[str, Any]:
    """Run one child benchmark and retain its complete stdout/stderr artifact."""
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
    wall = time.perf_counter() - started
    log = output / "logs" / f"{label}.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    result_path = output / label / "result.json"
    return {
        "status": "executed" if completed.returncode == 0 else "failed",
        "return_code": completed.returncode,
        "wall_seconds": wall,
        "log": str(log.relative_to(output)),
        "result": json.loads(result_path.read_text(encoding="utf-8")) if result_path.is_file() else None,
    }


def markdown_report(output: Path, baseline: dict[str, Any], ratio: dict[str, Any], diff: dict[str, Any]) -> str:
    ratio_execution = (ratio.get("result") or {}).get("execution", {})
    diff_stage_seconds = (diff.get("result") or {}).get("stage_seconds", {})
    diff_stats = (diff.get("result") or {}).get("aggregation_comparison", [])
    diff_by_name = {str(row["aggregation"]): row for row in diff_stats}

    def value(row: dict[str, Any] | None, field: str = "he") -> str:
        return str(row.get(field, "NOT_RUN")) if row else "NOT_RUN"

    return f"""# Installments payment aggregation benchmark

This report follows the original expressions exactly where the encrypted route
is feasible. It does not claim that a plaintext aggregate is an HE result.

## Source expressions

```python
ins['PAYMENT_PERC'] = ins['AMT_PAYMENT'] / ins['AMT_INSTALMENT']
ins['PAYMENT_DIFF'] = ins['AMT_INSTALMENT'] - ins['AMT_PAYMENT']
```

## Result matrix

| Feature | Encrypted feature | Max | Mean | Sum | Var | Session / reason |
|---|---|---|---|---|---|---|
| `PAYMENT_PERC` | `{ratio['status']}` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | Reciprocal CKKS feature proof only. Aggregating the deep ratio path exhausted the small server's depth/memory budget in prior trials; no aggregate is claimed. |
| `PAYMENT_DIFF` | `{diff['status']}` | DEFERRED | {value(diff_by_name.get('mean'))} | {value(diff_by_name.get('sum'))} | {value(diff_by_name.get('var'))} | Exact subtraction enters a separately serialized CKKS session. `sum`, `mean`, and `var` consume isolated branches of `payment_diff.ct`; max remains the dedicated CKKS↔FHEW lane. |

## Timing

| Item | Python feature seconds | HE evaluation/session timing |
|---|---:|---|
| `PAYMENT_PERC` | {baseline['payment_perc']['feature_seconds']:.9f} | encrypted feature: {ratio_execution.get('encrypted_payment_perc_seconds', 'NOT_RUN')}; full child wall time: {ratio.get('wall_seconds', 0):.6f} |
| `PAYMENT_DIFF` | {baseline['payment_diff']['feature_seconds']:.9f} | stage timings: `{json.dumps(diff_stage_seconds, sort_keys=True)}`; full child wall time: {diff.get('wall_seconds', 0):.6f} |

Encryption/decryption audit time is recorded by the child lanes but excluded from
the feature-calculation comparison. Child logs are under `logs/`.

## Max decision

`max` is deliberately not run inside this benchmark. The safe implementation
is a separate OpenFHE CKKS↔FHEW session with a public comparison-range contract
and duplicate-candidate padding (never an extreme sentinel). It keeps max and
argmax artifacts separate; this workload retains max only.

## Reusable generic kernel API

The plans used here are written to `kernel_api.json`. They expose generic
column `subtract` and bounded `ratio`, generic fixed-count `sum`/`mean`/`var`,
and separate-session `min`/`max` routes. Business feature names only select
these operations; they do not create special cryptographic kernels.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--ckks-mul-depth", type=int, default=12)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--allow-partial", action="store_true", help="write report even if one executable lane fails")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    baseline = python_baseline()
    plans = {
        "column_subtract": encrypted_column("subtract", args.vector_size).__dict__,
        "column_ratio": encrypted_column("ratio", args.vector_size).__dict__,
        "sum": encrypted_aggregation("sum", args.vector_size, len(DEMO_ROWS)).__dict__,
        "mean": encrypted_aggregation("mean", args.vector_size, len(DEMO_ROWS)).__dict__,
        "var": encrypted_aggregation("var", args.vector_size, len(DEMO_ROWS)).__dict__,
        "max": encrypted_aggregation("max", args.vector_size, len(DEMO_ROWS)).__dict__,
    }
    # MLIR is review material, retained in each plan but not duplicated in report.json.
    write_json(root / "kernel_api.json", plans)
    ratio_command = [sys.executable, "code/heir/scripts/run_payment_perc_depth_probe.py", "--output-dir", str(root / "payment_perc"), "--vector-size", str(args.vector_size), "--ckks-mul-depth", str(args.ckks_mul_depth), "--heir-opt", args.heir_opt, "--heir-translate", args.heir_translate, "--openfhe-dir", args.openfhe_dir]
    diff_command = [sys.executable, "code/heir/scripts/run_payment_diff_fixed_count_aggregates.py", "--output-dir", str(root / "payment_diff"), "--vector-size", str(args.vector_size), "--ckks-mul-depth", "4", "--heir-opt", args.heir_opt, "--heir-translate", args.heir_translate, "--openfhe-dir", args.openfhe_dir]
    ratio = run_lane("payment_perc", ratio_command, root)
    diff = run_lane("payment_diff", diff_command, root)
    report = markdown_report(root, baseline, ratio, diff)
    (root / "REPORT.md").write_text(report, encoding="utf-8")
    result = {"status": "complete" if ratio["status"] == diff["status"] == "executed" else "partial", "baseline": baseline, "lanes": {"payment_perc": ratio, "payment_diff": diff}, "max": {"status": "deferred_safe_scheme_switch_session", "reason": "requires duplicate-candidate padding and public range contract; tracked separately"}, "report": "REPORT.md"}
    write_json(root / "result.json", result)
    print(json.dumps({"status": result["status"], "report": str(root / "REPORT.md"), "lanes": {name: lane["status"] for name, lane in result["lanes"].items()}}, indent=2))
    if result["status"] != "complete" and not args.allow_partial:
        raise SystemExit("one or more encrypted lanes failed; report was written with no substituted results")


if __name__ == "__main__":
    main()
