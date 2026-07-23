"""Python orchestration for the source-built OpenFHE CKKS/FHEW MAX lane.

This module deliberately does not import the optional ``openfhe`` Python
package.  It builds a small C++ runner against the OpenFHE CMake installation
already present on the server.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Sequence

from code.heir.common import write_values
from code.heir.scripts.run_payment_features_ciphertext_demo import run


CMAKE = r"""cmake_minimum_required(VERSION 3.16)
project(source_built_openfhe_column_max LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(column_max_runner column_max_runner.cpp)
target_include_directories(column_max_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke"
  "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(column_max_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(column_max_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(column_max_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
"""


RUNNER = r"""
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

void require(bool value, const std::string& message) {
  if (!value) throw std::runtime_error(message);
}

std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path.string());
  std::string line;
  std::getline(input, line);
  std::vector<double> values;
  while (std::getline(input, line)) {
    if (!line.empty()) values.push_back(std::stod(line));
  }
  return values;
}

int main(int argc, char** argv) {
  // left.csv right.csv scale candidates left.ct right.ct derived.ct max.ct
  // result.json ring-dimension
  if (argc != 11) return 2;
  try {
    const double inputScale = std::stod(argv[3]);
    const uint32_t candidates = static_cast<uint32_t>(std::stoul(argv[4]));
    const uint32_t ringDimension = static_cast<uint32_t>(std::stoul(argv[10]));
    require(inputScale > 0, "input scale must be positive");
    require(candidates >= 2 && (candidates & (candidates - 1)) == 0,
            "candidate count must be a power of two");
    require(ringDimension >= 2 * candidates &&
            (ringDimension & (ringDimension - 1)) == 0,
            "ring dimension must be a power of two and fit all candidates");

    auto leftRaw = readVector(argv[1]);
    auto rightRaw = readVector(argv[2]);
    require(leftRaw.size() == candidates && rightRaw.size() == candidates,
            "padded parent sizes do not match candidate count");
    std::vector<double> left, right;
    left.reserve(candidates);
    right.reserve(candidates);
    for (uint32_t index = 0; index < candidates; ++index) {
      left.push_back(leftRaw[index] / inputScale);
      right.push_back(rightRaw[index] / inputScale);
      require(std::abs(left.back() - right.back()) < 0.5,
              "derived subtraction violates normalized MAX range");
    }

    const uint32_t depth =
        13 + static_cast<uint32_t>(std::log2(candidates));
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetMultiplicativeDepth(depth);
    parameters.SetFirstModSize(60);
    parameters.SetScalingModSize(50);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(ringDimension);
    parameters.SetBatchSize(candidates);
    parameters.SetSecretKeyDist(UNIFORM_TERNARY);
    parameters.SetKeySwitchTechnique(HYBRID);
    parameters.SetNumLargeDigits(3);
    auto context = GenCryptoContext(parameters);
    context->Enable(PKE);
    context->Enable(KEYSWITCH);
    context->Enable(LEVELEDSHE);
    context->Enable(ADVANCEDSHE);
    context->Enable(SCHEMESWITCH);
    context->Enable(FHE);
    auto keys = context->KeyGen();
    require(keys.good(), "CKKS key generation failed");

    SchSwchParams switching;
    switching.SetSecurityLevelCKKS(HEStd_NotSet);
    switching.SetSecurityLevelFHEW(TOY);
    switching.SetCtxtModSizeFHEWLargePrec(25);
    switching.SetNumSlotsCKKS(candidates);
    switching.SetNumValues(candidates);
    // OpenFHE also generates comparison-tree rotations on this route.  The
    // returned argmax remains unused and is never serialized.
    switching.SetComputeArgmin(true);
    auto lweSecretKey = context->EvalSchemeSwitchingSetup(switching);
    context->EvalSchemeSwitchingKeyGen(keys, lweSecretKey);
    context->EvalCompareSwitchPrecompute(1, 1, true);

    auto leftCt = context->Encrypt(
        keys.publicKey, context->MakeCKKSPackedPlaintext(left));
    auto rightCt = context->Encrypt(
        keys.publicKey, context->MakeCKKSPackedPlaintext(right));
    auto derivedCt = context->EvalSub(leftCt, rightCt);
    auto maxAndArgmax = context->EvalMaxSchemeSwitching(
        derivedCt, keys.publicKey, candidates, candidates);
    require(!maxAndArgmax.empty(), "encrypted MAX result is missing");

    require(Serial::SerializeToFile(argv[5], leftCt, SerType::BINARY),
            "cannot save encrypted left parent");
    require(Serial::SerializeToFile(argv[6], rightCt, SerType::BINARY),
            "cannot save encrypted right parent");
    require(Serial::SerializeToFile(argv[7], derivedCt, SerType::BINARY),
            "cannot save encrypted derived column");
    require(Serial::SerializeToFile(argv[8], maxAndArgmax[0], SerType::BINARY),
            "cannot save encrypted MAX");

    // The runner reaches the final client audit boundary only after max.ct
    // exists. No decrypted value is consumed by another encrypted operation.
    Plaintext audit;
    context->Decrypt(keys.secretKey, maxAndArgmax[0], &audit);
    audit->SetLength(1);
    std::ofstream result(argv[9]);
    result << std::setprecision(17)
           << "{\"maximum_normalized\":"
           << audit->GetRealPackedValue().at(0)
           << ",\"candidate_count\":" << candidates
           << ",\"ring_dimension\":" << context->GetRingDimension()
           << ",\"multiplicative_depth\":" << depth
           << ",\"argmax_retained\":false}\n";
    return 0;
  } catch (const OpenFHEException& error) {
    std::cerr << "OpenFHE source-built MAX error: " << error.what() << '\n';
    return 1;
  } catch (const std::exception& error) {
    std::cerr << "source-built MAX error: " << error.what() << '\n';
    return 1;
  }
}
"""


def _next_power_of_two(value: int) -> int:
    if value < 1:
        raise ValueError("at least one value is required")
    return max(2, 1 << (value - 1).bit_length())


@dataclass(frozen=True)
class SourceBuiltOpenFheColumnMax:
    """Run encrypted binary subtraction followed by exact MAX.

    Python writes only the two parent columns. ``PAYMENT_DIFF`` (or any other
    subtraction feature) is derived after both parents have been encrypted.
    """

    input_scale: float
    ring_dimension: int = 16384
    openfhe_dir: str = "/usr/local/lib/OpenFHE"

    def run_subtract_max(
        self,
        left: Sequence[float],
        right: Sequence[float],
        *,
        output_dir: Path,
        overwrite: bool = False,
    ) -> dict[str, object]:
        left_values = [float(value) for value in left]
        right_values = [float(value) for value in right]
        if not left_values or len(left_values) != len(right_values):
            raise ValueError("left and right must have the same non-zero length")
        if self.input_scale <= 0:
            raise ValueError("input_scale must be positive")
        candidates = _next_power_of_two(len(left_values))
        if self.ring_dimension < 2 * candidates:
            raise ValueError("ring_dimension cannot hold the padded candidates")

        root = output_dir.resolve()
        if root.exists():
            if not overwrite:
                raise FileExistsError(
                    f"refusing to overwrite source-built MAX output: {root}"
                )
            shutil.rmtree(root)
        inputs = root / "client_private"
        work = root / "runner"
        ciphertexts = root / "ciphertexts"
        inputs.mkdir(parents=True)
        work.mkdir()
        ciphertexts.mkdir()

        # Duplicate a genuine row to reach the comparison tree's power-of-two
        # size. Repetition cannot change the maximum.
        padding = candidates - len(left_values)
        left_values.extend([left_values[0]] * padding)
        right_values.extend([right_values[0]] * padding)
        left_path = inputs / "left_parent.csv"
        right_path = inputs / "right_parent.csv"
        write_values(left_path, left_values)
        write_values(right_path, right_values)

        (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        (work / "column_max_runner.cpp").write_text(RUNNER, encoding="utf-8")
        build = work / "build"
        run(
            [
                "cmake",
                "-S",
                str(work),
                "-B",
                str(build),
                f"-DOpenFHE_DIR={self.openfhe_dir}",
            ],
            work,
        )
        run(
            [
                "cmake",
                "--build",
                str(build),
                "--target",
                "column_max_runner",
            ],
            work,
        )
        result_path = inputs / "maximum_audit.json"
        run(
            [
                str((build / "column_max_runner").resolve()),
                str(left_path),
                str(right_path),
                str(self.input_scale),
                str(candidates),
                str(ciphertexts / "left_parent.ct"),
                str(ciphertexts / "right_parent.ct"),
                str(ciphertexts / "derived_subtraction.ct"),
                str(ciphertexts / "maximum.ct"),
                str(result_path),
                str(self.ring_dimension),
            ],
            work,
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result.update(
            {
                "maximum": float(result["maximum_normalized"])
                * self.input_scale,
                "real_count": len(left),
                "padding_count": padding,
                "backend": "source-built OpenFHE via CMake",
                "openfhe_dir": self.openfhe_dir,
            }
        )
        return result
