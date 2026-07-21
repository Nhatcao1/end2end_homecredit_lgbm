#!/usr/bin/env python3
"""Execute a process-isolated encrypted PAYMENT_DIFF aggregation DAG.

One CKKS session is created exactly once. Every later process reloads that
session and one saved ciphertext artifact. This makes mutable HEIR/OpenFHE
ciphertext ownership explicit and observable.
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
project(payment_diff_process_dag LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(process_dag_runner feature_output.cpp sum_output.cpp mean_output.cpp variance_output.cpp process_dag_runner.cpp)
target_include_directories(process_dag_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(process_dag_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(process_dag_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(process_dag_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "key/evalkey-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;
using ContextT = CryptoContext<DCRTPoly>;
using CiphertextT = Ciphertext<DCRTPoly>;
using PublicKeyT = PublicKey<DCRTPoly>;
using PrivateKeyT = PrivateKey<DCRTPoly>;

void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
std::filesystem::path sessionFile(const std::filesystem::path& session, const char* name) { return session / name; }
std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path); if (!input) throw std::runtime_error("cannot open input");
  std::string line; std::getline(input, line); std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}
ContextT loadContext(const std::filesystem::path& session) {
  ContextT context;
  require(Serial::DeserializeFromFile(sessionFile(session, "context.bin"), context, SerType::BINARY), "cannot load CKKS context");
  return context;
}
PublicKeyT loadPublicKey(const std::filesystem::path& session) {
  PublicKeyT key;
  require(Serial::DeserializeFromFile(sessionFile(session, "public.key"), key, SerType::BINARY), "cannot load public key");
  return key;
}
PrivateKeyT loadPrivateKey(const std::filesystem::path& session) {
  PrivateKeyT key;
  require(Serial::DeserializeFromFile(sessionFile(session, "audit_secret.key"), key, SerType::BINARY), "cannot load audit secret key");
  return key;
}
CiphertextT loadCiphertext(const std::filesystem::path& path) {
  CiphertextT ciphertext;
  require(Serial::DeserializeFromFile(path, ciphertext, SerType::BINARY), "cannot load ciphertext artifact");
  return ciphertext;
}
void loadEvaluationKeys(const std::filesystem::path& session) {
  std::ifstream input(sessionFile(session, "eval_mult.keys"), std::ios::binary);
  require(input.good(), "cannot open evaluation-key bundle");
  require(CryptoContextImpl<DCRTPoly>::DeserializeEvalMultKey(input, SerType::BINARY), "cannot load multiplication evaluation keys");
}
void saveSession(const std::filesystem::path& session, const ContextT& context, const PublicKeyT& publicKey, const PrivateKeyT& secretKey) {
  std::filesystem::create_directories(session);
  require(Serial::SerializeToFile(sessionFile(session, "context.bin"), context, SerType::BINARY), "cannot save CKKS context");
  require(Serial::SerializeToFile(sessionFile(session, "public.key"), publicKey, SerType::BINARY), "cannot save public key");
  // Benchmark-only audit material. A real evaluator receives no secret key.
  require(Serial::SerializeToFile(sessionFile(session, "audit_secret.key"), secretKey, SerType::BINARY), "cannot save audit secret key");
  std::ofstream output(sessionFile(session, "eval_mult.keys"), std::ios::binary);
  require(output.good(), "cannot create evaluation-key bundle");
  require(CryptoContextImpl<DCRTPoly>::SerializeEvalMultKey(output, SerType::BINARY, context), "cannot save multiplication evaluation keys");
}
void initialize(const std::filesystem::path& session) {
  auto context = fixed_count_variance__generate_crypto_context(); auto keys = context->KeyGen();
  require(keys.good(), "key generation failed");
  // Configure all stages once. Later processes only load these artifacts.
  context = fixed_count_variance__configure_crypto_context(context, keys.secretKey);
  context = fixed_count_mean__configure_crypto_context(context, keys.secretKey);
  context = fixed_count_sum__configure_crypto_context(context, keys.secretKey);
  context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
  saveSession(session, context, keys.publicKey, keys.secretKey);
}
void feature(const std::filesystem::path& session, const char* duePath, const char* paidPath, const char* outputPath) {
  auto context = loadContext(session); auto publicKey = loadPublicKey(session);
  auto due = readVector(duePath); auto paid = readVector(paidPath);
  require(due.size() == @SIZE@ && paid.size() == @SIZE@, "bad vector size");
  auto encryptedDue = encrypted_subtract__encrypt__arg0(context, due, publicKey);
  auto encryptedPaid = encrypted_subtract__encrypt__arg1(context, paid, publicKey);
  auto encryptedFeature = encrypted_subtract(context, encryptedDue, encryptedPaid);
  require(Serial::SerializeToFile(outputPath, encryptedFeature, SerType::BINARY), "cannot save payment_diff ciphertext");
}
void sumStage(const std::filesystem::path& session, const char* inputPath, const char* outputPath) {
  auto context = loadContext(session); loadEvaluationKeys(session);
  auto result = fixed_count_sum(context, loadCiphertext(inputPath));
  require(Serial::SerializeToFile(outputPath, result, SerType::BINARY), "cannot save sum ciphertext");
}
void meanStage(const std::filesystem::path& session, const char* inputPath, const char* outputPath) {
  auto context = loadContext(session); loadEvaluationKeys(session);
  auto result = fixed_count_mean(context, loadCiphertext(inputPath));
  require(Serial::SerializeToFile(outputPath, result, SerType::BINARY), "cannot save mean ciphertext");
}
void varianceStage(const std::filesystem::path& session, const char* inputPath, const char* outputPath) {
  auto context = loadContext(session); loadEvaluationKeys(session);
  auto result = fixed_count_variance(context, loadCiphertext(inputPath));
  require(Serial::SerializeToFile(outputPath, result, SerType::BINARY), "cannot save variance ciphertext");
}
void audit(const std::filesystem::path& session, const char* featurePath, const char* sumPath, const char* meanPath, const char* variancePath, const char* featureAuditPath, const char* metricsPath) {
  auto context = loadContext(session); auto secretKey = loadPrivateKey(session);
  auto feature = encrypted_subtract__decrypt__result0(context, loadCiphertext(featurePath), secretKey);
  auto sum = fixed_count_sum__decrypt__result0(context, loadCiphertext(sumPath), secretKey);
  auto mean = fixed_count_mean__decrypt__result0(context, loadCiphertext(meanPath), secretKey);
  auto variance = fixed_count_variance__decrypt__result0(context, loadCiphertext(variancePath), secretKey);
  std::ofstream auditOutput(featureAuditPath); auditOutput << "value\n" << std::setprecision(17);
  for (auto value : feature) auditOutput << value << '\n';
  std::ofstream metrics(metricsPath); metrics << std::setprecision(17)
      << "{\"sum\":" << sum << ",\"mean\":" << mean << ",\"sample_variance\":" << variance << "}\n";
}
int main(int argc, char** argv) {
  try {
    if (argc < 3) return 2;
    const std::string stage(argv[1]); const std::filesystem::path session(argv[2]);
    if (stage == "init" && argc == 3) initialize(session);
    else if (stage == "feature" && argc == 6) feature(session, argv[3], argv[4], argv[5]);
    else if (stage == "sum" && argc == 5) sumStage(session, argv[3], argv[4]);
    else if (stage == "mean" && argc == 5) meanStage(session, argv[3], argv[4]);
    else if (stage == "variance" && argc == 5) varianceStage(session, argv[3], argv[4]);
    else if (stage == "audit" && argc == 9) audit(session, argv[3], argv[4], argv[5], argv[6], argv[7], argv[8]);
    else return 2;
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def execute_stage(runner: Path, stage: str, *paths: Path) -> tuple[float, str]:
    """Run one process-isolated stage; it receives only artifact paths."""
    return run([str(runner), stage, *(str(path.resolve()) for path in paths)], runner.parent)


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
    (work / "process_dag_runner.cpp").write_text(RUNNER.replace("@SIZE@", str(args.vector_size)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "process_dag_runner"], work)
    runner = build / "process_dag_runner"
    session = root / "session"; ciphertexts = root / "ciphertexts"; ciphertexts.mkdir()
    audit_path, metrics_path = root / "feature_audit.csv", root / "metrics.json"
    stage_seconds = {}
    for stage, stage_paths in (
        ("init", (session,)),
        ("feature", (session, due_path, paid_path, ciphertexts / "payment_diff.ct")),
        ("sum", (session, ciphertexts / "payment_diff.ct", ciphertexts / "sum.ct")),
        ("mean", (session, ciphertexts / "payment_diff.ct", ciphertexts / "mean.ct")),
        ("variance", (session, ciphertexts / "payment_diff.ct", ciphertexts / "variance.ct")),
        ("audit", (session, ciphertexts / "payment_diff.ct", ciphertexts / "sum.ct", ciphertexts / "mean.ct", ciphertexts / "variance.ct", audit_path, metrics_path)),
    ):
        stage_seconds[stage], _ = execute_stage(runner, stage, *stage_paths)
    feature = [float(row["value"]) for row in read_csv(audit_path)][:valid_count]
    expected_feature = [left - right for left, right in zip(due, paid)]
    expected_sum, expected_mean, expected_variance = fixed_count_statistics_reference(expected_feature)
    he = json.loads(metrics_path.read_text(encoding="utf-8"))
    feature_rows = [{"row": index, "python": expected, "he": feature[index], "absolute_error": abs(expected - feature[index])} for index, expected in enumerate(expected_feature)]
    aggregation_rows = [
        {"aggregation": "max", "python": max(expected_feature), "he": "NOT_RUN", "status": "pending CKKS-to-FHEW comparison/switching benchmark", "absolute_error": ""},
        {"aggregation": "mean", "python": expected_mean, "he": he["mean"], "status": "encrypted CKKS (public fixed count=3)", "absolute_error": abs(expected_mean - he["mean"])},
        {"aggregation": "sum", "python": expected_sum, "he": he["sum"], "status": "encrypted CKKS", "absolute_error": abs(expected_sum - he["sum"])},
        {"aggregation": "var", "python": expected_variance, "he": he["sample_variance"], "status": "encrypted CKKS sample variance (public fixed count=3)", "absolute_error": abs(expected_variance - he["sample_variance"])},
    ]
    write_csv(root / "feature_comparison.csv", list(feature_rows[0]), feature_rows)
    write_csv(root / "aggregation_comparison.csv", list(aggregation_rows[0]), aggregation_rows)
    result = {"status": "heir_generated_ckks_executed", "scope": "one CKKS session; process-isolated feature, sum, mean, variance and audit stages", "important_limit": "max is intentionally not executed; variable/private group counts require the separate encrypted-count finalizer", "generated": generated, "build_seconds": {"configure": configure_seconds, "build": build_seconds}, "stage_seconds": stage_seconds, "feature_comparison": feature_rows, "aggregation_comparison": aggregation_rows, "execution": he, "ciphertext_artifacts": [str(item) for item in sorted(ciphertexts.glob("*.ct"))], "session_artifacts": ["context.bin", "public.key", "eval_mult.keys", "audit_secret.key (benchmark audit only)"]}
    write_json(root / "result.json", result)
    print(json.dumps({"status": result["status"], "stage_seconds": stage_seconds, "aggregation_comparison": aggregation_rows, "output_dir": str(root)}, indent=2))


if __name__ == "__main__":
    main()
