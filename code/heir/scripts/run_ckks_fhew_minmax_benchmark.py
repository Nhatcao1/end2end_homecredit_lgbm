#!/usr/bin/env python3
"""Benchmark literal encrypted MIN and MAX over one packed CKKS vector.

This is distinct from ``run_ckks_fhew_comparison_benchmark.py``:

* comparison answers a lane-wise predicate for rules such as ``DPD > 0``;
* this benchmark reduces one encrypted vector to its encrypted global minimum
  and maximum, using OpenFHE's CKKS↔FHEW binary-tree algorithms.

The input is normalized after encryption encoding (not derived by the client):
the public input scale puts values inside ``(-0.5, 0.5]`` as required by the
OpenFHE unit-circle min/max route. The final audit rescales the decrypted min
and max to the original units only for comparison with Python.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from code.heir.common import read_csv, write_csv, write_json, write_values
from code.heir.scripts.run_ckks_fhew_comparison_benchmark import CMAKE
from code.heir.scripts.run_payment_features_ciphertext_demo import run


DEFAULT_PREPARED_BATCH = Path("data/prepared/installments_columns/batches/batch_000000.csv")
DEFAULT_VALUE_COUNT = 100


RUNNER = r'''
#include <algorithm>
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
  std::string line; std::getline(input, line);
  std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}
double decryptScalar(const CryptoContext<DCRTPoly>& context, const PrivateKey<DCRTPoly>& key,
                     const Ciphertext<DCRTPoly>& ciphertext) {
  Plaintext plain;
  context->Decrypt(key, ciphertext, &plain);
  plain->SetLength(1);
  return plain->GetRealPackedValue().at(0);
}

int main(int argc, char** argv) {
  // padded-values.csv input-scale encrypted-count min.ct max.ct metrics.json
  if (argc != 7) return 2;
  try {
    constexpr uint32_t firstModSize = 60;
    constexpr uint32_t scalingModSize = 50;
    const double inputScale = std::stod(argv[2]);
    const uint32_t numValues = static_cast<uint32_t>(std::stoul(argv[3]));
    const uint32_t slots = numValues;
    require(inputScale > 0, "input scale must be positive");
    require(numValues >= 2 && (numValues & (numValues - 1)) == 0,
            "encrypted candidate count must be a power of two");
    require(numValues <= 4096, "candidate count exceeds the 8192-ring CKKS slot capacity");
    auto rawValues = readVector(argv[1]);
    require(rawValues.size() == numValues, "padded input size does not match encrypted candidate count");
    std::vector<double> normalized;
    normalized.reserve(numValues);
    for (double value : rawValues) {
      const double encoded = value / inputScale;
      require(encoded > -0.5 && encoded <= 0.5,
              "value violates unit-circle input contract; raise --input-scale before encryption");
      normalized.push_back(encoded);
    }

    const auto setupStart = Clock::now();
    // Official OpenFHE unit-route depth: CKKS→FHEW + FHEW→CKKS + selection
    // multiplications + log2(number of candidates).
    const uint32_t multiplicativeDepth = 9 + 3 + 1 + static_cast<uint32_t>(std::log2(numValues));
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetMultiplicativeDepth(multiplicativeDepth);
    parameters.SetFirstModSize(firstModSize);
    parameters.SetScalingModSize(scalingModSize);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(8192);
    parameters.SetBatchSize(slots);
    parameters.SetSecretKeyDist(UNIFORM_TERNARY);
    parameters.SetKeySwitchTechnique(HYBRID);
    parameters.SetNumLargeDigits(3);
    auto context = GenCryptoContext(parameters);
    context->Enable(PKE); context->Enable(KEYSWITCH); context->Enable(LEVELEDSHE);
    context->Enable(ADVANCEDSHE); context->Enable(SCHEMESWITCH); context->Enable(FHE);
    auto keys = context->KeyGen();
    require(keys.good(), "CKKS key generation failed");
    SchSwchParams switchParameters;
    switchParameters.SetSecurityLevelCKKS(HEStd_NotSet);
    switchParameters.SetSecurityLevelFHEW(TOY);
    switchParameters.SetCtxtModSizeFHEWLargePrec(25);
    switchParameters.SetNumSlotsCKKS(slots);
    switchParameters.SetNumValues(numValues);
    switchParameters.SetComputeArgmin(true);
    auto lweSecretKey = context->EvalSchemeSwitchingSetup(switchParameters);
    context->EvalSchemeSwitchingKeyGen(keys, lweSecretKey);
    // Inputs are already normalized to (-0.5, 0.5], so their differences are
    // in (-1, 1]. This is the documented OpenFHE unit-circle min/max route.
    context->EvalCompareSwitchPrecompute(1, 1);
    const double setupSeconds = seconds(setupStart);

    const auto encryptStart = Clock::now();
    auto encryptedInput = context->Encrypt(keys.publicKey, context->MakeCKKSPackedPlaintext(normalized));
    const double encryptSeconds = seconds(encryptStart);
    const auto minStart = Clock::now();
    auto encryptedMinAndIgnoredArgmin = context->EvalMinSchemeSwitching(encryptedInput, keys.publicKey, numValues, slots);
    const double minSeconds = seconds(minStart);
    const auto maxStart = Clock::now();
    auto encryptedMaxAndIgnoredArgmax = context->EvalMaxSchemeSwitching(encryptedInput, keys.publicKey, numValues, slots);
    const double maxSeconds = seconds(maxStart);
    require(!encryptedMinAndIgnoredArgmin.empty() && !encryptedMaxAndIgnoredArgmax.empty(), "min/max result missing");
    require(Serial::SerializeToFile(argv[4], encryptedMinAndIgnoredArgmin[0], SerType::BINARY), "cannot save encrypted min");
    require(Serial::SerializeToFile(argv[5], encryptedMaxAndIgnoredArgmax[0], SerType::BINARY), "cannot save encrypted max");
    const auto auditStart = Clock::now();
    const double minNormalized = decryptScalar(context, keys.secretKey, encryptedMinAndIgnoredArgmin[0]);
    const double maxNormalized = decryptScalar(context, keys.secretKey, encryptedMaxAndIgnoredArgmax[0]);
    const double auditSeconds = seconds(auditStart);
    std::ofstream metrics(argv[6]);
    metrics << std::setprecision(17)
      << "{\"setup_seconds\":" << setupSeconds
      << ",\"encrypt_seconds\":" << encryptSeconds
      << ",\"min_evaluation_seconds\":" << minSeconds
      << ",\"max_evaluation_seconds\":" << maxSeconds
      << ",\"audit_decrypt_seconds\":" << auditSeconds
      << ",\"input_scale\":" << inputScale
      << ",\"encrypted_candidate_count\":" << numValues
      << ",\"ring_dimension\":8192"
      << ",\"multiplicative_depth\":" << multiplicativeDepth
      << ",\"min_normalized\":" << minNormalized
      << ",\"max_normalized\":" << maxNormalized
      << ",\"input_contract\":\"all normalized values in (-0.5,0.5]; padded encrypted candidate count is a power of two\""
      << ",\"argmin_argmax_returned_but_not_retained\":true}\n";
    return 0;
  } catch (const OpenFHEException& error) {
    std::cerr << "OpenFHE min/max-session error: " << error.what() << '\n'; return 1;
  } catch (const std::exception& error) {
    std::cerr << "min/max-session error: " << error.what() << '\n'; return 1;
  }
}
'''


def _read_values(path: Path) -> list[float]:
    return [float(row["value"]) for row in read_csv(path)]


def _next_power_of_two(value: int) -> int:
    if value < 2:
        return 2
    return 1 << (value - 1).bit_length()


def _validate(values: list[float], input_scale: float) -> None:
    if not values:
        raise ValueError("min/max benchmark needs at least one real value")
    if len(values) > 4096:
        raise ValueError("the 8192-ring benchmark supports at most 4096 real values per encrypted vector")
    if input_scale <= 0:
        raise ValueError("input scale must be positive")
    if any(not (-input_scale / 2 < value <= input_scale / 2) for value in values):
        raise ValueError("input violates the unit-circle contract; use an input scale greater than twice the absolute maximum")


def _pad_with_real_candidate(values: list[float]) -> tuple[list[float], dict[str, int | str]]:
    """Pad to a power of two without inventing an artificial low/high value."""
    encrypted_count = _next_power_of_two(len(values))
    if encrypted_count > 4096:
        raise ValueError("padded candidate count exceeds 4096 CKKS slots")
    padded = list(values)
    # Repeating one genuine candidate cannot change either min or max. This
    # avoids a sentinel whose value might accidentally become the reduction.
    padded.extend([values[0]] * (encrypted_count - len(values)))
    return padded, {
        "real_candidate_count": len(values),
        "encrypted_candidate_count": encrypted_count,
        "padding_count": encrypted_count - len(values),
        "padding_rule": "repeat the first genuine candidate; this cannot alter min or max",
    }


def _read_prepared_values(path: Path, column: str, count: int) -> list[float]:
    """Load a real sanitized parent column from a prepared installments batch."""
    if count < 1:
        raise ValueError("value count must be positive")
    if not path.is_file():
        raise FileNotFoundError(
            f"prepared installments batch is missing: {path}; run "
            "prepare_full_installments_columns.py first or pass --prepared-batch"
        )
    values: list[float] = []
    for row in read_csv(path):
        if column not in row:
            raise ValueError(f"prepared batch does not contain requested column {column!r}")
        if "valid" in row and float(row["valid"]) != 1.0:
            continue
        values.append(float(row[column]))
        if len(values) == count:
            break
    if len(values) != count:
        raise ValueError(f"prepared batch contains only {len(values)} valid values; requested {count}")
    return values


def _resolve_input_scale(values: list[float], requested_scale: float) -> tuple[float, str]:
    if requested_scale > 0:
        return requested_scale, "caller-supplied public encoding scale"
    if requested_scale < 0:
        raise ValueError("input scale must be zero (auto) or positive")
    max_abs = max(abs(value) for value in values)
    # Power-of-two scale strictly larger than twice the observed magnitude.
    # This is client-side representation calibration, not a min/max result.
    return float(1 << max(1, math.ceil(math.log2(max(1.0, 2.0 * max_abs + 1.0))))), "auto-selected public encoding scale from loaded input range"


def _report(result: dict[str, object]) -> str:
    execution = result["execution"]
    rows = result["results"]
    return f'''# CKKS↔FHEW literal MIN/MAX benchmark

This is a **reduction benchmark**, distinct from the lane-wise comparison
benchmark. One encrypted CKKS vector is passed separately to OpenFHE's
`EvalMinSchemeSwitching` and `EvalMaxSchemeSwitching`; each performs an
encrypted binary comparison/selection tree and returns one encrypted CKKS
value.

## Input contract

| Rule | Value |
|---|---:|
| Data source | `{result["input_source"]["kind"]}` — `{result["input_source"]["path"]}` |
| Parent column | `{result["input_source"]["column"]}` |
| Real candidates | {result["packing"]["real_candidate_count"]} |
| Encrypted candidates | {result["packing"]["encrypted_candidate_count"]} (power of two) |
| Duplicate padding lanes | {result["packing"]["padding_count"]} |
| Public input scale | {execution["input_scale"]} |
| Scale policy | {result["input_scale_policy"]} |
| Encoded interval | `(-0.5, 0.5]` |

The owner divides raw values by the public scale **only while encoding the
plaintext into CKKS**. It does not compute min or max client-side. If the raw
count is not a power of two (for example, 100), the client repeats one genuine
candidate to reach 128 encrypted lanes. Repetition cannot alter min or max.
The audit multiplies the decrypted result by the same scale only to express
accuracy in the original units. Tied values are valid: they produce the same
min/max value; only the discarded argmin/argmax identity may be non-unique.

## Python versus encrypted result

| Aggregate | Python | HE decrypted audit | Absolute error | Status |
|---|---:|---:|---:|---|
{''.join(f'| {row["aggregate"]} | {row["python"]:.12g} | {row["he"]:.12g} | {row["absolute_error"]:.6g} | {row["status"]} |\n' for row in rows)}

## Timing

| Stage | Seconds |
|---|---:|
| Python `min` + `max` audit baseline | {result["python_seconds"]:.9f} |
| One-time CKKS/FHEW session and switching-key setup | {execution["setup_seconds"]:.6f} |
| Encrypt one packed input vector | {execution["encrypt_seconds"]:.6f} |
| Encrypted min reduction | {execution["min_evaluation_seconds"]:.6f} |
| Encrypted max reduction | {execution["max_evaluation_seconds"]:.6f} |
| Audit decryption only | {execution["audit_decrypt_seconds"]:.6f} |

## Ciphertext boundary

`ciphertexts/encrypted_min.ct` and `ciphertexts/encrypted_max.ct` retain the
aggregate values. They can only be consumed while this dedicated OpenFHE
scheme-switching context and its evaluation keys remain loaded. They cannot be
mixed directly with ordinary HEIR CKKS ciphertexts. Argmin/argmax are returned
internally by OpenFHE but deliberately neither decrypted nor serialized.
'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--openfhe-dir", default="/usr/local/lib/OpenFHE")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--prepared-batch", type=Path, default=DEFAULT_PREPARED_BATCH, help="sanitized installments batch created by prepare_full_installments_columns.py")
    source.add_argument("--input-csv", type=Path, help="optional one-column real-data CSV with header 'value'")
    parser.add_argument("--column", default="AMT_PAYMENT", choices=("AMT_PAYMENT", "AMT_INSTALMENT"), help="prepared installments parent column")
    parser.add_argument("--value-count", type=int, default=DEFAULT_VALUE_COUNT, help="real values selected from the source; e.g. 100 or 1000")
    parser.add_argument("--input-scale", type=float, default=0.0, help="public encoding scale; 0 selects a power-of-two scale from loaded values")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    if root.exists():
        if not args.overwrite:
            raise FileExistsError(f"refusing to overwrite: {root}; pass --overwrite")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    if args.input_csv:
        values = _read_values(args.input_csv)
        source_info: dict[str, object] = {"kind": "one-column real-data CSV", "path": str(args.input_csv.resolve()), "column": "value"}
    else:
        prepared_batch = args.prepared_batch.resolve()
        values = _read_prepared_values(prepared_batch, args.column, args.value_count)
        source_info = {"kind": "prepared installments batch", "path": str(prepared_batch), "column": args.column, "requested_real_rows": args.value_count}
    input_scale, scale_policy = _resolve_input_scale(values, args.input_scale)
    _validate(values, input_scale)
    padded_values, packing = _pad_with_real_candidate(values)
    inputs = root / "plaintext_inputs"
    write_values(inputs / "raw_values.csv", values)
    write_values(inputs / "padded_values.csv", padded_values)
    python_started = time.perf_counter()
    python_min, python_max = min(values), max(values)
    python_seconds = time.perf_counter() - python_started
    work = root / "runner"
    work.mkdir()
    (work / "minmax_runner.cpp").write_text(RUNNER, encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE.replace("comparison_runner", "minmax_runner"), encoding="utf-8")
    build = work / "build"
    configure_seconds, _ = run(["cmake", "-S", str(work), "-B", str(build), f"-DOpenFHE_DIR={args.openfhe_dir}"], work)
    build_seconds, _ = run(["cmake", "--build", str(build), "--target", "minmax_runner"], work)
    ciphertexts = root / "ciphertexts"
    ciphertexts.mkdir()
    metrics_path = root / "execution.json"
    run([
        str((build / "minmax_runner").resolve()), str((inputs / "padded_values.csv").resolve()), str(input_scale), str(packing["encrypted_candidate_count"]),
        str((ciphertexts / "encrypted_min.ct").resolve()), str((ciphertexts / "encrypted_max.ct").resolve()), str(metrics_path.resolve()),
    ], work)
    execution = json.loads(metrics_path.read_text(encoding="utf-8"))
    results = [
        {"aggregate": "min", "python": python_min, "he": execution["min_normalized"] * input_scale,
         "absolute_error": abs(python_min - execution["min_normalized"] * input_scale), "status": "encrypted CKKS↔FHEW reduction"},
        {"aggregate": "max", "python": python_max, "he": execution["max_normalized"] * input_scale,
         "absolute_error": abs(python_max - execution["max_normalized"] * input_scale), "status": "encrypted CKKS↔FHEW reduction"},
    ]
    write_csv(root / "minmax_audit.csv", list(results[0]), results)
    result: dict[str, object] = {
        "status": "openfhe_ckks_fhew_minmax_executed",
        "scope": "literal packed encrypted minimum and maximum reductions",
        "input_source": source_info,
        "input_scale_policy": scale_policy,
        "packing": packing,
        "python_seconds": python_seconds,
        "build_seconds": {"configure": configure_seconds, "build": build_seconds},
        "execution": execution,
        "results": results,
        "ciphertext_artifacts": [str(item) for item in sorted(ciphertexts.glob("*.ct"))],
    }
    write_json(root / "result.json", result)
    (root / "REPORT.md").write_text(_report(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "results": results, "output_dir": str(root)}, indent=2))


if __name__ == "__main__":
    main()
