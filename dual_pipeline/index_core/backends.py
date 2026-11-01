"""Embedding backend implementations and model manifest verification."""

from __future__ import annotations

import math
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Protocol, Tuple, runtime_checkable

import numpy as np

from config.credentials import CredentialManager
from dual_pipeline.assurance import (
    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    DEVELOPMENT,
    ENTERPRISE_PRODUCTION,
    INTERNAL_OPERATIONAL,
    load_internal_operational_policy,
    policy_digest,
    validate_internal_backend_policy,
)
from dual_pipeline.core import ROOT, digest, is_link_or_reparse, read_json
from dual_pipeline.hash_cache import cached_sha256_file
from dual_pipeline.model_registry import ModelTier, get_binding

from .constants import BGE_M3_MODEL_POLICY, DENSE_DIMENSIONS, MAX_COLBERT_TOKENS


def _resolve_bge_m3_model_id() -> str:
    if BGE_M3_MODEL_POLICY.exists():
        try:
            if mid := str(read_json(BGE_M3_MODEL_POLICY).get("model_id", "")).strip():
                return mid
        except Exception:
            pass
    return get_binding(ModelTier.EMBEDDING_MULTI_VECTOR).model_id


CANONICAL_BGE_M3_MODEL_ID: str = _resolve_bge_m3_model_id()
from .batching_optimizer import (
    clear_bge_manifest_cache, decode_colbert_matrix, decode_dense_array,
    get_cached_manifest_validation, prepare_length_sorted_batches,
)
from .manifest_validator import validate_bge_model_manifest

_BGEM3_CACHE_LOCK = threading.RLock()
_BGEM3_MODEL_CACHE: dict[tuple[str, str, str, bool, str], Any] = {}
_MPS_INFERENCE_LOCK = threading.RLock()


def clear_bge_m3_model_cache() -> None:
    """Clear cached in-memory BGEM3FlagModel instances."""
    with _BGEM3_CACHE_LOCK:
        _BGEM3_MODEL_CACHE.clear()


def _sync_mps_if_needed(device: str) -> None:
    if device == "mps":
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.synchronize()
        except Exception:
            pass



@runtime_checkable
class EmbeddingBackend(Protocol):
    backend_id: str
    model_id: str
    model_revision: str
    production_qualified: bool
    assurance_tier: str
    model_manifest_digest: str | None
    colbert_dimensions: int
    retrieval_encoder: str

    @property
    def encoding_fingerprint(self) -> dict[str, Any]: ...
    @property
    def encoding_fingerprint_digest(self) -> str: ...

    def encode(self, text: str) -> dict[str, Any]: ...
    def encode_dense_only(self, text: str) -> list[float]: ...
    def encode_many_dense_only(
        self, texts: Iterable[str], *, batch_size: int | None = None
    ) -> list[list[float]]: ...


from .recording_guard import is_live_recording_active


def _resolve_embedding_device(
    requested_device: str | None = None,
    *,
    default_device: str = "auto",
) -> str:
    env_device = CredentialManager.get_credential("NEE_EMBEDDING_DEVICE", "").strip().lower()
    target = (requested_device or env_device or default_device).casefold()
    if target in ("auto", ""):
        if is_live_recording_active(ROOT):
            return "cpu"
        try:
            import torch
            if torch.backends.mps.is_available():
                return "mps"
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"
    if target in ("mps", "cuda", "cpu"):
        if target == "mps" and requested_device is None and is_live_recording_active(ROOT):
            return "cpu"
        return target
    raise ValueError(f"unsupported embedding device: {requested_device or env_device}")


def _resolve_embedding_fp16(
    device: str, requested_fp16: bool | None = None
) -> bool:
    if requested_fp16 is not None:
        return requested_fp16
    env_fp16 = CredentialManager.get_credential("NEE_EMBEDDING_FP16", "").strip().lower()
    if env_fp16 in ("1", "true", "yes"):
        return True
    if env_fp16 in ("0", "false", "no"):
        return False
    return device in ("mps", "cuda")


class FlagEmbeddingBackend:
    """Optional enterprise-production BGE-M3 backend loaded only on explicit request."""

    backend_id = "flagembedding-bge-m3"
    model_id = CANONICAL_BGE_M3_MODEL_ID
    model_revision = "unconfigured"
    production_qualified = True
    assurance_tier = ENTERPRISE_PRODUCTION
    model_manifest_digest = None
    colbert_dimensions = 0
    retrieval_encoder = "real-bge-m3"
    supports_batch_encoding = True

    def __init__(
        self,
        model_path: str,
        model_revision: str | None,
        model_manifest: str | None,
        *,
        device: str | None = None,
        use_fp16: bool | None = None,
        default_device: str = "auto",
    ) -> None:
        supplied_model_path = Path(model_path).expanduser()
        if is_link_or_reparse(supplied_model_path):
            raise RuntimeError("BGE-M3 model root cannot be a link or reparse point")
        resolved_model_path = supplied_model_path.resolve()
        if not resolved_model_path.is_dir():
            raise RuntimeError("BGE-M3 generation requires --model-path to name a locally provisioned pinned model directory")
        if not model_revision or not re.fullmatch(r"[0-9a-f]{40}", model_revision):
            raise RuntimeError("BGE-M3 generation requires the immutable 40-hex model commit revision")
        if not model_manifest:
            raise RuntimeError("BGE-M3 generation requires --model-manifest for complete file verification")
        manifest_path_obj = Path(model_manifest)
        verified_manifest = get_cached_manifest_validation(
            resolved_model_path, manifest_path_obj, model_revision, validate_bge_model_manifest
        )
        pin = read_json(BGE_M3_MODEL_POLICY)
        if (
            pin.get("revision") != model_revision
            or pin.get("expected_manifest_digest") != verified_manifest["manifest_digest"]
            or pin.get("expected_snapshot_file_count") != verified_manifest["file_count"]
            or pin.get("expected_snapshot_size_bytes") != verified_manifest["snapshot_size_bytes"]
        ):
            raise RuntimeError("BGE-M3 snapshot does not match the tracked production model policy")
        try:
            from FlagEmbedding import BGEM3FlagModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency absent in CI artifact
            raise RuntimeError(
                f"BGE-M3 generation requires the FlagEmbedding dependency and a locally available pinned {CANONICAL_BGE_M3_MODEL_ID} model"
            ) from exc
        self.model_id = str(pin.get("model_id") or CANONICAL_BGE_M3_MODEL_ID)
        self.model_revision = model_revision
        self.model_manifest_digest = verified_manifest["manifest_digest"]
        self.model_path = str(resolved_model_path)
        self.model_manifest_path = str(Path(model_manifest).resolve())
        self._snapshot_file_count = verified_manifest["file_count"]
        self._snapshot_size_bytes = verified_manifest["snapshot_size_bytes"]
        resolved_device = _resolve_embedding_device(
            device, default_device=default_device
        )
        resolved_fp16 = _resolve_embedding_fp16(resolved_device, use_fp16)
        self.execution_device = resolved_device
        self.use_fp16 = resolved_fp16
        cache_key = (
            str(resolved_model_path),
            self.model_revision,
            self.model_manifest_digest,
            resolved_fp16,
            self.execution_device,
        )
        from dual_pipeline.l0.ingestion_timing import timed_child_span
        with timed_child_span("model_initialization", {"model_id": self.model_id, "device": self.execution_device}):
            with _BGEM3_CACHE_LOCK:
                warm_cache_hit = cache_key in _BGEM3_MODEL_CACHE
                if warm_cache_hit:
                    self._model = _BGEM3_MODEL_CACHE[cache_key]
                else:
                    self._model = BGEM3FlagModel(
                        str(resolved_model_path),
                        use_fp16=resolved_fp16,
                        devices=[self.execution_device],
                    )
                    _BGEM3_MODEL_CACHE[cache_key] = self._model
        self._residency_verified = False
        # Snapshot was verified via verified_manifest; no duplicate validation needed on cold miss

    def verify_snapshot(self) -> dict[str, Any]:
        """Reverify the exact snapshot after load and at release/query boundaries."""

        verified = get_cached_manifest_validation(
            Path(self.model_path),
            Path(self.model_manifest_path),
            self.model_revision,
            validate_bge_model_manifest,
        )
        if (
            verified["manifest_digest"] != self.model_manifest_digest
            or verified["file_count"] != self._snapshot_file_count
            or verified["snapshot_size_bytes"] != self._snapshot_size_bytes
        ):
            raise RuntimeError("BGE-M3 snapshot changed after its initial verification")
        return verified

    def _assert_device_residency(self, force: bool = False) -> None:
        if getattr(self, "_residency_verified", False) and not force:
            return
        devices: set[str] = set()
        for owner in (getattr(self._model, "model", None), self._model):
            if owner is None:
                continue
            for accessor_name in ("parameters", "buffers"):
                accessor = getattr(owner, accessor_name, None)
                if callable(accessor):
                    try:
                        iterator = iter(accessor())
                    except TypeError:
                        continue
                    for value in iterator:
                        if getattr(value, "device", None) is not None:
                            devices.add(str(value.device))
        if not devices:
            return
        expected = self.execution_device.casefold()
        for device in devices:
            dev_lower = device.casefold()
            if (
                (expected == "cpu" and dev_lower != "cpu")
                or (expected == "mps" and not dev_lower.startswith("mps"))
                or (expected == "cuda" and not dev_lower.startswith("cuda"))
            ):
                raise RuntimeError(
                    f"BGE-M3 parameters or buffers are not {expected.upper()}-resident: {sorted(devices)}"
                )
        self._residency_verified = True

    def _assert_cpu_residency(self, force: bool = False) -> None:
        self._assert_device_residency(force=force)

    def _decode_encoded_result(
        self, result: dict[str, Any], index: int
    ) -> dict[str, Any]:
        dense_raw = result["dense_vecs"][index]
        dense = decode_dense_array(dense_raw)
        sparse_raw = result["lexical_weights"][index]
        sparse = {str(key): float(value) for key, value in sparse_raw.items()}
        if not sparse or any(not math.isfinite(value) for value in sparse.values()):
            raise RuntimeError("BGE-M3 returned an empty or non-finite sparse vector")
        colbert_raw = result["colbert_vecs"][index]
        colbert = decode_colbert_matrix(colbert_raw)
        self.colbert_dimensions = len(colbert[0])
        tokens = [f"token-{token_index}" for token_index in range(len(colbert))]
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert_tokens": tokens,
            "colbert": colbert,
        }

    def encode_many(
        self, texts: Iterable[str], *, batch_size: int | None = None
    ) -> list[dict[str, Any]]:  # pragma: no cover - optional backend
        values = list(texts)
        if not values:
            return []
        sorted_unique, inverse_indices, _ = prepare_length_sorted_batches(values)
        if batch_size is not None:
            effective_batch_size = batch_size
        else:
            default_size = 64 if getattr(self, "execution_device", "cpu") in ("mps", "cuda") else 16
            effective_batch_size = min(default_size, len(sorted_unique))
        with _MPS_INFERENCE_LOCK:
            result = self._model.encode(
                sorted_unique, batch_size=effective_batch_size, return_dense=True, return_sparse=True, return_colbert_vecs=True,
            )
            _sync_mps_if_needed(getattr(self, "execution_device", "cpu"))
        self._assert_device_residency()
        result_lengths = {
            len(result.get(key, ()))
            for key in ("dense_vecs", "lexical_weights", "colbert_vecs")
        }
        if result_lengths != {len(sorted_unique)}:
            raise RuntimeError(
                "BGE-M3 returned a batch with inconsistent result cardinality"
            )
        decoded_unique = [
            self._decode_encoded_result(result, index)
            for index in range(len(sorted_unique))
        ]
        return [decoded_unique[idx] for idx in inverse_indices]

    def encode(self, text: str) -> dict[str, Any]:  # pragma: no cover - optional backend
        return self.encode_many([text])[0]

    def encode_many_dense_only(
        self, texts: Iterable[str], *, batch_size: int | None = None
    ) -> list[list[float]]:  # pragma: no cover - optional backend
        values = list(texts)
        if not values:
            return []
        sorted_unique, inverse_indices, _ = prepare_length_sorted_batches(values)
        if batch_size is not None:
            effective_batch_size = batch_size
        else:
            default_size = 64 if getattr(self, "execution_device", "cpu") in ("mps", "cuda") else 16
            effective_batch_size = min(default_size, len(sorted_unique))
        with _MPS_INFERENCE_LOCK:
            result = self._model.encode(
                sorted_unique, batch_size=effective_batch_size, return_dense=True, return_sparse=False, return_colbert_vecs=False,
            )
            _sync_mps_if_needed(getattr(self, "execution_device", "cpu"))
        self._assert_device_residency()
        dense_vecs = result.get("dense_vecs")
        if dense_vecs is None or len(dense_vecs) != len(sorted_unique):
            raise RuntimeError(
                "BGE-M3 returned a batch with inconsistent dense vector cardinality"
            )
        decoded_unique_dense = [
            decode_dense_array(np.asarray(dense_vecs[index], dtype=np.float32))
            for index in range(len(sorted_unique))
        ]
        return [decoded_unique_dense[idx] for idx in inverse_indices]

    def encode_dense_only(self, text: str) -> list[float]:  # pragma: no cover - optional backend
        return self.encode_many_dense_only([text])[0]

    def encode_many_colbert_only(
        self, texts: Iterable[str], *, batch_size: int | None = None
    ) -> list[list[list[float]]]:  # pragma: no cover - optional backend
        values = list(texts)
        if not values:
            return []
        sorted_unique, inverse_indices, _ = prepare_length_sorted_batches(values)
        if batch_size is not None:
            effective_batch_size = batch_size
        else:
            default_size = 64 if getattr(self, "execution_device", "cpu") in ("mps", "cuda") else 16
            effective_batch_size = min(default_size, len(sorted_unique))
        with _MPS_INFERENCE_LOCK:
            result = self._model.encode(
                sorted_unique, batch_size=effective_batch_size, return_dense=False, return_sparse=False, return_colbert_vecs=True,
            )
            _sync_mps_if_needed(getattr(self, "execution_device", "cpu"))
        self._assert_device_residency()
        colbert_vecs = result.get("colbert_vecs")
        if colbert_vecs is None or len(colbert_vecs) != len(sorted_unique):
            raise RuntimeError(
                "BGE-M3 returned a batch with inconsistent ColBERT vector cardinality"
            )
        decoded_unique_colbert = [
            decode_colbert_matrix(np.asarray(colbert_vecs[index], dtype=np.float32))
            for index in range(len(sorted_unique))
        ]
        return [decoded_unique_colbert[idx] for idx in inverse_indices]

    @property
    def encoding_fingerprint(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id, "model_revision": self.model_revision,
            "model_manifest_digest": self.model_manifest_digest,
            "precision": "float16" if self.use_fp16 else "float32",
            "device": self.execution_device, "tokenizer": self.model_id,
            "max_length": 8192, "truncation": True, "normalize_embeddings": True,
            "dense_dimensions": DENSE_DIMENSIONS, "max_colbert_tokens": MAX_COLBERT_TOKENS,
        }

    @property
    def encoding_fingerprint_digest(self) -> str:
        return digest(self.encoding_fingerprint)

    def encode_streaming_windows(
        self, texts: Iterable[str], *, window_size: int = 64, inference_batch_size: int | None = None,
    ) -> Iterable[list[dict[str, Any]]]:
        """Yields encoded result lists window-by-window to avoid accumulating full corpora in memory."""
        chunk: list[str] = []
        for text in texts:
            chunk.append(text)
            if len(chunk) >= window_size:
                yield self.encode_many(chunk, batch_size=inference_batch_size)
                chunk = []
        if chunk:
            yield self.encode_many(chunk, batch_size=inference_batch_size)


class DevelopmentFlagEmbeddingBackend(FlagEmbeddingBackend):
    """Real local BGE-M3 execution that is permanently non-production."""

    backend_id = "flagembedding-bge-m3-dev"
    production_qualified = False
    assurance_tier = DEVELOPMENT

    def __init__(
        self, model_path: str = CANONICAL_BGE_M3_MODEL_ID, model_revision: str | None = None, model_manifest: str | None = None,
        *, device: str | None = None, use_fp16: bool | None = None,
    ) -> None:
        super().__init__(model_path, model_revision, model_manifest, device=device, use_fp16=use_fp16, default_device="auto")


class InternalOperationalFlagEmbeddingBackend(FlagEmbeddingBackend):
    """Real local BGE-M3 governed for a trusted PartnerAdvisory team runtime."""

    backend_id = "flagembedding-bge-m3-internal"
    production_qualified = False
    assurance_tier = INTERNAL_OPERATIONAL

    def __init__(
        self,
        model_path: str,
        model_revision: str | None,
        model_manifest: str | None,
        internal_policy: str | Path,
        *,
        device: str | None = None,
        use_fp16: bool | None = None,
    ) -> None:
        policy_path = Path(internal_policy).expanduser().resolve()
        policy = load_internal_operational_policy(policy_path)
        super().__init__(
            model_path, model_revision, model_manifest, device=device, use_fp16=use_fp16, default_device="auto",
        )
        validate_internal_backend_policy(
            policy=policy, model_id=self.model_id, model_revision=self.model_revision,
            model_manifest_digest=self.model_manifest_digest,
        )
        self.internal_operational_policy_path = str(policy_path)
        self.internal_operational_policy_digest = policy_digest(policy)


def _resolve_bge_m3_config(
    model_path: str | None, model_revision: str | None, model_manifest: str | None
) -> Tuple[str, str | None, str | None]:
    path = model_path or CredentialManager.get_credential("NEE_BGE_M3_MODEL_PATH", "") or CANONICAL_BGE_M3_MODEL_ID
    rev = model_revision or CredentialManager.get_credential("NEE_BGE_M3_MODEL_REVISION", "") or None
    if rev is None and BGE_M3_MODEL_POLICY.exists():
        try:
            rev = read_json(BGE_M3_MODEL_POLICY).get("revision")
        except Exception:
            pass
    if rev is None and path and re.fullmatch(r"[0-9a-f]{40}", Path(path).name):
        rev = Path(path).name
    manifest = model_manifest or CredentialManager.get_credential("NEE_BGE_M3_MODEL_MANIFEST", "") or None
    if manifest is None and path and rev:
        cand = Path(path).parent / f"bge-m3-manifest-{rev}.json"
        if cand.exists():
            manifest = str(cand)
    return path, rev, manifest


def get_backend(
    name: str, *, allow_dev_backend: bool = False, allow_internal_backend: bool = False,
    model_path: str | None = None, model_revision: str | None = None, model_manifest: str | None = None,
    internal_policy: str | Path = DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    device: str | None = None, use_fp16: bool | None = None,
) -> EmbeddingBackend:
    path, rev, manifest = _resolve_bge_m3_config(model_path, model_revision, model_manifest)

    if name in ("bge-m3", "flagembedding-bge-m3"):
        return FlagEmbeddingBackend(path, rev, manifest, device=device, use_fp16=use_fp16)
    if name in ("bge-m3-dev", "flagembedding-bge-m3-dev"):
        if not allow_dev_backend:
            raise RuntimeError("the real BGE-M3 development backend is non-production and requires --allow-dev-bge-m3")
        return DevelopmentFlagEmbeddingBackend(path, rev, manifest, device=device, use_fp16=use_fp16)
    if name in ("bge-m3-internal", "flagembedding-bge-m3-internal"):
        if not allow_internal_backend:
            raise RuntimeError("the internal operational BGE-M3 backend requires --allow-internal-operational")
        return InternalOperationalFlagEmbeddingBackend(path, rev, manifest, internal_policy, device=device, use_fp16=use_fp16)
    raise ValueError(f"unsupported embedding backend: {name}")
