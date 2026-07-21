#!/usr/bin/env python3
"""Run one standalone OpenFHE CKKS↔FHEW max session for PAYMENT_DIFF.

HEIR generates the ordinary CKKS arithmetic kernels elsewhere. This direct
OpenFHE runner exists because the installed HEIR OpenFHE lowering has no
scheme-switching operation. It retains only the encrypted max value; the
OpenFHE argmax result is discarded in memory.
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
from code.heir.scripts.run_payment_features_ciphertext_demo import run


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(payment_diff_max_scheme_switch LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(max_runner max_runner.cpp)
target_include_directories(max_runner PRIVATE "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include" "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(max_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(max_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(max_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER = r'''
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
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path); if (!input) throw std::runtime_error("cannot open input");
  std::string line; std::getline(input, line); std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}
int main(int argc, char** argv) {
  if (argc != 9) return 2;
  try {
    constexpr uint32_t slots = 4, numValues = 4, logQCtxtFHEW = 25;
    constexpr double scaleSign = 512.0;
    auto due = readVector(argv[1]); auto paid = readVector(argv[2]);
    require(due.size() == slots && paid.size() == slots, "max session requires four packed lanes");
    // Dedicated scheme-switching context; never mix it with ordinary HEIR CKKS.
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetMultiplicativeDepth(16); parameters.SetFirstModSize(60);
    parameters.SetScalingModSize(50); parameters.SetScalingTechnique(FLEXIBLEAUTOEXT);
    parameters.SetSecurityLevel(HEStd_NotSet); parameters.SetRingDim(8192);
    parameters.SetBatchSize(slots); parameters.SetKeySwitchTechnique(HYBRID);
    parameters.SetNumLargeDigits(3);
    auto context = GenCryptoContext(parameters);
    context->Enable(PKE); context->Enable(KEYSWITCH); context->Enable(LEVELEDSHE);
    context->Enable(ADVANCEDSHE); context->Enable(SCHEMESWITCH);
    auto keys = context->KeyGen(); require(keys.good(), "scheme-switch session key generation failed");
    SchSwchParams switchParameters;
    switchParameters.SetSecurityLevelCKKS(HEStd_NotSet);
    switchParameters.SetSecurityLevelFHEW(TOY);
    switchParameters.SetCtxtModSizeFHEWLargePrec(logQCtxtFHEW);
    switchParameters.SetNumSlotsCKKS(slots); switchParameters.SetNumValues(numValues);
    switchParameters.SetComputeArgmin(true);
    auto lweSecretKey = context->EvalSchemeSwitchingSetup(switchParameters);
    auto lweContext = context->GetBinCCForSchemeSwitch();
    context->EvalSchemeSwitchingKeyGen(keys, lweSecretKey);
    const uint32_t modulusLWE = 1U << logQCtxtFHEW;
    const uint32_t pLWE = modulusLWE / (2U * lweContext->GetBeta().ConvertToInt());
    const double maxSafeAbs = (static_cast<double>(pLWE) / 2.0 - 1.0) / scaleSign;
    context->EvalCompareSwitchPrecompute(pLWE, scaleSign);
    auto encryptedDue = context->Encrypt(keys.publicKey, context->MakeCKKSPackedPlaintext(due));
    auto encryptedPaid = context->Encrypt(keys.publicKey, context->MakeCKKSPackedPlaintext(paid));
    auto encryptedFeature = context->EvalSub(encryptedDue, encryptedPaid);
    require(Serial::SerializeToFile(argv[3], encryptedDue, SerType::BINARY), "cannot save encrypted installment parent");
    require(Serial::SerializeToFile(argv[4], encryptedPaid, SerType::BINARY), "cannot save encrypted payment parent");
    require(Serial::SerializeToFile(argv[5], encryptedFeature, SerType::BINARY), "cannot save payment_diff feature");
    auto maxAndArgmax = context->EvalMaxSchemeSwitching(encryptedFeature, keys.publicKey, numValues, slots, pLWE, scaleSign);
    require(!maxAndArgmax.empty(), "scheme switch did not return encrypted max");
    require(Serial::SerializeToFile(argv[6], maxAndArgmax[0], SerType::BINARY), "cannot save encrypted max");
    // maxAndArgmax[1] is intentionally not saved or decrypted.
    Plaintext featurePlain; context->Decrypt(keys.secretKey, encryptedFeature, &featurePlain); featurePlain->SetLength(slots);
    Plaintext maxPlain; context->Decrypt(keys.secretKey, maxAndArgmax[0], &maxPlain); maxPlain->SetLength(1);
    std::ofstream featureAudit(argv[7]); featureAudit << "value\n" << std::setprecision(17);
    for (const auto& value : featurePlain->GetRealPackedValue()) featureAudit << value << '\n';
    std::ofstream metrics(argv[8]); metrics << std::setprecision(17)
      << "{\"max\":" << maxPlain->GetRealPackedValue()[0]
      << ",\"p_lwe\":" << pLWE << ",\"scale_sign\":" << scaleSign
      << ",\"max_safe_absolute_input\":" << maxSafeAbs
      << ",\"argmax_returned_by_openfhe_but_discarded\":true}\n";
    return 0;
  } catch (const OpenFHEException& error) { std::cerr << "OpenFHE max-session error: " << error.what() << '\n'; return 1; }
    catch (const std::exception& error) { std::cerr << "max-session error: " << error.what() << '\n'; return 1; }
}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    # Max needs a power-of-two candidate count. This synthetic lane is within
    # the FHEW comparison range, below the public review-feature lower bound
    # (-100), and therefore cannot win. Do not use an extreme sentinel here:
    # FHEW comparisons operate modulo a bounded plaintext space.
    padding_floor = -128.0
    due = [row["AMT_INSTALMENT"] for row in DEMO_ROWS] + [padding_floor]
    paid = [row["AMT_PAYMENT"] for row in DEMO_ROWS] + [0.0]
    expected_feature = [left - right for left, right in zip(due, paid)]
    inputs = root / "plaintext_inputs"; inputs.mkdir()
    due_path, paid_path = inputs / "amt_installment.csv", inputs / "amt_payment.csv"
    write_values(due_path, due); write_values(paid_path, paid)
    work = root / "runner"; work.mkdir()
    (work / "max_runner.cpp").write_text(RUNNER, encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work.resolve()), "-B", str(build.resolve()), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build.resolve()), "--target", "max_runner"], work)
    ciphertexts = root / "ciphertexts"; ciphertexts.mkdir()
    audit_path, metrics_path = root / "feature_audit.csv", root / "metrics.json"
    run([str((build / "max_runner").resolve()), str(due_path.resolve()), str(paid_path.resolve()), str((ciphertexts / "amt_installment.ct").resolve()), str((ciphertexts / "amt_payment.ct").resolve()), str((ciphertexts / "payment_diff.ct").resolve()), str((ciphertexts / "payment_diff_max.ct").resolve()), str(audit_path.resolve()), str(metrics_path.resolve())], work)
    feature = [float(row["value"]) for row in read_csv(audit_path)]
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    expected_max = max(expected_feature)
    rows = [{"row": index, "python": expected, "he": feature[index], "absolute_error": abs(expected - feature[index])} for index, expected in enumerate(expected_feature[: len(DEMO_ROWS)])]
    max_row = {"aggregation": "max", "python": expected_max, "he": metrics["max"], "absolute_error": abs(expected_max - metrics["max"]), "argmax_artifact": "not retained"}
    write_csv(root / "feature_comparison.csv", list(rows[0]), rows)
    write_csv(root / "max_comparison.csv", list(max_row), [max_row])
    result = {"status": "openfhe_ckks_fhew_max_executed", "scope": "standalone CKKS-to-FHEW max session; parent columns encrypted then PAYMENT_DIFF calculated after encryption", "important_limit": "separate OpenFHE scheme-switching context; it cannot consume a ciphertext from the ordinary HEIR CKKS session", "comparison_range_contract": {"review_payment_diff_range": "[-100, 160]", "padding_floor": padding_floor, "rule": "all real candidates and padding must have absolute value less than execution.max_safe_absolute_input; choose range/padding from public schema bounds, never an extreme sentinel"}, "padding": {"synthetic_lane": 3, "payment_diff_value": padding_floor, "reason": "power-of-two candidate count required for max"}, "argmax": "OpenFHE returns it internally but this runner neither serializes nor decrypts it", "build_seconds": {"configure": configure_seconds, "build": build_seconds}, "feature_comparison": rows, "max_comparison": max_row, "execution": metrics, "ciphertext_artifacts": [str(item) for item in sorted(ciphertexts.glob("*.ct"))]}
    write_json(root / "result.json", result)
    print(json.dumps({"status": result["status"], "max_comparison": max_row, "output_dir": str(root)}, indent=2))


if __name__ == "__main__":
    main()
