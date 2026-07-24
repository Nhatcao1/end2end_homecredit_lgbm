"""Small public save/load API for named HEIR/OpenFHE ciphertext columns.

This intentionally does not emulate a Pandas DataFrame.  Version 1 stores two
named numeric columns encrypted for one compiled HEIR binary operation
(``add``, ``subtract``, or ``multiply``).  The operation, CKKS scale, tensor
width, public context, keys, and column order are recorded in a checked
manifest so a later process can safely reconstruct the exact program.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

from code.heir.python_api.checkpoint import (
    _artifact_record,
    _module,
    _raw_ciphertext,
    _sha256,
    _source_sha256,
    _wrap_binary_inputs,
    compile_checkpointable_binary_column,
)
from code.heir.python_api.official_columns import (
    BinaryOperation,
    OfficialCkksBinaryColumn,
)


FORMAT = "heir-encrypted-dataset-v1"


def _binding(module: Any, name: str) -> Any:
    """Resolve generated ``__checkpoint_*`` bindings without name-mangling."""
    try:
        return getattr(module, name)
    except AttributeError as error:
        raise RuntimeError(
            f"generated HEIR module is missing checkpoint binding {name}"
        ) from error


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _validate_columns(
    columns: Mapping[str, Sequence[float]],
) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...], int]:
    if len(columns) != 2:
        raise ValueError("EncryptedDataset v1 requires exactly two columns")
    names = tuple(columns)
    if any(not name or not name.strip() for name in names):
        raise ValueError("encrypted column names must not be empty")
    if len(set(names)) != len(names):
        raise ValueError("encrypted column names must be unique")

    materialized = tuple(
        tuple(float(value) for value in columns[name]) for name in names
    )
    counts = {len(values) for values in materialized}
    if len(counts) != 1:
        raise ValueError("encrypted columns must have the same row count")
    valid_count = counts.pop()
    if valid_count < 1:
        raise ValueError("encrypted columns must not be empty")
    if not all(
        math.isfinite(value)
        for values in materialized
        for value in values
    ):
        raise ValueError("encrypted columns must not contain NaN or infinity")
    return names, materialized, valid_count


@dataclass
class EncryptedDataset:
    """Two named ciphertext columns tied to one compatible HEIR context.

    Use :meth:`encrypt` to create a dataset, :meth:`save` to persist it, and
    :meth:`load` in a later process.  ``evaluate(left, right)`` executes the
    binary operation recorded in the manifest and returns another ciphertext;
    it does not decrypt.
    """

    program: OfficialCkksBinaryColumn
    columns: dict[str, Any]
    valid_count: int
    manifest: dict[str, Any] | None = None
    checkpoint_dir: Path | None = None

    def __post_init__(self) -> None:
        if len(self.columns) != 2:
            raise ValueError("EncryptedDataset v1 requires exactly two columns")
        if any(not name or not name.strip() for name in self.columns):
            raise ValueError("encrypted column names must not be empty")
        if not 1 <= self.valid_count <= self.program.width:
            raise ValueError("valid_count must be in [1, program.width]")
        if not self.program._is_setup:
            raise RuntimeError("EncryptedDataset requires a setup HEIR program")

    @classmethod
    def encrypt(
        cls,
        columns: Mapping[str, Sequence[float]],
        *,
        operation: BinaryOperation,
        width: int,
        input_scale: float = 1.0,
        debug: bool = False,
    ) -> "EncryptedDataset":
        """Compile, setup, pack, and encrypt two named numeric columns."""
        names, values, valid_count = _validate_columns(columns)
        if valid_count > width:
            raise ValueError(
                f"{valid_count} rows do not fit compiled width {width}"
            )
        program = compile_checkpointable_binary_column(
            operation=operation,
            width=width,
            input_scale=input_scale,
            debug=debug,
        )
        program.setup()
        encrypted = program.encrypt(values[0], values[1])
        return cls(
            program=program,
            columns={
                names[0]: _raw_ciphertext(encrypted[0]),
                names[1]: _raw_ciphertext(encrypted[1]),
            },
            valid_count=valid_count,
        )

    @property
    def operation(self) -> BinaryOperation:
        return self.program.operation

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(self.columns)

    def __getitem__(self, name: str) -> Any:
        return self.columns[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.columns)

    def evaluate(self, left: str, right: str) -> Any:
        """Evaluate the recorded operation on two named ciphertext columns."""
        try:
            left_ciphertext = self.columns[left]
            right_ciphertext = self.columns[right]
        except KeyError as error:
            raise KeyError(
                f"unknown encrypted column {error.args[0]!r}; "
                f"available columns are {list(self.columns)}"
            ) from error
        wrapped = _wrap_binary_inputs(
            self.program,
            _raw_ciphertext(left_ciphertext),
            _raw_ciphertext(right_ciphertext),
        )
        return self.program.eval(wrapped)

    def decrypt_result(self, encrypted_result: Any) -> tuple[float, ...]:
        """Decrypt a final result only when this dataset was loaded for audit."""
        if self.program._program.keypair.secretKey is None:
            raise RuntimeError(
                "the evaluator dataset has no audit secret; reload with "
                "for_audit=True on the client"
            )
        return self.program.decrypt(
            encrypted_result,
            valid_count=self.valid_count,
        )

    def save(
        self,
        checkpoint_dir: Path,
        *,
        include_audit_key: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Persist named ciphertext columns and their compatible public state."""
        root = checkpoint_dir.resolve()
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise ValueError(
                f"refusing to use broad encrypted dataset path: {root}"
            )
        if root.exists():
            if not overwrite:
                raise FileExistsError(
                    f"refusing to overwrite encrypted dataset: {root}"
                )
            shutil.rmtree(root)
        public = root / "public"
        column_dir = public / "columns"
        private = root / "client_private"
        column_dir.mkdir(parents=True)
        if include_audit_key:
            private.mkdir()

        module = _module(self.program)
        client = self.program._program
        required_bindings = (
            "__checkpoint_save_context",
            "__checkpoint_save_public_key",
            "__checkpoint_save_ciphertexts",
        )
        missing = [
            name for name in required_bindings if not hasattr(module, name)
        ]
        if missing:
            raise RuntimeError(
                "program was not compiled with checkpoint bindings; use "
                "EncryptedDataset.encrypt(). Missing: "
                + ", ".join(missing)
            )

        paths = {
            "context": public / "context.bin",
            "public_key": public / "public.key",
        }
        _binding(module, "__checkpoint_save_context")(
            client.crypto_context,
            str(paths["context"]),
        )
        _binding(module, "__checkpoint_save_public_key")(
            client.keypair.publicKey,
            str(paths["public_key"]),
        )
        if self.operation == "multiply":
            paths["eval_mult_keys"] = public / "eval_mult.keys"
            _binding(module, "__checkpoint_save_eval_mult_keys")(
                client.crypto_context,
                str(paths["eval_mult_keys"]),
            )

        column_records: dict[str, dict[str, Any]] = {}
        for index, (name, ciphertext) in enumerate(self.columns.items()):
            path = column_dir / f"column_{index:06d}.ct"
            _binding(module, "__checkpoint_save_ciphertexts")(
                _raw_ciphertext(ciphertext),
                str(path),
            )
            column_records[name] = _artifact_record(
                path,
                root=root,
                visibility="evaluator",
            )

        audit_secret: dict[str, Any] | None = None
        if include_audit_key:
            secret_path = private / "audit_secret.key"
            _binding(module, "__checkpoint_save_private_key")(
                client.keypair.secretKey,
                str(secret_path),
            )
            secret_path.chmod(0o600)
            audit_secret = _artifact_record(
                secret_path,
                root=root,
                visibility="client-only",
            )
            audit_secret.update(
                {
                    "required_for_evaluation": False,
                    "required_for_audit_decryption": True,
                }
            )

        public_records = {
            name: _artifact_record(
                path,
                root=root,
                visibility="evaluator",
            )
            for name, path in paths.items()
        }
        manifest = {
            "format": FORMAT,
            "scheme": "CKKS",
            "dataset_kind": "named_binary_columns",
            "binary_operation": self.operation,
            "column_order": list(self.columns),
            "columns": column_records,
            "width": self.program.width,
            "valid_count": self.valid_count,
            "input_scale": self.program.input_scale,
            "output_scale": self.program.output_scale,
            "mlir_sha256": _source_sha256(self.program.mlir),
            "context_fingerprint_sha256": _sha256(paths["context"]),
            "heir_py_version": _version("heir_py"),
            "openfhe_python_version": _version("openfhe"),
            "no_intermediate_decryption": True,
            "public_artifacts": public_records,
            "audit_secret": audit_secret,
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        self.manifest = manifest
        self.checkpoint_dir = root
        return manifest

    @classmethod
    def load(
        cls,
        checkpoint_dir: Path,
        *,
        for_audit: bool = False,
        debug: bool = False,
    ) -> "EncryptedDataset":
        """Validate, compile, and restore a saved encrypted dataset."""
        root = checkpoint_dir.resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"encrypted dataset manifest is missing: {manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != FORMAT:
            raise RuntimeError("unsupported encrypted dataset format")
        if manifest.get("dataset_kind") != "named_binary_columns":
            raise RuntimeError("unsupported encrypted dataset kind")
        saved_heir_version = manifest.get("heir_py_version", "unknown")
        current_heir_version = _version("heir_py")
        if (
            saved_heir_version != "unknown"
            and current_heir_version != saved_heir_version
        ):
            raise RuntimeError(
                "encrypted dataset HEIR version mismatch: saved "
                f"{saved_heir_version}, current {current_heir_version}"
            )

        def checked_path(record: Mapping[str, Any], label: str) -> Path:
            path = Path(str(record["path"]))
            if not path.is_absolute():
                path = root / path
            if not path.is_file():
                raise FileNotFoundError(
                    f"encrypted dataset artifact is missing: {path}"
                )
            if _sha256(path) != record["sha256"]:
                raise RuntimeError(
                    f"encrypted dataset artifact hash mismatch: {label}"
                )
            return path

        public_paths = {
            name: checked_path(record, name)
            for name, record in manifest["public_artifacts"].items()
        }
        column_order = tuple(manifest["column_order"])
        if len(column_order) != 2 or set(column_order) != set(
            manifest["columns"]
        ):
            raise RuntimeError("encrypted dataset column manifest is invalid")
        column_paths = {
            name: checked_path(manifest["columns"][name], f"column:{name}")
            for name in column_order
        }
        if (
            _sha256(public_paths["context"])
            != manifest["context_fingerprint_sha256"]
        ):
            raise RuntimeError("encrypted dataset context fingerprint mismatch")

        program = compile_checkpointable_binary_column(
            operation=manifest["binary_operation"],
            width=int(manifest["width"]),
            input_scale=float(manifest["input_scale"]),
            debug=debug,
        )
        if _source_sha256(program.mlir) != manifest["mlir_sha256"]:
            raise RuntimeError(
                "saved dataset circuit does not match the compiled HEIR program"
            )
        module = _module(program)
        context = _binding(module, "__checkpoint_load_context")(
            str(public_paths["context"])
        )
        public_key = _binding(module, "__checkpoint_load_public_key")(
            str(public_paths["public_key"])
        )
        if "eval_mult_keys" in public_paths:
            _binding(module, "__checkpoint_load_eval_mult_keys")(
                context,
                str(public_paths["eval_mult_keys"]),
            )

        secret_key = None
        if for_audit:
            record = manifest.get("audit_secret")
            if not record:
                raise FileNotFoundError(
                    "encrypted dataset has no client audit key; save with "
                    "include_audit_key=True"
                )
            secret_path = checked_path(record, "audit_secret")
            secret_key = _binding(
                module,
                "__checkpoint_load_private_key",
            )(str(secret_path))

        program._program.crypto_context = context
        program._program.keypair = SimpleNamespace(
            publicKey=public_key,
            secretKey=secret_key,
        )
        program._is_setup = True
        columns = {
            name: _binding(
                module,
                "__checkpoint_load_ciphertexts",
            )(str(column_paths[name]))
            for name in column_order
        }
        return cls(
            program=program,
            columns=columns,
            valid_count=int(manifest["valid_count"]),
            manifest=manifest,
            checkpoint_dir=root,
        )
