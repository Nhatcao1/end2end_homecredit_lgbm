#!/usr/bin/env python3
"""Run PAYMENT_PERC alone with explicit CKKS generation parameters.

This is a precision probe, not an aggregation benchmark. It keeps the ratio
calculation isolated from encrypted sum so a decode failure can be attributed
to the reciprocal circuit or its CKKS parameters.
"""

from __future__ import annotations

import argparse
import json
import re
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
    payment_perc_newton_mlir,
)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_perc_depth_probe LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(payment_perc_runner heir_output.cpp payment_perc_runner.cpp)
target_include_directories(payment_perc_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke"
  "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(payment_perc_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(payment_perc_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(payment_perc_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;

std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path.string());
  std::string line;
  std::getline(input, line);
  std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}

void require(bool ok, const std::string& message) {
  if (!ok) throw std::runtime_error(message);
}

int main(int argc, char** argv) {
  if (argc != 10) return 2;
  try {
    const auto payment = readVector(argv[1]);
    const auto installment = readVector(argv[2]);
    const auto valid = readVector(argv[3]);
    require(payment.size() == @VECTOR_SIZE@ && installment.size() == @VECTOR_SIZE@ &&
                valid.size() == @VECTOR_SIZE@,
            "input vectors must equal generated vector size");

    auto context = payment_perc_newton__generate_crypto_context();
    auto keys = context->KeyGen();
    require(keys.good(), "OpenFHE key generation failed");
    context = payment_perc_newton__configure_crypto_context(context, keys.secretKey);

    const auto encryptionStarted = std::chrono::steady_clock::now();
    auto encryptedPayment = payment_perc_newton__encrypt__arg0(context, payment, keys.publicKey);
    auto encryptedInstallment = payment_perc_newton__encrypt__arg1(context, installment, keys.publicKey);
    auto encryptedValid = payment_perc_newton__encrypt__arg2(context, valid, keys.publicKey);
    const auto evaluationStarted = std::chrono::steady_clock::now();
    auto encryptedResult = payment_perc_newton(
        context, encryptedPayment, encryptedInstallment, encryptedValid);
    const auto auditStarted = std::chrono::steady_clock::now();

    require(Serial::SerializeToFile(argv[4], encryptedPayment, SerType::BINARY),
            "cannot save payment ciphertext container");
    require(Serial::SerializeToFile(argv[5], encryptedInstallment, SerType::BINARY),
            "cannot save installment ciphertext container");
    require(Serial::SerializeToFile(argv[6], encryptedValid, SerType::BINARY),
            "cannot save validity ciphertext container");
    require(Serial::SerializeToFile(argv[7], encryptedResult, SerType::BINARY),
            "cannot save PAYMENT_PERC ciphertext container");

    auto decrypted = payment_perc_newton__decrypt__result0(
        context, encryptedResult, keys.secretKey);
    const auto done = std::chrono::steady_clock::now();

    std::ofstream audit(argv[8]);
    audit << "value\n" << std::setprecision(17);
    for (const auto& value : decrypted) audit << value << '\n';

    std::ofstream metrics(argv[9]);
    metrics << "{\n"
            << "  \"encryption_seconds\": "
            << std::chrono::duration<double>(evaluationStarted - encryptionStarted).count()
            << ",\n  \"encrypted_payment_perc_seconds\": "
            << std::chrono::duration<double>(auditStarted - evaluationStarted).count()
            << ",\n  \"audit_seconds\": "
            << std::chrono::duration<double>(done - auditStarted).count()
            << "\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
'''


def run(command: list[str], cwd: Path, output: Path | None = None) -> tuple[float, str]:
    started = time.perf_counter()
    if output is None:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        text = completed.stdout + completed.stderr
    else:
        with output.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(command, cwd=cwd, text=True, stdout=handle, stderr=subprocess.PIPE)
        text = completed.stderr
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{text}")
    return time.perf_counter() - started, text


def scheme_to_openfhe_option(
    *, mul_depth: int, first_mod_size: int, scaling_mod_size: int
) -> str:
    """Create the documented HEIR pipeline option as one subprocess argument."""
    if mul_depth <= 0:
        raise ValueError("ckks mul depth must be positive")
    options = ["entry-function=payment_perc_newton", f"mul-depth={mul_depth}"]
    if first_mod_size:
        options.append(f"first-mod-size={first_mod_size}")
    if scaling_mod_size:
        options.append(f"scaling-mod-size={scaling_mod_size}")
    return "--scheme-to-openfhe=" + " ".join(options)


def generated_parameter(source: str, name: str) -> int | None:
    match = re.search(rf"{re.escape(name)}\((\d+)\);", source)
    return int(match.group(1)) if match else None


def generate(
    root: Path,
    *,
    vector_size: int,
    heir_opt: str,
    heir_translate: str,
    mul_depth: int,
    first_mod_size: int,
    scaling_mod_size: int,
) -> dict[str, Any]:
    source = (root / "payment_perc_newton.mlir").resolve()
    source.write_text(payment_perc_newton_mlir(vector_size), encoding="utf-8")
    lowered = root / "lowered_openfhe.mlir"
    header = root / "heir_output.h"
    cpp = root / "heir_output.cpp"
    lower_seconds, _ = run(
        [
            heir_opt,
            f"--mlir-to-ckks=ciphertext-degree={vector_size}",
            scheme_to_openfhe_option(
                mul_depth=mul_depth,
                first_mod_size=first_mod_size,
                scaling_mod_size=scaling_mod_size,
            ),
            str(source),
        ],
        root,
        lowered,
    )
    header_seconds, _ = run(
        [
            heir_translate,
            "--emit-openfhe-pke-header",
            "--openfhe-include-type=install-relative",
            str(lowered.resolve()),
        ],
        root,
        header,
    )
    cpp_seconds, _ = run(
        [
            heir_translate,
            "--emit-openfhe-pke",
            "--openfhe-include-type=install-relative",
            str(lowered.resolve()),
        ],
        root,
        cpp,
    )
    emitted = cpp.read_text(encoding="utf-8", errors="replace")
    return {
        "requested": {
            "mul_depth": mul_depth,
            "first_mod_size": first_mod_size or "HEIR default",
            "scaling_mod_size": scaling_mod_size or "HEIR default",
        },
        "emitted_openfhe": {
            "multiplicative_depth": generated_parameter(emitted, "SetMultiplicativeDepth"),
            "first_mod_size": generated_parameter(emitted, "SetFirstModSize"),
            "scaling_mod_size": generated_parameter(emitted, "SetScalingModSize"),
            "ring_dim": generated_parameter(emitted, "SetRingDim"),
        },
        "sha256": {
            "source": sha256_file(source),
            "lowered": sha256_file(lowered),
            "header": sha256_file(header),
            "cpp": sha256_file(cpp),
        },
        "timings_seconds": {
            "lower": lower_seconds,
            "header": header_seconds,
            "cpp": cpp_seconds,
        },
    }


def execute(root: Path, *, vector_size: int, openfhe_dir: str) -> tuple[list[float], dict[str, Any]]:
    work = root / "runner"
    work.mkdir()
    for name in ("heir_output.cpp", "heir_output.h"):
        shutil.copy2(root / name, work / name)
    (work / "payment_perc_runner.cpp").write_text(
        RUNNER.replace("@VECTOR_SIZE@", str(vector_size)), encoding="utf-8"
    )
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure = ["cmake", "-S", str(work.resolve()), "-B", str(build.resolve())]
    if openfhe_dir:
        configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    configure_seconds, configure_log = run(configure, work)
    build_seconds, build_log = run(
        ["cmake", "--build", str(build.resolve()), "--target", "payment_perc_runner"],
        work,
    )
    (work / "build.log").write_text(configure_log + build_log, encoding="utf-8")

    inputs = root / "plaintext_inputs"
    ciphertexts = root / "ciphertexts"
    ciphertexts.mkdir()
    audit = root / "decrypted_audit.csv"
    metrics = root / "metrics.json"
    command = [
        str((build / "payment_perc_runner").resolve()),
        str((inputs / "amt_payment.csv").resolve()),
        str((inputs / "amt_installment_safe.csv").resolve()),
        str((inputs / "validity.csv").resolve()),
        str((ciphertexts / "payment.ct").resolve()),
        str((ciphertexts / "installment_safe.ct").resolve()),
        str((ciphertexts / "validity.ct").resolve()),
        str((ciphertexts / "payment_perc.ct").resolve()),
        str(audit.resolve()),
        str(metrics.resolve()),
    ]
    wall_seconds, runner_log = run(command, work)
    (work / "runner.log").write_text(runner_log, encoding="utf-8")
    values = [float(row["value"]) for row in read_csv(audit)]
    result = json.loads(metrics.read_text(encoding="utf-8"))
    result.update(
        {
            "build_seconds": {"configure": configure_seconds, "build": build_seconds},
            "runner_wall_seconds": wall_seconds,
        }
    )
    return values, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--ckks-mul-depth", type=int, default=12)
    parser.add_argument("--ckks-first-mod-size", type=int, default=0)
    parser.add_argument("--ckks-scaling-mod-size", type=int, default=0)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    args = parser.parse_args()

    if args.vector_size < len(DEMO_ROWS):
        raise ValueError("vector size must fit demo rows")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        if root == Path.cwd().resolve() or root == root.parent:
            raise ValueError("unsafe output directory")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    # Client-side safety preparation only: no ratio is calculated here. Invalid
    # denominators are replaced with the public scale and later zeroed by valid.
    public_scale = 1000.0
    safe_min, safe_max = 500.0, 1000.0
    payment = [row["AMT_PAYMENT"] for row in DEMO_ROWS]
    original_installment = [row["AMT_INSTALMENT"] for row in DEMO_ROWS]
    validity = [float(safe_min <= value <= safe_max and value > 0.0) for value in original_installment]
    safe_installment = [
        value if valid else public_scale
        for value, valid in zip(original_installment, validity)
    ]
    padding = [0.0] * (args.vector_size - len(DEMO_ROWS))
    inputs = root / "plaintext_inputs"
    inputs.mkdir()
    write_values(inputs / "amt_payment.csv", payment + padding)
    write_values(inputs / "amt_installment_original.csv", original_installment + padding)
    write_values(inputs / "amt_installment_safe.csv", safe_installment + [public_scale] * len(padding))
    write_values(inputs / "validity.csv", validity + padding)

    generation = generate(
        root,
        vector_size=args.vector_size,
        heir_opt=args.heir_opt,
        heir_translate=args.heir_translate,
        mul_depth=args.ckks_mul_depth,
        first_mod_size=args.ckks_first_mod_size,
        scaling_mod_size=args.ckks_scaling_mod_size,
    )
    values, execution = execute(root, vector_size=args.vector_size, openfhe_dir=args.openfhe_dir)
    rows = []
    for index, (paid, due, valid) in enumerate(zip(payment, original_installment, validity)):
        expected = paid / due if valid else 0.0
        rows.append(
            {
                "row": index,
                "AMT_PAYMENT": paid,
                "AMT_INSTALMENT_original": due,
                "valid": valid,
                "PAYMENT_PERC_python": expected,
                "PAYMENT_PERC_he": values[index],
                "absolute_error": abs(expected - values[index]),
                "relative_error": abs(expected - values[index]) / max(abs(expected), 1e-12),
            }
        )
    write_csv(root / "comparison.csv", list(rows[0]), rows)
    result = {
        "status": "payment_perc_ckks_probe_executed",
        "scope": "PAYMENT_PERC only; no encrypted sum or aggregation",
        "range_contract": {
            "normalized_installment_range": [0.5, 1.0],
            "public_scale": public_scale,
            "invalid_denominator_replacement": public_scale,
        },
        "generation": generation,
        "execution": execution,
        "comparison": rows,
    }
    write_json(root / "result.json", result)
    print(json.dumps({"status": result["status"], "generation": generation, "comparison": rows}, indent=2))


if __name__ == "__main__":
    main()
