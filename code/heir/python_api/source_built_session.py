"""File-backed simple HE API for the server's source-built OpenFHE install.

Unlike :mod:`simple_session`, this backend does not import the optional
``openfhe`` Python wheel.  It compiles one small C++ runner against the
server's existing OpenFHE CMake package. Ciphertexts are represented in Python
as typed file handles, so a later Python process can reload the session and
continue evaluating without plaintext parent data.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Literal, Sequence
from uuid import uuid4

from code.heir.common import read_csv, write_values
from code.heir.scripts.run_payment_features_ciphertext_demo import run


CMAKE = r"""cmake_minimum_required(VERSION 3.16)
project(simple_source_built_ckks_session LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenFHE CONFIG REQUIRED)
set(HEIR_FLAGS "${OpenFHE_CXX_FLAGS}")
string(REPLACE "-Werror" "" HEIR_FLAGS "${HEIR_FLAGS}")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${HEIR_FLAGS}")
add_executable(simple_ckks_session_runner simple_ckks_session_runner.cpp)
target_include_directories(simple_ckks_session_runner PRIVATE
  "${OpenFHE_INCLUDE}" "${OpenFHE_INCLUDE}/third-party/include"
  "${OpenFHE_INCLUDE}/core" "${OpenFHE_INCLUDE}/pke"
  "${OpenFHE_INCLUDE}/binfhe")
target_link_directories(simple_ckks_session_runner PRIVATE "${OpenFHE_LIBDIR}")
target_link_libraries(simple_ckks_session_runner PRIVATE ${OpenFHE_SHARED_LIBRARIES})
set_target_properties(simple_ckks_session_runner PROPERTIES
  BUILD_RPATH "${OpenFHE_LIBDIR}")
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
using Context = CryptoContext<DCRTPoly>;
using CiphertextT = Ciphertext<DCRTPoly>;
using PublicKeyT = PublicKey<DCRTPoly>;
using PrivateKeyT = PrivateKey<DCRTPoly>;

void require(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

std::filesystem::path item(const std::filesystem::path& session,
                           const std::string& name) {
  return session / name;
}

std::vector<double> readValues(const std::filesystem::path& path) {
  std::ifstream input(path);
  require(input.good(), "cannot open numeric input " + path.string());
  std::string line;
  std::getline(input, line);
  std::vector<double> result;
  while (std::getline(input, line))
    if (!line.empty()) result.push_back(std::stod(line));
  return result;
}

Context loadContext(const std::filesystem::path& session) {
  Context value;
  require(Serial::DeserializeFromFile(
              item(session, "public/context.bin").string(), value,
              SerType::BINARY),
          "cannot load CKKS context");
  return value;
}

PublicKeyT loadPublicKey(const std::filesystem::path& session) {
  PublicKeyT value;
  require(Serial::DeserializeFromFile(
              item(session, "public/public.key").string(), value,
              SerType::BINARY),
          "cannot load public key");
  return value;
}

PrivateKeyT loadPrivateKey(const std::filesystem::path& session) {
  PrivateKeyT value;
  require(Serial::DeserializeFromFile(
              item(session, "client_private/audit_secret.key").string(), value,
              SerType::BINARY),
          "cannot load client audit secret");
  return value;
}

CiphertextT loadCiphertext(const std::filesystem::path& path) {
  CiphertextT value;
  require(Serial::DeserializeFromFile(path.string(), value, SerType::BINARY),
          "cannot load ciphertext " + path.string());
  return value;
}

void saveCiphertext(const std::filesystem::path& path,
                    const CiphertextT& value) {
  std::filesystem::create_directories(path.parent_path());
  require(Serial::SerializeToFile(path.string(), value, SerType::BINARY),
          "cannot save ciphertext " + path.string());
}

void initialize(const std::filesystem::path& session, uint32_t width,
                uint32_t ringDimension) {
  require(width >= 2 && (width & (width - 1)) == 0,
          "width must be a power of two");
  require(ringDimension >= 2 * width &&
              (ringDimension & (ringDimension - 1)) == 0,
          "ring dimension must be a power of two and fit width");
  const uint32_t depth = 18 + static_cast<uint32_t>(std::log2(width));
  CCParams<CryptoContextCKKSRNS> parameters;
  parameters.SetMultiplicativeDepth(depth);
  parameters.SetFirstModSize(60);
  parameters.SetScalingModSize(50);
  parameters.SetScalingTechnique(FLEXIBLEAUTO);
  parameters.SetSecurityLevel(HEStd_NotSet);
  parameters.SetRingDim(ringDimension);
  parameters.SetBatchSize(width);
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
  std::filesystem::create_directories(item(session, "public"));
  std::filesystem::create_directories(item(session, "client_private"));
  require(Serial::SerializeToFile(
              item(session, "public/context.bin").string(), context,
              SerType::BINARY),
          "cannot save CKKS context");
  require(Serial::SerializeToFile(
              item(session, "public/public.key").string(), keys.publicKey,
              SerType::BINARY),
          "cannot save public key");
  require(Serial::SerializeToFile(
              item(session, "client_private/audit_secret.key").string(),
              keys.secretKey, SerType::BINARY),
          "cannot save client audit secret");
}

void encryptColumn(const std::filesystem::path& session,
                   const std::filesystem::path& inputPath,
                   const std::filesystem::path& outputPath,
                   double scale, uint32_t validCount, uint32_t width) {
  auto raw = readValues(inputPath);
  require(raw.size() == validCount && validCount >= 2 && validCount <= width,
          "plaintext row count does not match valid_count");
  require(scale > 0, "scale must be positive");
  std::vector<double> normalized;
  normalized.reserve(width);
  for (double value : raw) {
    const double encoded = value / scale;
    require(encoded > -0.5 && encoded <= 0.5,
            "parent value violates normalized CKKS range");
    normalized.push_back(encoded);
  }
  // Duplicate padding preserves MIN/MAX. SUM/MEAN/VAR apply a public mask.
  normalized.resize(width, normalized.front());
  auto context = loadContext(session);
  auto publicKey = loadPublicKey(session);
  auto ciphertext = context->Encrypt(
      publicKey, context->MakeCKKSPackedPlaintext(normalized));
  saveCiphertext(outputPath, ciphertext);
}

void binaryOperation(const std::filesystem::path& session,
                     const std::string& operation,
                     const std::filesystem::path& leftPath,
                     const std::filesystem::path& rightPath,
                     const std::filesystem::path& outputPath) {
  auto context = loadContext(session);
  auto left = loadCiphertext(leftPath);
  auto right = loadCiphertext(rightPath);
  CiphertextT result;
  if (operation == "add") result = context->EvalAdd(left, right);
  else if (operation == "subtract") result = context->EvalSub(left, right);
  else if (operation == "multiply") {
    auto secretKey = loadPrivateKey(session);
    context->EvalMultKeyGen(secretKey);
    result = context->EvalMult(left, right);
  } else throw std::runtime_error("unsupported binary operation");
  saveCiphertext(outputPath, result);
}

void statistics(const std::filesystem::path& session,
                const std::filesystem::path& inputPath,
                const std::filesystem::path& sumPath,
                const std::filesystem::path& meanPath,
                const std::filesystem::path& variancePath,
                uint32_t validCount, uint32_t width) {
  require(validCount >= 2 && validCount <= width,
          "statistics valid_count is invalid");
  auto context = loadContext(session);
  auto secretKey = loadPrivateKey(session);
  context->EvalMultKeyGen(secretKey);
  context->EvalSumKeyGen(secretKey);
  auto input = loadCiphertext(inputPath);
  std::vector<double> mask(width, 0.0);
  for (uint32_t index = 0; index < validCount; ++index) mask[index] = 1.0;
  auto masked = context->EvalMult(
      input, context->MakeCKKSPackedPlaintext(mask));
  auto encryptedSum = context->EvalSum(masked, width);
  auto encryptedMean = context->EvalMult(
      encryptedSum, 1.0 / static_cast<double>(validCount));
  auto encryptedSquares = context->EvalMult(masked, masked);
  auto encryptedSquareSum = context->EvalSum(encryptedSquares, width);
  auto centeredSquareSum = context->EvalSub(
      encryptedSquareSum, context->EvalMult(encryptedSum, encryptedMean));
  auto encryptedVariance = context->EvalMult(
      centeredSquareSum, 1.0 / static_cast<double>(validCount - 1));
  saveCiphertext(sumPath, encryptedSum);
  saveCiphertext(meanPath, encryptedMean);
  saveCiphertext(variancePath, encryptedVariance);
}

void minmax(const std::filesystem::path& session,
            const std::filesystem::path& inputPath,
            const std::filesystem::path& minimumPath,
            const std::filesystem::path& maximumPath,
            uint32_t width) {
  auto context = loadContext(session);
  auto publicKey = loadPublicKey(session);
  auto secretKey = loadPrivateKey(session);
  KeyPair<DCRTPoly> keys;
  keys.publicKey = publicKey;
  keys.secretKey = secretKey;
  SchSwchParams switching;
  switching.SetSecurityLevelCKKS(HEStd_NotSet);
  switching.SetSecurityLevelFHEW(TOY);
  switching.SetCtxtModSizeFHEWLargePrec(25);
  switching.SetNumSlotsCKKS(width);
  switching.SetNumValues(width);
  switching.SetComputeArgmin(true);
  auto lweSecretKey = context->EvalSchemeSwitchingSetup(switching);
  context->EvalSchemeSwitchingKeyGen(keys, lweSecretKey);
  context->EvalCompareSwitchPrecompute(1, 1, true);
  auto input = loadCiphertext(inputPath);
  auto minimum = context->EvalMinSchemeSwitching(
      input, publicKey, width, width);
  auto maximum = context->EvalMaxSchemeSwitching(
      input, publicKey, width, width);
  require(!minimum.empty() && !maximum.empty(),
          "scheme-switch MIN/MAX produced no ciphertext");
  saveCiphertext(minimumPath, minimum[0]);
  saveCiphertext(maximumPath, maximum[0]);
}

void decrypt(const std::filesystem::path& session,
             const std::filesystem::path& inputPath,
             const std::filesystem::path& outputPath,
             double scale, uint32_t count) {
  auto context = loadContext(session);
  auto secretKey = loadPrivateKey(session);
  auto input = loadCiphertext(inputPath);
  Plaintext plaintext;
  context->Decrypt(secretKey, input, &plaintext);
  plaintext->SetLength(count);
  std::ofstream output(outputPath);
  require(output.good(), "cannot create final audit output");
  output << "value\n" << std::setprecision(17);
  const auto values = plaintext->GetRealPackedValue();
  for (uint32_t index = 0; index < count; ++index)
    output << values.at(index) * scale << '\n';
}

int main(int argc, char** argv) {
  try {
    if (argc < 3) return 2;
    const std::string stage(argv[1]);
    const std::filesystem::path session(argv[2]);
    if (stage == "init" && argc == 5)
      initialize(session, std::stoul(argv[3]), std::stoul(argv[4]));
    else if (stage == "encrypt" && argc == 8)
      encryptColumn(session, argv[3], argv[4], std::stod(argv[5]),
                    std::stoul(argv[6]), std::stoul(argv[7]));
    else if ((stage == "add" || stage == "subtract" ||
              stage == "multiply") && argc == 6)
      binaryOperation(session, stage, argv[3], argv[4], argv[5]);
    else if (stage == "statistics" && argc == 9)
      statistics(session, argv[3], argv[4], argv[5], argv[6],
                 std::stoul(argv[7]), std::stoul(argv[8]));
    else if (stage == "minmax" && argc == 7)
      minmax(session, argv[3], argv[4], argv[5], std::stoul(argv[6]));
    else if (stage == "decrypt" && argc == 7)
      decrypt(session, argv[3], argv[4], std::stod(argv[5]),
              std::stoul(argv[6]));
    else return 2;
    return 0;
  } catch (const OpenFHEException& error) {
    std::cerr << "OpenFHE simple-session error: " << error.what() << '\n';
    return 1;
  } catch (const std::exception& error) {
    std::cerr << "simple-session error: " << error.what() << '\n';
    return 1;
  }
}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceBuiltEncryptedColumn:
    path: Path
    scale: float
    valid_count: int
    session_fingerprint: str


@dataclass(frozen=True)
class SourceBuiltEncryptedScalar:
    path: Path
    scale: float
    source_count: int
    session_fingerprint: str


class SourceBuiltCkksSession:
    """Simple file-backed API using the server's OpenFHE CMake install."""

    def __init__(self, root: Path, manifest: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.manifest = manifest
        self.width = int(manifest["width"])
        self.ring_dimension = int(manifest["ring_dimension"])
        self.input_scale = float(manifest["input_scale"])
        self.openfhe_dir = str(manifest["openfhe_dir"])
        self._fingerprint = str(manifest["context_sha256"])
        self._runner = self.root / "runner/build/simple_ckks_session_runner"
        if not self._runner.is_file():
            raise FileNotFoundError(
                f"source-built session runner is missing: {self._runner}"
            )
        self._statistics_cache: dict[str, dict[str, SourceBuiltEncryptedScalar]] = {}
        self._minmax_cache: dict[str, dict[str, SourceBuiltEncryptedScalar]] = {}

    @classmethod
    def create(
        cls,
        *,
        checkpoint_dir: Path,
        width: int,
        input_scale: float,
        ring_dimension: int = 16384,
        openfhe_dir: str = "/usr/local/lib/OpenFHE",
        overwrite: bool = False,
    ) -> "SourceBuiltCkksSession":
        root = checkpoint_dir.resolve()
        if root.exists():
            if not overwrite:
                raise FileExistsError(
                    f"refusing to overwrite source-built session: {root}"
                )
            if root == Path(root.anchor) or root == Path.home().resolve():
                raise ValueError(f"refusing to remove broad path: {root}")
            shutil.rmtree(root)
        work = root / "runner"
        work.mkdir(parents=True)
        (work / "CMakeLists.txt").write_text(CMAKE, encoding="utf-8")
        (work / "simple_ckks_session_runner.cpp").write_text(
            RUNNER,
            encoding="utf-8",
        )
        build = work / "build"
        run(
            [
                "cmake",
                "-S",
                str(work.resolve()),
                "-B",
                str(build.resolve()),
                f"-DOpenFHE_DIR={openfhe_dir}",
            ],
            work,
        )
        run(
            [
                "cmake",
                "--build",
                str(build.resolve()),
                "--target",
                "simple_ckks_session_runner",
            ],
            work,
        )
        runner = build / "simple_ckks_session_runner"
        run(
            [
                str(runner.resolve()),
                "init",
                str(root),
                str(width),
                str(ring_dimension),
            ],
            work,
        )
        context = root / "public/context.bin"
        manifest = {
            "format": "source-built-simple-ckks-session-v1",
            "backend": "source-built OpenFHE C++",
            "width": width,
            "ring_dimension": ring_dimension,
            "input_scale": input_scale,
            "openfhe_dir": openfhe_dir,
            "context_sha256": _sha256(context),
            "client_secret_present": True,
            "client_secret_required_to_regenerate_switching_keys": True,
            "columns": {},
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        return cls(root, manifest)

    @classmethod
    def load(cls, checkpoint_dir: Path) -> "SourceBuiltCkksSession":
        root = checkpoint_dir.resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"source-built session manifest is missing: {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "source-built-simple-ckks-session-v1":
            raise RuntimeError("unsupported source-built session format")
        context = root / "public/context.bin"
        if not context.is_file() or _sha256(context) != manifest[
            "context_sha256"
        ]:
            raise RuntimeError("source-built session context hash mismatch")
        return cls(root, manifest)

    def encrypt_column(
        self,
        values: Sequence[float],
        *,
        name: str,
    ) -> SourceBuiltEncryptedColumn:
        materialized = [float(value) for value in values]
        if not name or not name.strip():
            raise ValueError("encrypted column name must not be empty")
        if not 2 <= len(materialized) <= self.width:
            raise ValueError("column length must be in [2, width]")
        input_path = self.root / "client_private/inputs" / f"{name}.csv"
        output_path = self.root / "ciphertexts" / f"{name}.ct"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_values(input_path, materialized)
        self._execute(
            "encrypt",
            input_path,
            output_path,
            self.input_scale,
            len(materialized),
            self.width,
        )
        column = SourceBuiltEncryptedColumn(
            output_path,
            self.input_scale,
            len(materialized),
            self._fingerprint,
        )
        self._register(name, column, "column")
        return column

    def load_column(self, name: str) -> SourceBuiltEncryptedColumn:
        record = self._record(name, "column")
        return SourceBuiltEncryptedColumn(
            self.root / record["path"],
            float(record["scale"]),
            int(record["valid_count"]),
            self._fingerprint,
        )

    def add(
        self,
        left: SourceBuiltEncryptedColumn,
        right: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedColumn:
        return self._binary("add", left, right, left.scale)

    def subtract(
        self,
        left: SourceBuiltEncryptedColumn,
        right: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedColumn:
        return self._binary("subtract", left, right, left.scale)

    def multiply(
        self,
        left: SourceBuiltEncryptedColumn,
        right: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedColumn:
        return self._binary("multiply", left, right, left.scale * right.scale)

    def sum(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedScalar:
        return self._statistics(column)["sum"]

    def mean(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedScalar:
        return self._statistics(column)["mean"]

    def variance(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedScalar:
        return self._statistics(column)["variance"]

    def minimum(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedScalar:
        return self._minmax(column)["minimum"]

    def maximum(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> SourceBuiltEncryptedScalar:
        return self._minmax(column)["maximum"]

    def decrypt_column(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> tuple[float, ...]:
        self._validate(column)
        return self._decrypt(column.path, column.scale, column.valid_count)

    def decrypt_scalar(self, scalar: SourceBuiltEncryptedScalar) -> float:
        self._validate(scalar)
        return self._decrypt(scalar.path, scalar.scale, 1)[0]

    def _binary(
        self,
        operation: Literal["add", "subtract", "multiply"],
        left: SourceBuiltEncryptedColumn,
        right: SourceBuiltEncryptedColumn,
        output_scale: float,
    ) -> SourceBuiltEncryptedColumn:
        self._validate(left)
        self._validate(right)
        if left.valid_count != right.valid_count:
            raise ValueError("encrypted columns have different valid counts")
        if operation != "multiply" and left.scale != right.scale:
            raise ValueError("encrypted columns have different scales")
        output = self.root / "ciphertexts/derived" / (
            f"{operation}_{uuid4().hex}.ct"
        )
        self._execute(operation, left.path, right.path, output)
        return SourceBuiltEncryptedColumn(
            output,
            output_scale,
            left.valid_count,
            self._fingerprint,
        )

    def _statistics(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> dict[str, SourceBuiltEncryptedScalar]:
        self._validate(column)
        cache_key = str(column.path.resolve())
        if cache_key in self._statistics_cache:
            return self._statistics_cache[cache_key]
        output = self.root / "ciphertexts/aggregates" / uuid4().hex
        paths = {
            "sum": output / "sum.ct",
            "mean": output / "mean.ct",
            "variance": output / "variance.ct",
        }
        self._execute(
            "statistics",
            column.path,
            paths["sum"],
            paths["mean"],
            paths["variance"],
            column.valid_count,
            self.width,
        )
        result = {
            "sum": SourceBuiltEncryptedScalar(
                paths["sum"], column.scale, column.valid_count,
                self._fingerprint,
            ),
            "mean": SourceBuiltEncryptedScalar(
                paths["mean"], column.scale, column.valid_count,
                self._fingerprint,
            ),
            "variance": SourceBuiltEncryptedScalar(
                paths["variance"], column.scale * column.scale,
                column.valid_count, self._fingerprint,
            ),
        }
        self._statistics_cache[cache_key] = result
        return result

    def _minmax(
        self,
        column: SourceBuiltEncryptedColumn,
    ) -> dict[str, SourceBuiltEncryptedScalar]:
        self._validate(column)
        cache_key = str(column.path.resolve())
        if cache_key in self._minmax_cache:
            return self._minmax_cache[cache_key]
        output = self.root / "ciphertexts/minmax" / uuid4().hex
        paths = {
            "minimum": output / "minimum.ct",
            "maximum": output / "maximum.ct",
        }
        self._execute(
            "minmax",
            column.path,
            paths["minimum"],
            paths["maximum"],
            self.width,
        )
        result = {
            name: SourceBuiltEncryptedScalar(
                path,
                column.scale,
                column.valid_count,
                self._fingerprint,
            )
            for name, path in paths.items()
        }
        self._minmax_cache[cache_key] = result
        return result

    def _decrypt(
        self,
        path: Path,
        scale: float,
        count: int,
    ) -> tuple[float, ...]:
        output = self.root / "client_private/audits" / (
            f"{uuid4().hex}.csv"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        self._execute("decrypt", path, output, scale, count)
        return tuple(float(row["value"]) for row in read_csv(output))

    def _execute(self, stage: str, *arguments: object) -> None:
        run(
            [
                str(self._runner.resolve()),
                stage,
                str(self.root),
                *(str(argument) for argument in arguments),
            ],
            self._runner.parent,
        )

    def _register(
        self,
        name: str,
        value: SourceBuiltEncryptedColumn,
        kind: str,
    ) -> None:
        self.manifest["columns"][name] = {
            "kind": kind,
            "path": str(value.path.relative_to(self.root)),
            "scale": value.scale,
            "valid_count": value.valid_count,
            "sha256": _sha256(value.path),
        }
        (self.root / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    def _record(self, name: str, kind: str) -> dict[str, Any]:
        try:
            record = self.manifest["columns"][name]
        except KeyError as error:
            raise KeyError(
                f"encrypted checkpoint has no column {name!r}"
            ) from error
        if record["kind"] != kind:
            raise RuntimeError(f"checkpoint item {name!r} is not a {kind}")
        path = self.root / record["path"]
        if not path.is_file() or _sha256(path) != record["sha256"]:
            raise RuntimeError(f"ciphertext hash mismatch for {name!r}")
        return record

    def _validate(
        self,
        value: SourceBuiltEncryptedColumn | SourceBuiltEncryptedScalar,
    ) -> None:
        if value.session_fingerprint != self._fingerprint:
            raise ValueError(
                "ciphertext belongs to a different OpenFHE session"
            )
        if not value.path.is_file():
            raise FileNotFoundError(
                f"ciphertext artifact is missing: {value.path}"
            )
