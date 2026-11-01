"""Pinned real BGE-M3 backend used by retrieval-facing tests.

This module deliberately has no substitute or hash-based encoder.  Tests that
exercise retrieval must use the same pinned BGE-M3 model family as the
operator evaluation path.  The backend is loaded lazily and shared within a
process so a test suite does not repeatedly load the multi-gigabyte snapshot.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.index import DevelopmentFlagEmbeddingBackend

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _ensure_env_loaded() -> None:
    if not os.environ.get("NEE_ENV_LOADED"):
        env_file = _REPOSITORY_ROOT / "env_di"
        if env_file.is_file():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_file)
                os.environ["NEE_ENV_LOADED"] = "1"
            except ImportError:
                pass


_MODEL_POLICY_PATH = _REPOSITORY_ROOT / "requirements" / "bge-m3-model.json"
_MODEL_POLICY = json.loads(_MODEL_POLICY_PATH.read_text(encoding="utf-8"))
_REVISION = str(_MODEL_POLICY["revision"])
_LOCAL_MODEL_ROOT = _REPOSITORY_ROOT / "models" / "bge-m3" / _REVISION
_LOCAL_MANIFEST = _REPOSITORY_ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{_REVISION}.json"
_FALLBACK_MODEL_ROOT = Path(r"C:\NEE\models\bge-m3") / _REVISION
_FALLBACK_MANIFEST = Path(r"C:\NEE\models\bge-m3") / f"bge-m3-manifest-{_REVISION}.json"
_DEFAULT_MODEL_ROOT = _LOCAL_MODEL_ROOT if _LOCAL_MODEL_ROOT.exists() else _FALLBACK_MODEL_ROOT
_DEFAULT_MANIFEST = _LOCAL_MANIFEST if _LOCAL_MANIFEST.exists() else _FALLBACK_MANIFEST
_BACKEND: DevelopmentFlagEmbeddingBackend | None = None
_BACKEND_LOCK = threading.RLock()
_VERIFIED_SNAPSHOT: dict[str, Any] | None = None


def _real_backend() -> DevelopmentFlagEmbeddingBackend:
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _ensure_env_loaded()
                model_root = Path(
                    os.environ.get("NEE_BGE_M3_MODEL_PATH", str(_DEFAULT_MODEL_ROOT))
                ).expanduser()
                manifest = Path(
                    os.environ.get("NEE_BGE_M3_MODEL_MANIFEST", str(_DEFAULT_MANIFEST))
                ).expanduser()
                _BACKEND = DevelopmentFlagEmbeddingBackend(
                    str(model_root),
                    _REVISION,
                    str(manifest),
                    device=os.environ.get("NEE_EMBEDDING_DEVICE", "auto"),
                )
    return _BACKEND


class BgeM3TestBackend:
    """Lazy proxy for the pinned, real BGE-M3 development backend."""

    supports_batch_encoding = True
    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = _REVISION
    production_qualified = False
    assurance_tier = "development"
    retrieval_encoder = "real-bge-m3"
    model_manifest_digest = str(_MODEL_POLICY["expected_manifest_digest"])

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "encode" in cls.__dict__ and "encode_many" not in cls.__dict__:
            cls.supports_batch_encoding = False

    def __init__(self) -> None:
        if os.environ.get("NEE_RUN_BGE_TESTS") != "1":
            raise unittest.SkipTest("real-BGE tests disabled; set NEE_RUN_BGE_TESTS=1 explicitly")

    def encode(self, text: str) -> dict[str, Any]:
        return _real_backend().encode(text)

    def encode_many(self, texts: list[str]) -> list[dict[str, Any]]:
        return _real_backend().encode_many(texts)

    def encode_dense_only(self, text: str) -> list[float]:
        return _real_backend().encode_dense_only(text)

    def encode_many_dense_only(
        self, texts: list[str], *, batch_size: int | None = None
    ) -> list[list[float]]:
        return _real_backend().encode_many_dense_only(texts, batch_size=batch_size)

    def verify_snapshot(self) -> dict[str, Any]:
        # The concrete backend verifies the full immutable snapshot when it is
        # created.  Retain that verified result for this test process so a
        # large suite does not rehash the same pinned model for every case.
        # This cache contains only manifest metadata; encode() always invokes
        # the real BGE-M3 model above.
        global _VERIFIED_SNAPSHOT
        if _VERIFIED_SNAPSHOT is None:
            with _BACKEND_LOCK:
                if _VERIFIED_SNAPSHOT is None:
                    _VERIFIED_SNAPSHOT = _real_backend().verify_snapshot()
        return dict(_VERIFIED_SNAPSHOT)

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_backend(), name)


class MetadataOnlyBgeM3Backend:
    """BGE-shaped metadata double for controls that must never encode text."""

    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = _REVISION
    production_qualified = False
    assurance_tier = "development"
    retrieval_encoder = "metadata-only"
    model_manifest_digest = str(_MODEL_POLICY["expected_manifest_digest"])

    def encode(self, text: str) -> dict[str, Any]:
        del text
        raise AssertionError("metadata-only BGE test double must not encode text")

    def verify_snapshot(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": self.model_manifest_digest,
        }
