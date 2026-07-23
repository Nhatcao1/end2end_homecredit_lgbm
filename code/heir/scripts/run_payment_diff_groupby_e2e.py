#!/usr/bin/env python3
"""Run one post-PSI, small-group end-to-end PAYMENT_DIFF aggregation proof.

This is intentionally one orchestrator for the original installments feature
family, rather than four disconnected micro-benchmarks.  It uses the private
PSI bridge only to choose eligible applicant groups.  The actual HE path is:

``AMT_INSTALMENT, AMT_PAYMENT -> PAYMENT_DIFF -> MAX/MEAN/SUM/VAR``.

The only decryptions are the final aggregate audit values after *all* groups
and all aggregate branches have completed.  CKKS arithmetic comes from HEIR
generated subtract/multiply/sum kernels; OpenFHE provides the required
CKKS-to-FHEW maximum reduction on the same live context.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import write_json
from code.heir.scripts.generate_ckks_baseline_kernels import generate
from code.heir.scripts.run_payment_features_ciphertext_demo import copy_generated_sources, run


KEY = "SK_ID_CURR"
REQUIRED = (KEY, "AMT_PAYMENT", "AMT_INSTALMENT")


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_diff_groupby_e2e LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(payment_diff_e2e_runner sub_output.cpp mul_output.cpp sum_output.cpp payment_diff_e2e_runner.cpp)
target_include_directories(payment_diff_e2e_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(payment_diff_e2e_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(payment_diff_e2e_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(payment_diff_e2e_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
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
#include "binfhecontext.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "openfhe.h"
#include "scheme/ckksrns/ckksrns-ser.h"
#include "sub_output.h"
#include "mul_output.h"
#include "sum_output.h"
using namespace lbcrypto;
using Bundle = std::vector<Ciphertext<DCRTPoly>>;
struct Group {
  std::vector<double> installment, payment, maxInstallment, maxPayment;
  std::vector<int> seen, real; size_t count = 0;
};
struct Final {
  unsigned long long id; size_t count;
  double encrypt, feature, square, sum, variance, maximum;
  Bundle total, mean, varianceValue; Ciphertext<DCRTPoly> maximumValue;
};
double seconds(std::chrono::steady_clock::time_point start) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(); }
void require(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }
std::vector<std::string> split(const std::string& line) { std::stringstream in(line); std::vector<std::string> out; std::string value; while (std::getline(in, value, ',')) out.push_back(value); return out; }
std::map<unsigned long long, Group> readGroups(const std::string& path, size_t slots, double scale) {
  std::ifstream input(path); require(input.good(), "cannot open " + path); std::string line; std::getline(input, line); std::map<unsigned long long, Group> groups;
  while (std::getline(input, line)) {
    if (line.empty()) continue; auto fields = split(line); require(fields.size() == 5, "expected five group CSV columns");
    const auto id = std::stoull(fields[0]); const auto lane = std::stoull(fields[1]); const double payment = std::stod(fields[2]); const double installment = std::stod(fields[3]); const int valid = std::stoi(fields[4]);
    require(lane < slots && (valid == 0 || valid == 1), "invalid lane or validity mask");
    auto inserted = groups.emplace(id, Group{std::vector<double>(slots, 0.0), std::vector<double>(slots, 0.0), std::vector<double>(slots, 0.0), std::vector<double>(slots, 0.0), std::vector<int>(slots, 0), std::vector<int>(slots, 0), 0});
    Group& group = inserted.first->second; require(!group.seen[lane], "duplicate group lane"); group.seen[lane] = 1;
    if (!valid) { require(payment == 0.0 && installment == 0.0, "invalid lane must use zero parent values"); continue; }
    group.payment[lane] = payment / scale; group.installment[lane] = installment / scale; group.real[lane] = 1; ++group.count;
  }
  require(!groups.empty(), "no groups in prepared input");
  for (auto& item : groups) {
    Group& group = item.second; require(group.count > 1, "each selected group needs at least two rows for sample variance");
    size_t first = 0; while (first < slots && !group.real[first]) ++first;
    // Sum/mean/variance use zero padding. MAX gets an independent encrypted
    // representation whose non-real lanes repeat a genuine parent pair, so
    // padding cannot change the maximum even when PAYMENT_DIFF is negative.
    require(first < slots, "group has no real lane");
    group.maxInstallment = group.installment; group.maxPayment = group.payment;
    for (size_t lane = 0; lane < slots; ++lane) if (!group.real[lane]) { group.maxInstallment[lane] = group.installment[first]; group.maxPayment[lane] = group.payment[first]; }
  }
  return groups;
}
double decryptScalar(const CryptoContext<DCRTPoly>& context, const PrivateKey<DCRTPoly>& key, const Ciphertext<DCRTPoly>& ciphertext) {
  Plaintext plain; context->Decrypt(key, ciphertext, &plain); plain->SetLength(1); return plain->GetRealPackedValue().at(0);
}
int main(int argc, char** argv) {
  // executable + prepared CSV + scale + HE results CSV + execution JSON
  if (argc != 5) return 2;
  try {
    const size_t slots = @SLOTS@; const double scale = std::stod(argv[2]); require(scale > 0.0, "input scale must be positive");
    const auto groups = readGroups(argv[1], slots, scale);
    auto setupStart = std::chrono::steady_clock::now();
    auto context = encrypted_multiply__generate_crypto_context();
    context->Enable(PKE); context->Enable(KEYSWITCH); context->Enable(LEVELEDSHE); context->Enable(ADVANCEDSHE); context->Enable(SCHEMESWITCH); context->Enable(FHE);
    auto keys = context->KeyGen(); require(keys.good(), "key generation failed");
    context = encrypted_multiply__configure_crypto_context(context, keys.secretKey);
    context = encrypted_sum__configure_crypto_context(context, keys.secretKey);
    context = encrypted_subtract__configure_crypto_context(context, keys.secretKey);
    SchSwchParams switchParameters; switchParameters.SetSecurityLevelCKKS(HEStd_NotSet); switchParameters.SetSecurityLevelFHEW(TOY); switchParameters.SetCtxtModSizeFHEWLargePrec(25); switchParameters.SetNumSlotsCKKS(slots); switchParameters.SetNumValues(slots); switchParameters.SetComputeArgmin(false);
    auto lweSecretKey = context->EvalSchemeSwitchingSetup(switchParameters);
    context->EvalSchemeSwitchingKeyGen(keys, lweSecretKey); context->EvalCompareSwitchPrecompute(1, 1, true);
    const double setupSeconds = seconds(setupStart);
    std::vector<Final> final; final.reserve(groups.size());
    for (const auto& item : groups) {
      const auto& group = item.second; auto started = std::chrono::steady_clock::now();
      auto due = encrypted_subtract__encrypt__arg0(context, group.installment, keys.publicKey); auto paid = encrypted_subtract__encrypt__arg1(context, group.payment, keys.publicKey);
      auto maxDue = encrypted_subtract__encrypt__arg0(context, group.maxInstallment, keys.publicKey); auto maxPaid = encrypted_subtract__encrypt__arg1(context, group.maxPayment, keys.publicKey); const double encrypt = seconds(started);
      started = std::chrono::steady_clock::now(); auto diff = encrypted_subtract(context, due, paid); auto maxDiff = encrypted_subtract(context, maxDue, maxPaid); const double feature = seconds(started);
      auto squareInput = diff; started = std::chrono::steady_clock::now(); auto squared = encrypted_multiply(context, squareInput, squareInput); const double square = seconds(started);
      auto sumInput = diff; auto squareSumInput = squared; started = std::chrono::steady_clock::now(); auto total = encrypted_sum(context, sumInput); auto squareTotal = encrypted_sum(context, squareSumInput); require(total.size() == 1 && squareTotal.size() == 1, "expected scalar encrypted sums"); const double sum = seconds(started);
      started = std::chrono::steady_clock::now(); const double inverse = 1.0 / static_cast<double>(group.count); Bundle mean = total; mean[0] = context->EvalMult(mean[0], inverse); auto sumTimesMean = context->EvalMult(total[0], mean[0]); auto varianceScalar = context->EvalSub(squareTotal[0], sumTimesMean); varianceScalar = context->EvalMult(varianceScalar, 1.0 / static_cast<double>(group.count - 1)); Bundle variance{varianceScalar}; const double varianceSeconds = seconds(started);
      started = std::chrono::steady_clock::now(); auto maximumValues = context->EvalMaxSchemeSwitching(maxDiff[0], keys.publicKey, slots, slots); require(!maximumValues.empty(), "scheme-switched maximum missing"); const double maximum = seconds(started);
      final.push_back(Final{item.first, group.count, encrypt, feature, square, sum, varianceSeconds, maximum, std::move(total), std::move(mean), std::move(variance), maximumValues[0]});
    }
    // Final audit boundary: no decrypted value above was used by an HE operation.
    std::ofstream output(argv[3]); output << std::setprecision(17) << "opaque_group_id,count,encrypt_seconds,feature_seconds,square_seconds,sum_reduce_seconds,variance_finalize_seconds,max_switch_seconds,audit_decrypt_seconds,he_online_seconds,he_max,he_mean,he_sum,he_var\n";
    for (const auto& item : final) {
      auto started = std::chrono::steady_clock::now(); const double maximum = decryptScalar(context, keys.secretKey, item.maximumValue) * scale; const double mean = encrypted_sum__decrypt__result0(context, item.mean, keys.secretKey) * scale; const double total = encrypted_sum__decrypt__result0(context, item.total, keys.secretKey) * scale; const double variance = encrypted_sum__decrypt__result0(context, item.varianceValue, keys.secretKey) * scale * scale; const double audit = seconds(started);
      const double online = item.encrypt + item.feature + item.square + item.sum + item.variance + item.maximum;
      output << item.id << ',' << item.count << ',' << item.encrypt << ',' << item.feature << ',' << item.square << ',' << item.sum << ',' << item.variance << ',' << item.maximum << ',' << audit << ',' << online << ',' << maximum << ',' << mean << ',' << total << ',' << variance << '\n';
    }
    std::ofstream meta(argv[4]); meta << std::setprecision(17) << "{\"setup_seconds\":" << setupSeconds << ",\"groups\":" << groups.size() << ",\"logical_slots\":" << slots << ",\"ring_dimension\":" << context->GetRingDimension() << ",\"one_crypto_context\":true,\"context_origin\":\"HEIR generated CKKS multiply context; subtract and sum configured on the same instance\",\"maximum_route\":\"FHEW switching keys attached to that same CryptoContext; no second CKKS encryption\",\"pipeline\":\"CKKS HEIR subtract/multiply/sum plus same-context CKKS-to-FHEW maximum; audit decrypt only at end\"}\n";
    return 0;
  } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
'''


def _finite(value: str | None) -> float | None:
    try:
        parsed = float(value or "")
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _bridge_keys(bridge_dir: Path) -> set[str]:
    path = bridge_dir / "private_exchange" / "sender_application_layout.csv"
    if not path.is_file():
        raise FileNotFoundError(f"post-PSI bridge layout is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != {"app_index", KEY}:
            raise ValueError("unexpected sender bridge layout schema")
        return {(row.get(KEY) or "").strip() for row in reader if (row.get(KEY) or "").strip()}


def _prepare(
    installments: Path, bridge_dir: Path, output: Path, group_count: int, bucket_size: int
) -> dict[str, Any]:
    """Create one private, post-PSI fixed-block fixture and Pandas reference."""
    keys = _bridge_keys(bridge_dir)
    counts: Counter[str] = Counter()
    raw_rows = invalid_rows = 0
    with installments.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(REQUIRED) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"installments CSV is missing: {sorted(missing)}")
        for row in reader:
            raw_rows += 1; key = (row.get(KEY) or "").strip()
            if key not in keys:
                continue
            if _finite(row.get("AMT_PAYMENT")) is None or _finite(row.get("AMT_INSTALMENT")) is None:
                invalid_rows += 1; continue
            counts[key] += 1
    selected = sorted((key for key, count in counts.items() if 2 <= count <= bucket_size), key=lambda key: (-counts[key], hashlib.blake2b(key.encode(), digest_size=8).digest()))[:group_count]
    if len(selected) != group_count:
        raise ValueError(f"only {len(selected)} post-PSI groups fit bucket {bucket_size}; requested {group_count}")
    rows: dict[str, list[tuple[float, float]]] = defaultdict(list)
    with installments.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get(KEY) or "").strip()
            if key not in selected:
                continue
            payment, installment = _finite(row.get("AMT_PAYMENT")), _finite(row.get("AMT_INSTALMENT"))
            if payment is not None and installment is not None:
                rows[key].append((payment, installment))
    if any(len(rows[key]) != counts[key] for key in selected):
        raise RuntimeError("source passes disagree for a selected post-PSI group")
    private, ready = output / "client_private", output / "he_ready"
    private.mkdir(parents=True); ready.mkdir(parents=True)
    blocks, reference, mapping = ready / "group_blocks.csv", private / "pandas_groupby_reference.csv", private / "group_mapping.csv"
    with blocks.open("w", encoding="utf-8", newline="") as block_file, reference.open("w", encoding="utf-8", newline="") as ref_file, mapping.open("w", encoding="utf-8", newline="") as map_file:
        block_writer, ref_writer, map_writer = csv.writer(block_file), csv.writer(ref_file), csv.writer(map_file)
        block_writer.writerow(["opaque_group_id", "lane", "AMT_PAYMENT", "AMT_INSTALMENT", "validity_mask"])
        ref_writer.writerow(["opaque_group_id", "count", "max", "mean", "sum", "var"]); map_writer.writerow(["opaque_group_id", KEY, "count"])
        for opaque, key in enumerate(selected):
            parents = rows[key]; diff = [installment - payment for payment, installment in parents]
            total = math.fsum(diff); mean = total / len(diff); variance = math.fsum((value - mean) ** 2 for value in diff) / (len(diff) - 1)
            ref_writer.writerow([opaque, len(diff), max(diff), mean, total, variance]); map_writer.writerow([opaque, key, len(diff)])
            for lane in range(bucket_size):
                if lane < len(parents):
                    payment, installment = parents[lane]; block_writer.writerow([opaque, lane, format(payment, ".17g"), format(installment, ".17g"), 1])
                else:
                    block_writer.writerow([opaque, lane, 0, 0, 0])
    max_bound = max(abs(payment) + abs(installment) for group in rows.values() for payment, installment in group)
    scale = float(2 ** max(1, math.ceil(math.log2(2.0 * max_bound + 1.0))))
    result: dict[str, Any] = {"status": "post_psi_payment_diff_fixture_ready", "bridge_dir": str(bridge_dir), "source_rows_scanned": raw_rows, "post_psi_applicants": len(keys), "invalid_parent_rows_with_matched_key": invalid_rows, "groups": len(selected), "bucket_size": bucket_size, "real_rows": sum(len(rows[key]) for key in selected), "input_scale": scale, "privacy_note": "group mapping and Pandas reference are client private; HE-ready input has opaque groups and numeric parent columns only"}
    write_json(output / "preparation.json", result)
    return result


def _patch_depth(path: Path, depth: int) -> int:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"SetMultiplicativeDepth\((\d+)\);", source)
    if not match:
        raise ValueError("HEIR multiply source has no explicit multiplicative-depth setting")
    original = int(match.group(1))
    path.write_text(source[:match.start(1)] + str(depth) + source[match.end(1):], encoding="utf-8")
    return original


def _pandas_reference(path: Path) -> tuple[list[dict[str, float]], dict[str, float]]:
    try:
        import pandas as pd
    except ImportError as error:
        raise RuntimeError("install pandas in the active HEIR environment: python3 -m pip install pandas") from error
    frame = pd.read_csv(path)
    real = frame.loc[frame["validity_mask"] == 1, ["opaque_group_id", "AMT_PAYMENT", "AMT_INSTALMENT"]].copy()
    started = time.perf_counter()
    real["PAYMENT_DIFF"] = real["AMT_INSTALMENT"] - real["AMT_PAYMENT"]
    feature_seconds = time.perf_counter() - started
    started = time.perf_counter()
    result = real.groupby("opaque_group_id")["PAYMENT_DIFF"].agg(["max", "mean", "sum", "var"])
    aggregation_seconds = time.perf_counter() - started
    return [
        {"opaque_group_id": int(index), "count": int((real["opaque_group_id"] == index).sum()), **{key: float(value) for key, value in row.items()}}
        for index, row in result.iterrows()
    ], {
        "payment_diff_expression_seconds": feature_seconds,
        "groupby_aggregations_seconds": aggregation_seconds,
        "full_workload_seconds": feature_seconds + aggregation_seconds,
    }


def _status(observed: float, expected: float, tolerance: float) -> tuple[float, str]:
    error = abs(observed - expected)
    return error, "PASS" if error <= tolerance * max(1.0, abs(expected)) else "FAIL"


def _report(root: Path, preparation: dict[str, Any], pandas_timing: dict[str, float], tolerance: float) -> None:
    he = list(csv.DictReader((root / "he_results.csv").open(encoding="utf-8", newline="")))
    reference = {int(row["opaque_group_id"]): row for row in csv.DictReader((root / "client_private" / "pandas_groupby_reference.csv").open(encoding="utf-8", newline=""))}
    execution = json.loads((root / "execution.json").read_text(encoding="utf-8"))
    lines = [
        "# Post-PSI PAYMENT_DIFF end-to-end aggregation proof", "",
        "This is one small execution of the original `installments_payments()` feature family after PSI alignment. It returns `PAYMENT_DIFF_MAX`, `PAYMENT_DIFF_MEAN`, `PAYMENT_DIFF_SUM`, and `PAYMENT_DIFF_VAR` for every selected opaque applicant group.", "",
        "No parent or derived feature is decrypted between operations. CKKS subtraction, square, sum, mean, and variance operate on ciphertexts. Maximum uses OpenFHE CKKS↔FHEW switching on the derived encrypted `PAYMENT_DIFF` branch in the same live context. Decryption occurs only at the final audit boundary.", "",
        "| Post-PSI applicants available | Selected groups | Real installment rows | Bucket lanes/group | Public representation scale |", "|---:|---:|---:|---:|---:|",
        f"| {preparation['post_psi_applicants']} | {preparation['groups']} | {preparation['real_rows']} | {preparation['bucket_size']} | {preparation['input_scale']:g} |", "",
        "## Final aggregate accuracy", "", "| Opaque group | Output | Pandas | Final HE audit | Absolute error | Status |", "|---:|---|---:|---:|---:|---|",
    ]
    all_pass = True
    fields = (("MAX", "max", "he_max"), ("MEAN", "mean", "he_mean"), ("SUM", "sum", "he_sum"), ("VAR", "var", "he_var"))
    for row in he:
        ref = reference[int(row["opaque_group_id"])]
        for label, plain_field, he_field in fields:
            error, status = _status(float(row[he_field]), float(ref[plain_field]), tolerance); all_pass &= status == "PASS"
            lines.append(f"| {row['opaque_group_id']} | `PAYMENT_DIFF_{label}` | {float(ref[plain_field]):.12g} | {float(row[he_field]):.12g} | {error:.12g} | {status} |")
    def total(field: str) -> float: return sum(float(row[field]) for row in he)
    he_online = total("he_online_seconds")
    audit = total("audit_decrypt_seconds")
    setup = float(execution["setup_seconds"])
    lines += [
        "", "## Equivalent Pandas workload", "",
        "The Python timing uses the exact selected post-PSI rows and equivalent original-code logic below. It excludes CSV read and client selection/padding, just as the HE calculation comparison excludes those client-only steps.", "",
        "```python", "real['PAYMENT_DIFF'] = real['AMT_INSTALMENT'] - real['AMT_PAYMENT']", "real.groupby('opaque_group_id')['PAYMENT_DIFF'].agg(['max', 'mean', 'sum', 'var'])", "```", "",
        "## Full end-to-end latency after PSI", "",
        "PSI execution itself is excluded. `HE online` sums the ciphertext path across every selected group: parent encryption, `PAYMENT_DIFF`, square, encrypted reductions, encrypted mean/variance, and CKKS↔FHEW maximum. It excludes only the final audit decrypt. The shared context/setup is paid once for the entire workload.", "",
        "| Client post-PSI layout | Shared one-context setup | HE encrypt, all groups | PAYMENT_DIFF, all groups | Square, all groups | SUM reductions, all groups | Mean/variance, all groups | MAX switch, all groups | HE online, all groups | Final audit decrypt, all groups | Pandas expression | Pandas groupby | Pandas total |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {preparation.get('client_prepare_seconds', 0.0):.9f} | {setup:.9f} | {total('encrypt_seconds'):.9f} | {total('feature_seconds'):.9f} | {total('square_seconds'):.9f} | {total('sum_reduce_seconds'):.9f} | {total('variance_finalize_seconds'):.9f} | {total('max_switch_seconds'):.9f} | {he_online:.9f} | {audit:.9f} | {pandas_timing['payment_diff_expression_seconds']:.9f} | {pandas_timing['groupby_aggregations_seconds']:.9f} | {pandas_timing['full_workload_seconds']:.9f} |",
        "", "| Fair workload comparison | Seconds | HE ÷ Pandas |", "|---|---:|---:|",
        f"| Pandas complete expression + groupby | {pandas_timing['full_workload_seconds']:.9f} | 1.00× |",
        f"| HE all groups: encryption through final encrypted aggregate | {he_online:.9f} | {he_online / pandas_timing['full_workload_seconds']:.2f}× |",
        f"| HE all groups: one setup + online + final audit | {setup + he_online + audit:.9f} | {(setup + he_online + audit) / pandas_timing['full_workload_seconds']:.2f}× |",
        "", "## Context contract", "",
        f"`one_crypto_context = {execution['one_crypto_context']}`. The context begins as `{execution['context_origin']}`. `{execution['maximum_route']}`", "",
        f"Overall audit status: **{'PASS' if all_pass else 'FAIL'}** with relative tolerance `{tolerance:g}`. A failed MAX/VAR entry is a failed HE result and must not be used downstream.", "", "Private files: `client_private/group_mapping.csv`, `client_private/pandas_groupby_reference.csv`. HE input: `he_ready/group_blocks.csv`. Raw timing rows: `he_results.csv`, `execution.json`."
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(root / "result.json", {"status": "payment_diff_groupby_e2e_executed", "accuracy_status": "PASS" if all_pass else "FAIL", "groups": len(he), "report": "REPORT.md"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-dir", type=Path, required=True, help="PSI-to-HEIR bridge for installments only")
    parser.add_argument("--installments", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--group-count", type=int, choices=(2, 5), default=2)
    parser.add_argument("--bucket-size", type=int, default=128)
    parser.add_argument("--ciphertext-degree", type=int, default=65536, help="large enough for the integrated FHEW MAX route")
    parser.add_argument("--ckks-mul-depth", type=int, default=20, help="shared depth for square, variance, and FHEW MAX")
    parser.add_argument("--relative-tolerance", type=float, default=1e-5)
    parser.add_argument("--heir-opt", default="heir-opt"); parser.add_argument("--heir-translate", default="heir-translate")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.bucket_size < 2 or args.bucket_size & (args.bucket_size - 1): raise ValueError("bucket size must be a power of two >= 2 for FHEW MAX")
    if args.ckks_mul_depth < 20: raise ValueError("--ckks-mul-depth must be at least 20 for the integrated FHEW MAX route")
    if args.ciphertext_degree < 65536: raise ValueError("--ciphertext-degree must be at least 65536 for the integrated FHEW MAX route")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite: raise FileExistsError(f"refusing to overwrite {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    started = time.perf_counter(); preparation = _prepare(args.installments.resolve(), args.bridge_dir.resolve(), root, args.group_count, args.bucket_size); preparation["client_prepare_seconds"] = time.perf_counter() - started; write_json(root / "preparation.json", preparation)
    pandas, pandas_timing = _pandas_reference(root / "he_ready" / "group_blocks.csv")
    with (root / "client_private" / "pandas_groupby_reference.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["opaque_group_id", "count", "max", "mean", "sum", "var"])
        writer.writeheader(); writer.writerows(pandas)
    # Generate only the generic HEIR arithmetic kernels required by this one feature family.
    generated = root / "generated"
    generation = generate(generated, slot_count=args.bucket_size, ciphertext_degree=args.ciphertext_degree, lower=True, heir_opt=args.heir_opt, heir_translate=args.heir_translate, profile="all", entries=("encrypted_subtract", "encrypted_multiply", "encrypted_sum"))
    entries = {str(item["entry_function"]): item for item in generation["kernels"]}
    work = root / "runner"; work.mkdir()
    for entry, prefix in (("encrypted_subtract", "sub"), ("encrypted_multiply", "mul"), ("encrypted_sum", "sum")):
        copy_generated_sources((generated / entries[entry]["source"]).parent, work, prefix)
    translated_depth = _patch_depth(work / "mul_output.cpp", args.ckks_mul_depth)
    (work / "payment_diff_e2e_runner.cpp").write_text(RUNNER.replace("@SLOTS@", str(args.bucket_size)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"; configure_seconds, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work); build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "payment_diff_e2e_runner"], work)
    he, execution = root / "he_results.csv", root / "execution.json"
    wall_seconds, log = run(["env", "OMP_NUM_THREADS=1", str((build / "payment_diff_e2e_runner").resolve()), str((root / "he_ready" / "group_blocks.csv").resolve()), str(preparation["input_scale"]), str(he.resolve()), str(execution.resolve())], work)
    (work / "runner.log").write_text(log, encoding="utf-8")
    metadata = json.loads(execution.read_text(encoding="utf-8")); metadata.update({"generated_kernels": [item["entry_function"] for item in generation["kernels"]], "translated_multiply_depth_before_patch": translated_depth, "requested_multiplicative_depth": args.ckks_mul_depth, "build_seconds": {"configure": configure_seconds, "build": build_seconds}, "runner_wall_seconds": wall_seconds, "pandas_equivalent_timing_seconds": pandas_timing}); write_json(execution, metadata)
    _report(root, preparation, pandas_timing, args.relative_tolerance)
    print((root / "result.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
