#!/usr/bin/env python3
"""Execute one encrypted PAYMENT_DIFF -> moments -> mean/variance chain."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv, write_csv, write_json, write_values
from code.heir.examples.quick_installments_features import DEMO_ROWS
from code.heir.kernels.moments import moments_mlir
from code.heir.kernels.statistics import mean_sample_variance_mlir
from code.heir.operations.columns import binary_mlir
from code.heir.scripts.run_payment_features_ciphertext_demo import (
    copy_generated_sources,
    generate,
    run,
)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_diff_moments LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(moments_runner feature_output.cpp moments_output.cpp final_output.cpp moments_runner.cpp)
target_include_directories(moments_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(moments_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(moments_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(moments_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''

RUNNER = r'''
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <tuple>
#include <vector>
#include "feature_output.h"
#include "moments_output.h"
#include "final_output.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;
std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path); if (!input) throw std::runtime_error("cannot open input");
  std::string line; std::getline(input, line); std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
int main(int argc, char** argv) {
  if (argc != 12) return 2;
  try {
    auto due = readVector(argv[1]); auto paid = readVector(argv[2]); auto valid = readVector(argv[3]);
    require(due.size() == @SIZE@ && paid.size() == @SIZE@ && valid.size() == @SIZE@, "bad vector size");
    auto context = encrypted_subtract__generate_crypto_context(); auto keys = context->KeyGen();
    require(keys.good(), "key generation failed");
    context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    context = moments__configure_crypto_context(context, keys.secretKey);
    context = mean_sample_variance__configure_crypto_context(context, keys.secretKey);
    auto encryptedDue = encrypted_subtract__encrypt__arg0(context, due, keys.publicKey);
    auto encryptedPaid = encrypted_subtract__encrypt__arg1(context, paid, keys.publicKey);
    auto encryptedValid = moments__encrypt__arg1(context, valid, keys.publicKey);
    auto encryptedFeature = encrypted_subtract(context, encryptedDue, encryptedPaid);
    require(Serial::SerializeToFile(argv[4], encryptedFeature, SerType::BINARY), "cannot save feature ciphertext");
    auto featureAudit = encrypted_subtract__decrypt__result0(context, encryptedFeature, keys.secretKey);
    auto encryptedMoments = moments(context, encryptedFeature, encryptedValid);
    auto encryptedCount = std::get<0>(encryptedMoments); auto encryptedSum = std::get<1>(encryptedMoments); auto encryptedSquares = std::get<2>(encryptedMoments);
    auto encryptedFinal = mean_sample_variance(context, encryptedCount, encryptedSum, encryptedSquares);
    auto encryptedMean = std::get<0>(encryptedFinal); auto encryptedVariance = std::get<1>(encryptedFinal);
    require(Serial::SerializeToFile(argv[5], encryptedCount, SerType::BINARY), "cannot save count ciphertext");
    require(Serial::SerializeToFile(argv[6], encryptedSum, SerType::BINARY), "cannot save sum ciphertext");
    require(Serial::SerializeToFile(argv[7], encryptedSquares, SerType::BINARY), "cannot save sum-square ciphertext");
    require(Serial::SerializeToFile(argv[8], encryptedMean, SerType::BINARY), "cannot save mean ciphertext");
    require(Serial::SerializeToFile(argv[9], encryptedVariance, SerType::BINARY), "cannot save variance ciphertext");
    auto count = moments__decrypt__result0(context, encryptedCount, keys.secretKey);
    auto sum = moments__decrypt__result1(context, encryptedSum, keys.secretKey);
    auto squares = moments__decrypt__result2(context, encryptedSquares, keys.secretKey);
    auto mean = mean_sample_variance__decrypt__result0(context, encryptedMean, keys.secretKey);
    auto variance = mean_sample_variance__decrypt__result1(context, encryptedVariance, keys.secretKey);
    std::ofstream audit(argv[10]); audit << "value\n" << std::setprecision(17); for (auto value : featureAudit) audit << value << '\n';
    std::ofstream metrics(argv[11]); metrics << std::setprecision(17) << "{\"count\":" << count << ",\"sum\":" << sum << ",\"sum_squares\":" << squares << ",\"mean\":" << mean << ",\"sample_variance\":" << variance << "}\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--ckks-mul-depth", type=int, default=24)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    args = parser.parse_args()
    if args.vector_size < len(DEMO_ROWS): raise ValueError("vector size must fit demo rows")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite: raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    inputs = root / "plaintext_inputs"; inputs.mkdir()
    due = [row["AMT_INSTALMENT"] for row in DEMO_ROWS]; paid = [row["AMT_PAYMENT"] for row in DEMO_ROWS]
    padding = [0.0] * (args.vector_size - len(DEMO_ROWS)); valid = [1.0] * len(DEMO_ROWS) + padding
    write_values(inputs / "due.csv", due + padding); write_values(inputs / "paid.csv", paid + padding); write_values(inputs / "valid.csv", valid)
    feature_dir, moments_dir, final_dir = root / "feature", root / "moments", root / "finalization"
    generated = {
        "feature": generate(feature_dir, "encrypted_subtract", binary_mlir(args.vector_size, "subtract"), args.vector_size, args.heir_opt, args.heir_translate, 2, args.ckks_mul_depth),
        "moments": generate(moments_dir, "moments", moments_mlir(args.vector_size), args.vector_size, args.heir_opt, args.heir_translate, 2, args.ckks_mul_depth),
        "finalization": generate(final_dir, "mean_sample_variance", mean_sample_variance_mlir(), args.vector_size, args.heir_opt, args.heir_translate, 3, args.ckks_mul_depth),
    }
    work = root / "runner"; work.mkdir(); copy_generated_sources(feature_dir, work, "feature"); copy_generated_sources(moments_dir, work, "moments"); copy_generated_sources(final_dir, work, "final")
    (work / "moments_runner.cpp").write_text(RUNNER.replace("@SIZE@", str(args.vector_size)), encoding="utf-8"); (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"; configure = ["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"]
    run(configure, work); run(["cmake", "--build", str(build.resolve()), "--target", "moments_runner"], work)
    ciphertexts = root / "ciphertexts"; ciphertexts.mkdir(); audit = root / "feature_audit.csv"; metrics = root / "metrics.json"
    command = [str((build / "moments_runner").resolve()), str((inputs / "due.csv").resolve()), str((inputs / "paid.csv").resolve()), str((inputs / "valid.csv").resolve()), str((ciphertexts / "payment_diff.ct").resolve()), str((ciphertexts / "count.ct").resolve()), str((ciphertexts / "sum.ct").resolve()), str((ciphertexts / "sum_squares.ct").resolve()), str((ciphertexts / "mean.ct").resolve()), str((ciphertexts / "variance.ct").resolve()), str(audit.resolve()), str(metrics.resolve())]
    run(command, work)
    feature_values = [float(row["value"]) for row in read_csv(audit)]; actual = json.loads(metrics.read_text())
    expected_values = [left - right for left, right in zip(due, paid)]; expected_sum = sum(expected_values); expected_mean = expected_sum / len(expected_values); expected_var = sum((value - expected_mean) ** 2 for value in expected_values) / (len(expected_values) - 1)
    rows = [{"row": index, "python": expected, "he": feature_values[index], "absolute_error": abs(expected - feature_values[index])} for index, expected in enumerate(expected_values)]
    summary = [{"statistic": "count", "python": 3.0, "he": actual["count"]}, {"statistic": "sum", "python": expected_sum, "he": actual["sum"]}, {"statistic": "mean", "python": expected_mean, "he": actual["mean"]}, {"statistic": "sample_variance", "python": expected_var, "he": actual["sample_variance"]}]
    for row in summary: row["absolute_error"] = abs(row["python"] - row["he"])
    write_csv(root / "feature_comparison.csv", list(rows[0]), rows); write_csv(root / "statistics_comparison.csv", list(summary[0]), summary)
    write_json(root / "result.json", {"status": "encrypted_payment_diff_moments_executed", "scope": "one feature ciphertext consumed once by moments then encrypted mean/sample-variance finalization", "generated": generated, "feature_comparison": rows, "statistics_comparison": summary})
    print(json.dumps({"status": "encrypted_payment_diff_moments_executed", "statistics_comparison": summary}, indent=2))


if __name__ == "__main__": main()
