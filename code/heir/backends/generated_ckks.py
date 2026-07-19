"""Strict runner for HEIR-generated CKKS/OpenFHE dot-product source."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from code.heir.common import sha256_file
from code.heir.kernels.dot_product import dot_product_mlir


CMAKE_TEMPLATE = r'''cmake_minimum_required(VERSION 3.16)
project(pos_count_heir_ckks LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(pos_count_runner heir_output.cpp pos_count_runner.cpp)
target_include_directories(pos_count_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke" "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(pos_count_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(pos_count_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(pos_count_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER_TEMPLATE = r'''
#include <chrono>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "heir_output.h"

std::vector<double> readVector(const std::filesystem::path& path) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("cannot open " + path.string());
  std::string line;
  std::getline(input, line);
  std::vector<double> values;
  while (std::getline(input, line)) if (!line.empty()) values.push_back(std::stod(line));
  return values;
}

int main(int argc, char** argv) {
  if (argc != 7) return 2;
  try {
    const auto masks = readVector(argv[1]);
    const auto weights = readVector(argv[2]);
    const std::size_t appCount = std::stoull(argv[3]);
    const std::size_t slots = std::stoull(argv[4]);
    if (masks.size() != appCount * slots || weights.size() != slots)
      throw std::runtime_error("tensor shape mismatch");

    auto context = dot_product__generate_crypto_context();
    auto keyPair = context->KeyGen();
    context = dot_product__configure_crypto_context(context, keyPair.secretKey);
    double encryptSeconds = 0, computeSeconds = 0, decryptSeconds = 0;
    std::ofstream csv(argv[6]);
    csv << "app_index,POS_COUNT\n" << std::setprecision(17);
    const auto allStarted = std::chrono::steady_clock::now();
    for (std::size_t app = 0; app < appCount; ++app) {
      double count = 0;
      for (std::size_t offset = 0; offset < slots; offset += @VECTOR_SIZE@) {
        std::vector<double> left(@VECTOR_SIZE@, 0), right(@VECTOR_SIZE@, 0);
        for (std::size_t i = 0; i < @VECTOR_SIZE@ && offset + i < slots; ++i) {
          left[i] = masks[app * slots + offset + i];
          right[i] = weights[offset + i];
        }
        auto t0 = std::chrono::steady_clock::now();
        auto encLeft = dot_product__encrypt__arg0(context, left, keyPair.publicKey);
        auto encRight = dot_product__encrypt__arg1(context, right, keyPair.publicKey);
        auto t1 = std::chrono::steady_clock::now();
        auto encResult = dot_product(context, encLeft, encRight);
        auto t2 = std::chrono::steady_clock::now();
        count += dot_product__decrypt__result0(context, encResult, keyPair.secretKey);
        auto t3 = std::chrono::steady_clock::now();
        encryptSeconds += std::chrono::duration<double>(t1 - t0).count();
        computeSeconds += std::chrono::duration<double>(t2 - t1).count();
        decryptSeconds += std::chrono::duration<double>(t3 - t2).count();
      }
      csv << app << ',' << count << '\n';
    }
    const auto allEnded = std::chrono::steady_clock::now();
    std::ofstream result(argv[5]);
    result << "{\n"
           << "  \"backend\": \"heir_generated_ckks_openfhe\",\n"
           << "  \"scheme\": \"CKKS\",\n"
           << "  \"generated_function\": \"dot_product\",\n"
           << "  \"application_count\": " << appCount << ",\n"
           << "  \"slots_per_application\": " << slots << ",\n"
           << "  \"encryption_seconds\": " << encryptSeconds << ",\n"
           << "  \"encrypted_compute_seconds\": " << computeSeconds << ",\n"
           << "  \"decryption_seconds\": " << decryptSeconds << ",\n"
           << "  \"runner_seconds\": "
           << std::chrono::duration<double>(allEnded - allStarted).count() << "\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "pos_count_runner failed: " << error.what() << '\n';
    return 1;
  }
}
'''


def _run(command: list[str], cwd: Path) -> tuple[float, str]:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    output = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{output}")
    return time.perf_counter() - started, output


def _validate_ckks_source(work_dir: Path) -> dict[str, Any]:
    cpp = work_dir / "heir_output.cpp"
    header = work_dir / "heir_output.h"
    text = cpp.read_text(encoding="utf-8", errors="replace") + header.read_text(
        encoding="utf-8", errors="replace"
    )
    if "CKKS" not in text and "CryptoContextCKKSRNS" not in text:
        raise ValueError("generated HEIR source is not identifiable as CKKS")
    forbidden = [name for name in ("CryptoContextBGVRNS", "CryptoContextBFVRNS", "BinFHEContext") if name in text]
    if forbidden:
        raise ValueError(f"generated source is not CKKS-only: {forbidden}")
    required = (
        "dot_product__generate_crypto_context",
        "dot_product__configure_crypto_context",
        "dot_product__encrypt__arg0",
        "dot_product__encrypt__arg1",
        "dot_product__decrypt__result0",
        "dot_product",
    )
    missing = [symbol for symbol in required if symbol not in text]
    if missing:
        raise ValueError(f"generated source is missing symbols: {missing}")
    return {
        "heir_output_cpp": str(cpp),
        "heir_output_h": str(header),
        "heir_output_cpp_sha256": sha256_file(cpp),
        "heir_output_h_sha256": sha256_file(header),
        "required_symbols": list(required),
        "ckks_only": True,
    }


def run_generated_pos_count(
    run_dir: Path,
    generated_dir: Path,
    openfhe_dir: str,
    vector_size: int,
    application_count: int,
    slots_per_application: int,
) -> tuple[dict[str, Any], dict[str, float], str]:
    """Compile and run pre-generated HEIR CKKS source for POS_COUNT."""
    work_dir = (run_dir / "heir_generated_ckks").resolve()
    build_dir = work_dir / "build"
    work_dir.mkdir(parents=True, exist_ok=False)
    for name in ("heir_output.cpp", "heir_output.h"):
        source = generated_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"missing generated HEIR source: {source}")
        shutil.copy2(source, work_dir / name)
    (work_dir / f"dot_product_{vector_size}.mlir").write_text(
        dot_product_mlir(vector_size), encoding="utf-8"
    )
    proof = _validate_ckks_source(work_dir)
    (work_dir / "pos_count_runner.cpp").write_text(
        RUNNER_TEMPLATE.replace("@VECTOR_SIZE@", str(vector_size)), encoding="utf-8"
    )
    (work_dir / "CMakeLists.txt").write_text(CMAKE_TEMPLATE, encoding="utf-8")

    configure = ["cmake", "-S", str(work_dir), "-B", str(build_dir)]
    if openfhe_dir:
        configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    timings: dict[str, float] = {}
    logs: list[str] = []
    timings["cmake_configure_seconds"], output = _run(configure, run_dir)
    logs.append(output)
    timings["build_seconds"], output = _run(
        ["cmake", "--build", str(build_dir), "--target", "pos_count_runner"], run_dir
    )
    logs.append(output)
    result_path = run_dir / "heir_result.json"
    decrypted_path = run_dir / "heir_decrypted.csv"
    timings["runner_wall_seconds"], output = _run(
        [
            str(build_dir / "pos_count_runner"),
            str(run_dir / "tensors" / "history_mask_matrix.csv"),
            str(run_dir / "tensors" / "unit_weights.csv"),
            str(application_count),
            str(slots_per_application),
            str(result_path),
            str(decrypted_path),
        ],
        run_dir,
    )
    logs.append(output)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["heir_proof"] = proof
    return result, timings, "\n".join(logs)
