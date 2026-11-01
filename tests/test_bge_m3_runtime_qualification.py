#!/usr/bin/env python3
"""Controls for the real, pinned BGE-M3 runtime qualification tool."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from dual_pipeline import index as index_module
from dual_pipeline.core import digest, sha256_file, write_json
from tools import qualify_bge_m3_runtime as qualification

ROOT = Path(__file__).resolve().parents[1]
PINNED_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
_LOCAL_MODEL_ROOT = ROOT / "models" / "bge-m3" / PINNED_REVISION
_LOCAL_MANIFEST = ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{PINNED_REVISION}.json"
_FALLBACK_MODEL_ROOT = rf"C:\NEE\models\bge-m3\{PINNED_REVISION}"
_FALLBACK_MANIFEST = rf"C:\NEE\models\bge-m3\bge-m3-manifest-{PINNED_REVISION}.json"
MODEL_ROOT = Path(
    os.environ.get(
        "NEE_BGE_M3_MODEL_PATH",
        str(_LOCAL_MODEL_ROOT if _LOCAL_MODEL_ROOT.exists() else _FALLBACK_MODEL_ROOT),
    )
)
MODEL_MANIFEST = Path(
    os.environ.get(
        "NEE_BGE_M3_MODEL_MANIFEST",
        str(_LOCAL_MANIFEST if _LOCAL_MANIFEST.exists() else _FALLBACK_MANIFEST),
    )
)


@pytest.mark.heavy_model
class BgeM3RuntimeQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-bge-qualification-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unpinned_revision_fails_before_model_initialization(self) -> None:
        with self.assertRaises(qualification.QualificationFailure) as raised:
            qualification.qualify(
                model_path=self.root / "not-used",
                model_revision="a" * 40,
                model_manifest=self.root / "not-used.json",
            )
        self.assertEqual("MODEL_REVISION_NOT_PINNED", raised.exception.code)

    def test_runtime_validation_uses_cpu_contract(self) -> None:
        with (
            mock.patch(
                "dual_pipeline.preflight.check_runtime_contract",
                return_value={"status": "PASS"},
            ),
            mock.patch(
                "dual_pipeline.preflight.check_pip_dependencies",
                return_value={"status": "PASS"},
            ),
            mock.patch(
                "dual_pipeline.preflight.check_locked_environment",
                return_value={"status": "PASS"},
            ),
            mock.patch(
                "dual_pipeline.preflight.check_cpu",
                return_value={"status": "PASS"},
            ),
        ):
            checks = qualification._validate_runtime_contract()
        self.assertEqual("PASS", checks["cpu_contract"]["status"])
        self.assertNotIn("cuda_contract", checks)

    def test_cli_writes_fail_receipt_and_returns_nonzero(self) -> None:
        output = self.root / "failed-receipt.json"
        exit_code = qualification.main(
            [
                "--model-path",
                str(self.root / "not-used"),
                "--model-revision",
                "a" * 40,
                "--model-manifest",
                str(self.root / "not-used.json"),
                "--output",
                str(output),
            ]
        )
        self.assertEqual(1, exit_code)
        receipt = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("FAIL", receipt["status"])
        self.assertEqual("MODEL_REVISION_NOT_PINNED", receipt["failure"]["code"])
        self.assertFalse(receipt["production_release_authorized"])

    def test_manifest_validation_detects_mutated_snapshot_bytes(self) -> None:
        model = self.root / "model"
        model.mkdir()
        payload = model / "model.safetensors"
        payload.write_bytes(b"original")
        manifest_value = {
            "schema_version": "1.0.0",
            "model_id": "BAAI/bge-m3",
            "model_revision": PINNED_REVISION,
            "files": [
                {
                    "path": payload.name,
                    "size_bytes": payload.stat().st_size,
                    "sha256": sha256_file(payload),
                }
            ],
        }
        manifest_value["manifest_digest"] = digest(manifest_value)
        manifest = self.root / "manifest.json"
        write_json(manifest, manifest_value)
        payload.write_bytes(b"mutated")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            index_module.validate_bge_model_manifest(model, manifest, PINNED_REVISION)

    @unittest.skipUnless(
        os.environ.get("NEE_RUN_REAL_BGE_QUALIFICATION") == "1",
        "set NEE_RUN_REAL_BGE_QUALIFICATION=1 to qualify the pinned local model",
    )
    def test_real_pinned_bge_qualification(self) -> None:
        receipt = qualification.qualify(
            model_path=MODEL_ROOT,
            model_revision=PINNED_REVISION,
            model_manifest=MODEL_MANIFEST,
        )
        self.assertEqual("PASS", receipt["status"])
        self.assertEqual(PINNED_REVISION, receipt["model"]["model_revision"])
        self.assertTrue(
            all(
                item["determinism"]["within_tolerance"]
                for item in receipt["qualification"]["cases"]
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
