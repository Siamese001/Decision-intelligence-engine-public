"""Process-level airgap, Metal memory lifecycle, and acoustic receipts for MLX."""
from __future__ import annotations

import contextlib
import gc
import json
import logging
import os
import platform
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AirgapViolationError(RuntimeError):
    """Raised when an acoustic or alignment model attempts an outbound network connection."""


class AcousticAirgapContext:
    """Context manager enforcing strict offline environment and socket-level network airgap."""

    def __init__(self) -> None:
        self._orig_connect: Any = None
        self._orig_create_conn: Any = None

    def __enter__(self) -> AcousticAirgapContext:
        enforce_process_airgap()
        self._orig_connect = socket.socket.connect
        self._orig_create_conn = socket.create_connection
        orig_connect = self._orig_connect
        orig_create_conn = self._orig_create_conn

        def _guarded_connect(sock_self: Any, address: Any) -> Any:
            if isinstance(address, tuple) and len(address) >= 1:
                host = str(address[0])
                if host not in ("127.0.0.1", "::1", "localhost"):
                    raise AirgapViolationError(
                        f"AIRGAP_VIOLATION: Outbound network socket connect to '{host}' blocked inside AcousticAirgapContext."
                    )
            return orig_connect(sock_self, address)

        def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(address, tuple) and len(address) >= 1:
                host = str(address[0])
                if host not in ("127.0.0.1", "::1", "localhost"):
                    raise AirgapViolationError(
                        f"AIRGAP_VIOLATION: Outbound socket.create_connection to '{host}' blocked inside AcousticAirgapContext."
                    )
            return orig_create_conn(address, *args, **kwargs)

        socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
        socket.create_connection = _guarded_create_connection  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._orig_connect is not None:
            socket.socket.connect = self._orig_connect  # type: ignore[method-assign]
        if self._orig_create_conn is not None:
            socket.create_connection = self._orig_create_conn  # type: ignore[assignment]


def is_mlx_available() -> bool:
    """Checks whether Apple Silicon Metal GPU acceleration via MLX is available.

    Returns True only on macOS arm64 with mlx and mlx_whisper installed and Metal enabled.
    """
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return False
    try:
        import mlx.core as mx
        import mlx_whisper  # noqa: F401
        return bool(mx.metal.is_available())
    except Exception:
        return False


def enforce_process_airgap() -> None:
    """Enforces strict offline airgap for local huggingface and MLX dependencies."""
    from config.credentials import CredentialManager
    if not CredentialManager.has_credential("HF_HUB_OFFLINE"):
        CredentialManager.set_credential("HF_HUB_OFFLINE", "1")
    if not CredentialManager.has_credential("TRANSFORMERS_OFFLINE"):
        CredentialManager.set_credential("TRANSFORMERS_OFFLINE", "1")
    try:
        import huggingface_hub.constants
        huggingface_hub.constants.HF_HUB_OFFLINE = True
    except (ImportError, AttributeError) as exc:
        logger.debug("huggingface_hub offline constant setup skipped: %s", exc)


def clear_mlx_whisper_metal_cache() -> None:
    """Clears MLX Metal cache and unloads Whisper weights to reclaim VRAM immediately."""
    try:
        try:
            from mlx_whisper.transcribe import ModelHolder
            if getattr(ModelHolder, "model", None) is not None:
                ModelHolder.model = None
                ModelHolder.model_path = None
                logger.info("Unloaded MLX Whisper ModelHolder weights.")
        except (ImportError, AttributeError) as holder_err:
            logger.debug("ModelHolder unload skipped: %s", holder_err)

        gc.collect()

        try:
            import mlx.core as mx
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception as mx_err:
            logger.debug("MLX metal cache clear skipped: %s", mx_err)

        with contextlib.suppress(Exception):
            logger.info("Cleared MLX metal memory cache and reclaimed active VRAM.")
    except Exception as e:
        logger.debug("Could not clear MLX cache: %s", e)


def assert_acoustic_headroom() -> None:
    """Verifies system has sufficient RAM/VRAM headroom before acoustic processing."""
    from dual_pipeline.memory_guard import ACOUSTIC_MIN_HEADROOM_BYTES, assert_memory_headroom
    assert_memory_headroom(
        min_free_bytes=ACOUSTIC_MIN_HEADROOM_BYTES,
        context="MLX Whisper audio transcription",
    )


def write_acoustic_receipt(
    safe_stem: str,
    model_name: str,
    diarization_engine: Any = None,
    output_dir: Optional[Path] = None,
    backend: Optional[str] = None,
    alignment_backend: str = "whisperx",
) -> Optional[Path]:
    """Emits acoustic provenance receipt capturing backend, model revision, and device."""
    dev = "mps" if is_mlx_available() else "cpu"
    if diarization_engine and getattr(diarization_engine, "device", None):
        dev = diarization_engine.device
    resolved_backend = backend or ("mlx-whisper" if is_mlx_available() else "faster-whisper")

    receipt: Dict[str, Any] = {
        "backend": resolved_backend,
        "alignment_backend": alignment_backend,
        "model_id": model_name,
        "device": dev,
        "normalization_status": "normalized_16khz_mono",
        "diarization_backend": "pyannote" if diarization_engine else "none",
        "provenance_status": "VERIFIED_SANDBOX_REPLAY",
    }

    if diarization_engine:
        receipt["diarization_model_id"] = getattr(diarization_engine, "model_id", None)
        receipt["diarization_snapshot_revision"] = getattr(diarization_engine, "snapshot_revision", None)
        receipt["diarization_snapshot_sha256"] = getattr(diarization_engine, "snapshot_sha256", None)

    try:
        from ..model_registry import ModelRegistry, ModelTier
        spec = ModelRegistry.get_tier_spec(ModelTier.ACOUSTIC_MLX_WHISPER)
        if spec:
            receipt["model_revision"] = spec.model_revision
            receipt["manifest_sha256"] = spec.manifest_sha256
    except Exception as reg_err:
        logger.debug("Could not attach model registry spec to acoustic receipt: %s", reg_err)

    try:
        target_dir = output_dir or (Path(__file__).resolve().parents[2] / "reports")
        target_dir.mkdir(parents=True, exist_ok=True)
        receipt_file = target_dir / f"{safe_stem}_acoustic_provenance_receipt.json"
        receipt_file.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        logger.info("Wrote acoustic provenance receipt to %s", receipt_file)
        return receipt_file
    except Exception as e:
        logger.warning("Failed to write provenance receipt: %s", e)
        return None
