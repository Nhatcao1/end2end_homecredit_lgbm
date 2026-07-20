#!/usr/bin/env python3
"""Encrypt, calculate, and audit-decrypt PAYMENT_PERC and PAYMENT_DIFF only."""

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

from code.heir.common import read_csv, sha256_file, write_csv, write_json, write_values
from code.heir.examples.quick_installments_features import (
    DEMO_ROWS,
    payment_perc_newton_mlir,
)
from code.heir.kernels.sum import encrypted_sum_mlir
from code.heir.operations.columns import binary_mlir
from code.heir.scripts.run_payment_perc_depth_probe import patch_translated_mul_depth


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_features_demo LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(ciphertext_runner feature_output.cpp sum_output.cpp ciphertext_runner.cpp)
target_include_directories(ciphertext_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke"
  "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(ciphertext_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(ciphertext_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(ciphertext_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "feature_output.h"
#include "sum_output.h"
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
  while (std::getline(input, line)) {
    if (!line.empty()) values.push_back(std::stod(line));
  }
  return values;
}

void require(bool ok, const std::string& message) {
  if (!ok) throw std::runtime_error(message);
}

int main(int argc, char** argv) {
  if (argc != @ARGC@) return 2;
  try {
    const auto first = readVector(argv[1]);
    const auto second = readVector(argv[2]);
    require(first.size() == @VECTOR_SIZE@ && second.size() == @VECTOR_SIZE@,
            "input vectors must equal generated vector size");
    @READ_VALID@

    auto context = @ENTRY@__generate_crypto_context();
    auto keys = context->KeyGen();
    require(keys.good(), "OpenFHE key generation failed");
    context = @ENTRY@__configure_crypto_context(context, keys.secretKey);
    context = encrypted_sum__configure_crypto_context(context, keys.secretKey);

    const auto encryptionStarted = std::chrono::steady_clock::now();
    auto encryptedFirst = @ENTRY@__encrypt__arg0(context, first, keys.publicKey);
    auto encryptedSecond = @ENTRY@__encrypt__arg1(context, second, keys.publicKey);
    @ENCRYPT_VALID@
    const auto featureStarted = std::chrono::steady_clock::now();
    auto encryptedResult = @ENTRY@(context, @CALL_ARGUMENTS@);
    const auto featureAuditStarted = std::chrono::steady_clock::now();

    require(Serial::SerializeToFile(argv[@FIRST_CT@], encryptedFirst,
                                    SerType::BINARY),
            "cannot save first ciphertext container");
    require(Serial::SerializeToFile(argv[@SECOND_CT@], encryptedSecond,
                                    SerType::BINARY),
            "cannot save second ciphertext container");
    @SAVE_VALID@
    require(Serial::SerializeToFile(argv[@RESULT_CT@], encryptedResult,
                                    SerType::BINARY),
            "cannot save result ciphertext container");

    auto decrypted = @ENTRY@__decrypt__result0(
        context, encryptedResult, keys.secretKey);
    // HEIR's generated sum may reuse ciphertext storage in place. The feature
    // artifact and audit therefore happen before sum consumes this edge.
    const auto sumStarted = std::chrono::steady_clock::now();
    auto encryptedSum = encrypted_sum(context, encryptedResult);
    const auto sumAuditStarted = std::chrono::steady_clock::now();
    require(Serial::SerializeToFile(argv[@SUM_CT@], encryptedSum,
                                    SerType::BINARY),
            "cannot save sum ciphertext");
    const double decryptedSum = encrypted_sum__decrypt__result0(
        context, encryptedSum, keys.secretKey);
    const auto done = std::chrono::steady_clock::now();

    std::ofstream audit(argv[@AUDIT@]);
    audit << "value\n" << std::setprecision(17);
    for (const auto& value : decrypted) audit << value << '\n';

    std::ofstream metrics(argv[@METRICS@]);
    metrics << "{\n"
            << "  \"encryption_seconds\": "
            << std::chrono::duration<double>(featureStarted - encryptionStarted).count()
            << ",\n  \"encrypted_feature_seconds\": "
            << std::chrono::duration<double>(featureAuditStarted - featureStarted).count()
            << ",\n  \"feature_audit_seconds\": "
            << std::chrono::duration<double>(sumStarted - featureAuditStarted).count()
            << ",\n  \"encrypted_sum_seconds\": "
            << std::chrono::duration<double>(sumAuditStarted - sumStarted).count()
            << ",\n  \"sum_audit_seconds\": "
            << std::chrono::duration<double>(done - sumAuditStarted).count()
            << ",\n  \"sum_audit\": " << std::setprecision(17) << decryptedSum
            << "\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
'''


def run(
    command: list[str], cwd: Path, output: Path | None = None
) -> tuple[float, str]:
    started = time.perf_counter()
    if output:
        with output.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(
                command, cwd=cwd, text=True, stdout=handle, stderr=subprocess.PIPE
            )
        text = completed.stderr
    else:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        text = completed.stdout + completed.stderr
    if completed.returncode:
        detail = text if text.strip() else "(process produced no stdout or stderr)"
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: "
            f"{' '.join(command)}\n{detail}"
        )
    return time.perf_counter() - started, text


def generate(
    directory: Path,
    entry: str,
    mlir: str,
    size: int,
    heir_opt: str,
    heir_translate: str,
    arity: int,
    ckks_mul_depth: int,
) -> dict[str, Any]:
    directory.mkdir()
    source = (directory / "source.mlir").resolve()
    source.write_text(mlir, encoding="utf-8")
    lowered = directory / "lowered_openfhe.mlir"
    header = directory / "heir_output.h"
    cpp = directory / "heir_output.cpp"
    lower, _ = run(
        [
            heir_opt,
            f"--mlir-to-ckks=ciphertext-degree={size}",
            f"--scheme-to-openfhe=entry-function={entry}",
            str(source),
        ],
        directory,
        lowered,
    )
    header_seconds, _ = run(
        [
            heir_translate,
            "--emit-openfhe-pke-header",
            "--openfhe-include-type=install-relative",
            str(lowered.resolve()),
        ],
        directory,
        header,
    )
    cpp_seconds, _ = run(
        [
            heir_translate,
            "--emit-openfhe-pke",
            "--openfhe-include-type=install-relative",
            str(lowered.resolve()),
        ],
        directory,
        cpp,
    )
    translated = cpp.read_text(encoding="utf-8", errors="replace")
    generated_cpp, inferred_depth = patch_translated_mul_depth(
        translated, ckks_mul_depth
    )
    cpp.write_text(generated_cpp, encoding="utf-8")
    generated = header.read_text(errors="replace") + cpp.read_text(errors="replace")
    required = (
        entry,
        *(f"{entry}__encrypt__arg{index}" for index in range(arity)),
        f"{entry}__decrypt__result0",
    )
    missing = [symbol for symbol in required if symbol not in generated]
    if missing or ("CKKS" not in generated and "CryptoContextCKKSRNS" not in generated):
        raise ValueError(f"unexpected generated CKKS source; missing={missing}")
    return {
        "entry": entry,
        "ckks_context": {
            "inferred_multiplicative_depth": inferred_depth,
            "requested_multiplicative_depth": ckks_mul_depth,
            "method": "patch translated OpenFHE context for installed-HEIR compatibility",
        },
        "source_sha256": sha256_file(source),
        "header_sha256": sha256_file(header),
        "cpp_sha256": sha256_file(cpp),
        "timings_seconds": {
            "lower": lower,
            "header": header_seconds,
            "cpp": cpp_seconds,
        },
    }


def copy_generated_sources(source_dir: Path, work: Path, prefix: str) -> None:
    """Rename one HEIR output pair so two generated kernels can link together."""
    header = (source_dir / "heir_output.h").read_text(encoding="utf-8")
    header = header.replace("HEIR_OUTPUT", f"{prefix.upper()}_OUTPUT")
    (work / f"{prefix}_output.h").write_text(header, encoding="utf-8")
    cpp = (source_dir / "heir_output.cpp").read_text(encoding="utf-8")
    cpp = cpp.replace('"heir_output.h"', f'"{prefix}_output.h"')
    (work / f"{prefix}_output.cpp").write_text(cpp, encoding="utf-8")


def execute(
    directory: Path,
    sum_directory: Path,
    entry: str,
    size: int,
    first: Path,
    second: Path,
    valid: Path | None,
    openfhe_dir: str,
) -> tuple[list[float], dict[str, Any]]:
    has_valid = valid is not None
    work = directory / "runner"
    work.mkdir()
    copy_generated_sources(directory, work, "feature")
    copy_generated_sources(sum_directory, work, "sum")

    options = (
        {
            "@ARGC@": "11",
            "@READ_VALID@": 'const auto valid = readVector(argv[3]); require(valid.size() == @VECTOR_SIZE@, "bad validity size");',
            "@ENCRYPT_VALID@": "auto encryptedValid = @ENTRY@__encrypt__arg2(context, valid, keys.publicKey);",
            "@CALL_ARGUMENTS@": "encryptedFirst, encryptedSecond, encryptedValid",
            "@FIRST_CT@": "4",
            "@SECOND_CT@": "5",
            "@SAVE_VALID@": 'require(Serial::SerializeToFile(argv[6], encryptedValid, SerType::BINARY), "cannot save validity ciphertext container");',
            "@RESULT_CT@": "7",
            "@SUM_CT@": "8",
            "@AUDIT@": "9",
            "@METRICS@": "10",
        }
        if has_valid
        else {
            "@ARGC@": "9",
            "@READ_VALID@": "",
            "@ENCRYPT_VALID@": "",
            "@CALL_ARGUMENTS@": "encryptedFirst, encryptedSecond",
            "@FIRST_CT@": "3",
            "@SECOND_CT@": "4",
            "@SAVE_VALID@": "",
            "@RESULT_CT@": "5",
            "@SUM_CT@": "6",
            "@AUDIT@": "7",
            "@METRICS@": "8",
        }
    )
    source = RUNNER.replace("@ENTRY@", entry).replace("@VECTOR_SIZE@", str(size))
    for marker, value in options.items():
        source = source.replace(
            marker,
            value.replace("@ENTRY@", entry).replace("@VECTOR_SIZE@", str(size)),
        )
    (work / "ciphertext_runner.cpp").write_text(source, encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")

    build = work / "build"
    configure = ["cmake", "-S", str(work.resolve()), "-B", str(build.resolve())]
    if openfhe_dir:
        configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    configure_seconds, configure_log = run(configure, work)
    build_seconds, build_log = run(
        ["cmake", "--build", str(build.resolve()), "--target", "ciphertext_runner"],
        work,
    )
    (work / "build.log").write_text(configure_log + build_log, encoding="utf-8")

    ciphertexts = directory / "ciphertexts"
    ciphertexts.mkdir()
    audit = directory / "decrypted_audit.csv"
    metrics = directory / "metrics.json"
    command = [
        str((build / "ciphertext_runner").resolve()),
        str(first.resolve()),
        str(second.resolve()),
    ]
    if has_valid:
        command += [
            str(valid.resolve()),
            str((ciphertexts / "first.ct").resolve()),
            str((ciphertexts / "second.ct").resolve()),
            str((ciphertexts / "validity.ct").resolve()),
            str((ciphertexts / "result.ct").resolve()),
            str((ciphertexts / "sum.ct").resolve()),
            str(audit.resolve()),
            str(metrics.resolve()),
        ]
    else:
        command += [
            str((ciphertexts / "first.ct").resolve()),
            str((ciphertexts / "second.ct").resolve()),
            str((ciphertexts / "result.ct").resolve()),
            str((ciphertexts / "sum.ct").resolve()),
            str(audit.resolve()),
            str(metrics.resolve()),
        ]
    wall, log = run(command, work)
    (work / "runner.log").write_text(log, encoding="utf-8")

    values = [float(row["value"]) for row in read_csv(audit)]
    result = json.loads(metrics.read_text())
    result.update(
        {
            "build_seconds": {
                "configure": configure_seconds,
                "build": build_seconds,
            },
            "runner_wall_seconds": wall,
            "ciphertext_files": [
                str(item) for item in sorted(ciphertexts.glob("*.ct"))
            ],
            "scope": "feature calculation followed only by encrypted sum",
        }
    )
    return values, result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--ckks-mul-depth", type=int, default=12)
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

    inputs = root / "plaintext_inputs"
    inputs.mkdir()
    payment = [row["AMT_PAYMENT"] for row in DEMO_ROWS]
    installment = [row["AMT_INSTALMENT"] for row in DEMO_ROWS]
    padding = [0.0] * (args.vector_size - len(DEMO_ROWS))
    payment_path = inputs / "amt_payment.csv"
    installment_path = inputs / "amt_installment.csv"
    valid_path = inputs / "validity.csv"
    write_values(payment_path, payment + padding)
    write_values(installment_path, installment + padding)
    write_values(valid_path, [1.0] * len(DEMO_ROWS) + padding)

    perc_dir = root / "payment_perc"
    diff_dir = root / "payment_diff"
    sum_dir = root / "sum_kernel"
    generated = {
        "sum": generate(
            sum_dir,
            "encrypted_sum",
            encrypted_sum_mlir(args.vector_size),
            args.vector_size,
            args.heir_opt,
            args.heir_translate,
            1,
            args.ckks_mul_depth,
        ),
        "payment_perc": generate(
            perc_dir,
            "payment_perc_newton",
            payment_perc_newton_mlir(args.vector_size),
            args.vector_size,
            args.heir_opt,
            args.heir_translate,
            3,
            args.ckks_mul_depth,
        ),
        "payment_diff": generate(
            diff_dir,
            "encrypted_subtract",
            binary_mlir(args.vector_size, "subtract"),
            args.vector_size,
            args.heir_opt,
            args.heir_translate,
            2,
            args.ckks_mul_depth,
        ),
    }
    perc, perc_info = execute(
        perc_dir,
        sum_dir,
        "payment_perc_newton",
        args.vector_size,
        payment_path,
        installment_path,
        valid_path,
        args.openfhe_dir,
    )
    diff, diff_info = execute(
        diff_dir,
        sum_dir,
        "encrypted_subtract",
        args.vector_size,
        installment_path,
        payment_path,
        None,
        args.openfhe_dir,
    )

    rows = []
    for index, (paid, due) in enumerate(zip(payment, installment)):
        expected_perc = paid / due
        expected_diff = due - paid
        rows.append(
            {
                "row": index,
                "AMT_PAYMENT": paid,
                "AMT_INSTALMENT": due,
                "PAYMENT_PERC_python": expected_perc,
                "PAYMENT_PERC_he": perc[index],
                "PAYMENT_PERC_abs_error": abs(expected_perc - perc[index]),
                "PAYMENT_DIFF_python": expected_diff,
                "PAYMENT_DIFF_he": diff[index],
                "PAYMENT_DIFF_abs_error": abs(expected_diff - diff[index]),
            }
        )
    write_csv(root / "comparison.csv", list(rows[0]), rows)
    sum_rows = [
        {
            "feature": "PAYMENT_PERC",
            "python_sum": sum(row["PAYMENT_PERC_python"] for row in rows),
            "he_sum": perc_info["sum_audit"],
            "absolute_error": abs(
                sum(row["PAYMENT_PERC_python"] for row in rows)
                - perc_info["sum_audit"]
            ),
        },
        {
            "feature": "PAYMENT_DIFF",
            "python_sum": sum(row["PAYMENT_DIFF_python"] for row in rows),
            "he_sum": diff_info["sum_audit"],
            "absolute_error": abs(
                sum(row["PAYMENT_DIFF_python"] for row in rows)
                - diff_info["sum_audit"]
            ),
        },
    ]
    write_csv(root / "sum_comparison.csv", list(sum_rows[0]), sum_rows)
    result = {
        "status": "heir_generated_ckks_executed",
        "scope": "PAYMENT_PERC and PAYMENT_DIFF followed only by encrypted sum",
        "generated": generated,
        "execution": {"payment_perc": perc_info, "payment_diff": diff_info},
        "comparison": rows,
        "sum_comparison": sum_rows,
        "note": "The separate HEIR encrypted_sum kernel consumes each feature ciphertext directly in the same CKKS context. No mean, variance, grouping, or max is run.",
    }
    write_json(root / "result.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "comparison": rows,
                "sum_comparison": sum_rows,
                "output_dir": str(root),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
