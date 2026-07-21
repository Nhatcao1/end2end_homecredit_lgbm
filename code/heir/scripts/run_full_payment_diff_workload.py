#!/usr/bin/env python3
"""Run one full-data, whole-vector HE workload for ``PAYMENT_DIFF``.

The client-prepared input contains all sanitized installment rows in fixed-size
batches. One CKKS context is shared by the entire run. Each batch encrypts the
two parent columns, derives ``AMT_INSTALMENT - AMT_PAYMENT`` after encryption,
then creates independent sum and square-sum branches. Encrypted batch moments
are accumulated before one global encrypted mean/variance finalization.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.kernels.fixed_count_statistics import (
    fixed_count_sum_mlir,
    fixed_count_sum_squares_mlir,
)
from code.heir.operations.columns import binary_mlir
from code.heir.scripts.run_payment_features_ciphertext_demo import (
    copy_generated_sources,
    generate,
    run,
)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(full_payment_diff_workload LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(full_workload_runner feature_output.cpp sum_output.cpp square_output.cpp full_workload_runner.cpp)
target_include_directories(full_workload_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(full_workload_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(full_workload_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(full_workload_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER = r'''
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "feature_output.h"
#include "sum_output.h"
#include "square_output.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;
using ContextT = CryptoContext<DCRTPoly>;
using CiphertextT = Ciphertext<DCRTPoly>;
using Bundle = std::vector<CiphertextT>;

void require(bool ok, const std::string& message) { if (!ok) throw std::runtime_error(message); }
double seconds(std::chrono::steady_clock::time_point start) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}
struct Batch { std::vector<double> payment, installment; };
Batch readBatch(const std::filesystem::path& path) {
  std::ifstream input(path); require(input.good(), "cannot open batch " + path.string());
  std::string line; std::getline(input, line); Batch result;
  while (std::getline(input, line)) {
    if (line.empty()) continue;
    std::stringstream fields(line); std::string payment, installment, valid;
    std::getline(fields, payment, ','); std::getline(fields, installment, ','); std::getline(fields, valid, ',');
    result.payment.push_back(std::stod(payment)); result.installment.push_back(std::stod(installment));
  }
  require(result.payment.size() == @SIZE@ && result.installment.size() == @SIZE@, "batch does not match vector size");
  return result;
}
Bundle addBundles(const ContextT& context, const Bundle& left, const Bundle& right) {
  require(left.size() == right.size(), "ciphertext bundle shape mismatch"); Bundle output; output.reserve(left.size());
  for (size_t i = 0; i < left.size(); ++i) output.push_back(context->EvalAdd(left[i], right[i]));
  return output;
}
Bundle multiplyPublic(const ContextT& context, const Bundle& values, double scalar) {
  Bundle output; output.reserve(values.size());
  for (const auto& value : values) output.push_back(context->EvalMult(value, scalar));
  return output;
}
Bundle loadBundle(const std::filesystem::path& path) {
  Bundle output;
  require(Serial::DeserializeFromFile(path.string(), output, SerType::BINARY), "cannot load ciphertext artifact " + path.string());
  return output;
}
int main(int argc, char** argv) {
  if (argc != 5) return 2;
  try {
    const std::filesystem::path listPath(argv[1]), artifactDir(argv[3]), metricsPath(argv[4]);
    const uint64_t fullCount = std::stoull(argv[2]); require(fullCount > 1, "full count must exceed one");
    const auto setupStarted = std::chrono::steady_clock::now();
    auto context = fixed_count_sum_squares__generate_crypto_context(); auto keys = context->KeyGen();
    require(keys.good(), "key generation failed");
    context = fixed_count_sum_squares__configure_crypto_context(context, keys.secretKey);
    context = fixed_count_sum__configure_crypto_context(context, keys.secretKey);
    context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    const double setupSeconds = seconds(setupStarted);
    std::ifstream list(listPath); require(list.good(), "cannot open batch path list");
    std::string line; Bundle totalSum, totalSquares; uint64_t batches = 0;
    double encryptionSeconds = 0.0, featureSeconds = 0.0, reductionSeconds = 0.0, branchSeconds = 0.0;
    const auto featureDir = artifactDir / "payment_diff_batches";
    std::filesystem::create_directories(featureDir);
    while (std::getline(list, line)) {
      if (line.empty()) continue; const auto batch = readBatch(line);
      auto started = std::chrono::steady_clock::now();
      auto encryptedDue = encrypted_subtract__encrypt__arg0(context, batch.installment, keys.publicKey);
      auto encryptedPaid = encrypted_subtract__encrypt__arg1(context, batch.payment, keys.publicKey);
      encryptionSeconds += seconds(started);
      started = std::chrono::steady_clock::now();
      auto feature = encrypted_subtract(context, encryptedDue, encryptedPaid);
      featureSeconds += seconds(started);
      started = std::chrono::steady_clock::now();
      const auto featurePath = featureDir / ("payment_diff_" + std::to_string(batches) + ".ct");
      require(Serial::SerializeToFile(featurePath.string(), feature, SerType::BINARY), "cannot save PAYMENT_DIFF feature artifact");
      // Generated reduction code may consume a bundle in place. Reload two
      // independent branches from the saved feature artifact rather than
      // recomputing PAYMENT_DIFF or sharing a mutable ciphertext object.
      auto sumFeature = loadBundle(featurePath);
      auto squareFeature = loadBundle(featurePath);
      branchSeconds += seconds(started);
      started = std::chrono::steady_clock::now();
      auto sum = fixed_count_sum(context, sumFeature);
      auto squares = fixed_count_sum_squares(context, squareFeature);
      reductionSeconds += seconds(started);
      started = std::chrono::steady_clock::now();
      if (batches == 0) { totalSum = sum; totalSquares = squares; }
      else { totalSum = addBundles(context, totalSum, sum); totalSquares = addBundles(context, totalSquares, squares); }
      branchSeconds += seconds(started); ++batches;
    }
    require(batches > 0, "no batches provided");
    auto finalStarted = std::chrono::steady_clock::now();
    auto mean = multiplyPublic(context, totalSum, 1.0 / static_cast<double>(fullCount));
    require(totalSum.size() == 1 && totalSquares.size() == 1 && mean.size() == 1, "expected scalar output bundles");
    auto sumTimesMean = context->EvalMult(totalSum[0], mean[0]);
    auto varianceValue = context->EvalSub(totalSquares[0], sumTimesMean);
    varianceValue = context->EvalMult(varianceValue, 1.0 / static_cast<double>(fullCount - 1));
    Bundle variance{varianceValue};
    const double finalizationSeconds = seconds(finalStarted);
    const double workloadSeconds = featureSeconds + reductionSeconds + branchSeconds + finalizationSeconds;
    std::filesystem::create_directories(artifactDir);
    require(Serial::SerializeToFile((artifactDir / "payment_diff_sum.ct").string(), totalSum, SerType::BINARY), "cannot save sum artifact");
    require(Serial::SerializeToFile((artifactDir / "payment_diff_mean.ct").string(), mean, SerType::BINARY), "cannot save mean artifact");
    require(Serial::SerializeToFile((artifactDir / "payment_diff_variance.ct").string(), variance, SerType::BINARY), "cannot save variance artifact");
    auto auditStarted = std::chrono::steady_clock::now();
    const double sumAudit = fixed_count_sum__decrypt__result0(context, totalSum, keys.secretKey);
    const double meanAudit = fixed_count_sum__decrypt__result0(context, mean, keys.secretKey);
    const double varianceAudit = fixed_count_sum__decrypt__result0(context, variance, keys.secretKey);
    const double auditSeconds = seconds(auditStarted);
    std::ofstream metrics(metricsPath); metrics << std::setprecision(17)
      << "{\"batch_count\":" << batches << ",\"setup_seconds\":" << setupSeconds
      << ",\"encryption_seconds\":" << encryptionSeconds << ",\"feature_seconds\":" << featureSeconds
      << ",\"reduction_seconds\":" << reductionSeconds << ",\"he_workload_seconds\":" << workloadSeconds
      << ",\"he_pipeline_seconds\":" << encryptionSeconds + workloadSeconds
      << ",\"branch_materialization_and_accumulation_seconds\":" << branchSeconds
      << ",\"finalization_seconds\":" << finalizationSeconds << ",\"audit_seconds\":" << auditSeconds
      << ",\"sum\":" << sumAudit << ",\"mean\":" << meanAudit << ",\"sample_variance\":" << varianceAudit << "}\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def markdown_report(preparation: dict[str, object], execution: dict[str, float]) -> str:
    oracle = preparation["plaintext_pandas_oracle"]  # type: ignore[index]
    diff = oracle["payment_diff"]  # type: ignore[index]
    timings = oracle["timings_seconds"]  # type: ignore[index]
    rows = []
    for name, he_key in (("sum", "sum"), ("mean", "mean"), ("var", "sample_variance")):
        plain = float(diff["sample_var" if name == "var" else name])  # type: ignore[index]
        he = float(execution[he_key])
        rows.append(f"| `{name}` | {plain} | {he} | {abs(plain - he)} |")
    return f"""# Full PAYMENT_DIFF whole-dataframe HE workload

## Scope

All sanitized rows from `installments_payments.csv` are processed in one
workload: **no groupby**. Client numeric/null representation and packing happen
before timing starts. Each packed batch uses the same CKKS context; parent
columns are encrypted once per batch, `PAYMENT_DIFF` is calculated once and
saved as a ciphertext artifact, then independent reloaded branches produce sum
and square-sum. Those encrypted moments are accumulated before one global
encrypted mean and sample-variance finalization.

| Raw rows | Kept rows | Dropped rows | CKKS vector size | Batches |
|---:|---:|---:|---:|---:|
| {preparation['client_sanitation']['raw_rows']} | {preparation['client_sanitation']['kept_rows']} | {preparation['client_sanitation']['dropped_rows']} | {preparation['packing']['vector_size']} | {execution['batch_count']} |

## Python versus HE accuracy

Python is the streamed Pandas whole-dataframe oracle; HE values are decrypted
only for this benchmark audit.

| Aggregate | Pandas | HE audit | Absolute error |
|---|---:|---:|---:|
{chr(10).join(rows)}

## Latency

| Component | Seconds |
|---|---:|
| Python/Pandas workload: feature + whole-dataframe aggregation | {timings['pandas_feature_expressions'] + timings['pandas_whole_dataframe_aggregation']} |
| HE setup (one context + keys) | {execution['setup_seconds']} |
| HE encryption (all batches) | {execution['encryption_seconds']} |
| HE workload headline: feature + persisted CT branches + reductions + global finalization | {execution['he_workload_seconds']} |
| HE full online pipeline: encryption + HE workload | {execution['he_pipeline_seconds']} |
| `PAYMENT_DIFF` feature calculation | {execution['feature_seconds']} |
| Ciphertext materialization + branch accumulation | {execution['branch_materialization_and_accumulation_seconds']} |
| Encrypted reductions | {execution['reduction_seconds']} |
| Global encrypted finalization | {execution['finalization_seconds']} |
| Audit decryption | {execution['audit_seconds']} |

`PAYMENT_PERC` full aggregation is intentionally not included: the bounded
reciprocal feature works as a micro-proof, but its chained aggregation still
exceeds the current server's depth/memory budget. No result is substituted.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--heir-opt", default="heir-opt")
    parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--ckks-mul-depth", type=int, default=6)
    args = parser.parse_args()
    prepared = args.prepared_dir.resolve()
    preparation = json.loads((prepared / "preparation_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((prepared / "batch_manifest.json").read_text(encoding="utf-8"))
    vector_size = int(preparation["packing"]["vector_size"])
    full_count = int(preparation["client_sanitation"]["kept_rows"])
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    paths = root / "batch_paths.txt"
    paths.write_text("\n".join(str((prepared / entry["file"]).resolve()) for entry in manifest["batches"]) + "\n", encoding="utf-8")
    feature, total, squares = root / "feature", root / "sum", root / "sum_squares"
    generated = {
        "payment_diff": generate(feature, "encrypted_subtract", binary_mlir(vector_size, "subtract"), vector_size, args.heir_opt, args.heir_translate, 2, args.ckks_mul_depth),
        "sum": generate(total, "fixed_count_sum", fixed_count_sum_mlir(vector_size, vector_size), vector_size, args.heir_opt, args.heir_translate, 1, args.ckks_mul_depth),
        "sum_squares": generate(squares, "fixed_count_sum_squares", fixed_count_sum_squares_mlir(vector_size, vector_size), vector_size, args.heir_opt, args.heir_translate, 1, args.ckks_mul_depth),
    }
    work = root / "runner"; work.mkdir()
    for directory, prefix in ((feature, "feature"), (total, "sum"), (squares, "square")):
        copy_generated_sources(directory, work, prefix)
    (work / "full_workload_runner.cpp").write_text(RUNNER.replace("@SIZE@", str(vector_size)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "full_workload_runner"], work)
    artifacts = root / "ciphertexts"; metrics = root / "metrics.json"
    wall_seconds, log = run([str((build / "full_workload_runner").resolve()), str(paths.resolve()), str(full_count), str(artifacts.resolve()), str(metrics.resolve())], work)
    (work / "runner.log").write_text(log, encoding="utf-8")
    execution = json.loads(metrics.read_text(encoding="utf-8")); execution.update({"build_seconds": {"configure": configure_seconds, "build": build_seconds}, "runner_wall_seconds": wall_seconds})
    report = markdown_report(preparation, execution)
    (root / "REPORT.md").write_text(report, encoding="utf-8")
    result = {"status": "full_payment_diff_he_workload_executed", "prepared_input": str(prepared), "generated": generated, "execution": execution, "report": "REPORT.md"}
    write_json(root / "result.json", result)
    print(json.dumps({"status": result["status"], "report": str(root / "REPORT.md"), "execution": execution}, indent=2))


if __name__ == "__main__":
    main()
