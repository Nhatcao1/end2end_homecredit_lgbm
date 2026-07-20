#!/usr/bin/env python3
"""Encrypt two raw columns, calculate PAYMENT_PERC/PAYMENT_DIFF, audit-decrypt."""

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
from code.heir.examples.quick_installments_features import DEMO_ROWS, payment_perc_newton_mlir
from code.heir.operations.columns import binary_mlir


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_features_demo LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(ciphertext_runner heir_output.cpp ciphertext_runner.cpp)
target_include_directories(ciphertext_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
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
#include "heir_output.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;

std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path.string());
  std::string line; std::getline(input, line); std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}
void require(bool ok, const std::string& message) { if (!ok) throw std::runtime_error(message); }

int main(int argc, char** argv) {
  if (argc != 12) return 2;
  try {
    const auto first = readVector(argv[1]); const auto second = readVector(argv[2]);
    const auto groupMask = readVector(argv[3]);
    const double publicGroupCount = std::stod(argv[11]);
    require(first.size() == @VECTOR_SIZE@ && second.size() == @VECTOR_SIZE@ &&
                groupMask.size() == @VECTOR_SIZE@,
            "input vectors must equal generated vector size");
    require(publicGroupCount > 1.0, "sample variance requires group count greater than one");
    auto context = @ENTRY@__generate_crypto_context(); auto keys = context->KeyGen();
    require(keys.good(), "OpenFHE key generation failed");
    context = @ENTRY@__configure_crypto_context(context, keys.secretKey);
    context->EvalSumKeyGen(keys.secretKey);
    const auto encryptionStarted = std::chrono::steady_clock::now();
    auto encryptedFirst = @ENTRY@__encrypt__arg0(context, first, keys.publicKey);
    auto encryptedSecond = @ENTRY@__encrypt__arg1(context, second, keys.publicKey);
    @ENCRYPT_MASK@
    const auto evaluationStarted = std::chrono::steady_clock::now();
    auto encryptedResult = @ENTRY@(context, @CALL_ARGUMENTS@);
    const auto aggregationStarted = std::chrono::steady_clock::now();
    auto encryptedMasked = context->EvalMult(encryptedResult, encryptedMask);
    auto encryptedCount = context->EvalSum(encryptedMask, @VECTOR_SIZE@);
    auto encryptedSum = context->EvalSum(encryptedMasked, @VECTOR_SIZE@);
    auto encryptedMean = context->EvalMult(encryptedSum, 1.0 / publicGroupCount);
    auto encryptedSquare = context->EvalMult(encryptedResult, encryptedResult);
    auto encryptedMaskedSquare = context->EvalMult(encryptedSquare, encryptedMask);
    auto encryptedSumSquares = context->EvalSum(encryptedMaskedSquare, @VECTOR_SIZE@);
    auto encryptedSumSquared = context->EvalMult(encryptedSum, encryptedSum);
    auto encryptedCorrection = context->EvalMult(encryptedSumSquared, 1.0 / publicGroupCount);
    auto encryptedVarianceNumerator = context->EvalSub(encryptedSumSquares, encryptedCorrection);
    auto encryptedVariance = context->EvalMult(encryptedVarianceNumerator, 1.0 / (publicGroupCount - 1.0));
    const auto auditStarted = std::chrono::steady_clock::now();
    require(Serial::SerializeToFile(argv[4], encryptedFirst, SerType::BINARY), "cannot save first ciphertext");
    require(Serial::SerializeToFile(argv[5], encryptedSecond, SerType::BINARY), "cannot save second ciphertext");
    require(Serial::SerializeToFile(argv[6], encryptedMask, SerType::BINARY), "cannot save group-mask ciphertext");
    require(Serial::SerializeToFile(argv[7], encryptedResult, SerType::BINARY), "cannot save result ciphertext");
    const std::filesystem::path aggregateDir(argv[10]);
    std::filesystem::create_directories(aggregateDir);
    require(Serial::SerializeToFile((aggregateDir / "count.ct").string(), encryptedCount, SerType::BINARY), "cannot save count ciphertext");
    require(Serial::SerializeToFile((aggregateDir / "sum.ct").string(), encryptedSum, SerType::BINARY), "cannot save sum ciphertext");
    require(Serial::SerializeToFile((aggregateDir / "mean.ct").string(), encryptedMean, SerType::BINARY), "cannot save mean ciphertext");
    require(Serial::SerializeToFile((aggregateDir / "var.ct").string(), encryptedVariance, SerType::BINARY), "cannot save variance ciphertext");
    auto decrypted = @ENTRY@__decrypt__result0(context, encryptedResult, keys.secretKey);
    auto countAudit = @ENTRY@__decrypt__result0(context, encryptedCount, keys.secretKey);
    auto sumAudit = @ENTRY@__decrypt__result0(context, encryptedSum, keys.secretKey);
    auto meanAudit = @ENTRY@__decrypt__result0(context, encryptedMean, keys.secretKey);
    auto varianceAudit = @ENTRY@__decrypt__result0(context, encryptedVariance, keys.secretKey);
    const auto done = std::chrono::steady_clock::now();
    std::ofstream audit(argv[8]); audit << "value\n" << std::setprecision(17);
    for (const auto& value : decrypted) audit << value << '\n';
    std::ofstream metrics(argv[9]); metrics << std::setprecision(17);
    metrics << "{\n  \"encryption_seconds\": " << std::chrono::duration<double>(evaluationStarted-encryptionStarted).count()
            << ",\n  \"encrypted_feature_seconds\": " << std::chrono::duration<double>(aggregationStarted-evaluationStarted).count()
            << ",\n  \"encrypted_aggregation_seconds\": " << std::chrono::duration<double>(auditStarted-aggregationStarted).count()
            << ",\n  \"audit_seconds\": " << std::chrono::duration<double>(done-auditStarted).count()
            << ",\n  \"aggregate_audit\": {\"count\": " << countAudit[0]
            << ", \"sum\": " << sumAudit[0] << ", \"mean\": " << meanAudit[0]
            << ", \"var\": " << varianceAudit[0] << "}\n}\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def run(command: list[str], cwd: Path, output: Path | None = None) -> tuple[float, str]:
    started = time.perf_counter()
    if output:
        with output.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(command, cwd=cwd, text=True, stdout=handle, stderr=subprocess.PIPE)
        text = completed.stderr
    else:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
        text = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{text}")
    return time.perf_counter() - started, text


def generate(directory: Path, entry: str, mlir: str, size: int, heir_opt: str, heir_translate: str) -> dict[str, Any]:
    directory.mkdir()
    source = (directory / "source.mlir").resolve(); source.write_text(mlir, encoding="utf-8")
    lowered = directory / "lowered_openfhe.mlir"
    header = directory / "heir_output.h"
    original_cpp = directory / "heir_output.heir.cpp"
    cpp = directory / "heir_output.cpp"
    lower, _ = run([heir_opt, f"--mlir-to-ckks=ciphertext-degree={size}", f"--scheme-to-openfhe=entry-function={entry}", str(source)], directory, lowered)
    header_seconds, _ = run([heir_translate, "--emit-openfhe-pke-header", "--openfhe-include-type=install-relative", str(lowered.resolve())], directory, header)
    cpp_seconds, _ = run([heir_translate, "--emit-openfhe-pke", "--openfhe-include-type=install-relative", str(lowered.resolve())], directory, original_cpp)
    original_text = original_cpp.read_text(encoding="utf-8")
    patched_text, depth_edits = re.subn(
        r"SetMultiplicativeDepth\((\d+)\)",
        lambda match: f"SetMultiplicativeDepth({int(match.group(1)) + 4})",
        original_text,
    )
    if depth_edits == 0:
        raise ValueError("cannot extend generated CKKS depth for encrypted aggregation")
    cpp.write_text(patched_text, encoding="utf-8")
    content = header.read_text(errors="replace") + cpp.read_text(errors="replace")
    required = (entry, f"{entry}__encrypt__arg0", f"{entry}__encrypt__arg1", f"{entry}__decrypt__result0")
    missing = [symbol for symbol in required if symbol not in content]
    if missing or ("CKKS" not in content and "CryptoContextCKKSRNS" not in content):
        raise ValueError(f"unexpected generated CKKS source; missing={missing}")
    return {"entry": entry, "source_sha256": sha256_file(source), "header_sha256": sha256_file(header), "heir_cpp_sha256": sha256_file(original_cpp), "aggregation_depth_extended_cpp_sha256": sha256_file(cpp), "extra_multiplicative_depth": 4, "timings_seconds": {"lower": lower, "header": header_seconds, "cpp": cpp_seconds}}


def execute(directory: Path, entry: str, size: int, first: Path, second: Path, group_mask: Path, mask_is_function_arg: bool, openfhe_dir: str, group_count: int) -> tuple[list[float], dict[str, Any]]:
    work = directory / "runner"; work.mkdir()
    for name in ("heir_output.cpp", "heir_output.h"): shutil.copy2(directory / name, work / name)
    options = ({"@ENCRYPT_MASK@":"auto encryptedMask = @ENTRY@__encrypt__arg2(context, groupMask, keys.publicKey);", "@CALL_ARGUMENTS@":"encryptedFirst, encryptedSecond, encryptedMask"} if mask_is_function_arg else {"@ENCRYPT_MASK@":"auto encryptedMask = @ENTRY@__encrypt__arg0(context, groupMask, keys.publicKey);", "@CALL_ARGUMENTS@":"encryptedFirst, encryptedSecond"})
    source = RUNNER.replace("@ENTRY@", entry).replace("@VECTOR_SIZE@", str(size))
    for marker, value in options.items(): source = source.replace(marker, value.replace("@ENTRY@", entry).replace("@VECTOR_SIZE@", str(size)))
    (work / "ciphertext_runner.cpp").write_text(source, encoding="utf-8"); (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"; configure = ["cmake", "-S", str(work.resolve()), "-B", str(build.resolve())]
    if openfhe_dir: configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    configure_seconds, configure_log = run(configure, work); build_seconds, build_log = run(["cmake", "--build", str(build.resolve()), "--target", "ciphertext_runner"], work)
    (work / "build.log").write_text(configure_log + build_log, encoding="utf-8")
    ciphertexts = directory / "ciphertexts"; ciphertexts.mkdir(); aggregates = ciphertexts / "aggregates"
    audit, metrics = directory / "decrypted_audit.csv", directory / "metrics.json"
    command = [
        str((build / "ciphertext_runner").resolve()), str(first.resolve()), str(second.resolve()), str(group_mask.resolve()),
        str((ciphertexts / "first.ct").resolve()), str((ciphertexts / "second.ct").resolve()),
        str((ciphertexts / "group_mask.ct").resolve()), str((ciphertexts / "result.ct").resolve()),
        str(audit.resolve()), str(metrics.resolve()), str(aggregates.resolve()), str(group_count),
    ]
    wall, log = run(command, work); (work / "runner.log").write_text(log, encoding="utf-8")
    values = [float(row["value"]) for row in read_csv(audit)]; result = json.loads(metrics.read_text())
    result.update({"build_seconds":{"configure":configure_seconds,"build":build_seconds}, "runner_wall_seconds":wall, "ciphertext_files":[str(item) for item in sorted(ciphertexts.rglob("*.ct"))], "group_count_policy":"public demo metadata; feature values remain encrypted"})
    return values, result


def sample_statistics(values: list[float]) -> dict[str, float]:
    count = len(values)
    total = sum(values)
    mean = total / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    return {"count": float(count), "sum": total, "mean": mean, "var": variance}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vector-size", type=int, default=8); parser.add_argument("--heir-opt", default="heir-opt"); parser.add_argument("--heir-translate", default="heir-translate"); parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    args = parser.parse_args()
    if args.vector_size < len(DEMO_ROWS): raise ValueError("vector size must fit demo rows")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite: raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        if root == Path.cwd().resolve() or root == root.parent: raise ValueError("unsafe output directory")
        shutil.rmtree(root)
    root.mkdir(parents=True); inputs = root / "plaintext_inputs"; inputs.mkdir()
    payment, installment = [row["AMT_PAYMENT"] for row in DEMO_ROWS], [row["AMT_INSTALMENT"] for row in DEMO_ROWS]; pad = [0.0] * (args.vector_size-len(DEMO_ROWS))
    payment_path, installment_path, valid_path = inputs / "amt_payment.csv", inputs / "amt_installment.csv", inputs / "validity.csv"
    write_values(payment_path, payment+pad); write_values(installment_path, installment+pad); write_values(valid_path, [1.0]*len(DEMO_ROWS)+pad)
    perc_dir, diff_dir = root / "payment_perc", root / "payment_diff"
    generated = {"payment_perc":generate(perc_dir, "payment_perc_newton", payment_perc_newton_mlir(args.vector_size), args.vector_size, args.heir_opt, args.heir_translate), "payment_diff":generate(diff_dir, "encrypted_subtract", binary_mlir(args.vector_size, "subtract"), args.vector_size, args.heir_opt, args.heir_translate)}
    group_count = len(DEMO_ROWS)
    perc, perc_info = execute(perc_dir, "payment_perc_newton", args.vector_size, payment_path, installment_path, valid_path, True, args.openfhe_dir, group_count)
    diff, diff_info = execute(diff_dir, "encrypted_subtract", args.vector_size, installment_path, payment_path, valid_path, False, args.openfhe_dir, group_count)
    rows = [{"row":i, "AMT_PAYMENT":paid, "AMT_INSTALMENT":due, "PAYMENT_PERC_python":paid/due, "PAYMENT_PERC_he":perc[i], "PAYMENT_PERC_abs_error":abs(paid/due-perc[i]), "PAYMENT_DIFF_python":due-paid, "PAYMENT_DIFF_he":diff[i], "PAYMENT_DIFF_abs_error":abs(due-paid-diff[i])} for i,(paid,due) in enumerate(zip(payment,installment))]
    write_csv(root / "comparison.csv", list(rows[0]), rows)
    aggregate_rows = []
    for feature, plaintext_values, info in (
        ("PAYMENT_PERC", [paid / due for paid, due in zip(payment, installment)], perc_info),
        ("PAYMENT_DIFF", [due - paid for paid, due in zip(payment, installment)], diff_info),
    ):
        expected = sample_statistics(plaintext_values)
        actual = info["aggregate_audit"]
        for operation in ("count", "sum", "mean", "var"):
            aggregate_rows.append({"feature":feature, "operation":operation, "python":expected[operation], "he":actual[operation], "absolute_error":abs(expected[operation]-actual[operation])})
    write_csv(root / "aggregates_comparison.csv", list(aggregate_rows[0]), aggregate_rows)
    result = {"status":"heir_generated_ckks_executed", "generated":generated, "execution":{"payment_perc":perc_info,"payment_diff":diff_info}, "comparison":rows, "aggregates_comparison":aggregate_rows, "implemented_aggregations":["sum","mean","var"], "deferred_aggregations":["max"], "note":"Feature ciphertexts feed encrypted OpenFHE count/sum/mean/sample-var operations without intermediate decryption. PAYMENT_PERC is HEIR-generated approximate reciprocal CKKS; PAYMENT_DIFF is HEIR-generated CKKS subtraction."}
    write_json(root / "result.json", result); print(json.dumps({"status":result["status"],"comparison":rows,"output_dir":str(root)}, indent=2))


if __name__ == "__main__": main()
