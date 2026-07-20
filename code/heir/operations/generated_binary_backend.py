"""Small OpenFHE harness for one HEIR-generated binary column operation.

The runner intentionally creates a fresh context per benchmark: it is a
standalone operation benchmark, not the retired multi-stage DAG.  It writes
the decrypted audit vector only to the local run directory.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from code.heir.common import sha256_file


CMAKE = r'''cmake_minimum_required(VERSION 3.16)
project(generic_binary_ckks LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(binary_runner heir_output.cpp binary_runner.cpp)
target_include_directories(binary_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke")
target_link_directories(binary_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(binary_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(binary_runner PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


RUNNER = r'''
#include <chrono>
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
  if (argc != 5) return 2;
  try {
    const auto left = readVector(argv[1]);
    const auto right = readVector(argv[2]);
    if (left.size() != @VECTOR_SIZE@ || right.size() != @VECTOR_SIZE@)
      throw std::runtime_error("inputs must be exactly the generated vector size");
    auto context = @ENTRY@__generate_crypto_context();
    auto keyPair = context->KeyGen();
    if (!keyPair.good()) throw std::runtime_error("OpenFHE key generation failed");
    context = @ENTRY@__configure_crypto_context(context, keyPair.secretKey);
    const auto encryptionStarted = std::chrono::steady_clock::now();
    auto encryptedLeft = @ENTRY@__encrypt__arg0(context, left, keyPair.publicKey);
    auto encryptedRight = @ENTRY@__encrypt__arg1(context, right, keyPair.publicKey);
    const auto evaluationStarted = std::chrono::steady_clock::now();
    auto encryptedResult = @ENTRY@(context, encryptedLeft, encryptedRight);
    const auto decryptionStarted = std::chrono::steady_clock::now();
    auto result = @ENTRY@__decrypt__result0(context, encryptedResult, keyPair.secretKey);
    const auto completed = std::chrono::steady_clock::now();
    std::ofstream output(argv[4]);
    output << "value\n" << std::setprecision(17);
    for (const auto& item : result) output << item << '\n';
    std::ofstream metrics(argv[3]);
    metrics << "{\n"
            << "  \"backend\": \"heir_generated_ckks_openfhe\",\n"
            << "  \"scheme\": \"CKKS\",\n"
            << "  \"encryption_seconds\": " << std::chrono::duration<double>(evaluationStarted - encryptionStarted).count() << ",\n"
            << "  \"encrypted_evaluation_seconds\": " << std::chrono::duration<double>(decryptionStarted - evaluationStarted).count() << ",\n"
            << "  \"decryption_seconds\": " << std::chrono::duration<double>(completed - decryptionStarted).count() << "\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "binary_runner failed: " << error.what() << '\n';
    return 1;
  }
}
'''


def _run(command: list[str], cwd: Path) -> tuple[float, str]:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    output = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{output}")
    return time.perf_counter() - started, output


def _validate(generated_dir: Path, operation: str) -> dict[str, Any]:
    entry = f"encrypted_{operation}"
    cpp, header = generated_dir / "heir_output.cpp", generated_dir / "heir_output.h"
    if not cpp.is_file() or not header.is_file():
        raise FileNotFoundError(f"generated HEIR sources missing under {generated_dir}")
    text = cpp.read_text(encoding="utf-8", errors="replace") + header.read_text(encoding="utf-8", errors="replace")
    required = (entry, f"{entry}__generate_crypto_context", f"{entry}__configure_crypto_context", f"{entry}__encrypt__arg0", f"{entry}__encrypt__arg1", f"{entry}__decrypt__result0")
    missing = [symbol for symbol in required if symbol not in text]
    if missing or ("CKKS" not in text and "CryptoContextCKKSRNS" not in text):
        raise ValueError(f"generated source is not the expected CKKS binary operation; missing={missing}")
    return {"entry_function": entry, "cpp_sha256": sha256_file(cpp), "header_sha256": sha256_file(header), "required_symbols": list(required)}


def run_generated_binary(
    *, run_dir: Path, generated_dir: Path, operation: str, vector_size: int,
    left_path: Path, right_path: Path, openfhe_dir: str = "",
) -> dict[str, Any]:
    """Build then execute a standalone HEIR/OpenFHE encrypted operation."""
    proof = _validate(generated_dir, operation)
    work = (run_dir / "heir_generated_ckks").resolve()
    work.mkdir(parents=True, exist_ok=False)
    for name in ("heir_output.cpp", "heir_output.h"):
        shutil.copy2(generated_dir / name, work / name)
    entry = f"encrypted_{operation}"
    (work / "binary_runner.cpp").write_text(RUNNER.replace("@ENTRY@", entry).replace("@VECTOR_SIZE@", str(vector_size)), encoding="utf-8")
    (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
    build = work / "build"
    configure = ["cmake", "-S", str(work), "-B", str(build)]
    if openfhe_dir:
        configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    configure_seconds, configure_log = _run(configure, work)
    build_seconds, build_log = _run(["cmake", "--build", str(build), "--target", "binary_runner"], work)
    (work / "build.log").write_text(configure_log + build_log, encoding="utf-8")
    metrics_path, output_path = run_dir / "heir_metrics.json", run_dir / "heir_decrypted.csv"
    runner_seconds, runner_log = _run([str(build / "binary_runner"), str(left_path.resolve()), str(right_path.resolve()), str(metrics_path), str(output_path)], work)
    (work / "runner.log").write_text(runner_log, encoding="utf-8")
    result = json.loads(metrics_path.read_text(encoding="utf-8"))
    result.update({"generated_proof": proof, "build_timings_seconds": {"cmake_configure_seconds": configure_seconds, "build_seconds": build_seconds}, "runner_wall_seconds": runner_seconds, "decrypted_audit_path": str(output_path)})
    return result
