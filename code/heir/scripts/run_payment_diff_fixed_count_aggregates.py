#!/usr/bin/env python3
"""Run encrypted PAYMENT_DIFF -> sum/mean/variance for one fixed-size group.

``max`` is deliberately reported as pending: it requires the separate
CKKS-to-FHEW comparison lane and is not fabricated by this CKKS runner.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv, write_csv, write_json, write_values
from code.heir.examples.quick_installments_features import DEMO_ROWS
from code.heir.kernels.fixed_count_statistics import (
    fixed_count_mean_mlir,
    fixed_count_statistics_reference,
    fixed_count_sum_mlir,
    fixed_count_variance_mlir,
)
from code.heir.operations.columns import binary_mlir
from code.heir.scripts.run_payment_features_ciphertext_demo import (
    copy_generated_sources,
    generate,
    run,
)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_diff_fixed_count_aggregates LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(aggregate_runner feature_output.cpp sum_output.cpp mean_output.cpp variance_output.cpp aggregate_runner.cpp)
target_include_directories(aggregate_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(aggregate_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(aggregate_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(aggregate_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "mean_output.h"
#include "variance_output.h"
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
Ciphertext<DCRTPoly> loadCiphertext(const char* path) {
  Ciphertext<DCRTPoly> ciphertext;
  require(Serial::DeserializeFromFile(path, ciphertext, SerType::BINARY), "cannot load source ciphertext branch");
  return ciphertext;
}
int main(int argc, char** argv) {
  if (argc != 9) return 2;
  try {
    auto due = readVector(argv[1]); auto paid = readVector(argv[2]);
    require(due.size() == @SIZE@ && paid.size() == @SIZE@, "bad vector size");
    // Variance owns the shared context: it is the deepest ordinary CKKS branch.
    auto context = fixed_count_variance__generate_crypto_context(); auto keys = context->KeyGen();
    require(keys.good(), "key generation failed");
    context = fixed_count_variance__configure_crypto_context(context, keys.secretKey);
    context = fixed_count_mean__configure_crypto_context(context, keys.secretKey);
    context = fixed_count_sum__configure_crypto_context(context, keys.secretKey);
    context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    auto started = std::chrono::steady_clock::now();
    auto encryptedDue = encrypted_subtract__encrypt__arg0(context, due, keys.publicKey);
    auto encryptedPaid = encrypted_subtract__encrypt__arg1(context, paid, keys.publicKey);
    auto afterEncryption = std::chrono::steady_clock::now();
    auto encryptedFeature = encrypted_subtract(context, encryptedDue, encryptedPaid);
    // This artifact is the source of truth. Each consumer gets an independent
    // deserialized branch because generated HEIR code may mutate its input.
    require(Serial::SerializeToFile(argv[3], encryptedFeature, SerType::BINARY), "cannot save feature");
    auto featureAudit = encrypted_subtract__decrypt__result0(context, encryptedFeature, keys.secretKey);
    auto afterFeature = std::chrono::steady_clock::now();
    auto encryptedSum = fixed_count_sum(context, loadCiphertext(argv[3]));
    auto encryptedMean = fixed_count_mean(context, loadCiphertext(argv[3]));
    auto encryptedVariance = fixed_count_variance(context, loadCiphertext(argv[3]));
    require(Serial::SerializeToFile(argv[4], encryptedSum, SerType::BINARY), "cannot save sum");
    require(Serial::SerializeToFile(argv[5], encryptedMean, SerType::BINARY), "cannot save mean");
    require(Serial::SerializeToFile(argv[6], encryptedVariance, SerType::BINARY), "cannot save variance");
    auto sum = fixed_count_sum__decrypt__result0(context, encryptedSum, keys.secretKey);
    auto mean = fixed_count_mean__decrypt__result0(context, encryptedMean, keys.secretKey);
    auto variance = fixed_count_variance__decrypt__result0(context, encryptedVariance, keys.secretKey);
    auto done = std::chrono::steady_clock::now();
    std::ofstream audit(argv[7]); audit << "value\n" << std::setprecision(17);
    for (auto value : featureAudit) audit << value << '\n';
    std::ofstream metrics(argv[8]); metrics << std::setprecision(17)
      << "{\"sum\":" << sum << ",\"mean\":" << mean << ",\"sample_variance\":" << variance
      << ",\"encryption_seconds\":" << std::chrono::duration<double>(afterEncryption-started).count()
      << ",\"feature_seconds\":" << std::chrono::duration<double>(afterFeature-afterEncryption).count()
      << ",\"statistics_seconds\":" << std::chrono::duration<double>(done-afterFeature).count() << "}\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--vector-size", type=int, default=8)
    parser.add_argument("--ckks-mul-depth", type=int, default=4)
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    args = parser.parse_args()
    valid_count = len(DEMO_ROWS)
    if args.vector_size < valid_count:
        raise ValueError("vector size must fit the three review rows")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    inputs = root / "plaintext_inputs"; inputs.mkdir()
    due = [row["AMT_INSTALMENT"] for row in DEMO_ROWS]
    paid = [row["AMT_PAYMENT"] for row in DEMO_ROWS]
    padding = [0.0] * (args.vector_size - valid_count)
    due_path, paid_path = inputs / "amt_installment.csv", inputs / "amt_payment.csv"
    write_values(due_path, due + padding); write_values(paid_path, paid + padding)
    feature_dir, sum_dir, mean_dir, variance_dir = root / "feature", root / "sum", root / "mean", root / "variance"
    generated = {
        "payment_diff": generate(feature_dir, "encrypted_subtract", binary_mlir(args.vector_size, "subtract"), args.vector_size, args.heir_opt, args.heir_translate, 2, args.ckks_mul_depth),
        "sum": generate(sum_dir, "fixed_count_sum", fixed_count_sum_mlir(args.vector_size, valid_count), args.vector_size, args.heir_opt, args.heir_translate, 1, args.ckks_mul_depth),
        "mean": generate(mean_dir, "fixed_count_mean", fixed_count_mean_mlir(args.vector_size, valid_count), args.vector_size, args.heir_opt, args.heir_translate, 1, args.ckks_mul_depth),
        "variance": generate(variance_dir, "fixed_count_variance", fixed_count_variance_mlir(args.vector_size, valid_count), args.vector_size, args.heir_opt, args.heir_translate, 1, args.ckks_mul_depth),
    }
    work = root / "runner"; work.mkdir()
    for directory, prefix in ((feature_dir, "feature"), (sum_dir, "sum"), (mean_dir, "mean"), (variance_dir, "variance")):
        copy_generated_sources(directory, work, prefix)
    (work / "aggregate_runner.cpp").write_text(RUNNER.replace("@SIZE@", str(args.vector_size)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    run(["cmake", "--build", str(build.resolve()), "--target", "aggregate_runner"], work)
    ciphertexts = root / "ciphertexts"; ciphertexts.mkdir()
    audit, metrics = root / "feature_audit.csv", root / "metrics.json"
    run([str((build / "aggregate_runner").resolve()), str(due_path.resolve()), str(paid_path.resolve()), str((ciphertexts / "payment_diff.ct").resolve()), str((ciphertexts / "sum.ct").resolve()), str((ciphertexts / "mean.ct").resolve()), str((ciphertexts / "variance.ct").resolve()), str(audit.resolve()), str(metrics.resolve())], work)
    feature = [float(row["value"]) for row in read_csv(audit)][:valid_count]
    expected_feature = [left - right for left, right in zip(due, paid)]
    expected_sum, expected_mean, expected_variance = fixed_count_statistics_reference(expected_feature)
    he = json.loads(metrics.read_text(encoding="utf-8"))
    feature_rows = [{"row": index, "python": expected, "he": feature[index], "absolute_error": abs(expected - feature[index])} for index, expected in enumerate(expected_feature)]
    aggregation_rows = [
        {"aggregation": "max", "python": max(expected_feature), "he": "NOT_RUN", "status": "pending CKKS-to-FHEW comparison/switching benchmark", "absolute_error": ""},
        {"aggregation": "mean", "python": expected_mean, "he": he["mean"], "status": "encrypted CKKS (public fixed count=3)", "absolute_error": abs(expected_mean - he["mean"])},
        {"aggregation": "sum", "python": expected_sum, "he": he["sum"], "status": "encrypted CKKS", "absolute_error": abs(expected_sum - he["sum"])},
        {"aggregation": "var", "python": expected_variance, "he": he["sample_variance"], "status": "encrypted CKKS sample variance (public fixed count=3)", "absolute_error": abs(expected_variance - he["sample_variance"])},
    ]
    write_csv(root / "feature_comparison.csv", list(feature_rows[0]), feature_rows)
    write_csv(root / "aggregation_comparison.csv", list(aggregation_rows[0]), aggregation_rows)
    result = {"status": "heir_generated_ckks_executed", "scope": "PAYMENT_DIFF then sum/mean/sample variance from independently loaded branches of the same encrypted source feature; count fixed publicly at 3", "important_limit": "max is intentionally not executed; variable/private group counts require the separate encrypted-count finalizer, currently an OOM bootstrap experiment on the small server", "generated": generated, "feature_comparison": feature_rows, "aggregation_comparison": aggregation_rows, "execution": he, "ciphertext_artifacts": [str(item) for item in sorted(ciphertexts.glob("*.ct"))]}
    write_json(root / "result.json", result)
    print(json.dumps({"status": result["status"], "aggregation_comparison": aggregation_rows, "output_dir": str(root)}, indent=2))


if __name__ == "__main__":
    main()
