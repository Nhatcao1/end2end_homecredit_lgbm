#!/usr/bin/env python3
"""Benchmark encrypted pairwise and threshold comparison with CKKS↔FHEW.

This is deliberately a direct OpenFHE benchmark, not a HEIR-generated CKKS
kernel: the installed HEIR lowering supports CKKS arithmetic, whereas an exact
ordered predicate needs OpenFHE's CKKS-to-FHEW scheme-switching API.

It evaluates two reusable predicates over the first four packed lanes:

* ``left < right`` where both parent columns are encrypted;
* ``left < public_threshold`` where the public threshold is encoded and
  encrypted in the same session.

The output remains a CKKS ciphertext after the FHEW comparison.  Decryption is
used only for the local audit files, not as a step in the encrypted predicate.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv, write_csv, write_json, write_values
from code.heir.scripts.run_payment_features_ciphertext_demo import run


# No pair has a zero or near-zero difference.  This is intentional: CKKS is
# approximate before switching, so strict equality cannot be certified by the
# sign route without a separately agreed equality/tolerance policy.
DEFAULT_LEFT = (-8.0, -0.5, 2.25, 6.0)
DEFAULT_RIGHT = (-7.0, 1.0, -1.25, 3.5)


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(ckks_fhew_comparison_benchmark LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(comparison_runner comparison_runner.cpp)
target_include_directories(comparison_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(comparison_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(comparison_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(comparison_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER = r'''
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "binfhecontext.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "openfhe.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;

using Clock = std::chrono::steady_clock;
double seconds(Clock::time_point start) {
  return std::chrono::duration<double>(Clock::now() - start).count();
}
void require(bool value, const std::string& message) {
  if (!value) throw std::runtime_error(message);
}
std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path.string());
  std::string line;
  std::getline(input, line);  // CSV header
  std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}
void writeAudit(const std::filesystem::path& path, const std::vector<double>& values) {
  std::ofstream output(path);
  if (!output) throw std::runtime_error("cannot write " + path.string());
  output << "raw_comparison_value\n" << std::setprecision(17);
  for (double value : values) output << value << '\n';
}
std::vector<double> decrypt(const CryptoContext<DCRTPoly>& context, const PrivateKey<DCRTPoly>& key,
                            const Ciphertext<DCRTPoly>& ciphertext, uint32_t slots) {
  Plaintext plain;
  context->Decrypt(key, ciphertext, &plain);
  plain->SetLength(slots);
  return plain->GetRealPackedValue();
}

int main(int argc, char** argv) {
  // left.csv right.csv threshold.csv pairwise.ct threshold.ct pairwise-audit.csv threshold-audit.csv metrics.json
  if (argc != 9) return 2;
  try {
    constexpr uint32_t slots = 4;
    constexpr uint32_t logQCtxtFHEW = 25;
    constexpr double scaleSign = 512.0;
    auto left = readVector(argv[1]);
    auto right = readVector(argv[2]);
    auto threshold = readVector(argv[3]);
    require(left.size() == slots && right.size() == slots && threshold.size() == slots,
            "comparison benchmark requires exactly four packed lanes");

    const auto setupStart = Clock::now();
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetMultiplicativeDepth(17);
    parameters.SetFirstModSize(60);
    parameters.SetScalingModSize(50);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(8192);
    parameters.SetBatchSize(slots);
    parameters.SetSecretKeyDist(UNIFORM_TERNARY);
    parameters.SetKeySwitchTechnique(HYBRID);
    parameters.SetNumLargeDigits(3);
    auto context = GenCryptoContext(parameters);
    context->Enable(PKE); context->Enable(KEYSWITCH); context->Enable(LEVELEDSHE);
    context->Enable(ADVANCEDSHE); context->Enable(SCHEMESWITCH);
    auto keys = context->KeyGen();
    require(keys.good(), "CKKS key generation failed");
    SchSwchParams switchParameters;
    switchParameters.SetSecurityLevelCKKS(HEStd_NotSet);
    switchParameters.SetSecurityLevelFHEW(TOY);
    switchParameters.SetCtxtModSizeFHEWLargePrec(logQCtxtFHEW);
    switchParameters.SetNumSlotsCKKS(slots);
    switchParameters.SetNumValues(slots);
    auto lweSecretKey = context->EvalSchemeSwitchingSetup(switchParameters);
    auto lweContext = context->GetBinCCForSchemeSwitch();
    lweContext->BTKeyGen(lweSecretKey);
    context->EvalSchemeSwitchingKeyGen(keys, lweSecretKey);
    const uint32_t modulusLWE = 1U << logQCtxtFHEW;
    const uint32_t pLWE = modulusLWE / (2U * lweContext->GetBeta().ConvertToInt());
    const double maxSafeAbsoluteDifference = (static_cast<double>(pLWE) / 2.0 - 1.0) / scaleSign;
    context->EvalCompareSwitchPrecompute(pLWE, scaleSign);
    const double setupSeconds = seconds(setupStart);

    const auto encryptStart = Clock::now();
    auto encryptedLeft = context->Encrypt(keys.publicKey, context->MakeCKKSPackedPlaintext(left));
    auto encryptedRight = context->Encrypt(keys.publicKey, context->MakeCKKSPackedPlaintext(right));
    auto encryptedThreshold = context->Encrypt(keys.publicKey, context->MakeCKKSPackedPlaintext(threshold));
    const double encryptSeconds = seconds(encryptStart);

    const auto pairwiseStart = Clock::now();
    auto encryptedPairwise = context->EvalCompareSchemeSwitching(encryptedLeft, encryptedRight, slots, slots);
    const double pairwiseSeconds = seconds(pairwiseStart);
    const auto thresholdStart = Clock::now();
    auto encryptedThresholdResult = context->EvalCompareSchemeSwitching(encryptedLeft, encryptedThreshold, slots, slots);
    const double thresholdSeconds = seconds(thresholdStart);

    require(Serial::SerializeToFile(argv[4], encryptedPairwise, SerType::BINARY), "cannot save pairwise ciphertext");
    require(Serial::SerializeToFile(argv[5], encryptedThresholdResult, SerType::BINARY), "cannot save threshold ciphertext");
    const auto auditStart = Clock::now();
    auto pairwiseAudit = decrypt(context, keys.secretKey, encryptedPairwise, slots);
    auto thresholdAudit = decrypt(context, keys.secretKey, encryptedThresholdResult, slots);
    const double auditSeconds = seconds(auditStart);
    writeAudit(argv[6], pairwiseAudit);
    writeAudit(argv[7], thresholdAudit);
    std::ofstream metrics(argv[8]);
    metrics << std::setprecision(17)
      << "{\"setup_seconds\":" << setupSeconds
      << ",\"encrypt_seconds\":" << encryptSeconds
      << ",\"pairwise_compare_seconds\":" << pairwiseSeconds
      << ",\"threshold_compare_seconds\":" << thresholdSeconds
      << ",\"audit_decrypt_seconds\":" << auditSeconds
      << ",\"p_lwe\":" << pLWE
      << ",\"scale_sign\":" << scaleSign
      << ",\"max_safe_absolute_difference\":" << maxSafeAbsoluteDifference
      << ",\"raw_result_contract\":\"approximately one means first input is less than second input; approximately zero means false\"}\n";
    return 0;
  } catch (const OpenFHEException& error) {
    std::cerr << "OpenFHE comparison-session error: " << error.what() << '\n'; return 1;
  } catch (const std::exception& error) {
    std::cerr << "comparison-session error: " << error.what() << '\n'; return 1;
  }
}
'''


def _read_values(path: Path) -> list[float]:
    """Read either owner input (`value`) or a one-column runner audit file."""
    rows = read_csv(path)
    if not rows:
        return []
    fields = list(rows[0])
    if len(fields) != 1:
        raise ValueError(f"expected exactly one numeric column in {path}, found {fields}")
    return [float(row[fields[0]]) for row in rows]


def _assert_input_contract(left: list[float], right: list[float], threshold: float, minimum_margin: float) -> None:
    if len(left) != 4 or len(right) != 4:
        raise ValueError("the current sparse comparison benchmark accepts exactly four values per parent column")
    if minimum_margin <= 0:
        raise ValueError("minimum margin must be positive")
    for label, values in (("pairwise", [a - b for a, b in zip(left, right)]), ("threshold", [a - threshold for a in left])):
        close = [index for index, value in enumerate(values) if abs(value) < minimum_margin]
        if close:
            raise ValueError(f"{label} lanes {close} violate the public minimum comparison margin {minimum_margin}; equality/near-zero is not accepted")


def _classification_rows(left: list[float], right: list[float], raw: list[float], mode: str, minimum_margin: float) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for lane, (first, second, raw_value) in enumerate(zip(left, right, raw)):
        expected = first < second
        # OpenFHE returns an approximate CKKS encoding of the FHEW predicate
        # bit: approximately one means ``first < second`` and approximately
        # zero means false. The 0.5 audit threshold is deliberately far from
        # either decoded result; it is not a secret-data threshold.
        observed = raw_value > 0.5
        rows.append({
            "mode": mode,
            "lane": lane,
            "left": first,
            "right_or_threshold": second,
            "difference": first - second,
            "public_minimum_margin": minimum_margin,
            "python_left_less_than_right": expected,
            "he_raw_comparison_value": raw_value,
            "he_left_less_than_right": observed,
            "match": expected == observed,
        })
    return rows


def _report(result: dict[str, object]) -> str:
    rows = result["rows"]
    pairwise = [row for row in rows if row["mode"] == "encrypted pairwise"]
    threshold = [row for row in rows if row["mode"] == "public threshold"]
    execution = result["execution"]
    return f'''# CKKS↔FHEW comparison benchmark

This benchmark tests an encrypted ordered predicate. Ordinary CKKS arithmetic
does not provide an exact `>` / `<` operation, so OpenFHE switches the
encrypted difference to FHEW for `EvalSign`, then returns an encrypted CKKS
sign result. HEIR is not used for this scheme-switching operation.

## What was tested

| Route | Encrypted inputs | Predicate | Result stays encrypted |
|---|---|---|---|
| Pairwise | `left`, `right` parent columns | `left < right` | Yes, returned CKKS ciphertext |
| Threshold | `left` and an encrypted repetition of public threshold | `left < threshold` | Yes, returned CKKS ciphertext |

## Safety contract

The route is only accepted for non-equal values with absolute difference at
least `{result["minimum_margin"]}`. Exact equality and values close to zero
are deliberately excluded: CKKS is approximate before the FHEW switch. The
maximum safe absolute difference for this OpenFHE session is
`{execution["max_safe_absolute_difference"]}`. Inputs outside that public
range must be normalized or rejected before encryption.

`he_raw_comparison_value > 0.5` means `left < right` (the decrypted values are
expected to be approximately `1` for true and `0` for false). The raw result
stays encrypted in the pipeline; this report decrypts it only for audit.

## Accuracy

| Route | Lanes | Correct lanes | Accuracy |
|---|---:|---:|---:|
| Pairwise | {len(pairwise)} | {sum(bool(row["match"]) for row in pairwise)} | {sum(bool(row["match"]) for row in pairwise) / len(pairwise):.0%} |
| Threshold | {len(threshold)} | {sum(bool(row["match"]) for row in threshold)} | {sum(bool(row["match"]) for row in threshold) / len(threshold):.0%} |

Full lane-level audit: `comparison_audit.csv`.

## Timing

| Stage | Seconds |
|---|---:|
| One-time CKKS/FHEW session and switching-key setup | {execution["setup_seconds"]:.6f} |
| Encrypt three packed inputs | {execution["encrypt_seconds"]:.6f} |
| Pairwise comparison evaluation | {execution["pairwise_compare_seconds"]:.6f} |
| Threshold comparison evaluation | {execution["threshold_compare_seconds"]:.6f} |
| Audit decryption only | {execution["audit_decrypt_seconds"]:.6f} |

## Artifacts

`ciphertexts/pairwise_comparison.ct` and
`ciphertexts/threshold_comparison.ct` record the two encrypted results. They
can feed a later selection or clipping operation **while the same dedicated
scheme-switching context and evaluation keys remain loaded**. A serialized
ciphertext alone is not sufficient to resume that session. These ciphertexts
cannot be mixed with ciphertexts from the ordinary HEIR CKKS context.
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    parser.add_argument("--left-csv", type=Path, help="optional one-column CSV with header 'value' and four values")
    parser.add_argument("--right-csv", type=Path, help="optional one-column CSV with header 'value' and four values")
    parser.add_argument("--threshold", type=float, default=0.0, help="public threshold for the second comparison route")
    parser.add_argument("--minimum-margin", type=float, default=0.25, help="public gap required between compared values")
    args = parser.parse_args()
    if bool(args.left_csv) != bool(args.right_csv):
        parser.error("provide both --left-csv and --right-csv, or neither")
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    left = _read_values(args.left_csv) if args.left_csv else list(DEFAULT_LEFT)
    right = _read_values(args.right_csv) if args.right_csv else list(DEFAULT_RIGHT)
    _assert_input_contract(left, right, args.threshold, args.minimum_margin)
    inputs = root / "plaintext_inputs"
    write_values(inputs / "left.csv", left)
    write_values(inputs / "right.csv", right)
    write_values(inputs / "threshold.csv", [args.threshold] * len(left))
    work = root / "runner"
    work.mkdir()
    (work / "comparison_runner.cpp").write_text(RUNNER, encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work), "-B", str(build), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build), "--target", "comparison_runner"], work)
    ciphertexts = root / "ciphertexts"
    ciphertexts.mkdir()
    pairwise_audit = root / "pairwise_raw_audit.csv"
    threshold_audit = root / "threshold_raw_audit.csv"
    metrics_path = root / "execution.json"
    run([
        str((build / "comparison_runner").resolve()),
        str((inputs / "left.csv").resolve()), str((inputs / "right.csv").resolve()), str((inputs / "threshold.csv").resolve()),
        str((ciphertexts / "pairwise_comparison.ct").resolve()), str((ciphertexts / "threshold_comparison.ct").resolve()),
        str(pairwise_audit.resolve()), str(threshold_audit.resolve()), str(metrics_path.resolve()),
    ], work)
    pairwise_raw = _read_values(pairwise_audit)
    threshold_raw = _read_values(threshold_audit)
    rows = _classification_rows(left, right, pairwise_raw, "encrypted pairwise", args.minimum_margin)
    rows.extend(_classification_rows(left, [args.threshold] * len(left), threshold_raw, "public threshold", args.minimum_margin))
    write_csv(root / "comparison_audit.csv", list(rows[0]), rows)
    execution = json.loads(metrics_path.read_text(encoding="utf-8"))
    result: dict[str, object] = {
        "status": "openfhe_ckks_fhew_comparison_executed",
        "scope": "four-lane encrypted pairwise and threshold comparison; raw output audited after decryption",
        "predicate": "left < right; raw OpenFHE CKKS comparison value above 0.5 means true",
        "important_limit": "strict equality is not accepted; each pre-encryption difference must meet the public minimum margin",
        "minimum_margin": args.minimum_margin,
        "build_seconds": {"configure": configure_seconds, "build": build_seconds},
        "execution": execution,
        "rows": rows,
        "ciphertext_artifacts": [str(item) for item in sorted(ciphertexts.glob("*.ct"))],
    }
    write_json(root / "result.json", result)
    (root / "REPORT.md").write_text(_report(result), encoding="utf-8")
    correct = sum(bool(row["match"]) for row in rows)
    print(json.dumps({"status": result["status"], "correct_lanes": f"{correct}/{len(rows)}", "output_dir": str(root)}, indent=2))


if __name__ == "__main__":
    main()
