#!/usr/bin/env python3
"""Run separate CKKS-SQSUM-01 and CKKS-VAR-01 benchmarks.

The workflow shares encryption and packed reductions, but writes distinct
square-sum and sample-variance metrics.  It never decrypts between them.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.scripts.run_payment_features_ciphertext_demo import (
    copy_generated_sources,
    run,
)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(ckks_variance_benchmark LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(variance_runner sum_output.cpp multiply_output.cpp variance_runner.cpp)
target_include_directories(variance_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(variance_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(variance_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(variance_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''

RUNNER = r'''
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "sum_output.h"
#include "multiply_output.h"
using namespace lbcrypto;

double seconds(std::chrono::steady_clock::time_point started) {
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
}
std::vector<double> read(const std::string& path) {
  std::ifstream input(path); if (!input) throw std::runtime_error("cannot open " + path);
  std::string line; std::getline(input, line); std::vector<double> values;
  while (std::getline(input, line)) { std::stringstream fields(line); std::string left; std::getline(fields, left, ','); values.push_back(std::stod(left)); }
  return values;
}
std::vector<size_t> counts(const std::string& text) {
  std::vector<size_t> result; std::stringstream fields(text); std::string field;
  while (std::getline(fields, field, ',')) result.push_back(std::stoull(field)); return result;
}
int main(int argc, char** argv) {
  if (argc != 6) return 2;
  try {
    const size_t slots = @SIZE@; const double inputScale = std::stod(argv[3]);
    if (!(inputScale > 0.0)) throw std::runtime_error("input scale must be positive");
    auto setup = std::chrono::steady_clock::now();
    auto context = encrypted_multiply__generate_crypto_context();
    auto keys = context->KeyGen(); if (!keys.good()) throw std::runtime_error("key generation failed");
    context = encrypted_multiply__configure_crypto_context(context, keys.secretKey);
    context = encrypted_sum__configure_crypto_context(context, keys.secretKey);
    std::ofstream meta(argv[5]);
    meta << std::setprecision(17) << "{\"setup_seconds\":" << seconds(setup)
         << ",\"ring_dimension\":" << context->GetRingDimension()
         << ",\"slot_count\":" << slots << ",\"input_scale\":" << inputScale
         << ",\"omp_num_threads\":1}\n";
    std::ofstream out(argv[4]); out << std::setprecision(17)
      << "value_count,decimals,repetition,ciphertext_chunks,input_scale,encrypt_seconds,sum_evaluate_seconds,square_evaluate_seconds,merge_seconds,variance_finalize_seconds,square_decrypt_seconds,variance_decrypt_seconds,online_seconds,he_sum_squares,square_abs_error,he_sample_variance,variance_abs_error\n";
    for (size_t count : counts(argv[2])) for (int decimals : {1, 2, 3, 6}) {
      auto raw = read(std::string(argv[1]) + "/add_sub_" + std::to_string(count) + "_" + std::to_string(decimals) + "dp.csv");
      if (count < 2) throw std::runtime_error("sample variance needs at least two values");
      std::vector<double> values; values.reserve(raw.size()); for (double value : raw) values.push_back(value / inputScale);
      double rawSquares = 0.0, rawSum = 0.0; for (double value : raw) { rawSum += value; rawSquares += value * value; }
      const double rawVariance = (rawSquares - rawSum * rawSum / static_cast<double>(count)) / static_cast<double>(count - 1);
      for (int repetition = 1; repetition <= 5; ++repetition) {
        double encryption = 0.0, sumEvaluation = 0.0, squareEvaluation = 0.0, merge = 0.0; size_t chunks = 0; bool first = true;
        decltype(encrypted_sum__encrypt__arg0(context, std::vector<double>(slots), keys.publicKey)) totalSum, totalSquares;
        for (size_t start = 0; start < values.size(); start += slots, ++chunks) {
          std::vector<double> block(slots, 0.0); const size_t take = std::min(slots, values.size() - start);
          std::copy(values.begin() + start, values.begin() + start + take, block.begin());
          auto started = std::chrono::steady_clock::now(); auto encrypted = encrypted_multiply__encrypt__arg0(context, block, keys.publicKey); encryption += seconds(started);
          // Two immutable branches of the same encrypted input; no re-encryption.
          auto sumInput = encrypted; auto squareLeft = encrypted; auto squareRight = encrypted;
          started = std::chrono::steady_clock::now(); auto partialSum = encrypted_sum(context, sumInput); sumEvaluation += seconds(started);
          started = std::chrono::steady_clock::now(); auto squares = encrypted_multiply(context, squareLeft, squareRight); auto partialSquares = encrypted_sum(context, squares); squareEvaluation += seconds(started);
          started = std::chrono::steady_clock::now();
          if (first) { totalSum = partialSum; totalSquares = partialSquares; first = false; }
          else for (size_t index = 0; index < totalSum.size(); ++index) { totalSum[index] = context->EvalAdd(totalSum[index], partialSum[index]); totalSquares[index] = context->EvalAdd(totalSquares[index], partialSquares[index]); }
          merge += seconds(started);
        }
        if (totalSum.size() != 1 || totalSquares.size() != 1) throw std::runtime_error("expected scalar encrypted reductions");
        auto started = std::chrono::steady_clock::now();
        const double inverseCount = 1.0 / static_cast<double>(count);
        auto mean = context->EvalMult(totalSum[0], inverseCount);
        auto secondMoment = context->EvalMult(totalSquares[0], inverseCount);
        auto meanSquared = context->EvalMult(mean, mean);
        auto populationVariance = context->EvalSub(secondMoment, meanSquared);
        auto sampleVariance = context->EvalMult(populationVariance, static_cast<double>(count) / static_cast<double>(count - 1));
        decltype(totalSum) variance{sampleVariance}; const double finalization = seconds(started);
        std::cerr << "audit=square_sum count=" << count << " decimals=" << decimals << " repetition=" << repetition << '\n';
        started = std::chrono::steady_clock::now(); double squareValue = encrypted_sum__decrypt__result0(context, totalSquares, keys.secretKey) * inputScale * inputScale; const double squareDecrypt = seconds(started);
        std::cerr << "audit=variance count=" << count << " decimals=" << decimals << " repetition=" << repetition << '\n';
        started = std::chrono::steady_clock::now(); double varianceValue = encrypted_sum__decrypt__result0(context, variance, keys.secretKey) * inputScale * inputScale; const double varianceDecrypt = seconds(started);
        out << count << ',' << decimals << ',' << repetition << ',' << chunks << ',' << inputScale << ',' << encryption << ',' << sumEvaluation << ',' << squareEvaluation << ',' << merge << ',' << finalization << ',' << squareDecrypt << ',' << varianceDecrypt << ',' << encryption + sumEvaluation + squareEvaluation + merge + finalization + squareDecrypt + varianceDecrypt << ',' << squareValue << ',' << std::abs(squareValue - rawSquares) << ',' << varianceValue << ',' << std::abs(varianceValue - rawVariance) << '\n';
      }
    }
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def raise_multiply_context_budget(source: str, requested_depth: int) -> tuple[str, int]:
    if requested_depth < 4:
        raise ValueError("--ckks-mul-depth must be at least 4 for sample variance")
    pattern = r"(SetMultiplicativeDepth\()\d+(\s*\);)"
    match = re.search(pattern, source)
    if match is None:
        raise ValueError("generated packed-multiply source has no SetMultiplicativeDepth call")
    original = int(re.search(r"\d+", match.group(0)).group(0))
    patched, count = re.subn(pattern, rf"\g<1>{requested_depth}\g<2>", source)
    if count != 1:
        raise ValueError(f"expected one packed-multiply depth setting; found {count}")
    return patched, original


def pandas_reference(data: Path, counts_: tuple[int, ...], input_scale: float, output: Path) -> None:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("install pandas: python3 -m pip install pandas") from error
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["value_count", "decimals", "repetition", "pandas_square_sum_seconds", "pandas_variance_seconds", "pandas_square_sum", "pandas_sample_variance"])
        for count in counts_:
            for decimals in (1, 2, 3, 6):
                raw = pd.read_csv(data / f"add_sub_{count}_{decimals}dp.csv", usecols=["left"])["left"]
                encoded = raw / input_scale
                for repetition in range(1, 6):
                    started = time.perf_counter(); square_sum = encoded.pow(2).sum(); square_seconds = time.perf_counter() - started
                    started = time.perf_counter(); variance = encoded.var(ddof=1); variance_seconds = time.perf_counter() - started
                    writer.writerow([count, decimals, repetition, square_seconds, variance_seconds, square_sum * input_scale * input_scale, variance * input_scale * input_scale])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--value-counts", nargs="+", type=int, default=[1_000, 50_000, 1_000_000])
    parser.add_argument("--input-scale", type=float, default=40_000.0)
    parser.add_argument("--ckks-mul-depth", type=int, default=12)
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.input_scale <= 0 or args.ckks_mul_depth < 4:
        raise ValueError("input scale must be positive and CKKS depth must be at least 4")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    manifest = json.loads((args.generated_dir / "generation_manifest.json").read_text(encoding="utf-8"))
    sum_kernel = next(item for item in manifest["kernels"] if item["entry_function"] == "encrypted_sum")
    multiply_kernel = next(item for item in manifest["kernels"] if item["entry_function"] == "encrypted_multiply")
    slots = int(sum_kernel["logical_value_count"])
    if int(multiply_kernel["logical_value_count"]) != slots:
        raise ValueError("SUM and packed multiplication must use the same CKKS lane count")
    work = root / "runner"; work.mkdir()
    copy_generated_sources((args.generated_dir / sum_kernel["source"]).parent, work, "sum")
    copy_generated_sources((args.generated_dir / multiply_kernel["source"]).parent, work, "multiply")
    multiply_cpp = work / "multiply_output.cpp"
    patched, original_depth = raise_multiply_context_budget(multiply_cpp.read_text(encoding="utf-8"), args.ckks_mul_depth)
    multiply_cpp.write_text(patched, encoding="utf-8")
    (work / "variance_runner.cpp").write_text(RUNNER.replace("@SIZE@", str(slots)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    run(["cmake", "--build", str(build.resolve()), "--target", "variance_runner"], work)
    counts_ = tuple(args.value_counts)
    pandas = root / "pandas_results.csv"; pandas_reference(args.data_dir.resolve(), counts_, args.input_scale, pandas)
    heir = root / "heir_results.csv"; execution_path = root / "execution.json"
    wall, log = run(["env", "OMP_NUM_THREADS=1", str((build / "variance_runner").resolve()), str(args.data_dir.resolve()), ",".join(map(str, counts_)), str(args.input_scale), str(heir.resolve()), str(execution_path.resolve())], work)
    (work / "runner.log").write_text(log, encoding="utf-8")
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["square_variance_context_budget"] = {"translated_depth_before_patch": original_depth, "requested_multiplicative_depth": args.ckks_mul_depth, "method": "patched translated HEIR packed-multiply context"}
    write_json(execution_path, execution)
    with heir.open(newline="", encoding="utf-8") as handle: heir_rows = list(csv.DictReader(handle))
    with pandas.open(newline="", encoding="utf-8") as handle: pandas_rows = list(csv.DictReader(handle))
    lines = [
        "# CKKS-SQSUM-01 and CKKS-VAR-01", "",
        "Both workloads use the same normalized encrypted input `x / scale`. `CKKS-SQSUM-01` first evaluates HEIR packed `CT×CT` (`x × x`) and then the HEIR SUM reduction. `CKKS-VAR-01` uses encrypted SUM and SUM-OF-SQUARES branches, then computes sample variance without an intermediate decrypt.", "",
        f"Input scale: `{args.input_scale:g}`. Shared CKKS depth: `{args.ckks_mul_depth}` (translated packed-multiply depth before patch: `{original_depth}`).", "",
        "## CKKS-SQSUM-01 — packed encrypted square, then encrypted sum", "",
        "| Values | Decimals | Pandas square sum (s) | HE encrypt (s) | HE square reduction (s) | HE merge (s) | Audit decrypt (s) | Square-sum max error |", "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for count in counts_:
        for decimals in ("1", "2", "3", "6"):
            he_rows = [row for row in heir_rows if row["value_count"] == str(count) and row["decimals"] == decimals]
            pd_rows = [row for row in pandas_rows if row["value_count"] == str(count) and row["decimals"] == decimals]
            lines.append(f"| {count} | {decimals} | {statistics.median(float(row['pandas_square_sum_seconds']) for row in pd_rows):.9f} | {statistics.median(float(row['encrypt_seconds']) for row in he_rows):.9f} | {statistics.median(float(row['square_evaluate_seconds']) for row in he_rows):.9f} | {statistics.median(float(row['merge_seconds']) for row in he_rows):.9f} | {statistics.median(float(row['square_decrypt_seconds']) for row in he_rows):.9f} | {max(float(row['square_abs_error']) for row in he_rows):.12g} |")
    lines.extend(["", "## CKKS-VAR-01 — encrypted sample variance", "", "Variance finalization is `n/(n-1) × (E[x²] - E[x]²)`. It starts from the encrypted SUM/SQSUM branches and therefore has no second encryption or plaintext hand-off.", "", "| Values | Decimals | Pandas variance (s) | HE SUM branch (s) | HE variance finalization (s) | Audit decrypt (s) | Variance max error |", "|---:|---:|---:|---:|---:|---:|---:|"])
    for count in counts_:
        for decimals in ("1", "2", "3", "6"):
            he_rows = [row for row in heir_rows if row["value_count"] == str(count) and row["decimals"] == decimals]
            pd_rows = [row for row in pandas_rows if row["value_count"] == str(count) and row["decimals"] == decimals]
            lines.append(f"| {count} | {decimals} | {statistics.median(float(row['pandas_variance_seconds']) for row in pd_rows):.9f} | {statistics.median(float(row['sum_evaluate_seconds']) for row in he_rows):.9f} | {statistics.median(float(row['variance_finalize_seconds']) for row in he_rows):.9f} | {statistics.median(float(row['variance_decrypt_seconds']) for row in he_rows):.9f} | {max(float(row['variance_abs_error']) for row in he_rows):.12g} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = {"status": "ckks_square_sum_variance_benchmark_executed", "report": "REPORT.md", "runner_wall_seconds": wall}
    write_json(root / "result.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
