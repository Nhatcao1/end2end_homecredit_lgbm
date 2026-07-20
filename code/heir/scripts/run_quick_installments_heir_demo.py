#!/usr/bin/env python3
"""Run one complete, tiny HEIR/OpenFHE installments feature demonstration.

This is an executable review tool, not a performance benchmark.  One command
creates the MLIR evidence, lowers it with HEIR, translates it to OpenFHE C++,
builds it, encrypts three small input vectors, evaluates the functions, and
decrypts only to write a review table.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv, sha256_file, write_csv, write_json, write_values
from code.heir.examples.quick_installments_features import (
    DEMO_ROWS,
    expected_plaintext,
    payment_perc_newton_mlir,
    positive_difference_mlir,
)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(quick_installments_heir_demo LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(feature_runner heir_output.cpp feature_runner.cpp)
target_include_directories(feature_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke"
  "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(feature_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(feature_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(feature_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER = r'''
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "heir_output.h"

std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path.string());
  std::string line;
  std::getline(input, line);
  std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}

int main(int argc, char** argv) {
  if (argc != 6) return 2;
  try {
    const auto first = readVector(argv[1]);
    const auto second = readVector(argv[2]);
    const auto valid = readVector(argv[3]);
    if (first.size() != @VECTOR_SIZE@ || second.size() != @VECTOR_SIZE@ || valid.size() != @VECTOR_SIZE@)
      throw std::runtime_error("all inputs must equal generated vector size");
    auto context = @ENTRY@__generate_crypto_context();
    auto keyPair = context->KeyGen();
    if (!keyPair.good()) throw std::runtime_error("OpenFHE key generation failed");
    context = @ENTRY@__configure_crypto_context(context, keyPair.secretKey);
    const auto encrypt_started = std::chrono::steady_clock::now();
    auto encrypted_first = @ENTRY@__encrypt__arg0(context, first, keyPair.publicKey);
    auto encrypted_second = @ENTRY@__encrypt__arg1(context, second, keyPair.publicKey);
    auto encrypted_valid = @ENTRY@__encrypt__arg2(context, valid, keyPair.publicKey);
    const auto evaluate_started = std::chrono::steady_clock::now();
    auto encrypted_result = @ENTRY@(context, encrypted_first, encrypted_second, encrypted_valid);
    const auto decrypt_started = std::chrono::steady_clock::now();
    auto result = @ENTRY@__decrypt__result0(context, encrypted_result, keyPair.secretKey);
    const auto done = std::chrono::steady_clock::now();
    std::ofstream output(argv[5]);
    output << "value\n" << std::setprecision(17);
    for (const auto& value : result) output << value << '\n';
    std::ofstream metrics(argv[4]);
    metrics << "{\n"
            << "  \"encryption_seconds\": " << std::chrono::duration<double>(evaluate_started - encrypt_started).count() << ",\n"
            << "  \"encrypted_evaluation_seconds\": " << std::chrono::duration<double>(decrypt_started - evaluate_started).count() << ",\n"
            << "  \"decryption_seconds\": " << std::chrono::duration<double>(done - decrypt_started).count() << "\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "feature_runner failed: " << error.what() << '\n';
    return 1;
  }
}
'''


def _run(command: list[str], cwd: Path, stdout_path: Path | None = None) -> tuple[float, str]:
    started = time.perf_counter()
    if stdout_path is None:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        output = completed.stdout + completed.stderr
    else:
        with stdout_path.open("w", encoding="utf-8") as output_file:
            completed = subprocess.run(command, cwd=cwd, text=True, stdout=output_file, stderr=subprocess.PIPE)
        output = completed.stderr
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{output}")
    return time.perf_counter() - started, output


def _generate(source: Path, entry: str, heir_opt: str, heir_translate: str, vector_size: int) -> dict[str, Any]:
    # HEIR is invoked from the feature directory. Resolve the source before
    # doing that so a workspace-relative path is not appended to itself.
    source = source.resolve()
    directory = source.parent
    lowered, header, cpp = directory / "lowered_openfhe.mlir", directory / "heir_output.h", directory / "heir_output.cpp"
    lower_seconds, _ = _run(
        [heir_opt, f"--mlir-to-ckks=ciphertext-degree={vector_size}", f"--scheme-to-openfhe=entry-function={entry}", str(source)],
        directory,
        lowered,
    )
    header_seconds, _ = _run(
        [heir_translate, "--emit-openfhe-pke-header", "--openfhe-include-type=install-relative", str(lowered)],
        directory,
        header,
    )
    cpp_seconds, _ = _run(
        [heir_translate, "--emit-openfhe-pke", "--openfhe-include-type=install-relative", str(lowered)],
        directory,
        cpp,
    )
    required = (entry, f"{entry}__encrypt__arg0", f"{entry}__encrypt__arg1", f"{entry}__encrypt__arg2", f"{entry}__decrypt__result0")
    generated = header.read_text(encoding="utf-8", errors="replace") + cpp.read_text(encoding="utf-8", errors="replace")
    missing = [symbol for symbol in required if symbol not in generated]
    if missing or ("CKKS" not in generated and "CryptoContextCKKSRNS" not in generated):
        raise ValueError(f"generated source is not expected CKKS output; missing={missing}")
    return {
        "entry": entry,
        "source_sha256": sha256_file(source),
        "lowered_sha256": sha256_file(lowered),
        "header_sha256": sha256_file(header),
        "cpp_sha256": sha256_file(cpp),
        "timings_seconds": {"lower": lower_seconds, "header": header_seconds, "cpp": cpp_seconds},
    }


def _evaluate(
    *, feature_dir: Path, entry: str, vector_size: int, first: Path, second: Path,
    valid: Path, openfhe_dir: str, generated_dir: Path | None = None,
) -> tuple[list[float], dict[str, Any]]:
    work = feature_dir / "runner"
    work.mkdir(parents=True)
    generated_dir = generated_dir or feature_dir
    for name in ("heir_output.cpp", "heir_output.h"):
        shutil.copy2(generated_dir / name, work / name)
    (work / "feature_runner.cpp").write_text(
        RUNNER.replace("@ENTRY@", entry).replace("@VECTOR_SIZE@", str(vector_size)), encoding="utf-8"
    )
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure = ["cmake", "-S", str(work.resolve()), "-B", str(build.resolve())]
    if openfhe_dir:
        configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    configure_seconds, configure_log = _run(configure, work)
    build_seconds, build_log = _run(["cmake", "--build", str(build.resolve()), "--target", "feature_runner"], work)
    (work / "build.log").write_text(configure_log + build_log, encoding="utf-8")
    metrics_path, output_path = feature_dir / "metrics.json", feature_dir / "decrypted_audit.csv"
    runner_seconds, runner_log = _run(
        [str(build / "feature_runner"), str(first.resolve()), str(second.resolve()), str(valid.resolve()), str(metrics_path.resolve()), str(output_path.resolve())], work
    )
    (work / "runner.log").write_text(runner_log, encoding="utf-8")
    values = [float(row["value"]) for row in read_csv(output_path)]
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["build_timings_seconds"] = {"configure": configure_seconds, "build": build_seconds}
    metrics["runner_wall_seconds"] = runner_seconds
    return values, metrics


def _write_report(path: Path, rows: list[dict[str, Any]], result: dict[str, Any]) -> None:
    table = ["| Row | PAYMENT_PERC Python | HE | DPD Python | HE | DBD Python | HE |", "|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        table.append(
            "| {row_index} | {PAYMENT_PERC_python:.8f} | {PAYMENT_PERC_he:.8f} | {DPD_python:.8f} | {DPD_he:.8f} | {DBD_python:.8f} | {DBD_he:.8f} |".format(**row)
        )
    path.write_text(
        "# Quick HEIR installments execution\n\n"
        "This command generated, lowered, translated, compiled, encrypted, evaluated, and decrypted strictly for this audit table.\n\n"
        "## Result table\n\n" + "\n".join(table) + "\n\n"
        "`PAYMENT_PERC` uses an approximate reciprocal polynomial. `DPD` and `DBD` use an approximate CKKS clipping polynomial. Their errors are reported in `quick_demo.json`; they are not presented as exact HE calculations.\n\n"
        "## Aggregation status\n\n"
        "The original `max`, `mean`, `sum`, `var`, `min`, and `nunique` aggregations are intentionally not included in this row-feature demo. The exact next operation is encrypted sum/count/sum-of-squares; max/min and nunique require separate comparison/set designs.\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    args = parser.parse_args()
    if args.vector_size < len(DEMO_ROWS):
        raise ValueError("vector size must fit the three demo rows")
    root = args.output_dir
    if root.exists():
        raise FileExistsError(f"refusing to overwrite: {root}")
    root.mkdir(parents=True)
    valid = [1.0] * len(DEMO_ROWS) + [0.0] * (args.vector_size - len(DEMO_ROWS))
    inputs = {
        "payment": [item["AMT_PAYMENT"] for item in DEMO_ROWS],
        "installment": [item["AMT_INSTALMENT"] for item in DEMO_ROWS],
        "entry": [item["DAYS_ENTRY_PAYMENT"] for item in DEMO_ROWS],
        "due": [item["DAYS_INSTALMENT"] for item in DEMO_ROWS],
    }
    input_paths: dict[str, Path] = {}
    for name, values in inputs.items():
        path = root / "inputs" / f"{name}.csv"
        write_values(path, values + [0.0] * (args.vector_size - len(values)))
        input_paths[name] = path
    valid_path = root / "inputs" / "valid.csv"
    write_values(valid_path, valid)

    payment_dir, difference_dir = root / "payment_perc", root / "positive_difference"
    payment_dir.mkdir()
    difference_dir.mkdir()
    payment_source = payment_dir / "payment_perc_newton.mlir"
    difference_source = difference_dir / "positive_difference_smoothstep.mlir"
    payment_source.write_text(payment_perc_newton_mlir(args.vector_size), encoding="utf-8")
    difference_source.write_text(positive_difference_mlir(args.vector_size), encoding="utf-8")
    generated = {
        "payment_perc": _generate(payment_source, "payment_perc_newton", args.heir_opt, args.heir_translate, args.vector_size),
        "positive_difference": _generate(difference_source, "positive_difference_smoothstep", args.heir_opt, args.heir_translate, args.vector_size),
    }
    payment, payment_metrics = _evaluate(feature_dir=payment_dir, entry="payment_perc_newton", vector_size=args.vector_size, first=input_paths["payment"], second=input_paths["installment"], valid=valid_path, openfhe_dir=args.openfhe_dir)
    dpd, dpd_metrics = _evaluate(feature_dir=difference_dir, entry="positive_difference_smoothstep", vector_size=args.vector_size, first=input_paths["entry"], second=input_paths["due"], valid=valid_path, openfhe_dir=args.openfhe_dir)
    # Reuse the same generated positive-difference executable; only the encrypted input order changes.
    dbd, dbd_metrics = _evaluate(feature_dir=difference_dir / "dbd_run", generated_dir=difference_dir, entry="positive_difference_smoothstep", vector_size=args.vector_size, first=input_paths["due"], second=input_paths["entry"], valid=valid_path, openfhe_dir=args.openfhe_dir)
    expected = expected_plaintext()
    table = [
        {
            "row_index": index,
            "PAYMENT_PERC_python": source["PAYMENT_PERC"], "PAYMENT_PERC_he": payment[index],
            "DPD_python": source["DPD"], "DPD_he": dpd[index],
            "DBD_python": source["DBD"], "DBD_he": dbd[index],
        }
        for index, source in enumerate(expected)
    ]
    result = {
        "status": "heir_generated_ckks_executed", "scheme": "CKKS", "vector_size": args.vector_size,
        "generated": generated,
        "timings_seconds": {"payment_perc": payment_metrics, "dpd": dpd_metrics, "dbd": dbd_metrics},
        "result_table": table,
        "notes": {"payment_perc": "approximate reciprocal polynomial", "dpd_dbd": "approximate CKKS clipping; not exact comparison"},
    }
    write_json(root / "quick_demo.json", result)
    write_csv(root / "result_table.csv", list(table[0]), table)
    _write_report(root / "quick_demo_report.md", table, result)
    print(json.dumps({"status": result["status"], "result_table": table, "output_dir": str(root)}, indent=2))


if __name__ == "__main__":
    main()
