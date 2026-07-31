"""Checkpointed CKKS-to-FHEW MAX through the official OpenFHE Python API.

The completed route is:

``parents -> encrypt -> subtract -> MAX -> serialize -> final audit decrypt``.

OpenFHE Python exposes ordinary context, key, evaluation-key, and ciphertext
serialization.  It does not expose the C++ ``SchemeSwitchingDataSerializer``
used to resume in the middle of scheme switching.  This checkpoint therefore
supports validation/reuse of a completed encrypted MAX result, not resuming a
partially executed comparison tree.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import time
from typing import Any

from code.heir.python_api.official_openfhe_minmax import (
    OfficialOpenFheMinMax,
    _load_openfhe,
    _next_power_of_two,
)


FORMAT = "openfhe-python-max-checkpoint-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record(path: Path, root: Path, visibility: str) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(root)),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "visibility": visibility,
    }


def _serialize(of: Any, path: Path, value: Any, label: str) -> None:
    if not of.SerializeToFile(str(path), value, of.BINARY):
        raise RuntimeError(f"OpenFHE Python could not serialize {label}")


def _package_version() -> str:
    try:
        return importlib.metadata.version("openfhe")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


class OpenFhePythonColumnMaxCheckpoint:
    """Evaluate and persist encrypted binary subtraction followed by MAX."""

    def __init__(
        self,
        *,
        input_scale: float,
        ring_dimension: int = 16384,
    ) -> None:
        if input_scale <= 0 or not math.isfinite(input_scale):
            raise ValueError("input_scale must be finite and positive")
        if ring_dimension < 4 or ring_dimension & (ring_dimension - 1):
            raise ValueError("ring_dimension must be a power of two")
        self.input_scale = float(input_scale)
        self.ring_dimension = ring_dimension

    def load_completed(self, output_dir: Path) -> dict[str, object]:
        """Validate and reuse an already completed encrypted MAX checkpoint."""
        root = output_dir.resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"completed OpenFHE-Python MAX manifest is missing: "
                f"{manifest_path}"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != FORMAT:
            raise RuntimeError("unsupported OpenFHE-Python MAX checkpoint")
        if float(manifest["input_scale"]) != self.input_scale:
            raise RuntimeError("MAX checkpoint input scale does not match")
        if int(manifest["ring_dimension"]) != self.ring_dimension:
            raise RuntimeError("MAX checkpoint ring dimension does not match")
        for name, record in manifest["artifacts"].items():
            path = root / record["path"]
            if not path.is_file():
                raise FileNotFoundError(
                    f"MAX checkpoint artifact is missing: {path}"
                )
            if _sha256(path) != record["sha256"]:
                raise RuntimeError(
                    f"MAX checkpoint artifact hash mismatch: {name}"
                )
        audit_path = (
            root
            / manifest["artifacts"]["maximum_audit"]["path"]
        )
        result = json.loads(audit_path.read_text(encoding="utf-8"))
        result.update(
            {
                "maximum": (
                    float(result["maximum_normalized"]) * self.input_scale
                ),
                "backend": "official OpenFHE Python",
                "openfhe_python_version": manifest[
                    "openfhe_python_version"
                ],
                "resumed": True,
            }
        )
        return result

    def run_subtract_max(
        self,
        left: Sequence[float],
        right: Sequence[float],
        *,
        output_dir: Path,
        overwrite: bool = False,
    ) -> dict[str, object]:
        """Run encrypted subtraction/MAX and save its completed audit bundle."""
        branch_started = time.perf_counter()
        left_values = [float(value) for value in left]
        right_values = [float(value) for value in right]
        if not left_values or len(left_values) != len(right_values):
            raise ValueError(
                "left and right must have the same non-zero length"
            )
        if not all(
            math.isfinite(value)
            for value in left_values + right_values
        ):
            raise ValueError("parent columns must not contain NaN or infinity")

        real_count = len(left_values)
        candidate_count = _next_power_of_two(real_count)
        if candidate_count > self.ring_dimension // 2:
            raise ValueError(
                "ring_dimension cannot hold the padded MAX candidates"
            )
        padding_count = candidate_count - real_count
        left_values.extend([left_values[0]] * padding_count)
        right_values.extend([right_values[0]] * padding_count)

        root = output_dir.resolve()
        if root.exists():
            if not overwrite:
                raise FileExistsError(
                    f"refusing to overwrite OpenFHE-Python MAX output: {root}"
                )
            if root == Path(root.anchor) or root == Path.home().resolve():
                raise ValueError(f"refusing to remove broad path: {root}")
            shutil.rmtree(root)
        public = root / "public"
        ciphertexts = root / "ciphertexts"
        private = root / "client_private"
        public.mkdir(parents=True)
        ciphertexts.mkdir()
        private.mkdir()

        engine = OfficialOpenFheMinMax(
            valid_count=candidate_count,
            input_scale=self.input_scale,
            ring_dimension=self.ring_dimension,
        )
        started = time.perf_counter()
        engine.setup()
        setup_seconds = time.perf_counter() - started

        started = time.perf_counter()
        left_ct = engine.encrypt(left_values)
        right_ct = engine.encrypt(right_values)
        parent_encrypt_seconds = time.perf_counter() - started

        started = time.perf_counter()
        derived_ct = engine._context.EvalSub(left_ct, right_ct)
        subtraction_seconds = time.perf_counter() - started

        started = time.perf_counter()
        maximum_ct = engine.eval_max(derived_ct)
        maximum_seconds = time.perf_counter() - started

        of = _load_openfhe()
        paths = {
            "context": public / "context.bin",
            "public_key": public / "public.key",
            "eval_automorphism_keys": public / "eval_automorphism.keys",
            "left_ciphertext": ciphertexts / "left_parent.ct",
            "right_ciphertext": ciphertexts / "right_parent.ct",
            "derived_ciphertext": ciphertexts / "derived_subtraction.ct",
            "maximum_ciphertext": ciphertexts / "maximum.ct",
            "audit_secret": private / "audit_secret.key",
            "maximum_audit": private / "maximum_audit.json",
        }
        started = time.perf_counter()
        _serialize(of, paths["context"], engine._context, "CKKS context")
        _serialize(
            of,
            paths["public_key"],
            engine._keys.publicKey,
            "public key",
        )
        if not engine._context.SerializeEvalAutomorphismKey(
            str(paths["eval_automorphism_keys"]),
            of.BINARY,
        ):
            raise RuntimeError(
                "OpenFHE Python could not serialize MAX automorphism keys"
            )
        _serialize(
            of,
            paths["left_ciphertext"],
            left_ct,
            "left parent ciphertext",
        )
        _serialize(
            of,
            paths["right_ciphertext"],
            right_ct,
            "right parent ciphertext",
        )
        _serialize(
            of,
            paths["derived_ciphertext"],
            derived_ct,
            "derived subtraction ciphertext",
        )
        _serialize(
            of,
            paths["maximum_ciphertext"],
            maximum_ct,
            "maximum ciphertext",
        )
        _serialize(
            of,
            paths["audit_secret"],
            engine._keys.secretKey,
            "client audit secret key",
        )
        paths["audit_secret"].chmod(0o600)
        serialize_seconds = time.perf_counter() - started

        # The only decryption happens after maximum.ct and its compatible
        # context/key bundle are safely on disk.
        started = time.perf_counter()
        plaintext = engine._context.Decrypt(
            engine._keys.secretKey,
            maximum_ct,
        )
        plaintext.SetLength(1)
        maximum_normalized = float(
            plaintext.GetRealPackedValue()[0]
        )
        audit_seconds = time.perf_counter() - started

        timings = {
            "context_and_switching_key_setup": setup_seconds,
            "parent_encrypt": parent_encrypt_seconds,
            "derived_subtraction": subtraction_seconds,
            "maximum_switch": maximum_seconds,
            "checkpoint_serialize": serialize_seconds,
            # Backward-compatible metric name used by the current report.
            "ciphertext_serialize": serialize_seconds,
            "audit_decrypt": audit_seconds,
            "branch_total": time.perf_counter() - branch_started,
        }
        result: dict[str, object] = {
            "maximum_normalized": maximum_normalized,
            "maximum": maximum_normalized * self.input_scale,
            "candidate_count": candidate_count,
            "ring_dimension": int(engine._context.GetRingDimension()),
            "multiplicative_depth": engine.multiplicative_depth,
            "real_count": real_count,
            "padding_count": padding_count,
            "argmax_retained": False,
            "backend": "official OpenFHE Python",
            "openfhe_python_version": _package_version(),
            "resumed": False,
            "timings_seconds": timings,
        }
        paths["maximum_audit"].write_text(
            json.dumps(result, indent=2) + "\n",
            encoding="utf-8",
        )

        artifacts = {
            name: _record(
                path,
                root,
                (
                    "client-only"
                    if name in {"audit_secret", "maximum_audit"}
                    else "evaluator"
                ),
            )
            for name, path in paths.items()
        }
        manifest = {
            "format": FORMAT,
            "scheme": "CKKS-to-FHEW-to-CKKS",
            "operation": "encrypted_subtract_then_maximum",
            "backend": "official OpenFHE Python",
            "openfhe_python_version": result["openfhe_python_version"],
            "input_scale": self.input_scale,
            "real_count": real_count,
            "candidate_count": candidate_count,
            "ring_dimension": self.ring_dimension,
            "multiplicative_depth": engine.multiplicative_depth,
            "no_intermediate_decryption": True,
            "completed_result_reload_supported": True,
            "mid_scheme_switch_resume_supported": False,
            "mid_scheme_switch_resume_note": (
                "OpenFHE Python does not expose the C++ "
                "SchemeSwitchingDataSerializer; rerun MAX if interrupted "
                "before maximum.ct is complete"
            ),
            "artifacts": artifacts,
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        return result
