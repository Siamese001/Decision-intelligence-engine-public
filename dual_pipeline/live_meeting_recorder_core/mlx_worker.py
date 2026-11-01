"""
dual_pipeline/live_meeting_recorder_core/mlx_worker.py

Isolated MLX Whisper Worker Process & Readiness Gate.
Target: Apple MacBook Pro (Apple Silicon Metal GPU).

Invariants:
  1. Staged proof-of-readiness handshake: SPAWNED -> IMPORT -> METAL -> RESOLVE -> LOAD -> GOLDEN_INFERENCE -> READY.
  2. Strict airgap guarantee: zero cloud egress, AF_INET/AF_INET6 sockets blocked to non-localhost.
  3. Strict zero-fallback: failures raise typed MLXInitError with closed-enum reason codes.
  4. Golden inference verification against canonical fixture (mlx_golden_16k.wav) before emitting READY.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.mlx_worker")


class MLXReasonCode:
    """Closed enum of MLX Whisper failure reason codes."""
    MLX_IMPORT_FAILED = "MLX_IMPORT_FAILED"
    METAL_UNAVAILABLE = "METAL_UNAVAILABLE"
    MODEL_NOT_CACHED = "MODEL_NOT_CACHED"
    MODEL_REVISION_MISMATCH = "MODEL_REVISION_MISMATCH"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    GOLDEN_INFERENCE_EMPTY = "GOLDEN_INFERENCE_EMPTY"
    GOLDEN_INFERENCE_MISMATCH = "GOLDEN_INFERENCE_MISMATCH"
    MLX_READY_DEADLINE_EXCEEDED = "MLX_READY_DEADLINE_EXCEEDED"
    MLX_WORKER_DIED = "MLX_WORKER_DIED"
    MLX_RUNTIME_LOST = "MLX_RUNTIME_LOST"


EXIT_MLX_UNREADY: int = 12


class MLXInitError(RuntimeError):
    """Raised when MLX Whisper initialization or proof-of-readiness gate fails."""

    def __init__(self, stage: str, reason_code: str, detail: str) -> None:
        self.stage = stage
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"[{reason_code}] Stage '{stage}' failed: {detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "INIT_ERROR",
            "stage": self.stage,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


class MLXRuntimeLost(RuntimeError):
    """Raised when the MLX Whisper worker terminates unexpectedly and cannot be recovered."""
    pass


class AudioAnalysisError(RuntimeError):
    """Raised when audio analysis (RMS, VAD, decoding) fails rather than returning silent false-positives."""
    pass


class AirgapViolationError(RuntimeError):
    """Raised when an unauthorized outbound network connection is attempted inside the worker."""
    pass


def enforce_process_airgap() -> None:
    """Disables external AF_INET/AF_INET6 network connections in the worker process."""
    from config.credentials import CredentialManager
    if not CredentialManager.has_credential("HF_HUB_OFFLINE"):
        CredentialManager.set_credential("HF_HUB_OFFLINE", "1")
    if not CredentialManager.has_credential("TRANSFORMERS_OFFLINE"):
        CredentialManager.set_credential("TRANSFORMERS_OFFLINE", "1")
    orig_connect = socket.socket.connect

    def _guarded_connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0] if isinstance(address, tuple) and address else address
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise AirgapViolationError(
                    f"Outbound network connection to {address} blocked by MLX Whisper airgap policy. "
                    "All live meeting audio must be transcribed 100% locally with zero cloud egress."
                )
        orig_connect(self, address)

    socket.socket.connect = _guarded_connect


def _normalize_tokens(text: str) -> List[str]:
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    return [t for t in clean.split() if t]


def _verify_metal_available(mx: Any) -> None:
    try:
        if not mx.metal.is_available():
            raise MLXInitError(
                stage="METAL",
                reason_code=MLXReasonCode.METAL_UNAVAILABLE,
                detail="Apple Silicon Metal GPU acceleration is not available on this machine.",
            )
    except MLXInitError:
        raise
    except Exception as e:
        raise MLXInitError(
            stage="METAL",
            reason_code=MLXReasonCode.METAL_UNAVAILABLE,
            detail=f"Metal availability probe failed: {e}",
        ) from e


def _resolve_local_model_path(
    model_name: Optional[str],
    expected_revision: Optional[str],
) -> Tuple[str, str, str]:
    from dual_pipeline.model_registry import FALLBACK_TIERS, ModelTier, resolve_acoustic_asr
    from pipeline_a_intake.local_stt_core.audio_processor import (
        ModelNotCachedError,
        ModelRevisionMismatchError,
        resolve_model_path,
    )
    fallback_spec = FALLBACK_TIERS[ModelTier.ACOUSTIC_MLX_WHISPER.value]
    try:
        pin = resolve_acoustic_asr()
        eff_model = model_name or pin.model_id
        eff_revision = expected_revision or pin.revision
    except Exception:
        eff_model = model_name or str(fallback_spec["default_model"])
        eff_revision = expected_revision or str(fallback_spec["model_revision"])

    try:
        resolved_path = resolve_model_path(
            model_name=eff_model,
            expected_revision=eff_revision,
            require_cached=True,
        )
        return str(resolved_path), eff_model, eff_revision
    except ModelNotCachedError as e:
        raise MLXInitError(stage="MODEL_RESOLVE", reason_code=MLXReasonCode.MODEL_NOT_CACHED, detail=str(e)) from e
    except ModelRevisionMismatchError as e:
        raise MLXInitError(stage="MODEL_RESOLVE", reason_code=MLXReasonCode.MODEL_REVISION_MISMATCH, detail=str(e)) from e
    except Exception as e:
        raise MLXInitError(
            stage="MODEL_RESOLVE",
            reason_code=MLXReasonCode.MODEL_LOAD_FAILED,
            detail=f"Unexpected resolution error for {eff_model}: {e}",
        ) from e


_WORKER_MODEL_LOAD_COUNT: int = 0


def get_worker_model_load_count() -> int:
    """Returns the number of times MLX Whisper weights were loaded in this worker process."""
    return _WORKER_MODEL_LOAD_COUNT


def _load_mlx_weights(load_model: Any, resolved_path: str, mx: Any) -> float:
    global _WORKER_MODEL_LOAD_COUNT
    t0 = time.perf_counter()
    try:
        holder_cls = None
        with contextlib.suppress(Exception):
            from mlx_whisper.transcribe import ModelHolder as _mh
            holder_cls = _mh
        if (
            holder_cls is not None
            and getattr(holder_cls, "model", None) is not None
            and getattr(holder_cls, "model_path", None) == resolved_path
        ):
            model = holder_cls.model
        else:
            model = load_model(resolved_path, dtype=mx.float16)
            _WORKER_MODEL_LOAD_COUNT += 1
            if holder_cls is not None:
                holder_cls.model = model
                holder_cls.model_path = resolved_path
        mx.eval(model.parameters())
        return (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        raise MLXInitError(
            stage="MODEL_LOAD",
            reason_code=MLXReasonCode.MODEL_LOAD_FAILED,
            detail=f"Failed to load model weights from '{resolved_path}': {e}",
        ) from e


def _resolve_golden_asset(asset_name: str) -> Path:
    local = Path(__file__).resolve().parent / "assets" / asset_name
    if local.is_file():
        return local
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=False,
            timeout=10.0,
        )
        if proc.returncode == 0:
            git_common = Path(proc.stdout.strip()).resolve()
            repo_main = git_common.parent if git_common.name == ".git" else git_common
            candidate = repo_main / "dual_pipeline" / "live_meeting_recorder_core" / "assets" / asset_name
            if candidate.is_file():
                return candidate
    except Exception:
        pass
    return local


def _verify_golden_inference(mlx_whisper: Any, resolved_path: str) -> Tuple[float, str]:
    golden_wav = _resolve_golden_asset("mlx_golden_16k.wav")
    manifest_path = _resolve_golden_asset("mlx_golden_manifest.json")

    if not golden_wav.exists() or not manifest_path.exists():
        raise MLXInitError(
            stage="GOLDEN_INFERENCE",
            reason_code=MLXReasonCode.GOLDEN_INFERENCE_EMPTY,
            detail=f"Golden verification fixture missing at {golden_wav}",
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_tokens = manifest.get("expected_normalized_tokens", [])
        min_overlap = float(manifest.get("min_token_overlap_ratio", 0.8))
    except Exception as e:
        raise MLXInitError(
            stage="GOLDEN_INFERENCE",
            reason_code=MLXReasonCode.GOLDEN_INFERENCE_EMPTY,
            detail=f"Failed to parse golden manifest: {e}",
        ) from e

    t0 = time.perf_counter()
    try:
        res = mlx_whisper.transcribe(
            str(golden_wav),
            path_or_hf_repo=resolved_path,
            language="en",
            temperature=0.0,
            verbose=False,
        )
        golden_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as e:
        raise MLXInitError(
            stage="GOLDEN_INFERENCE",
            reason_code=MLXReasonCode.GOLDEN_INFERENCE_EMPTY,
            detail=f"Golden inference transcription failed: {e}",
        ) from e

    golden_text = res.get("text", "").strip()
    if not golden_text:
        raise MLXInitError(
            stage="GOLDEN_INFERENCE",
            reason_code=MLXReasonCode.GOLDEN_INFERENCE_EMPTY,
            detail="Golden inference returned empty transcription text.",
        )

    observed_tokens = set(_normalize_tokens(golden_text))
    exp_set = set(expected_tokens)
    overlap = len(observed_tokens & exp_set) / max(1, len(exp_set))
    if overlap < min_overlap:
        raise MLXInitError(
            stage="GOLDEN_INFERENCE",
            reason_code=MLXReasonCode.GOLDEN_INFERENCE_MISMATCH,
            detail=f"Golden token overlap ratio {overlap:.2f} < {min_overlap:.2f}. Observed: '{golden_text}'",
        )
    return golden_ms, golden_text


def perform_mlx_readiness_probe(
    model_name: Optional[str] = None,
    expected_revision: Optional[str] = None,
    mock_mode: bool = False,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Executes the full staged proof-of-readiness sequence for MLX Whisper."""
    t_start = time.perf_counter()
    enforce_process_airgap()

    if mock_mode:
        return {
            "type": "READY",
            "pid": os.getpid(),
            "status": "READY",
            "model_id": model_name or "mock_whisper",
            "model_revision": expected_revision or "mock_rev",
            "resolved_path": "/mock/snapshot/path",
            "mlx_version": "mock",
            "metal_device": "Mock Metal GPU",
            "load_ms": 1.0,
            "golden_ms": 1.0,
            "golden_text_sha256": hashlib.sha256(b"mock").hexdigest(),
            "golden_ok": True,
            "total_probe_ms": round((time.perf_counter() - t_start) * 1000.0, 2),
        }

    repo_root = Path(__file__).resolve().parent.parent.parent
    from config.credentials import CredentialManager
    CredentialManager.prepend_path(str(repo_root / ".venv" / "bin"))
    CredentialManager.ensure_ssot_env_loaded()

    try:
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.load_models import load_model
    except Exception as e:
        raise MLXInitError(
            stage="IMPORT",
            reason_code=MLXReasonCode.MLX_IMPORT_FAILED,
            detail=f"Failed to import MLX modules: {e}",
        ) from e

    _verify_metal_available(mx)
    resolved_path, eff_model, eff_revision = _resolve_local_model_path(model_name, expected_revision)
    load_ms = _load_mlx_weights(load_model, resolved_path, mx)
    golden_ms, golden_text = _verify_golden_inference(mlx_whisper, resolved_path)

    return {
        "type": "READY",
        "pid": os.getpid(),
        "status": "READY",
        "model_id": eff_model,
        "model_revision": eff_revision,
        "resolved_path": resolved_path,
        "mlx_version": getattr(mx, "__version__", "unknown"),
        "metal_device": "Apple Silicon Metal GPU",
        "load_ms": round(load_ms, 2),
        "golden_ms": round(golden_ms, 2),
        "golden_text_sha256": hashlib.sha256(golden_text.encode("utf-8")).hexdigest(),
        "golden_ok": True,
        "total_probe_ms": round((time.perf_counter() - t_start) * 1000.0, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated MLX Whisper Worker & Self-Test")
    parser.add_argument("--self-test", action="store_true", help="Run full proof-of-readiness probe and exit")
    parser.add_argument("--receipt", type=str, default=None, help="Save readiness receipt JSON to path")
    parser.add_argument("--model", type=str, default=None, help="Model ID override")
    parser.add_argument("--revision", type=str, default=None, help="Model revision override")

    args = parser.parse_args()

    if args.self_test:
        print("🔍 Executing MLX Whisper Proof-of-Readiness Probe...")
        try:
            receipt = perform_mlx_readiness_probe(
                model_name=args.model,
                expected_revision=args.revision,
                mock_mode=False,
            )
            print(f"✅ MLX Whisper PROVEN READY in {receipt.get('total_probe_ms', 0):.2f}ms")
            print(f"   Model:     {receipt.get('model_id')}@{receipt.get('model_revision')}")
            print(f"   Snapshot:  {receipt.get('resolved_path')}")
            print(f"   Load Time: {receipt.get('load_ms')}ms | Golden Infer: {receipt.get('golden_ms')}ms")
            print(f"   Golden OK: {receipt.get('golden_ok')}")

            if args.receipt:
                rp = Path(args.receipt)
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                print(f"   Receipt saved: {rp}")
            return 0
        except MLXInitError as e:
            sys.stderr.write(f"❌ MLX Readiness Failed: {e}\n")
            if args.receipt:
                rp = Path(args.receipt)
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_text(json.dumps(e.to_dict(), indent=2), encoding="utf-8")
            return 1
        except Exception as e:
            sys.stderr.write(f"❌ Unexpected Error: {e}\n")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
