#!/usr/bin/env python3
"""Benchmark encrypted PAYMENT_DIFF count, sum, and mean on real inputs.

The client supplies sanitized ``AMT_INSTALMENT`` and ``AMT_PAYMENT`` only.
The runner derives PAYMENT_DIFF after encrypting those parents.  It then uses
the encrypted feature once for SUM and derives MEAN from that encrypted SUM.
COUNT is an independent encrypted reduction of the padded 0/1 validity mask.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.prepared_installments import load_prepared_parents, public_power_of_two_scale
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(real_payment_diff_sum_mean LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(payment_diff_runner sub_output.cpp sum_output.cpp mean_output.cpp payment_diff_runner.cpp)
target_include_directories(payment_diff_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(payment_diff_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(payment_diff_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(payment_diff_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "sum_output.h"
#include "mean_output.h"
using namespace lbcrypto;
using Bundle = std::vector<Ciphertext<DCRTPoly>>;
struct Parents { std::vector<double> installment, payment; };
double seconds(std::chrono::steady_clock::time_point start) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); }
void require(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }
Parents readParents(const std::string& path) {
  std::ifstream input(path); require(input.good(), "cannot open " + path);
  std::string line; std::getline(input, line); Parents result;
  while (std::getline(input, line)) {
    if (line.empty()) continue; std::stringstream fields(line); std::string installment, payment;
    std::getline(fields, installment, ','); std::getline(fields, payment, ',');
    result.installment.push_back(std::stod(installment)); result.payment.push_back(std::stod(payment));
  }
  require(!result.installment.empty() && result.installment.size() == result.payment.size(), "invalid parent input");
  return result;
}
std::vector<double> chunk(const std::vector<double>& values, size_t start, size_t slots) {
  std::vector<double> output(slots, 0.0); const size_t take = std::min(slots, values.size() - start);
  std::copy(values.begin() + start, values.begin() + start + take, output.begin()); return output;
}
Bundle addBundles(const CryptoContext<DCRTPoly>& context, const Bundle& left, const Bundle& right) {
  require(left.size() == right.size(), "ciphertext bundle shape mismatch"); Bundle output; output.reserve(left.size());
  for (size_t index = 0; index < left.size(); ++index) output.push_back(context->EvalAdd(left[index], right[index]));
  return output;
}
int main(int argc, char** argv) {
  // executable + input CSV + scale + repetitions + HE CSV + execution JSON
  if (argc != 6) return 2;
  try {
    const size_t slots = @SLOTS@; const double scale = std::stod(argv[2]); const int repetitions = std::stoi(argv[3]);
    const auto parents = readParents(argv[1]); const uint64_t count = parents.installment.size(); require(count > 1, "mean requires at least two rows");
    auto setup = std::chrono::steady_clock::now(); auto context = fixed_count_mean__generate_crypto_context(); auto keys = context->KeyGen();
    require(keys.good(), "key generation failed"); context = fixed_count_mean__configure_crypto_context(context, keys.secretKey);
    context = encrypted_sum__configure_crypto_context(context, keys.secretKey); context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    std::ofstream meta(argv[6]); meta << std::setprecision(17) << "{\"setup_seconds\":" << seconds(setup)
      << ",\"input_scale\":" << scale << ",\"logical_slots\":" << slots << ",\"ckks_slot_capacity\":" << context->GetRingDimension() / 2 << "}\n";
    std::ofstream out(argv[5]); out << std::setprecision(17)
      << "repetition,ciphertext_chunks,parent_encrypt_seconds,mask_encrypt_seconds,feature_seconds,branch_copy_seconds,count_reduce_seconds,sum_reduce_seconds,merge_seconds,mean_scale_seconds,audit_decrypt_seconds,online_seconds,he_count,count_abs_error,he_sum,sum_abs_error,he_mean,mean_abs_error\n";
    for (int repetition = 1; repetition <= repetitions; ++repetition) {
      double parentEncrypt = 0.0, maskEncrypt = 0.0, feature = 0.0, copies = 0.0, countReduce = 0.0, sumReduce = 0.0, merge = 0.0;
      bool first = true; Bundle totalCount, totalSum; size_t chunks = 0;
      for (size_t start = 0; start < count; start += slots, ++chunks) {
        auto installment = chunk(parents.installment, start, slots), payment = chunk(parents.payment, start, slots); std::vector<double> valid(slots, 0.0);
        const size_t used = std::min(slots, static_cast<size_t>(count - start)); for (size_t i = 0; i < used; ++i) { installment[i] /= scale; payment[i] /= scale; valid[i] = 1.0; }
        auto started = std::chrono::steady_clock::now(); auto encryptedInstallment = encrypted_subtract__encrypt__arg0(context, installment, keys.publicKey); auto encryptedPayment = encrypted_subtract__encrypt__arg1(context, payment, keys.publicKey); parentEncrypt += seconds(started);
        started = std::chrono::steady_clock::now(); auto encryptedValid = encrypted_sum__encrypt__arg0(context, valid, keys.publicKey); maskEncrypt += seconds(started);
        started = std::chrono::steady_clock::now(); auto paymentDiff = encrypted_subtract(context, encryptedInstallment, encryptedPayment); feature += seconds(started);
        // PAYMENT_DIFF is copied into its SUM branch. MEAN deliberately uses
        // the final encrypted SUM below; it never recomputes the feature.
        started = std::chrono::steady_clock::now(); auto diffForSum = paymentDiff; copies += seconds(started);
        started = std::chrono::steady_clock::now(); auto partialCount = encrypted_sum(context, encryptedValid); countReduce += seconds(started);
        started = std::chrono::steady_clock::now(); auto partialSum = encrypted_sum(context, diffForSum); sumReduce += seconds(started);
        started = std::chrono::steady_clock::now();
        if (first) { totalCount = std::move(partialCount); totalSum = std::move(partialSum); first = false; }
        else { totalCount = addBundles(context, totalCount, partialCount); totalSum = addBundles(context, totalSum, partialSum); }
        merge += seconds(started);
      }
      require(totalCount.size() == 1 && totalSum.size() == 1, "expected scalar encrypted reductions");
      auto started = std::chrono::steady_clock::now(); auto meanScalar = context->EvalMult(totalSum[0], 1.0 / static_cast<double>(count)); Bundle mean{meanScalar}; const double meanScale = seconds(started);
      started = std::chrono::steady_clock::now(); const double heCount = encrypted_sum__decrypt__result0(context, totalCount, keys.secretKey); const double heSum = encrypted_sum__decrypt__result0(context, totalSum, keys.secretKey) * scale; const double heMean = encrypted_sum__decrypt__result0(context, mean, keys.secretKey) * scale; const double decrypt = seconds(started);
      const double online = parentEncrypt + maskEncrypt + feature + copies + countReduce + sumReduce + merge + meanScale + decrypt;
      out << repetition << ',' << chunks << ',' << parentEncrypt << ',' << maskEncrypt << ',' << feature << ',' << copies << ',' << countReduce << ',' << sumReduce << ',' << merge << ',' << meanScale << ',' << decrypt << ',' << online << ',' << heCount << ',' << std::abs(heCount - static_cast<double>(count)) << ',' << heSum << ',' << "0," << heMean << ",0\n";
    }
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def patch_mean_depth(source: str, requested_depth: int) -> tuple[str, int]:
    if requested_depth < 2:
        raise ValueError("--ckks-mul-depth must be at least 2")
    pattern = r"(SetMultiplicativeDepth\()\d+(\s*\);)"
    match = re.search(pattern, source)
    if match is None:
        raise ValueError("generated Mean source has no SetMultiplicativeDepth call")
    original = int(re.search(r"\d+", match.group(0)).group(0))
    patched, count = re.subn(pattern, rf"\g<1>{requested_depth}\g<2>", source)
    if count != 1:
        raise ValueError(f"expected one Mean depth setting; found {count}")
    return patched, original


def pandas_reference(installment: list[float], payment: list[float], repetitions: int, output: Path) -> None:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("install pandas in the active environment: python3 -m pip install pandas") from error
    frame = pd.DataFrame({"AMT_INSTALMENT": installment, "AMT_PAYMENT": payment})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["repetition", "feature_seconds", "count_seconds", "sum_seconds", "mean_seconds", "workload_seconds", "count", "sum", "mean"])
        for repetition in range(1, repetitions + 1):
            started = time.perf_counter(); diff = frame["AMT_INSTALMENT"] - frame["AMT_PAYMENT"]; feature_seconds = time.perf_counter() - started
            started = time.perf_counter(); count = diff.count(); count_seconds = time.perf_counter() - started
            started = time.perf_counter(); total = diff.sum(); sum_seconds = time.perf_counter() - started
            started = time.perf_counter(); mean = diff.mean(); mean_seconds = time.perf_counter() - started
            writer.writerow([repetition, feature_seconds, count_seconds, sum_seconds, mean_seconds, feature_seconds + count_seconds + sum_seconds + mean_seconds, count, total, mean])


def median(rows: list[dict[str, str]], field: str) -> float:
    return statistics.median(float(row[field]) for row in rows)


def report(root: Path, value_count: int, scale: float, mean_depth: int, original_depth: int) -> None:
    with (root / "heir_results.csv").open(newline="", encoding="utf-8") as handle:
        he = list(csv.DictReader(handle))
    with (root / "pandas_results.csv").open(newline="", encoding="utf-8") as handle:
        pandas = list(csv.DictReader(handle))
    execution = json.loads((root / "execution.json").read_text(encoding="utf-8"))
    feature_he = median(he, "feature_seconds")
    count_he = feature_he + median(he, "count_reduce_seconds") + median(he, "merge_seconds")
    sum_he = feature_he + median(he, "sum_reduce_seconds") + median(he, "merge_seconds")
    mean_he = sum_he + median(he, "mean_scale_seconds")
    all_he = median(he, "feature_seconds") + median(he, "count_reduce_seconds") + median(he, "sum_reduce_seconds") + median(he, "merge_seconds") + median(he, "mean_scale_seconds")
    all_python = median(pandas, "workload_seconds")
    rows = [
        ("PAYMENT_DIFF feature", median(pandas, "feature_seconds"), feature_he, "feature_seconds"),
        ("Encrypted COUNT(valid mask)", median(pandas, "feature_seconds") + median(pandas, "count_seconds"), count_he, "count_abs_error"),
        ("PAYMENT_DIFF SUM", median(pandas, "feature_seconds") + median(pandas, "sum_seconds"), sum_he, "sum_abs_error"),
        ("PAYMENT_DIFF MEAN", median(pandas, "feature_seconds") + median(pandas, "mean_seconds"), mean_he, "mean_abs_error"),
    ]
    lines = [
        "# Real installments PAYMENT_DIFF: encrypted count, sum, and mean",
        "",
        "The client supplies only sanitized raw parent columns. `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT` is calculated after parent encryption. "
        "Its encrypted SUM is calculated once and reused by encrypted MEAN through a public `1/N` scalar. COUNT is a separate encrypted SUM of the padded 0/1 validity mask.",
        "",
        f"| Real rows | Logical lanes / CT | CKKS representation scale | Shared setup | Mean context depth |",
        "|---:|---:|---:|---:|---:|",
        f"| {value_count} | {execution['logical_slots']} | {scale:g} | {float(execution['setup_seconds']):.9f} s | {mean_depth} (translated default: {original_depth}) |",
        "",
        "## Per-output calculation and accuracy",
        "",
        "`HE calculation` excludes encryption and audit decryption. `HE online` below includes them once for the combined workload. "
        "Pandas DataFrame construction, CSV read, client null sanitation, and public normalization are excluded from all timers.",
        "",
        "| Output | Pandas calculation median (s) | HE calculation median (s) | HE calc ÷ Pandas | Max absolute error |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, python_seconds, he_seconds, error_field in rows:
        lines.append(f"| {label} | {python_seconds:.9f} | {he_seconds:.9f} | {he_seconds / python_seconds:.2f}× | {max(float(row[error_field]) for row in he):.12g} |")
    encryption = median(he, "parent_encrypt_seconds") + median(he, "mask_encrypt_seconds")
    online = median(he, "online_seconds")
    lines += [
        "",
        "## Shared end-to-end workload",
        "",
        "| Pandas feature + count + sum + mean (median s) | HE parent + mask encryption (median s) | HE calculation once (median s) | Audit decrypt (median s) | HE online (median s) | HE online ÷ Pandas |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {all_python:.9f} | {encryption:.9f} | {all_he:.9f} | {median(he, 'audit_decrypt_seconds'):.9f} | {online:.9f} | {online / all_python:.2f}× |",
        "",
        "The run is one in-memory shared CKKS session. It does not serialize a context or claim that a ciphertext can be loaded in a fresh process without its matching context, keys, and evaluation keys.",
        "",
        "Raw rows: `pandas_results.csv`, `heir_results.csv`, `execution.json`.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def one_run(args: argparse.Namespace, root: Path, value_count: int) -> None:
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    parents = load_prepared_parents(args.prepared_dir.resolve(), value_count)
    scale = args.input_scale or public_power_of_two_scale(parents.installment, parents.payment)
    stage = root / "plaintext_inputs.csv"
    with stage.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(["AMT_INSTALMENT", "AMT_PAYMENT"]); writer.writerows(zip(parents.installment, parents.payment))
    pandas_reference(parents.installment, parents.payment, args.repetitions, root / "pandas_results.csv")
    manifest = json.loads((args.generated_dir / "generation_manifest.json").read_text(encoding="utf-8"))
    wanted = {"encrypted_subtract": "sub", "encrypted_sum": "sum", "fixed_count_mean": "mean"}
    entries = {str(kernel["entry_function"]): kernel for kernel in manifest["kernels"]}
    missing = sorted(set(wanted) - set(entries))
    if missing:
        raise ValueError("generated kernel root is missing required entries: " + ", ".join(missing))
    slots = int(entries["encrypted_sum"]["logical_value_count"])
    if any(int(entries[name]["logical_value_count"]) != slots for name in wanted):
        raise ValueError("subtract, sum, and mean kernels must use the same logical lane count")
    work = root / "runner"; work.mkdir()
    for entry, prefix in wanted.items():
        copy_generated_sources((args.generated_dir / entries[entry]["source"]).parent, work, prefix)
    mean_cpp = work / "mean_output.cpp"
    patched, original_depth = patch_mean_depth(mean_cpp.read_text(encoding="utf-8"), args.ckks_mul_depth)
    mean_cpp.write_text(patched, encoding="utf-8")
    (work / "payment_diff_runner.cpp").write_text(RUNNER.replace("@SLOTS@", str(slots)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "payment_diff_runner"], work)
    he = root / "heir_results.csv"; execution = root / "execution.json"
    wall_seconds, log = run(["env", "OMP_NUM_THREADS=1", str((build / "payment_diff_runner").resolve()), str(stage.resolve()), str(scale), str(args.repetitions), str(he.resolve()), str(execution.resolve())], work)
    (work / "runner.log").write_text(log, encoding="utf-8")
    metadata = json.loads(execution.read_text(encoding="utf-8")); metadata.update({"value_count": value_count, "mean_context_budget": {"translated_depth_before_patch": original_depth, "requested_multiplicative_depth": args.ckks_mul_depth}, "build_seconds": {"configure": configure_seconds, "build": build_seconds}, "runner_wall_seconds": wall_seconds})
    write_json(execution, metadata)
    # Fill accuracy errors against the same pandas oracle after the HE process
    # finishes, preserving the C++ runner's actual encrypted measurements.
    pandas_rows = list(csv.DictReader((root / "pandas_results.csv").open(newline="", encoding="utf-8")))
    he_rows = list(csv.DictReader(he.open(newline="", encoding="utf-8")))
    for he_row, pandas_row in zip(he_rows, pandas_rows):
        he_row["sum_abs_error"] = str(abs(float(he_row["he_sum"]) - float(pandas_row["sum"])))
        he_row["mean_abs_error"] = str(abs(float(he_row["he_mean"]) - float(pandas_row["mean"])))
    with he.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=he_rows[0].keys()); writer.writeheader(); writer.writerows(he_rows)
    report(root, value_count, scale, args.ckks_mul_depth, original_depth)
    write_json(root / "result.json", {"status": "real_payment_diff_count_sum_mean_executed", "real_rows": value_count, "source_batches": parents.files_used, "report": "REPORT.md", "execution": "execution.json"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument("--prepared-dir", type=Path, default=Path("data/prepared/installments_columns"))
    parser.add_argument("--value-count", dest="value_counts", nargs="+", type=int, default=[1000, 5000])
    parser.add_argument("--input-scale", type=float, default=0.0)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--ckks-mul-depth", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(); root = args.output_dir.resolve()
    if not args.value_counts or any(value < 2 for value in args.value_counts):
        raise ValueError("each --value-count must be at least 2")
    if args.input_scale < 0:
        raise ValueError("--input-scale cannot be negative")
    if len(args.value_counts) == 1:
        one_run(args, root, args.value_counts[0]); print((root / "result.json").read_text(encoding="utf-8")); return
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    runs = []
    for count in args.value_counts:
        child = root / f"rows_{count}"
        one_run(args, child, count); runs.append({"value_count": count, "directory": child.name})
    write_json(root / "batch_result.json", {"status": "real_payment_diff_count_sum_mean_batch_executed", "runs": runs})
    (root / "REPORT.md").write_text("# Real PAYMENT_DIFF encrypted aggregations\n\n" + "\n".join(f"- `{item['value_count']}` rows: `{item['directory']}/REPORT.md`" for item in runs) + "\n", encoding="utf-8")
    print((root / "batch_result.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
