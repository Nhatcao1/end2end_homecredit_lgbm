from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code.heir.common import sha256_file
from code.heir.dag.contracts import STAGE_ORDER
from code.heir.dag.generated_backend import GeneratedCkksBackend
from code.heir.dag.pipeline import (
    dag_status,
    finalize_dag,
    initialize_dag,
    run_function_stage,
)
from code.heir.scripts import generate_dag_ckks_kernels as dag_generator
from tests import test_function_benchmarks as function_fixture


class FixtureBackend:
    """Test-only artifact producer; the production CLI cannot select this backend."""

    def __init__(self, generated_root: Path, build_root: Path, openfhe_dir: str = ""):
        self.generated_root = generated_root
        self.build_root = build_root

    def initialize_session(self, session_dir: Path, provider_kernel: str = "K03"):
        for relative, value in (
            ("public/crypto_context.bin", b"fixture-ckks-context"),
            ("public/public_key.bin", b"fixture-public-key"),
            ("public/evaluation_mult_keys.bin", b"fixture-mult-keys"),
            ("public/evaluation_rotation_keys.bin", b"fixture-rotation-keys"),
            ("client_private/secret_key.bin", b"fixture-secret-key"),
        ):
            path = session_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        return {
            "provider_kernel": provider_kernel,
            "generated_proof": {"ckks_only": True, "fixture": True},
            "timings_seconds": {"initializer_seconds": 0.0},
        }

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
    ):
        result_count = 1 if kernel_id == "K01" else 3
        output_dir.mkdir(parents=True)
        rows = []
        ciphertexts = []
        for app_index in range(applicant_count):
            for ordinal in range(result_count):
                path = output_dir / f"app_{app_index}_result_{ordinal}.ct"
                path.write_bytes(
                    f"fixture:{kernel_id}:{app_index}:{ordinal}".encode("ascii")
                )
                ciphertexts.append(str(path))
                rows.append(
                    {
                        "app_index": app_index,
                        "result_ordinal": ordinal,
                        "file": path.name,
                        "level": 1,
                        "scaling_factor": 2.0**40,
                    }
                )
        index = output_dir / "ciphertext_index.csv"
        with index.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=(
                    "app_index",
                    "result_ordinal",
                    "file",
                    "level",
                    "scaling_factor",
                ),
            )
            writer.writeheader()
            writer.writerows(rows)
        return {
            "kernel_id": kernel_id,
            "generated_proof": {"ckks_only": True, "fixture": True},
            "ciphertext_index": str(index),
            "ciphertext_files": ciphertexts,
            "timings_seconds": {
                "encryption_seconds": 0.0,
                "encrypted_evaluation_seconds": 0.0,
                "ciphertext_serialization_seconds": 0.0,
                "runner_wall_seconds": 0.0,
                "subprocess_wall_seconds": 0.0,
            },
            "peak_child_rss_kib": 1,
        }

    def continuity_probe(
        self, session_dir: Path, ciphertext: Path, output_path: Path
    ):
        output_path.write_bytes(ciphertext.read_bytes())
        return {
            "status": "ciphertext_deserialized_and_reserialized",
            "input_file": str(ciphertext),
            "output_file": str(output_path),
            "level": 1,
            "scaling_factor": 2.0**40,
            "seconds": 0.0,
            "output_sha256": sha256_file(output_path),
        }


def write_generation_manifest(path: Path, vector_size: int) -> None:
    path.mkdir(parents=True)
    for kernel_id in ("K01", "K02", "K03"):
        (path / kernel_id).mkdir()
    (path / "generation_manifest.json").write_text(
        json.dumps(
            {
                "status": "heir_generated_ckks_sources_ready",
                "scheme": "CKKS",
                "vector_size": vector_size,
                "kernels": [
                    {"kernel_id": kernel_id} for kernel_id in ("K01", "K02", "K03")
                ],
            }
        ),
        encoding="utf-8",
    )


class DagPipelineTest(unittest.TestCase):
    def test_generation_sets_ciphertext_degree_to_vector_size(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], output_path: Path) -> float:
            commands.append(command)
            output_path.write_text("// CKKS generated test source\n", encoding="utf-8")
            return 0.0

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "generated"
            with patch.object(dag_generator, "_run", side_effect=fake_run):
                manifest = dag_generator.generate(root, 8192, "heir-opt", "heir-translate")

        lowering = [command for command in commands if command[0] == "heir-opt"]
        self.assertEqual(len(lowering), 3)
        self.assertTrue(
            all(
                "--mlir-to-ckks=ciphertext-degree=8192" in command
                for command in lowering
            )
        )
        self.assertEqual(manifest["ciphertext_degree"], 8192)

    def test_production_evaluator_has_no_secret_key_or_decryption_path(self) -> None:
        backend = GeneratedCkksBackend(
            Path("generated"), Path("build"), openfhe_dir=""
        )
        for kernel_id in ("K01", "K02", "K03"):
            source = backend._evaluation_source(kernel_id)
            self.assertIn("SerializeToFile", source)
            self.assertIn("public_key.bin", source)
            self.assertIn("DeserializeEvalMultKey", source)
            self.assertNotIn("secret_key.bin", source)
            self.assertNotIn("__decrypt__", source)
            self.assertNotIn("PrivateKey", source)
            self.assertNotRegex(source, r"@[A-Z_]+@")

    def initialize(self, root: Path) -> Path:
        data, application = function_fixture.FunctionBenchmarkTest().make_data(root)
        generated = root / "generated"
        write_generation_manifest(generated, 8)
        run_root = root / "dag" / "test_run"
        initialize_dag(
            run_root,
            application_path=application,
            data_dir=data,
            generated_root=generated,
            openfhe_dir="",
            vector_size=8,
            application_row_limit=0,
            source_row_limit=0,
        )
        return run_root

    @patch("code.heir.dag.pipeline.GeneratedCkksBackend", FixtureBackend)
    def test_serial_dag_persists_and_finalizes_every_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_root = self.initialize(Path(temp))
            with self.assertRaisesRegex(ValueError, "completion marker"):
                run_function_stage(run_root, "previous")

            for stage in STAGE_ORDER:
                manifest = run_function_stage(run_root, stage)
                self.assertEqual(manifest["bundle_status"], "encrypted_complete")
                self.assertTrue(manifest["ciphertext_files"])
                self.assertTrue(
                    (run_root / "stages" / f"{STAGE_ORDER.index(stage) + 1:02d}_{stage}" / "COMPLETED.json").is_file()
                )
                resumed = run_function_stage(run_root, stage, resume=True)
                self.assertEqual(resumed["session_id"], manifest["session_id"])

            final = finalize_dag(run_root)
            self.assertEqual(final["status"], "encrypted_end_to_end_complete")
            self.assertEqual(len(final["function_bundles"]), 5)
            self.assertTrue(final["ciphertext_files"])
            status = dag_status(run_root)
            self.assertTrue(status["finalized"])
            self.assertTrue(
                all(item["status"] == "encrypted_complete" for item in status["stages"])
            )

    @patch("code.heir.dag.pipeline.GeneratedCkksBackend", FixtureBackend)
    def test_resume_rejects_tampered_ciphertext(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_root = self.initialize(Path(temp))
            manifest = run_function_stage(run_root, "bureau")
            stage_dir = run_root / "stages" / "01_bureau"
            ciphertext = stage_dir / manifest["ciphertext_files"][0]["file"]
            ciphertext.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "artifact (size|hash) changed"):
                run_function_stage(run_root, "bureau", resume=True)

    @patch("code.heir.dag.pipeline.GeneratedCkksBackend", FixtureBackend)
    def test_init_rejects_generated_vector_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, application = function_fixture.FunctionBenchmarkTest().make_data(root)
            generated = root / "generated"
            write_generation_manifest(generated, 16)
            with self.assertRaisesRegex(ValueError, "vector size differs"):
                initialize_dag(
                    root / "dag",
                    application_path=application,
                    data_dir=data,
                    generated_root=generated,
                    openfhe_dir="",
                    vector_size=8,
                    application_row_limit=0,
                    source_row_limit=0,
                )


if __name__ == "__main__":
    unittest.main()
