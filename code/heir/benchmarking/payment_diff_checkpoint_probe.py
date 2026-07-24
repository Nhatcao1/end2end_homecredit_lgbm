#!/usr/bin/env python3
"""External timing probe for ``payment_diff_checkpoint_e2e.py``.

All clocks live here, outside the application example. The probe imports the
example, wraps its existing dependencies with timing-only proxies, and invokes
the example's real ``main()`` function.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from code.heir.examples import payment_diff_checkpoint_e2e as application


class _TimedAggregateBranch:
    def __init__(
        self,
        program: Any,
        aggregate: str,
        trace: dict[str, Any],
        branch_started: float,
    ) -> None:
        self._wrapped = program
        self._aggregate = aggregate
        self._trace = trace
        self._branch_started = branch_started

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def setup(self) -> None:
        started = time.perf_counter()
        self._wrapped.setup()
        self._trace["aggregate_branches"][self._aggregate][
            "setup_seconds"
        ] = time.perf_counter() - started

    def encrypt(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = self._wrapped.encrypt(*args, **kwargs)
        self._trace["aggregate_branches"][self._aggregate][
            "parent_encrypt_seconds"
        ] = time.perf_counter() - started
        return result

    def eval(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = self._wrapped.eval(*args, **kwargs)
        self._trace["aggregate_branches"][self._aggregate][
            "evaluate_seconds"
        ] = time.perf_counter() - started
        return result


class _TimedMaximum:
    def __init__(
        self,
        wrapped: Any,
        trace: dict[str, Any],
    ) -> None:
        self._wrapped = wrapped
        self._trace = trace

    def load_completed(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = self._wrapped.load_completed(*args, **kwargs)
        self._trace["maximum_branch"] = {
            **dict(result.get("timings_seconds", {})),
            "branch_total_seconds": time.perf_counter() - started,
            "resumed": True,
        }
        return result

    def run_subtract_max(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        result = self._wrapped.run_subtract_max(*args, **kwargs)
        self._trace["maximum_branch"] = {
            **dict(result.get("timings_seconds", {})),
            "branch_total_seconds": time.perf_counter() - started,
            "resumed": False,
        }
        return result


def _read_final_outputs(checkpoint_dir: Path) -> dict[str, float]:
    path = checkpoint_dir / "client_private" / "payment_diff_features.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise RuntimeError(f"expected one final feature row in {path}")
    return {
        key: float(rows[0][key])
        for key in (
            "PAYMENT_DIFF_MAX",
            "PAYMENT_DIFF_MEAN",
            "PAYMENT_DIFF_SUM",
            "PAYMENT_DIFF_VAR",
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installments", type=Path, required=True)
    parser.add_argument("--bridge-dir", type=Path, required=True)
    parser.add_argument("--bucket-size", type=int, required=True)
    parser.add_argument("--max-ring-dimension", type=int, required=True)
    parser.add_argument("--openfhe-dir", required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--execution-json", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    trace: dict[str, Any] = {
        "workflow": "payment_diff_checkpoint_e2e",
        "instrumentation": (
            "external timing proxies; application example contains no clocks"
        ),
        "resume_checkpoints": False,
        "aggregate_branches": {},
        "final_audit": {},
    }

    original_prepare = application.prepare_post_psi_groups
    original_scale = application.public_power_of_two_scale
    original_compile = (
        application.compile_checkpointable_binary_column_aggregate
    )
    original_save = application.save_binary_column_aggregate_checkpoint
    original_maximum = application.SourceBuiltOpenFheColumnMax
    original_audit = application._audit_one_checkpoint

    def timed_prepare(*call_args: Any, **call_kwargs: Any) -> Any:
        started = time.perf_counter()
        layout = original_prepare(*call_args, **call_kwargs)
        trace["client_post_psi_prepare_seconds"] = (
            time.perf_counter() - started
        )
        group = layout.groups[0]
        trace["input"] = {
            "post_psi_applicants": layout.post_psi_applicants,
            "source_rows_scanned": layout.source_rows_scanned,
            "invalid_parent_rows": layout.invalid_parent_rows,
            "selected_groups": 1,
            "real_rows": group.real_count,
            "bucket_size": call_kwargs["bucket_size"],
        }
        return layout

    def timed_scale(*call_args: Any, **call_kwargs: Any) -> Any:
        started = time.perf_counter()
        result = original_scale(*call_args, **call_kwargs)
        trace["public_scale_seconds"] = time.perf_counter() - started
        trace["input"]["input_scale"] = result
        return result

    def timed_compile(*call_args: Any, **call_kwargs: Any) -> Any:
        aggregate = str(call_kwargs["aggregate"])
        branch_started = time.perf_counter()
        started = time.perf_counter()
        program = original_compile(*call_args, **call_kwargs)
        trace["aggregate_branches"][aggregate] = {
            "resumed": False,
            "compile_seconds": time.perf_counter() - started,
        }
        return _TimedAggregateBranch(
            program,
            aggregate,
            trace,
            branch_started,
        )

    def timed_save(program: Any, **call_kwargs: Any) -> Any:
        aggregate = str(program.aggregate)
        started = time.perf_counter()
        result = original_save(program, **call_kwargs)
        branch = trace["aggregate_branches"][aggregate]
        branch["checkpoint_save_seconds"] = time.perf_counter() - started
        branch["branch_total_seconds"] = (
            time.perf_counter() - program._branch_started
        )
        return result

    def timed_maximum(*call_args: Any, **call_kwargs: Any) -> Any:
        return _TimedMaximum(
            original_maximum(*call_args, **call_kwargs),
            trace,
        )

    def timed_audit(checkpoint_dir: Path) -> float:
        started = time.perf_counter()
        result = original_audit(checkpoint_dir)
        trace["final_audit"][f"{checkpoint_dir.name}_seconds"] = (
            time.perf_counter() - started
        )
        return result

    application.prepare_post_psi_groups = timed_prepare
    application.public_power_of_two_scale = timed_scale
    application.compile_checkpointable_binary_column_aggregate = timed_compile
    application.save_binary_column_aggregate_checkpoint = timed_save
    application.SourceBuiltOpenFheColumnMax = timed_maximum
    application._audit_one_checkpoint = timed_audit

    application_argv = [
        str(Path(application.__file__).resolve()),
        "--installments",
        str(args.installments.resolve()),
        "--bridge-dir",
        str(args.bridge_dir.resolve()),
        "--bucket-size",
        str(args.bucket_size),
        "--max-ring-dimension",
        str(args.max_ring_dimension),
        "--openfhe-dir",
        args.openfhe_dir,
        "--checkpoint-dir",
        str(args.checkpoint_dir.resolve()),
    ]
    if args.overwrite:
        application_argv.append("--overwrite")

    original_argv = sys.argv
    workflow_started = time.perf_counter()
    try:
        sys.argv = application_argv
        application.main()
    finally:
        sys.argv = original_argv
    trace["total_workflow_seconds"] = (
        time.perf_counter() - workflow_started
    )
    maximum = trace.get("maximum_branch", {})
    trace["final_audit"]["maximum_seconds"] = float(
        maximum.get("audit_decrypt", 0.0)
    )
    trace["final_outputs"] = _read_final_outputs(
        args.checkpoint_dir.resolve()
    )
    output = args.execution_json.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
