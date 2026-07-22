#!/usr/bin/env python3
"""Run the first encrypted real-data ``groupby(SK_ID_CURR)`` sum proof.

The client-side fixture contains one fixed-size, masked block per selected
applicant.  This runner encrypts its *parent* columns, evaluates the generic
HEIR subtraction kernel, and evaluates the generic HEIR sum kernel for each
block. The resulting encrypted scalar is one ``PAYMENT_DIFF`` sum per opaque
group.

This is intentionally a correctness-first grouped benchmark. Each applicant
uses an independent 8192-lane ciphertext in one shared CKKS session. It does
not yet claim the later segmented-reduction optimisation that would pack many
group results into one ciphertext.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(grouped_payment_diff_sum LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(groupby_sum_runner sub_output.cpp sum_output.cpp groupby_sum_runner.cpp)
target_include_directories(groupby_sum_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(groupby_sum_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(groupby_sum_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(groupby_sum_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER = r'''
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "sub_output.h"
#include "sum_output.h"
using namespace lbcrypto;
using Bundle = std::vector<Ciphertext<DCRTPoly>>;
struct Group { std::vector<double> payment, installment; std::vector<int> seen; };
double seconds(std::chrono::steady_clock::time_point start) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); }
void require(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }
std::vector<std::string> split(const std::string& line) { std::stringstream input(line); std::vector<std::string> fields; std::string value; while (std::getline(input, value, ',')) fields.push_back(value); return fields; }
std::map<unsigned long long, Group> readGroups(const std::string& path, size_t slots, size_t bucket, double scale) {
  std::ifstream input(path); require(input.good(), "cannot open " + path); std::string line; std::getline(input, line); std::map<unsigned long long, Group> groups;
  while (std::getline(input, line)) {
    if (line.empty()) continue; const auto fields = split(line); require(fields.size() == 7, "expected seven CSV columns");
    const auto groupId = std::stoull(fields[2]); const size_t lane = std::stoull(fields[3]); const double payment = std::stod(fields[4]); const double installment = std::stod(fields[5]); const int valid = std::stoi(fields[6]);
    require(lane < bucket && lane < slots, "lane outside declared group bucket"); require(valid == 0 || valid == 1, "validity mask must be 0 or 1");
    auto inserted = groups.emplace(groupId, Group{std::vector<double>(slots, 0.0), std::vector<double>(slots, 0.0), std::vector<int>(bucket, 0)}); Group& group = inserted.first->second;
    require(group.seen[lane] == 0, "duplicate lane for opaque group"); group.seen[lane] = 1;
    if (valid == 0) { require(payment == 0.0 && installment == 0.0, "padded lane must contain zero parents"); continue; }
    group.payment[lane] = payment / scale; group.installment[lane] = installment / scale;
  }
  require(!groups.empty(), "no group blocks read"); return groups;
}
int main(int argc, char** argv) {
  // executable + group blocks CSV + bucket + scale + repetitions + HE CSV + execution JSON
  if (argc != 7) return 2;
  try {
    const size_t slots = @SLOTS@; const size_t bucket = std::stoull(argv[2]); const double scale = std::stod(argv[3]); const int repetitions = std::stoi(argv[4]); require(repetitions > 0, "repetitions must be positive");
    const auto groups = readGroups(argv[1], slots, bucket, scale);
    auto setup = std::chrono::steady_clock::now(); auto context = encrypted_sum__generate_crypto_context(); auto keys = context->KeyGen(); require(keys.good(), "key generation failed");
    context = encrypted_sum__configure_crypto_context(context, keys.secretKey); context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    std::ofstream meta(argv[6]); meta << std::setprecision(17) << "{\"setup_seconds\":" << seconds(setup) << ",\"logical_slots\":" << slots << ",\"ckks_slot_capacity\":" << context->GetRingDimension() / 2 << ",\"groups\":" << groups.size() << ",\"bucket_size\":" << bucket << ",\"input_scale\":" << scale << "}\n";
    std::ofstream out(argv[5]); out << std::setprecision(17) << "repetition,opaque_group_id,parent_encrypt_seconds,feature_seconds,sum_reduce_seconds,audit_decrypt_seconds,online_seconds,he_sum\n";
    for (int repetition = 1; repetition <= repetitions; ++repetition) for (const auto& item : groups) {
      const auto& group = item.second; auto started = std::chrono::steady_clock::now(); auto encryptedInstallment = encrypted_subtract__encrypt__arg0(context, group.installment, keys.publicKey); auto encryptedPayment = encrypted_subtract__encrypt__arg1(context, group.payment, keys.publicKey); const double parentEncrypt = seconds(started);
      started = std::chrono::steady_clock::now(); auto paymentDiff = encrypted_subtract(context, encryptedInstallment, encryptedPayment); const double feature = seconds(started);
      started = std::chrono::steady_clock::now(); Bundle encryptedSum = encrypted_sum(context, paymentDiff); const double sumReduce = seconds(started);
      require(encryptedSum.size() == 1, "expected scalar HEIR sum reduction");
      started = std::chrono::steady_clock::now(); const double heSum = encrypted_sum__decrypt__result0(context, encryptedSum, keys.secretKey) * scale; const double decrypt = seconds(started);
      const double online = parentEncrypt + feature + sumReduce + decrypt;
      out << repetition << ',' << item.first << ',' << parentEncrypt << ',' << feature << ',' << sumReduce << ',' << decrypt << ',' << online << ',' << heSum << '\n';
    }
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def _read_blocks(path: Path) -> tuple[dict[int, list[dict[str, str]]], int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected = {
        "packed_ciphertext_batch", "segment_index", "opaque_group_id", "lane",
        "AMT_PAYMENT", "AMT_INSTALMENT", "validity_mask",
    }
    if not rows or set(rows[0]) != expected:
        raise ValueError("HE-ready group block CSV has an unexpected schema")
    groups: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[int(row["opaque_group_id"])].append(row)
    bucket_sizes = {len(value) for value in groups.values()}
    if len(bucket_sizes) != 1:
        raise ValueError("every opaque group must have exactly one fixed-size block")
    return dict(groups), bucket_sizes.pop()


def _scale(groups: dict[int, list[dict[str, str]]]) -> float:
    maximum = max(
        abs(float(row[column]))
        for rows in groups.values()
        for row in rows
        for column in ("AMT_PAYMENT", "AMT_INSTALMENT")
    )
    return float(2 ** max(0, math.ceil(math.log2(maximum or 1.0))))


def _read_reference(path: Path) -> dict[int, dict[str, float]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {
        int(row["opaque_group_id"]): {"sum": float(row["payment_diff_sum"])}
        for row in rows
    }


def _python_baseline(groups: dict[int, list[dict[str, str]]], repetitions: int, output: Path) -> None:
    """Time the matching in-memory Python group workload, excluding preparation."""
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "repetition", "opaque_group_id", "feature_seconds", "sum_seconds",
                "workload_seconds", "payment_diff_sum",
            ]
        )
        for repetition in range(1, repetitions + 1):
            for group_id, rows in groups.items():
                real = [row for row in rows if int(row["validity_mask"])]
                started = time.perf_counter()
                differences = [float(row["AMT_INSTALMENT"]) - float(row["AMT_PAYMENT"]) for row in real]
                feature_seconds = time.perf_counter() - started
                started = time.perf_counter()
                total = math.fsum(differences)
                sum_seconds = time.perf_counter() - started
                writer.writerow(
                    [repetition, group_id, feature_seconds, sum_seconds, feature_seconds + sum_seconds, total]
                )


def _relative_error(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(1.0, abs(expected))


def _report(root: Path, *, groups: dict[int, list[dict[str, str]]], scale: float, tolerance: float) -> None:
    with (root / "heir_results.csv").open("r", encoding="utf-8", newline="") as handle:
        he_rows = list(csv.DictReader(handle))
    with (root / "audited_results.csv").open("r", encoding="utf-8", newline="") as handle:
        audit_rows = list(csv.DictReader(handle))
    with (root / "python_results.csv").open("r", encoding="utf-8", newline="") as handle:
        python_rows = list(csv.DictReader(handle))
    execution = json.loads((root / "execution.json").read_text(encoding="utf-8"))
    by_group: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in he_rows:
        by_group[int(row["opaque_group_id"])].append(row)
    lines = [
        "# First encrypted PAYMENT_DIFF groupby proof",
        "",
        "Each opaque applicant group is one fixed 128-lane client-prepared block. The runner encrypts only `AMT_INSTALMENT` and `AMT_PAYMENT`. It calculates `PAYMENT_DIFF = AMT_INSTALMENT - AMT_PAYMENT` after encryption, then returns one encrypted sum per group.",
        "",
        "This is a correctness-first grouping test: every group uses an independent 8192-lane ciphertext within one shared CKKS session. It is not yet the packed segmented-reduction optimisation.",
        "",
        "| Groups | Bucket lanes/group | HEIR logical lanes/CT | Actual real rows | Padding lanes | CKKS scale | Shared setup (s) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {len(groups)} | {len(next(iter(groups.values())))} | {execution['logical_slots']} | {sum(int(row['validity_mask']) for rows in groups.values() for row in rows)} | {sum(1 - int(row['validity_mask']) for rows in groups.values() for row in rows)} | {scale:g} | {float(execution['setup_seconds']):.9f} |",
        "",
        "## Accuracy by opaque group",
        "",
        "| Opaque group | Reference PAYMENT_DIFF sum | HE sum | Sum absolute error | Status |",
        "|---:|---:|---:|---:|---|",
    ]
    for row in audit_rows:
        status = "PASS" if row["status"] == "PASS" else "FAIL"
        lines.append(
            f"| {row['opaque_group_id']} | {float(row['reference_sum']):.12g} | {float(row['he_sum']):.12g} | {float(row['sum_abs_error']):.12g} | {status} |"
        )
    parent_encrypt = statistics.median(float(row["parent_encrypt_seconds"]) for row in he_rows)
    feature = statistics.median(float(row["feature_seconds"]) for row in he_rows)
    reduction = statistics.median(float(row["sum_reduce_seconds"]) for row in he_rows)
    decrypt = statistics.median(float(row["audit_decrypt_seconds"]) for row in he_rows)
    online = statistics.median(float(row["online_seconds"]) for row in he_rows)
    python_workload = statistics.median(float(row["workload_seconds"]) for row in python_rows)
    he_calculation = feature + reduction
    lines += [
        "",
        "## Median latency per applicant block",
        "",
        "| Parent encrypt | PAYMENT_DIFF | Encrypted SUM | Audit decrypt | HE online |",
        "|---:|---:|---:|---:|---:|",
        f"| {parent_encrypt:.9f} | {feature:.9f} | {reduction:.9f} | {decrypt:.9f} | {online:.9f} |",
        "",
        "## Python baseline comparison",
        "",
        "Python timing is the matching in-memory per-group operation: calculate `PAYMENT_DIFF` and sum the real rows. CSV read, client grouping/padding, and DataFrame construction are excluded from both Python and HE timing.",
        "",
        "| Python workload median (s) | HE calculation median (s) | HE calc ÷ Python | HE online median (s) | HE online ÷ Python |",
        "|---:|---:|---:|---:|---:|",
        f"| {python_workload:.9f} | {he_calculation:.9f} | {he_calculation / python_workload:.2f}× | {online:.9f} | {online / python_workload:.2f}× |",
        "",
        f"Acceptance uses relative tolerance `{tolerance:g}` for the sum (with an absolute `{tolerance:g}` bound when the reference sum is zero). Raw result rows: `heir_results.csv`, `audited_results.csv`, `execution.json`.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--relative-tolerance", type=float, default=1e-6)
    parser.add_argument("--input-scale", type=float, default=0.0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.repetitions < 1 or args.relative_tolerance <= 0 or args.input_scale < 0:
        raise ValueError("repetitions/tolerance/scale must be positive (scale may be zero for automatic)")
    prepared = args.prepared_dir.resolve()
    blocks_path = prepared / "he_ready" / "group_blocks.csv"
    reference_path = prepared / "client_private" / "pandas_groupby_reference.csv"
    groups, bucket_size = _read_blocks(blocks_path)
    references = _read_reference(reference_path)
    if set(groups) != set(references):
        raise ValueError("HE-ready groups and private reference groups differ")
    scale = args.input_scale or _scale(groups)
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _python_baseline(groups, args.repetitions, root / "python_results.csv")
    manifest = json.loads((args.generated_dir.resolve() / "generation_manifest.json").read_text(encoding="utf-8"))
    entries = {str(item["entry_function"]): item for item in manifest["kernels"]}
    needed = {"encrypted_subtract": "sub", "encrypted_sum": "sum"}
    missing = sorted(set(needed) - set(entries))
    if missing:
        raise ValueError("generated kernel root is missing: " + ", ".join(missing))
    slots = int(entries["encrypted_sum"]["logical_value_count"])
    if slots != int(entries["encrypted_subtract"]["logical_value_count"]):
        raise ValueError("subtract and sum kernels must have matching logical lane counts")
    if bucket_size > slots:
        raise ValueError("group bucket cannot exceed HEIR kernel slot count")
    work = root / "runner"; work.mkdir()
    for entry, prefix in needed.items():
        copy_generated_sources((args.generated_dir.resolve() / entries[entry]["source"]).parent, work, prefix)
    (work / "groupby_sum_runner.cpp").write_text(RUNNER.replace("@SLOTS@", str(slots)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "groupby_sum_runner"], work)
    he_path, execution_path = root / "heir_results.csv", root / "execution.json"
    wall_seconds, log = run([
        "env", "OMP_NUM_THREADS=1", str((build / "groupby_sum_runner").resolve()), str(blocks_path),
        str(bucket_size), str(scale), str(args.repetitions), str(he_path.resolve()), str(execution_path.resolve()),
    ], work)
    (work / "runner.log").write_text(log, encoding="utf-8")
    with he_path.open("r", encoding="utf-8", newline="") as handle:
        he_rows = list(csv.DictReader(handle))
    latest: dict[int, dict[str, str]] = {}
    for row in he_rows:
        latest[int(row["opaque_group_id"])] = row
    audit_path = root / "audited_results.csv"
    all_pass = True
    with audit_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["opaque_group_id", "reference_sum", "he_sum", "sum_abs_error", "sum_relative_error", "status"])
        writer.writeheader()
        for group_id in sorted(references):
            row, reference = latest[group_id], references[group_id]
            he_sum = float(row["he_sum"])
            sum_error = abs(he_sum - reference["sum"])
            relative = _relative_error(he_sum, reference["sum"])
            status = "PASS" if relative <= args.relative_tolerance else "FAIL"
            all_pass = all_pass and status == "PASS"
            writer.writerow({"opaque_group_id": group_id, "reference_sum": reference["sum"], "he_sum": he_sum, "sum_abs_error": sum_error, "sum_relative_error": relative, "status": status})
    metadata = json.loads(execution_path.read_text(encoding="utf-8"))
    metadata.update({"generated_dir": str(args.generated_dir.resolve()), "prepared_dir": str(prepared), "build_seconds": {"configure": configure_seconds, "build": build_seconds}, "runner_wall_seconds": wall_seconds, "repetitions": args.repetitions, "relative_tolerance": args.relative_tolerance})
    write_json(execution_path, metadata)
    _report(root, groups=groups, scale=scale, tolerance=args.relative_tolerance)
    write_json(root / "result.json", {"status": "grouped_payment_diff_sum_executed", "accuracy_status": "PASS" if all_pass else "FAIL", "groups": len(groups), "bucket_size": bucket_size, "report": "REPORT.md"})
    print((root / "result.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
