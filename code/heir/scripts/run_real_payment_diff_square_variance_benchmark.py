#!/usr/bin/env python3
"""Benchmark encrypted PAYMENT_DIFF square-sum and sample variance on real rows.

This intentionally does not reuse the earlier unreliable fixed_count_sum_squares
runner.  It composes two already explicit generic operations instead:
``subtract`` derives PAYMENT_DIFF, ``multiply`` squares it lane-wise, then
``sum`` reduces both the feature and its square branch.  Sample variance is
finalized from encrypted moments without an intermediate decrypt.
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
from code.heir.prepared_installments import load_prepared_parents, public_power_of_two_scale
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(real_payment_diff_square_variance LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(square_variance_runner sub_output.cpp mul_output.cpp sum_output.cpp square_variance_runner.cpp)
target_include_directories(square_variance_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(square_variance_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(square_variance_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(square_variance_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "sub_output.h"
#include "mul_output.h"
#include "sum_output.h"
using namespace lbcrypto;
using Bundle = std::vector<Ciphertext<DCRTPoly>>;
struct Parents { std::vector<double> installment, payment; };
double seconds(std::chrono::steady_clock::time_point start) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); }
void require(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }
Parents readParents(const std::string& path) {
  std::ifstream input(path); require(input.good(), "cannot open " + path); std::string line; std::getline(input, line); Parents out;
  while (std::getline(input, line)) { if (line.empty()) continue; std::stringstream fields(line); std::string installment, payment; std::getline(fields, installment, ','); std::getline(fields, payment, ','); out.installment.push_back(std::stod(installment)); out.payment.push_back(std::stod(payment)); }
  require(out.installment.size() > 1 && out.installment.size() == out.payment.size(), "invalid parent input"); return out;
}
std::vector<double> chunk(const std::vector<double>& values, size_t start, size_t slots) {
  std::vector<double> out(slots, 0.0); const size_t take = std::min(slots, values.size() - start); std::copy(values.begin() + start, values.begin() + start + take, out.begin()); return out;
}
Bundle addBundles(const CryptoContext<DCRTPoly>& context, const Bundle& left, const Bundle& right) {
  require(left.size() == right.size(), "ciphertext bundle shape mismatch"); Bundle out; out.reserve(left.size()); for (size_t i = 0; i < left.size(); ++i) out.push_back(context->EvalAdd(left[i], right[i])); return out;
}
int main(int argc, char** argv) {
  // executable + CSV + scale + repetitions + HE CSV + execution JSON
  if (argc != 6) return 2;
  try {
    const size_t slots = @SLOTS@; const double scale = std::stod(argv[2]); const int repetitions = std::stoi(argv[3]); require(scale > 0.0, "input scale must be positive");
    const auto parents = readParents(argv[1]); const uint64_t count = parents.installment.size();
    auto setup = std::chrono::steady_clock::now(); auto context = encrypted_multiply__generate_crypto_context(); auto keys = context->KeyGen(); require(keys.good(), "key generation failed");
    context = encrypted_multiply__configure_crypto_context(context, keys.secretKey); context = encrypted_sum__configure_crypto_context(context, keys.secretKey); context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    std::ofstream meta(argv[5]); meta << std::setprecision(17) << "{\"setup_seconds\":" << seconds(setup) << ",\"input_scale\":" << scale << ",\"logical_slots\":" << slots << ",\"ckks_slot_capacity\":" << context->GetRingDimension() / 2 << "}\n";
    std::ofstream out(argv[4]); out << std::setprecision(17) << "repetition,ciphertext_chunks,parent_encrypt_seconds,feature_seconds,square_seconds,sum_reduce_seconds,square_sum_reduce_seconds,merge_seconds,variance_finalize_seconds,audit_decrypt_seconds,online_seconds,he_sum,he_square_sum,he_sample_variance,sum_abs_error,square_sum_abs_error,variance_abs_error\n";
    for (int repetition = 1; repetition <= repetitions; ++repetition) {
      double encryption = 0.0, feature = 0.0, square = 0.0, sumReduce = 0.0, squareReduce = 0.0, merge = 0.0; bool first = true; Bundle totalSum, totalSquareSum; size_t chunks = 0;
      for (size_t start = 0; start < count; start += slots, ++chunks) {
        auto installment = chunk(parents.installment, start, slots), payment = chunk(parents.payment, start, slots); for (auto& value : installment) value /= scale; for (auto& value : payment) value /= scale;
        auto started = std::chrono::steady_clock::now(); auto encryptedInstallment = encrypted_subtract__encrypt__arg0(context, installment, keys.publicKey); auto encryptedPayment = encrypted_subtract__encrypt__arg1(context, payment, keys.publicKey); encryption += seconds(started);
        started = std::chrono::steady_clock::now(); auto diff = encrypted_subtract(context, encryptedInstallment, encryptedPayment); feature += seconds(started);
        // Keep the original encrypted feature branch for SUM. The square
        // branch is a ciphertext copy, not a plaintext materialization.
        auto diffForSquare = diff;
        started = std::chrono::steady_clock::now(); auto squared = encrypted_multiply(context, diffForSquare, diffForSquare); square += seconds(started);
        started = std::chrono::steady_clock::now(); auto partialSum = encrypted_sum(context, diff); sumReduce += seconds(started);
        started = std::chrono::steady_clock::now(); auto partialSquareSum = encrypted_sum(context, squared); squareReduce += seconds(started);
        started = std::chrono::steady_clock::now(); if (first) { totalSum = std::move(partialSum); totalSquareSum = std::move(partialSquareSum); first = false; } else { totalSum = addBundles(context, totalSum, partialSum); totalSquareSum = addBundles(context, totalSquareSum, partialSquareSum); } merge += seconds(started);
      }
      require(totalSum.size() == 1 && totalSquareSum.size() == 1, "expected scalar encrypted reductions");
      // All final values stay encrypted. The formula is algebraically the
      // pandas ddof=1 sample variance: n/(n-1) * (E[x^2] - E[x]^2).
      auto started = std::chrono::steady_clock::now(); const double invCount = 1.0 / static_cast<double>(count); auto mean = context->EvalMult(totalSum[0], invCount); auto secondMoment = context->EvalMult(totalSquareSum[0], invCount); auto meanSquared = context->EvalMult(mean, mean); auto populationVariance = context->EvalSub(secondMoment, meanSquared); auto sampleVariance = context->EvalMult(populationVariance, static_cast<double>(count) / static_cast<double>(count - 1)); Bundle variance{sampleVariance}; const double finalization = seconds(started);
      started = std::chrono::steady_clock::now(); const double heSum = encrypted_sum__decrypt__result0(context, totalSum, keys.secretKey) * scale; const double heSquareSum = encrypted_sum__decrypt__result0(context, totalSquareSum, keys.secretKey) * scale * scale; const double heVariance = encrypted_sum__decrypt__result0(context, variance, keys.secretKey) * scale * scale; const double decrypt = seconds(started);
      const double online = encryption + feature + square + sumReduce + squareReduce + merge + finalization + decrypt;
      out << repetition << ',' << chunks << ',' << encryption << ',' << feature << ',' << square << ',' << sumReduce << ',' << squareReduce << ',' << merge << ',' << finalization << ',' << decrypt << ',' << online << ',' << heSum << ',' << heSquareSum << ',' << heVariance << ",0,0,0\n";
    }
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def patch_multiply_depth(source: str, requested_depth: int) -> tuple[str, int]:
    if requested_depth < 5:
        raise ValueError("--ckks-mul-depth must be at least 5 for sample variance")
    pattern = r"(SetMultiplicativeDepth\()\d+(\s*\);)"
    match = re.search(pattern, source)
    if match is None:
        raise ValueError("generated multiply source has no SetMultiplicativeDepth call")
    original = int(re.search(r"\d+", match.group(0)).group(0))
    patched, replacements = re.subn(pattern, rf"\g<1>{requested_depth}\g<2>", source)
    if replacements != 1:
        raise ValueError(f"expected one multiply depth setting; found {replacements}")
    return patched, original


def parameter(source: str, name: str) -> int | str:
    match = re.search(rf"{re.escape(name)}\((\d+)\);", source)
    return int(match.group(1)) if match else "HEIR default (not explicitly emitted)"


def pandas_reference(installment: list[float], payment: list[float], repetitions: int, output: Path) -> None:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("install pandas in the active environment: python3 -m pip install pandas") from error
    frame = pd.DataFrame({"AMT_INSTALMENT": installment, "AMT_PAYMENT": payment})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["repetition", "feature_seconds", "square_seconds", "sum_seconds", "square_sum_seconds", "variance_seconds", "workload_seconds", "sum", "square_sum", "sample_variance"])
        for repetition in range(1, repetitions + 1):
            started = time.perf_counter(); diff = frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]; feature = time.perf_counter() - started
            started = time.perf_counter(); squared = diff * diff; square = time.perf_counter() - started
            started = time.perf_counter(); total = diff.sum(); sum_seconds = time.perf_counter() - started
            started = time.perf_counter(); square_sum = squared.sum(); square_sum_seconds = time.perf_counter() - started
            started = time.perf_counter(); variance = diff.var(ddof=1); variance_seconds = time.perf_counter() - started
            writer.writerow([repetition, feature, square, sum_seconds, square_sum_seconds, variance_seconds, feature + square + sum_seconds + square_sum_seconds + variance_seconds, total, square_sum, variance])


def median(rows: list[dict[str, str]], field: str) -> float:
    return statistics.median(float(row[field]) for row in rows)


def tolerance_status(observed: float, expected: float, relative_tolerance: float) -> tuple[float, str]:
    error = abs(observed - expected); allowed = relative_tolerance * max(1.0, abs(expected))
    return error, "PASS" if error <= allowed else f"FAIL (allowed ≤ {allowed:.6g})"


def report(root: Path, value_count: int, scale: float, requested_depth: int, original_depth: int, relative_tolerance: float) -> None:
    he = list(csv.DictReader((root / "heir_results.csv").open(newline="", encoding="utf-8")))
    pandas = list(csv.DictReader((root / "pandas_results.csv").open(newline="", encoding="utf-8")))
    execution = json.loads((root / "execution.json").read_text(encoding="utf-8"))
    feature_he = median(he, "feature_seconds"); square_he = median(he, "square_seconds")
    sum_he = feature_he + median(he, "sum_reduce_seconds") + median(he, "merge_seconds")
    square_sum_he = feature_he + square_he + median(he, "square_sum_reduce_seconds") + median(he, "merge_seconds")
    variance_he = square_sum_he + median(he, "sum_reduce_seconds") + median(he, "variance_finalize_seconds")
    outputs = [
        ("PAYMENT_DIFF SUM", "sum", sum_he, "he_sum"),
        ("PAYMENT_DIFF SQSUM", "square_sum", square_sum_he, "he_square_sum"),
        ("PAYMENT_DIFF sample variance (ddof=1)", "sample_variance", variance_he, "he_sample_variance"),
    ]
    lines = [
        "# Real installments PAYMENT_DIFF: encrypted square-sum and variance",
        "",
        "This replaces the previous invalid square/variance route. `PAYMENT_DIFF` is derived after parent encryption. "
        "The generic HEIR CT×CT kernel squares that encrypted feature; the generic HEIR SUM kernel reduces both branches. "
        "Variance is finalized from encrypted SUM and SQSUM only—there is no intermediate decrypt.",
        "",
        f"| Real rows | Logical lanes / CT | Representation scale | CKKS depth | Relative audit tolerance |",
        "|---:|---:|---:|---:|---:|",
        f"| {value_count} | {execution['logical_slots']} | {scale:g} | {requested_depth} (translated default: {original_depth}) | {relative_tolerance:g} |",
        "",
        "| CKKS context parameter | Effective value |",
        "|---|---:|",
        f"| Multiplicative depth | {execution['emitted_ckks_parameters']['multiplicative_depth']} |",
        f"| First modulus size (bits) | {execution['emitted_ckks_parameters']['first_mod_size']} |",
        f"| Scaling modulus size (bits) | {execution['emitted_ckks_parameters']['scaling_mod_size']} |",
        "",
        "## Accuracy and calculation latency",
        "",
        "| Output | Pandas calculation median (s) | HE calculation median (s) | HE calc ÷ Pandas | Maximum absolute error | Relative-tolerance status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for label, pandas_field, he_seconds, he_field in outputs:
        python_seconds = median(pandas, pandas_field + "_seconds" if pandas_field != "sample_variance" else "variance_seconds")
        errors_and_status = [tolerance_status(float(row[he_field]), float(reference[pandas_field]), relative_tolerance) for row, reference in zip(he, pandas)]
        max_error = max(item[0] for item in errors_and_status); status = "PASS" if all(item[1] == "PASS" for item in errors_and_status) else next(item[1] for item in errors_and_status if item[1] != "PASS")
        lines.append(f"| {label} | {python_seconds:.9f} | {he_seconds:.9f} | {he_seconds / python_seconds:.2f}× | {max_error:.12g} | {status} |")
    online = median(he, "online_seconds"); pandas_workload = median(pandas, "workload_seconds")
    lines += [
        "",
        "## Shared workload timing",
        "",
        "| Pandas full workload (s) | HE encrypt (s) | HE feature + square + reductions + finalization (s) | HE audit decrypt (s) | HE online (s) | Online ÷ Pandas |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {pandas_workload:.9f} | {median(he, 'parent_encrypt_seconds'):.9f} | {online - median(he, 'parent_encrypt_seconds') - median(he, 'audit_decrypt_seconds'):.9f} | {median(he, 'audit_decrypt_seconds'):.9f} | {online:.9f} | {online / pandas_workload:.2f}× |",
        "",
        "A FAIL is a benchmark result, not a successful HE feature. Do not carry a failing SQSUM or variance ciphertext into later work.",
        "",
        "Raw rows: `pandas_results.csv`, `heir_results.csv`, `execution.json`.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def one_run(args: argparse.Namespace, root: Path, value_count: int) -> None:
    if root.exists():
        if not args.overwrite: raise FileExistsError(root)
        shutil.rmtree(root)
    root.mkdir(parents=True)
    parents = load_prepared_parents(args.prepared_dir.resolve(), value_count)
    scale = args.input_scale or public_power_of_two_scale(parents.installment, parents.payment)
    stage = root / "plaintext_inputs.csv"
    with stage.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["AMT_INSTALMENT", "AMT_PAYMENT"]); writer.writerows(zip(parents.installment, parents.payment))
    pandas_reference(parents.installment, parents.payment, args.repetitions, root / "pandas_results.csv")
    manifest = json.loads((args.generated_dir / "generation_manifest.json").read_text(encoding="utf-8")); entries = {str(item["entry_function"]): item for item in manifest["kernels"]}
    wanted = {"encrypted_subtract": "sub", "encrypted_multiply": "mul", "encrypted_sum": "sum"}; missing = sorted(set(wanted) - set(entries))
    if missing: raise ValueError("generated kernel root is missing required entries: " + ", ".join(missing))
    slots = int(entries["encrypted_sum"]["logical_value_count"])
    if any(int(entries[name]["logical_value_count"]) != slots for name in wanted): raise ValueError("subtract, multiply, and sum kernels must have the same logical lane count")
    work = root / "runner"; work.mkdir()
    for entry, prefix in wanted.items(): copy_generated_sources((args.generated_dir / entries[entry]["source"]).parent, work, prefix)
    mul_cpp = work / "mul_output.cpp"; patched, original_depth = patch_multiply_depth(mul_cpp.read_text(encoding="utf-8"), args.ckks_mul_depth); mul_cpp.write_text(patched, encoding="utf-8")
    parameters = {"multiplicative_depth": parameter(patched, "SetMultiplicativeDepth"), "first_mod_size": parameter(patched, "SetFirstModSize"), "scaling_mod_size": parameter(patched, "SetScalingModSize")}
    (work / "square_variance_runner.cpp").write_text(RUNNER.replace("@SLOTS@", str(slots)), encoding="utf-8"); (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"; configure, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work); build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "square_variance_runner"], work)
    he = root / "heir_results.csv"; execution = root / "execution.json"; wall, log = run(["env", "OMP_NUM_THREADS=1", str((build / "square_variance_runner").resolve()), str(stage.resolve()), str(scale), str(args.repetitions), str(he.resolve()), str(execution.resolve())], work); (work / "runner.log").write_text(log, encoding="utf-8")
    metadata = json.loads(execution.read_text(encoding="utf-8")); metadata.update({"value_count": value_count, "square_variance_context": {"translated_depth_before_patch": original_depth, "requested_multiplicative_depth": args.ckks_mul_depth}, "emitted_ckks_parameters": parameters, "build_seconds": {"configure": configure, "build": build_seconds}, "runner_wall_seconds": wall}); write_json(execution, metadata)
    report(root, value_count, scale, args.ckks_mul_depth, original_depth, args.relative_tolerance)
    write_json(root / "result.json", {"status": "real_payment_diff_square_variance_executed", "real_rows": value_count, "source_batches": parents.files_used, "report": "REPORT.md", "execution": "execution.json"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, required=True); parser.add_argument("--prepared-dir", type=Path, default=Path("data/prepared/installments_columns")); parser.add_argument("--value-count", dest="value_counts", nargs="+", type=int, default=[1000, 5000, 10000]); parser.add_argument("--input-scale", type=float, default=0.0); parser.add_argument("--repetitions", type=int, default=5); parser.add_argument("--ckks-mul-depth", type=int, default=6); parser.add_argument("--relative-tolerance", type=float, default=1e-5); parser.add_argument("--output-dir", type=Path, required=True); parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE"); parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(); root = args.output_dir.resolve()
    if not args.value_counts or any(value < 2 for value in args.value_counts): raise ValueError("each --value-count must be at least 2")
    if args.input_scale < 0 or args.relative_tolerance <= 0: raise ValueError("input scale must be non-negative and tolerance positive")
    if len(args.value_counts) == 1: one_run(args, root, args.value_counts[0]); print((root / "result.json").read_text(encoding="utf-8")); return
    if root.exists():
        if not args.overwrite: raise FileExistsError(root)
        shutil.rmtree(root)
    root.mkdir(parents=True); runs = []
    for count in args.value_counts:
        child = root / f"rows_{count}"; one_run(args, child, count); runs.append({"value_count": count, "directory": child.name})
    write_json(root / "batch_result.json", {"status": "real_payment_diff_square_variance_batch_executed", "runs": runs})
    (root / "REPORT.md").write_text("# Real PAYMENT_DIFF square-sum and variance\n\n" + "\n".join(f"- `{item['value_count']}` rows: `{item['directory']}/REPORT.md`" for item in runs) + "\n", encoding="utf-8")
    print((root / "batch_result.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
