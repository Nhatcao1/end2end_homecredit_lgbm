#!/usr/bin/env python3
"""Run both SecretFlow PSI containers and record server-side execution evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import sha256_file, write_json
from code.private_join.secretflow_adapter import validate_secretflow_outputs


def _compose_base(project_directory: Path, compose_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(project_directory),
        "-f",
        str(compose_file),
    ]


def _configured_images(command: list[str]) -> list[str]:
    result = subprocess.run(
        [*command, "config", "--images"],
        check=True,
        capture_output=True,
        text=True,
    )
    images = (line.strip() for line in result.stdout.splitlines() if line.strip())
    return list(dict.fromkeys(images))


def run_secretflow(
    project_directory: Path,
    compose_file: Path,
    receiver_output: Path,
    sender_output: Path,
    receiver_trace: Path,
    sender_trace: Path,
    log_path: Path,
    summary_path: Path,
    audit_path: Path,
    key_column: str = "SK_ID_CURR",
) -> dict[str, Any]:
    """Execute Compose once, stream logs, validate outputs, and save evidence."""
    existing = [path for path in (receiver_output, sender_output) if path.exists()]
    if existing:
        raise FileExistsError(
            "refusing to overwrite PSI output files: "
            + ", ".join(str(path) for path in existing)
        )
    command = _compose_base(project_directory, compose_file)
    images = _configured_images(command)
    run_command = [
        *command,
        "up",
        "--abort-on-container-exit",
        "--exit-code-from",
        "receiver",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            run_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        exit_code = process.wait()
    elapsed = time.perf_counter() - started
    summary: dict[str, Any] = {
        "status": "secretflow_psi_completed" if exit_code == 0 else "secretflow_psi_failed",
        "protocol": "PROTOCOL_RR22",
        "configured_images": images,
        "command": run_command,
        "exit_code": exit_code,
        "timings_seconds": {"compose_wall_seconds": elapsed},
        "log_file": str(log_path),
        "log_sha256": sha256_file(log_path),
    }
    if exit_code != 0:
        write_json(summary_path, summary)
        raise RuntimeError(
            f"SecretFlow PSI containers failed with exit code {exit_code}; see {log_path}"
        )

    audit = validate_secretflow_outputs(
        receiver_output, sender_output, audit_path, key_column
    )
    summary["validated_output"] = audit
    summary["traces"] = {
        "receiver": {
            "file": str(receiver_trace),
            "sha256": sha256_file(receiver_trace) if receiver_trace.is_file() else "missing",
        },
        "sender": {
            "file": str(sender_trace),
            "sha256": sha256_file(sender_trace) if sender_trace.is_file() else "missing",
        },
    }
    write_json(summary_path, summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-directory",
        type=Path,
        default=Path("deploy/secretflow_psi"),
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path("deploy/secretflow_psi/docker-compose.yml"),
    )
    parser.add_argument(
        "--receiver-output",
        type=Path,
        default=Path("data/psi/receiver/psi_output.csv"),
    )
    parser.add_argument(
        "--sender-output",
        type=Path,
        default=Path("data/psi/sender/psi_output.csv"),
    )
    parser.add_argument(
        "--receiver-trace",
        type=Path,
        default=Path("data/psi/receiver/receiver.trace"),
    )
    parser.add_argument(
        "--sender-trace",
        type=Path,
        default=Path("data/psi/sender/sender.trace"),
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("data/psi/secretflow_run.log"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/psi/secretflow_run_summary.json"),
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("data/psi/psi_output_audit.json"),
    )
    parser.add_argument("--key", default="SK_ID_CURR")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_secretflow(
        args.project_directory,
        args.compose_file,
        args.receiver_output,
        args.sender_output,
        args.receiver_trace,
        args.sender_trace,
        args.log,
        args.summary,
        args.audit,
        args.key,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
