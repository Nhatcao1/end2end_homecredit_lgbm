"""OpenFHE persistence harnesses around HEIR-generated CKKS C++ sources."""

from __future__ import annotations

import json
import resource
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from code.heir.common import sha256_file, write_json


CMAKE_TEMPLATE = r'''cmake_minimum_required(VERSION 3.16)
project(@PROJECT@ LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(@TARGET@ heir_output.cpp @RUNNER@)
target_include_directories(@TARGET@ PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke"
  "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(@TARGET@ PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(@TARGET@ PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(@TARGET@ PROPERTIES BUILD_RPATH "${OpenFHE_LIBDIR}")
'''


SESSION_RUNNER = r'''
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include "heir_output.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

void require(bool value, const std::string& message) {
  if (!value) throw std::runtime_error(message);
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  try {
    const std::filesystem::path root(argv[1]);
    const auto publicDir = root / "public";
    const auto privateDir = root / "client_private";
    std::filesystem::create_directories(publicDir);
    std::filesystem::create_directories(privateDir);

    auto context = @ENTRY@__generate_crypto_context();
    auto keyPair = context->KeyGen();
    require(keyPair.good(), "OpenFHE key generation failed");
    context = @ENTRY@__configure_crypto_context(context, keyPair.secretKey);

    require(Serial::SerializeToFile(
        (publicDir / "crypto_context.bin").string(), context, SerType::BINARY),
        "failed to serialize crypto context");
    require(Serial::SerializeToFile(
        (publicDir / "public_key.bin").string(), keyPair.publicKey, SerType::BINARY),
        "failed to serialize public key");
    require(Serial::SerializeToFile(
        (privateDir / "secret_key.bin").string(), keyPair.secretKey, SerType::BINARY),
        "failed to serialize private key");

    std::ofstream mult(publicDir / "evaluation_mult_keys.bin",
                       std::ios::out | std::ios::binary);
    require(mult.is_open(), "failed to open multiplication-key output");
    require(context->SerializeEvalMultKey(mult, SerType::BINARY),
            "failed to serialize multiplication keys");
    mult.close();

    std::ofstream rotations(publicDir / "evaluation_rotation_keys.bin",
                            std::ios::out | std::ios::binary);
    require(rotations.is_open(), "failed to open rotation-key output");
    require(context->SerializeEvalAutomorphismKey(rotations, SerType::BINARY),
            "failed to serialize rotation keys");
    rotations.close();

    std::cout << "CKKS session serialized\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "session initializer failed: " << error.what() << '\n';
    return 1;
  }
}
'''


EVALUATION_RUNNER = r'''
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>
#include "heir_output.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
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

std::vector<double> applicantVector(
    const std::vector<double>& values, std::size_t app, std::size_t appCount,
    std::size_t width, std::size_t vectorSize) {
  const bool shared = values.size() == width;
  require(shared || values.size() == appCount * width,
          "input tensor is neither shared nor applicant-major");
  std::vector<double> result(vectorSize, 0.0);
  for (std::size_t i = 0; i < width; ++i)
    result[i] = values[(shared ? 0 : app * width) + i];
  return result;
}

struct Writer {
  std::filesystem::path outputDir;
  std::ofstream index;
  std::size_t app = 0;
  std::size_t ordinal = 0;

  Writer(const std::filesystem::path& directory)
      : outputDir(directory) {
    std::filesystem::create_directories(outputDir);
    index.open(directory / "ciphertext_index.csv");
    require(index.is_open(), "cannot create ciphertext index");
    index << "app_index,result_ordinal,file,level,scaling_factor\n";
    index << std::setprecision(17);
  }

  void ciphertext(const CiphertextT& value) {
    require(static_cast<bool>(value), "generated function returned null ciphertext");
    const auto name = "app_" + std::to_string(app) + "_result_" +
                      std::to_string(ordinal) + ".ct";
    require(Serial::SerializeToFile(
        (outputDir / name).string(), value, SerType::BINARY),
        "failed to serialize evaluated ciphertext");
    index << app << ',' << ordinal << ',' << name << ',' << value->GetLevel()
          << ',' << value->GetScalingFactor() << '\n';
    ++ordinal;
  }
};

void serializeValue(Writer& writer, const CiphertextT& value) {
  writer.ciphertext(value);
}

template <typename Value>
void serializeValue(Writer& writer, const std::vector<Value>& values) {
  for (const auto& value : values) serializeValue(writer, value);
}

template <typename... Values>
void serializeValue(Writer& writer, const std::tuple<Values...>& values) {
  std::apply([&](const auto&... value) { (serializeValue(writer, value), ...); },
             values);
}

int main(int argc, char** argv) {
  if (argc != @ARGC@) return 2;
  try {
    const std::filesystem::path session(argv[1]);
    const std::size_t vectorSize = std::stoull(argv[2]);
    const std::size_t appCount = std::stoull(argv[3]);
    const std::size_t width = std::stoull(argv[4]);
    require(width > 0 && width <= vectorSize,
            "slots per applicant exceed generated HEIR vector size");

    CryptoContextT context;
    require(Serial::DeserializeFromFile(
        (session / "public" / "crypto_context.bin").string(), context,
        SerType::BINARY), "cannot deserialize crypto context");
    PublicKeyT publicKey;
    require(Serial::DeserializeFromFile(
        (session / "public" / "public_key.bin").string(), publicKey,
        SerType::BINARY), "cannot deserialize public key");
    std::ifstream mult(session / "public" / "evaluation_mult_keys.bin",
                       std::ios::in | std::ios::binary);
    require(mult.is_open() && context->DeserializeEvalMultKey(mult, SerType::BINARY),
            "cannot deserialize multiplication keys");
    std::ifstream rotations(session / "public" / "evaluation_rotation_keys.bin",
                            std::ios::in | std::ios::binary);
    require(rotations.is_open() &&
                context->DeserializeEvalAutomorphismKey(rotations, SerType::BINARY),
            "cannot deserialize rotation keys");

    @READ_INPUTS@
    Writer writer(argv[@OUTPUT_ARG@]);
    double encryptSeconds = 0.0;
    double evaluationSeconds = 0.0;
    double serializationSeconds = 0.0;
    const auto allStarted = std::chrono::steady_clock::now();
    for (std::size_t app = 0; app < appCount; ++app) {
      @PREPARE_INPUTS@
      const auto encryptStarted = std::chrono::steady_clock::now();
      @ENCRYPT_INPUTS@
      const auto evaluationStarted = std::chrono::steady_clock::now();
      auto encryptedResult = @ENTRY@(context, @ENCRYPTED_ARGUMENTS@);
      const auto serializationStarted = std::chrono::steady_clock::now();
      writer.app = app;
      writer.ordinal = 0;
      serializeValue(writer, encryptedResult);
      const auto finished = std::chrono::steady_clock::now();
      encryptSeconds += std::chrono::duration<double>(
          evaluationStarted - encryptStarted).count();
      evaluationSeconds += std::chrono::duration<double>(
          serializationStarted - evaluationStarted).count();
      serializationSeconds += std::chrono::duration<double>(
          finished - serializationStarted).count();
    }
    const auto allFinished = std::chrono::steady_clock::now();
    std::ofstream metrics(std::filesystem::path(argv[@OUTPUT_ARG@]) /
                          "runner_metrics.json");
    metrics << "{\n"
            << "  \"encryption_seconds\": " << encryptSeconds << ",\n"
            << "  \"encrypted_evaluation_seconds\": " << evaluationSeconds << ",\n"
            << "  \"ciphertext_serialization_seconds\": " << serializationSeconds << ",\n"
            << "  \"runner_wall_seconds\": "
            << std::chrono::duration<double>(allFinished - allStarted).count()
            << "\n}\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "persistent CKKS runner failed: " << error.what() << '\n';
    return 1;
  }
}
'''


CONTINUITY_RUNNER = r'''
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include "openfhe.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
using namespace lbcrypto;

int main(int argc, char** argv) {
  if (argc != 4) return 2;
  try {
    CryptoContext<DCRTPoly> context;
    if (!Serial::DeserializeFromFile(argv[1], context, SerType::BINARY))
      throw std::runtime_error("cannot deserialize context");
    Ciphertext<DCRTPoly> ciphertext;
    if (!Serial::DeserializeFromFile(argv[2], ciphertext, SerType::BINARY))
      throw std::runtime_error("cannot deserialize ciphertext");
    if (!ciphertext) throw std::runtime_error("deserialized ciphertext is null");
    if (!Serial::SerializeToFile(argv[3], ciphertext, SerType::BINARY))
      throw std::runtime_error("cannot reserialize ciphertext");
    std::cout << ciphertext->GetLevel() << " "
              << ciphertext->GetScalingFactor() << "\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "continuity probe failed: " << error.what() << '\n';
    return 1;
  }
}
'''


KERNELS = {
    "K01": {"entry": "dot_product", "arity": 2},
    "K02": {"entry": "moments", "arity": 2},
    "K03": {"entry": "difference_moments", "arity": 3},
}


def _run(command: list[str], cwd: Path) -> tuple[float, str]:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    output = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError(f"command failed: {' '.join(command)}\n{output}")
    return time.perf_counter() - started, output


def _generated_proof(generated_dir: Path, entry: str) -> dict[str, Any]:
    cpp = generated_dir / "heir_output.cpp"
    header = generated_dir / "heir_output.h"
    if not cpp.is_file() or not header.is_file():
        raise FileNotFoundError(f"missing HEIR-generated source under {generated_dir}")
    text = cpp.read_text(encoding="utf-8", errors="replace") + header.read_text(
        encoding="utf-8", errors="replace"
    )
    if "CKKS" not in text and "CryptoContextCKKSRNS" not in text:
        raise ValueError(f"{generated_dir} is not identifiable as CKKS output")
    forbidden = ("CryptoContextBGVRNS", "CryptoContextBFVRNS", "BinFHEContext")
    found = [name for name in forbidden if name in text]
    if found:
        raise ValueError(f"{generated_dir} contains non-CKKS backends: {found}")
    required = (
        entry,
        f"{entry}__generate_crypto_context",
        f"{entry}__configure_crypto_context",
        f"{entry}__encrypt__arg0",
    )
    missing = [name for name in required if name not in text]
    if missing:
        raise ValueError(f"{generated_dir} is missing generated symbols: {missing}")
    return {
        "entry_function": entry,
        "generated_cpp_sha256": sha256_file(cpp),
        "generated_header_sha256": sha256_file(header),
        "ckks_only": True,
    }


def _cmake(project: str, target: str, runner: str) -> str:
    return (
        CMAKE_TEMPLATE.replace("@PROJECT@", project)
        .replace("@TARGET@", target)
        .replace("@RUNNER@", runner)
    )


def _configure_and_build(
    work_dir: Path, target: str, openfhe_dir: str
) -> dict[str, float]:
    build_dir = work_dir / "build"
    configure = ["cmake", "-S", str(work_dir), "-B", str(build_dir)]
    if openfhe_dir:
        configure.append(f"-DOpenFHE_DIR={openfhe_dir}")
    configure_seconds, configure_log = _run(configure, work_dir)
    build_seconds, build_log = _run(
        ["cmake", "--build", str(build_dir), "--target", target], work_dir
    )
    (work_dir / "build.log").write_text(
        configure_log + build_log, encoding="utf-8"
    )
    return {
        "cmake_configure_seconds": configure_seconds,
        "build_seconds": build_seconds,
    }


def _copy_generated(generated_dir: Path, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in ("heir_output.cpp", "heir_output.h"):
        shutil.copy2(generated_dir / name, work_dir / name)


class GeneratedCkksBackend:
    """Compile, execute, serialize, and reload HEIR-generated CKKS results."""

    def __init__(
        self, generated_root: Path, build_root: Path, openfhe_dir: str = ""
    ) -> None:
        self.generated_root = generated_root
        self.build_root = build_root
        self.openfhe_dir = openfhe_dir

    def generated_dir(self, kernel_id: str) -> Path:
        return self.generated_root / kernel_id

    def initialize_session(
        self, session_dir: Path, provider_kernel: str = "K03"
    ) -> dict[str, Any]:
        info = KERNELS[provider_kernel]
        generated = self.generated_dir(provider_kernel)
        proof = _generated_proof(generated, info["entry"])
        work = self.build_root / "session_initializer"
        if not (work / "build" / "session_initializer").is_file():
            if work.exists():
                raise FileExistsError(
                    f"incomplete session build cache exists; inspect {work}"
                )
            _copy_generated(generated, work)
            (work / "session_initializer.cpp").write_text(
                SESSION_RUNNER.replace("@ENTRY@", info["entry"]), encoding="utf-8"
            )
            (work / "CMakeLists.txt").write_text(
                _cmake("heir_dag_session", "session_initializer", "session_initializer.cpp"),
                encoding="utf-8",
            )
            build_timings = _configure_and_build(
                work, "session_initializer", self.openfhe_dir
            )
            write_json(work / "generated_proof.json", proof)
        else:
            built_proof = json.loads(
                (work / "generated_proof.json").read_text(encoding="utf-8")
            )
            if built_proof != proof:
                raise ValueError("cached session initializer source hash changed")
            build_timings = {
                "cmake_configure_seconds": 0.0,
                "build_seconds": 0.0,
            }
        run_seconds, log = _run(
            [str(work / "build" / "session_initializer"), str(session_dir)], work
        )
        (session_dir / "session_initializer.log").write_text(log, encoding="utf-8")
        return {
            "provider_kernel": provider_kernel,
            "generated_proof": proof,
            "timings_seconds": {**build_timings, "initializer_seconds": run_seconds},
        }

    def _evaluation_source(self, kernel_id: str) -> str:
        info = KERNELS[kernel_id]
        arity = int(info["arity"])
        read_inputs = "\n    ".join(
            f"const auto raw{i} = readVector(argv[{5 + i}]);" for i in range(arity)
        )
        prepare_inputs = "\n      ".join(
            f"const auto input{i} = applicantVector(raw{i}, app, appCount, width, vectorSize);"
            for i in range(arity)
        )
        encrypt_inputs = "\n      ".join(
            f"auto encrypted{i} = {info['entry']}__encrypt__arg{i}(context, input{i}, publicKey);"
            for i in range(arity)
        )
        arguments = ", ".join(f"encrypted{i}" for i in range(arity))
        output_arg = 5 + arity
        return (
            EVALUATION_RUNNER.replace("@ENTRY@", info["entry"])
            .replace("@ARGC@", str(output_arg + 1))
            .replace("@READ_INPUTS@", read_inputs)
            .replace("@PREPARE_INPUTS@", prepare_inputs)
            .replace("@ENCRYPT_INPUTS@", encrypt_inputs)
            .replace("@ENCRYPTED_ARGUMENTS@", arguments)
            .replace("@OUTPUT_ARG@", str(output_arg))
        )

    def _build_evaluator(self, kernel_id: str) -> tuple[Path, dict[str, Any]]:
        info = KERNELS[kernel_id]
        generated = self.generated_dir(kernel_id)
        proof = _generated_proof(generated, info["entry"])
        work = self.build_root / f"evaluate_{kernel_id.lower()}"
        target = f"evaluate_{kernel_id.lower()}"
        executable = work / "build" / target
        if not executable.is_file():
            if work.exists():
                raise FileExistsError(
                    f"incomplete evaluator build cache exists; inspect {work}"
                )
            _copy_generated(generated, work)
            runner_name = f"{target}.cpp"
            (work / runner_name).write_text(
                self._evaluation_source(kernel_id), encoding="utf-8"
            )
            (work / "CMakeLists.txt").write_text(
                _cmake(f"heir_dag_{kernel_id.lower()}", target, runner_name),
                encoding="utf-8",
            )
            build_timings = _configure_and_build(work, target, self.openfhe_dir)
            write_json(work / "build_timings.json", build_timings)
            write_json(work / "generated_proof.json", proof)
        else:
            built_proof = json.loads(
                (work / "generated_proof.json").read_text(encoding="utf-8")
            )
            if built_proof != proof:
                raise ValueError(f"cached {kernel_id} evaluator source hash changed")
        return executable, proof

    def execute_job(
        self,
        *,
        kernel_id: str,
        session_dir: Path,
        vector_size: int,
        applicant_count: int,
        width: int,
        input_paths: list[Path],
        output_dir: Path,
    ) -> dict[str, Any]:
        expected_arity = int(KERNELS[kernel_id]["arity"])
        if len(input_paths) != expected_arity:
            raise ValueError(
                f"{kernel_id} expects {expected_arity} inputs, got {len(input_paths)}"
            )
        executable, proof = self._build_evaluator(kernel_id)
        before_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        wall_seconds, log = _run(
            [
                str(executable),
                str(session_dir),
                str(vector_size),
                str(applicant_count),
                str(width),
                *(str(path) for path in input_paths),
                str(output_dir),
            ],
            executable.parent,
        )
        after_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        (output_dir / "runner.log").write_text(log, encoding="utf-8")
        metrics = json.loads(
            (output_dir / "runner_metrics.json").read_text(encoding="utf-8")
        )
        index = output_dir / "ciphertext_index.csv"
        if not index.is_file():
            raise RuntimeError(f"generated runner produced no ciphertext index: {index}")
        ciphertexts = sorted(output_dir.glob("*.ct"))
        if not ciphertexts:
            raise RuntimeError(f"generated runner produced no ciphertexts: {output_dir}")
        return {
            "kernel_id": kernel_id,
            "generated_proof": proof,
            "ciphertext_index": str(index),
            "ciphertext_files": [str(path) for path in ciphertexts],
            "timings_seconds": {**metrics, "subprocess_wall_seconds": wall_seconds},
            "peak_child_rss_kib": max(before_rss, after_rss),
        }

    def continuity_probe(
        self, session_dir: Path, ciphertext: Path, output_path: Path
    ) -> dict[str, Any]:
        work = self.build_root / "continuity_probe"
        executable = work / "build" / "continuity_probe"
        if not executable.is_file():
            if work.exists():
                raise FileExistsError(
                    f"incomplete continuity build cache exists; inspect {work}"
                )
            work.mkdir(parents=True)
            (work / "continuity_probe.cpp").write_text(
                CONTINUITY_RUNNER, encoding="utf-8"
            )
            cmake = CMAKE_TEMPLATE.replace("@PROJECT@", "heir_dag_continuity")
            cmake = cmake.replace("@TARGET@", "continuity_probe")
            cmake = cmake.replace("heir_output.cpp @RUNNER@", "@RUNNER@")
            cmake = cmake.replace("@RUNNER@", "continuity_probe.cpp")
            (work / "CMakeLists.txt").write_text(cmake, encoding="utf-8")
            _configure_and_build(work, "continuity_probe", self.openfhe_dir)
        seconds, output = _run(
            [
                str(executable),
                str(session_dir / "public" / "crypto_context.bin"),
                str(ciphertext),
                str(output_path),
            ],
            work,
        )
        parts = output.strip().split()
        return {
            "status": "ciphertext_deserialized_and_reserialized",
            "input_file": str(ciphertext),
            "output_file": str(output_path),
            "level": int(parts[0]) if parts else None,
            "scaling_factor": float(parts[1]) if len(parts) > 1 else None,
            "seconds": seconds,
            "output_sha256": sha256_file(output_path),
        }
