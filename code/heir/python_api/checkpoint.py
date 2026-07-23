"""Persistent checkpoints for official HEIR-generated OpenFHE programs.

HEIR 2026.7.1 does not expose ciphertext/context serialization in its Python
client interface. This module keeps the official HEIR compilation pipeline,
but adds OpenFHE serialization bindings to the generated pybind module before
that module is compiled.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from code.heir.python_api.official_ckks_aggregates import OfficialCkksAggregate
from code.heir.python_api.official_columns import (
    BinaryOperation,
    OfficialCkksBinaryColumn,
)


SERIALIZATION_INCLUDES = r'''
#include <fstream>
#include <stdexcept>
#include <string>
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
'''


SERIALIZATION_HELPERS = r'''
namespace {
void checkpoint_require(bool ok, const char* message) {
  if (!ok) throw std::runtime_error(message);
}

void checkpoint_save_context(
    const CryptoContextT& value, const std::string& path) {
  checkpoint_require(
      Serial::SerializeToFile(path, value, SerType::BINARY),
      "cannot serialize CKKS context");
}

CryptoContextT checkpoint_load_context(const std::string& path) {
  CryptoContextT value;
  checkpoint_require(
      Serial::DeserializeFromFile(path, value, SerType::BINARY),
      "cannot deserialize CKKS context");
  return value;
}

void checkpoint_save_public_key(
    const PublicKeyT& value, const std::string& path) {
  checkpoint_require(
      Serial::SerializeToFile(path, value, SerType::BINARY),
      "cannot serialize public key");
}

PublicKeyT checkpoint_load_public_key(const std::string& path) {
  PublicKeyT value;
  checkpoint_require(
      Serial::DeserializeFromFile(path, value, SerType::BINARY),
      "cannot deserialize public key");
  return value;
}

void checkpoint_save_private_key(
    const PrivateKeyT& value, const std::string& path) {
  checkpoint_require(
      Serial::SerializeToFile(path, value, SerType::BINARY),
      "cannot serialize private key");
}

PrivateKeyT checkpoint_load_private_key(const std::string& path) {
  PrivateKeyT value;
  checkpoint_require(
      Serial::DeserializeFromFile(path, value, SerType::BINARY),
      "cannot deserialize private key");
  return value;
}

using CheckpointCiphertexts = std::vector<CiphertextT>;

void checkpoint_save_ciphertexts(
    const CheckpointCiphertexts& value, const std::string& path) {
  checkpoint_require(
      Serial::SerializeToFile(path, value, SerType::BINARY),
      "cannot serialize ciphertext bundle");
}

CheckpointCiphertexts checkpoint_load_ciphertexts(const std::string& path) {
  CheckpointCiphertexts value;
  checkpoint_require(
      Serial::DeserializeFromFile(path, value, SerType::BINARY),
      "cannot deserialize ciphertext bundle");
  return value;
}

void checkpoint_save_eval_sum_keys(
    const CryptoContextT& context, const std::string& path) {
  std::ofstream output(path, std::ios::binary);
  checkpoint_require(output.good(), "cannot create rotation-key file");
  checkpoint_require(
      CryptoContextImpl<DCRTPoly>::SerializeEvalSumKey(
          output, SerType::BINARY, context),
      "cannot serialize rotation/evaluation keys");
}

void checkpoint_load_eval_sum_keys(
    const CryptoContextT&, const std::string& path) {
  std::ifstream input(path, std::ios::binary);
  checkpoint_require(input.good(), "cannot open rotation-key file");
  checkpoint_require(
      CryptoContextImpl<DCRTPoly>::DeserializeEvalSumKey(
          input, SerType::BINARY),
      "cannot deserialize rotation/evaluation keys");
}

void checkpoint_save_eval_mult_keys(
    const CryptoContextT& context, const std::string& path) {
  std::ofstream output(path, std::ios::binary);
  checkpoint_require(output.good(), "cannot create multiplication-key file");
  checkpoint_require(
      CryptoContextImpl<DCRTPoly>::SerializeEvalMultKey(
          output, SerType::BINARY, context),
      "cannot serialize multiplication evaluation keys");
}

void checkpoint_load_eval_mult_keys(
    const CryptoContextT&, const std::string& path) {
  std::ifstream input(path, std::ios::binary);
  checkpoint_require(input.good(), "cannot open multiplication-key file");
  checkpoint_require(
      CryptoContextImpl<DCRTPoly>::DeserializeEvalMultKey(
          input, SerType::BINARY),
      "cannot deserialize multiplication evaluation keys");
}
}  // namespace
'''


SERIALIZATION_BINDINGS = r'''
  m.def("__checkpoint_save_context", &checkpoint_save_context);
  m.def("__checkpoint_load_context", &checkpoint_load_context);
  m.def("__checkpoint_save_public_key", &checkpoint_save_public_key);
  m.def("__checkpoint_load_public_key", &checkpoint_load_public_key);
  m.def("__checkpoint_save_private_key", &checkpoint_save_private_key);
  m.def("__checkpoint_load_private_key", &checkpoint_load_private_key);
  m.def("__checkpoint_save_ciphertexts", &checkpoint_save_ciphertexts);
  m.def("__checkpoint_load_ciphertexts", &checkpoint_load_ciphertexts);
  m.def("__checkpoint_save_eval_sum_keys", &checkpoint_save_eval_sum_keys);
  m.def("__checkpoint_load_eval_sum_keys", &checkpoint_load_eval_sum_keys);
  m.def("__checkpoint_save_eval_mult_keys", &checkpoint_save_eval_mult_keys);
  m.def("__checkpoint_load_eval_mult_keys", &checkpoint_load_eval_mult_keys);
'''


def patch_generated_pybind(source: str) -> str:
    """Add native OpenFHE persistence functions to HEIR's generated pybind."""
    if "__checkpoint_save_context" in source:
        return source
    module_marker = "PYBIND11_MODULE("
    module_index = source.find(module_marker)
    if module_index < 0:
        raise RuntimeError("HEIR pybind output has no PYBIND11_MODULE")
    bind_marker = "  bind_common(m);"
    if bind_marker not in source[module_index:]:
        raise RuntimeError("HEIR pybind output has no bind_common module setup")

    patched = SERIALIZATION_INCLUDES + "\n" + source
    module_index = patched.find(module_marker)
    patched = (
        patched[:module_index]
        + SERIALIZATION_HELPERS
        + "\n"
        + patched[module_index:]
    )
    bind_index = patched.find(bind_marker, module_index)
    bind_end = bind_index + len(bind_marker)
    return patched[:bind_end] + SERIALIZATION_BINDINGS + patched[bind_end:]


class _SerializationTranslateProxy:
    """Patch only the pybind file emitted by the official translator."""

    def __init__(self, delegate: Any):
        self._delegate = delegate

    def run_binary(self, *, input: str, options: list[Any]) -> Any:
        result = self._delegate.run_binary(input=input, options=options)
        rendered = [str(option) for option in options]
        if "--emit-openfhe-pke-pybind" in rendered:
            try:
                output = Path(rendered[rendered.index("-o") + 1])
            except (ValueError, IndexError) as error:
                raise RuntimeError(
                    "cannot locate HEIR pybind output path"
                ) from error
            output.write_text(
                patch_generated_pybind(output.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _checkpoint_backend(*, debug: bool) -> Any:
    try:
        from heir.backends.openfhe import OpenFHEBackend
        from heir.backends.openfhe import config as openfhe_config
    except ImportError as error:
        raise RuntimeError(
            "checkpointing requires heir_py[python,openfhe]==2026.7.1"
        ) from error

    class CheckpointOpenFHEBackend(OpenFHEBackend):
        def run_backend(
            self,
            workspace_dir: Any,
            heir_opt: Any,
            heir_translate: Any,
            func_name: str,
            arg_names: list[str],
            secret_args: list[int],
            heir_opt_output: str,
            debug: bool,
        ) -> Any:
            return super().run_backend(
                workspace_dir,
                heir_opt,
                _SerializationTranslateProxy(heir_translate),
                func_name,
                arg_names,
                secret_args,
                heir_opt_output,
                debug,
            )

    return CheckpointOpenFHEBackend(
        openfhe_config.resolve_config(debug=debug)
    )


def compile_checkpointable_sum(
    *,
    width: int,
    valid_count: int,
    debug: bool = False,
) -> OfficialCkksAggregate:
    """Compile official HEIR SUM with native checkpoint bindings attached."""
    return OfficialCkksAggregate(
        "sum",
        width,
        valid_count,
        debug,
        backend=_checkpoint_backend(debug=debug),
    )


def compile_checkpointable_binary_column(
    *,
    operation: BinaryOperation,
    width: int,
    input_scale: float = 1.0,
    debug: bool = False,
) -> OfficialCkksBinaryColumn:
    """Compile a generic +, -, or × column with checkpoint bindings."""
    return OfficialCkksBinaryColumn(
        operation,
        width,
        input_scale,
        debug,
        backend=_checkpoint_backend(debug=debug),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _module(program: Any) -> Any:
    return program._program.compilation_result.module


def _raw_ciphertext(value: Any) -> Any:
    return value.value if hasattr(value, "identifier") else value


def _wrap_input_ciphertext(
    program: OfficialCkksAggregate,
    raw_ciphertext: Any,
) -> Any:
    """Restore HEIR's argument identity wrapper for subsequent evaluation."""
    try:
        from heir.interfaces import EncValue
    except ImportError as error:
        raise RuntimeError("the official HEIR Python package is required") from error
    encryptors = program._program.compilation_result.arg_enc_funcs or {}
    if len(encryptors) != 1:
        raise RuntimeError(
            "expected exactly one encrypted input in restored SUM circuit"
        )
    return EncValue(next(iter(encryptors)), raw_ciphertext)


def _artifact_record(
    path: Path,
    *,
    root: Path,
    visibility: str,
) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "visibility": visibility,
    }


@dataclass(frozen=True)
class LoadedSumCheckpoint:
    """A restored official HEIR program and its saved ciphertext branches."""

    program: OfficialCkksAggregate
    input_ciphertext: Any
    result_ciphertext: Any
    manifest: dict[str, Any]


@dataclass(frozen=True)
class LoadedBinaryColumnCheckpoint:
    """Restored binary-column program, parents, and derived ciphertext."""

    program: OfficialCkksBinaryColumn
    left_ciphertext: Any
    right_ciphertext: Any
    result_ciphertext: Any
    manifest: dict[str, Any]


def save_sum_checkpoint(
    program: OfficialCkksAggregate,
    *,
    input_ciphertext: Any,
    result_ciphertext: Any,
    checkpoint_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Serialize one live SUM session without decrypting either ciphertext."""
    if program.operation != "sum" or not program._is_setup:
        raise RuntimeError("save_sum_checkpoint requires a setup SUM program")

    root = checkpoint_dir.resolve()
    public = root / "public"
    private = root / "client_private"
    paths = {
        "context": public / "context.bin",
        "public_key": public / "public.key",
        "eval_sum_keys": public / "eval_sum.keys",
        "input_ciphertext": public / "input.ct",
        "result_ciphertext": public / "sum.ct",
        "secret_key": private / "audit_secret.key",
        "manifest": root / "manifest.json",
    }
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            f"refusing to overwrite {existing[0]}; pass overwrite=True"
        )
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    client = program._program
    module = _module(program)
    module.__checkpoint_save_context(
        client.crypto_context, str(paths["context"])
    )
    module.__checkpoint_save_public_key(
        client.keypair.publicKey, str(paths["public_key"])
    )
    module.__checkpoint_save_private_key(
        client.keypair.secretKey, str(paths["secret_key"])
    )
    paths["secret_key"].chmod(0o600)
    module.__checkpoint_save_eval_sum_keys(
        client.crypto_context, str(paths["eval_sum_keys"])
    )
    module.__checkpoint_save_ciphertexts(
        _raw_ciphertext(input_ciphertext),
        str(paths["input_ciphertext"]),
    )
    module.__checkpoint_save_ciphertexts(
        _raw_ciphertext(result_ciphertext),
        str(paths["result_ciphertext"]),
    )

    public_records = {
        name: _artifact_record(path, root=root, visibility="evaluator")
        for name, path in paths.items()
        if name not in {"secret_key", "manifest"}
    }
    try:
        heir_version = importlib.metadata.version("heir_py")
    except importlib.metadata.PackageNotFoundError:
        heir_version = "unknown"
    manifest = {
        "format": "heir-openfhe-checkpoint-v1",
        "scheme": "CKKS",
        "operation": "sum",
        "width": program.width,
        "valid_count": program.valid_count,
        "mlir_sha256": _source_sha256(program.mlir),
        "heir_py_version": heir_version,
        "no_intermediate_decryption": True,
        "public_artifacts": public_records,
        "audit_secret": {
            "path": str(paths["secret_key"].relative_to(root)),
            "visibility": "client-only",
            "required_for_evaluation": False,
            "required_for_audit_decryption": True,
        },
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _wrap_binary_inputs(
    program: OfficialCkksBinaryColumn,
    left: Any,
    right: Any,
) -> tuple[Any, Any]:
    try:
        from heir.interfaces import EncValue
    except ImportError as error:
        raise RuntimeError("the official HEIR Python package is required") from error
    encryptors = program._program.compilation_result.arg_enc_funcs or {}
    if len(encryptors) != 2:
        raise RuntimeError("restored binary circuit must have two inputs")
    names = list(encryptors)
    return EncValue(names[0], left), EncValue(names[1], right)


def save_binary_column_checkpoint(
    program: OfficialCkksBinaryColumn,
    *,
    encrypted_columns: tuple[Any, Any],
    result_ciphertext: Any,
    valid_count: int,
    checkpoint_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save generic parent columns and their still-encrypted derived column."""
    if not program._is_setup:
        raise RuntimeError("binary column program must be setup before saving")
    if not 1 <= valid_count <= program.width:
        raise ValueError("valid_count must be in [1, width]")

    root = checkpoint_dir.resolve()
    public = root / "public"
    private = root / "client_private"
    paths = {
        "context": public / "context.bin",
        "public_key": public / "public.key",
        "left_ciphertext": public / "left.ct",
        "right_ciphertext": public / "right.ct",
        "result_ciphertext": public / "result.ct",
        "secret_key": private / "audit_secret.key",
        "manifest": root / "manifest.json",
    }
    if program.operation == "multiply":
        paths["eval_mult_keys"] = public / "eval_mult.keys"
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            f"refusing to overwrite {existing[0]}; pass overwrite=True"
        )
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    client = program._program
    module = _module(program)
    module.__checkpoint_save_context(
        client.crypto_context,
        str(paths["context"]),
    )
    module.__checkpoint_save_public_key(
        client.keypair.publicKey,
        str(paths["public_key"]),
    )
    module.__checkpoint_save_private_key(
        client.keypair.secretKey,
        str(paths["secret_key"]),
    )
    paths["secret_key"].chmod(0o600)
    if "eval_mult_keys" in paths:
        module.__checkpoint_save_eval_mult_keys(
            client.crypto_context,
            str(paths["eval_mult_keys"]),
        )
    left, right = encrypted_columns
    module.__checkpoint_save_ciphertexts(
        _raw_ciphertext(left),
        str(paths["left_ciphertext"]),
    )
    module.__checkpoint_save_ciphertexts(
        _raw_ciphertext(right),
        str(paths["right_ciphertext"]),
    )
    module.__checkpoint_save_ciphertexts(
        _raw_ciphertext(result_ciphertext),
        str(paths["result_ciphertext"]),
    )

    public_records = {
        name: _artifact_record(path, root=root, visibility="evaluator")
        for name, path in paths.items()
        if name not in {"secret_key", "manifest"}
    }
    try:
        heir_version = importlib.metadata.version("heir_py")
    except importlib.metadata.PackageNotFoundError:
        heir_version = "unknown"
    manifest = {
        "format": "heir-openfhe-checkpoint-v1",
        "scheme": "CKKS",
        "operation": "binary_column",
        "binary_operation": program.operation,
        "width": program.width,
        "valid_count": valid_count,
        "input_scale": program.input_scale,
        "output_scale": program.output_scale,
        "mlir_sha256": _source_sha256(program.mlir),
        "heir_py_version": heir_version,
        "no_intermediate_decryption": True,
        "public_artifacts": public_records,
        "audit_secret": {
            "path": str(paths["secret_key"].relative_to(root)),
            "visibility": "client-only",
            "required_for_evaluation": False,
            "required_for_audit_decryption": True,
        },
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _validate_artifacts(root: Path, manifest: dict[str, Any]) -> None:
    for name, record in manifest["public_artifacts"].items():
        path = Path(record["path"])
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise FileNotFoundError(f"checkpoint artifact is missing: {path}")
        if _sha256(path) != record["sha256"]:
            raise RuntimeError(f"checkpoint artifact hash mismatch: {name}")


def load_sum_checkpoint(
    checkpoint_dir: Path,
    *,
    for_audit: bool = True,
    debug: bool = False,
) -> LoadedSumCheckpoint:
    """Compile the same circuit and restore its OpenFHE state and ciphertexts."""
    root = checkpoint_dir.resolve()
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("format") != "heir-openfhe-checkpoint-v1":
        raise RuntimeError("unsupported HEIR/OpenFHE checkpoint format")
    if manifest.get("operation") != "sum":
        raise RuntimeError("this loader accepts SUM checkpoints only")
    _validate_artifacts(root, manifest)

    program = compile_checkpointable_sum(
        width=int(manifest["width"]),
        valid_count=int(manifest["valid_count"]),
        debug=debug,
    )
    if _source_sha256(program.mlir) != manifest["mlir_sha256"]:
        raise RuntimeError("checkpoint circuit does not match compiled SUM")

    module = _module(program)
    records = manifest["public_artifacts"]

    def public_path(name: str) -> str:
        path = Path(records[name]["path"])
        return str(path if path.is_absolute() else root / path)

    context = module.__checkpoint_load_context(public_path("context"))
    public_key = module.__checkpoint_load_public_key(public_path("public_key"))
    module.__checkpoint_load_eval_sum_keys(
        context, public_path("eval_sum_keys")
    )
    secret_key = None
    if for_audit:
        secret_path = Path(manifest["audit_secret"]["path"])
        if not secret_path.is_absolute():
            secret_path = root / secret_path
        if not secret_path.is_file():
            raise FileNotFoundError(
                f"client audit secret is missing: {secret_path}"
            )
        secret_key = module.__checkpoint_load_private_key(str(secret_path))

    program._program.crypto_context = context
    program._program.keypair = SimpleNamespace(
        publicKey=public_key,
        secretKey=secret_key,
    )
    program._is_setup = True
    raw_input = module.__checkpoint_load_ciphertexts(
        public_path("input_ciphertext")
    )
    return LoadedSumCheckpoint(
        program=program,
        input_ciphertext=_wrap_input_ciphertext(program, raw_input),
        result_ciphertext=module.__checkpoint_load_ciphertexts(
            public_path("result_ciphertext")
        ),
        manifest=manifest,
    )


def load_binary_column_checkpoint(
    checkpoint_dir: Path,
    *,
    for_audit: bool = True,
    debug: bool = False,
) -> LoadedBinaryColumnCheckpoint:
    """Restore a generic binary-column circuit and all saved ciphertexts."""
    root = checkpoint_dir.resolve()
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("format") != "heir-openfhe-checkpoint-v1":
        raise RuntimeError("unsupported HEIR/OpenFHE checkpoint format")
    if manifest.get("operation") != "binary_column":
        raise RuntimeError("this loader accepts binary-column checkpoints only")
    _validate_artifacts(root, manifest)

    program = compile_checkpointable_binary_column(
        operation=manifest["binary_operation"],
        width=int(manifest["width"]),
        input_scale=float(manifest["input_scale"]),
        debug=debug,
    )
    if _source_sha256(program.mlir) != manifest["mlir_sha256"]:
        raise RuntimeError("checkpoint circuit does not match compiled column")

    module = _module(program)
    records = manifest["public_artifacts"]

    def public_path(name: str) -> str:
        path = Path(records[name]["path"])
        return str(path if path.is_absolute() else root / path)

    context = module.__checkpoint_load_context(public_path("context"))
    public_key = module.__checkpoint_load_public_key(public_path("public_key"))
    if "eval_mult_keys" in records:
        module.__checkpoint_load_eval_mult_keys(
            context,
            public_path("eval_mult_keys"),
        )
    secret_key = None
    if for_audit:
        secret_path = Path(manifest["audit_secret"]["path"])
        if not secret_path.is_absolute():
            secret_path = root / secret_path
        if not secret_path.is_file():
            raise FileNotFoundError(
                f"client audit secret is missing: {secret_path}"
            )
        secret_key = module.__checkpoint_load_private_key(str(secret_path))

    program._program.crypto_context = context
    program._program.keypair = SimpleNamespace(
        publicKey=public_key,
        secretKey=secret_key,
    )
    program._is_setup = True
    raw_left = module.__checkpoint_load_ciphertexts(
        public_path("left_ciphertext")
    )
    raw_right = module.__checkpoint_load_ciphertexts(
        public_path("right_ciphertext")
    )
    left, right = _wrap_binary_inputs(program, raw_left, raw_right)
    return LoadedBinaryColumnCheckpoint(
        program=program,
        left_ciphertext=left,
        right_ciphertext=right,
        result_ciphertext=module.__checkpoint_load_ciphertexts(
            public_path("result_ciphertext")
        ),
        manifest=manifest,
    )
