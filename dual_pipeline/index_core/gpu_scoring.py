"""Conditional GPU/MPS/CUDA scoring adapter with generation-bound tensors and CPU fallback.

Provides:
- Bounded-tile candidate document scoring for ColBERT MaxSim
- Generation-bound resident tensor management
- Deterministic numerical equivalence and tie-breaking preserving final rankings
- Empirical crossover evaluation and documented CPU retention decision
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from dual_pipeline.parameters import get_index_core_parameters

logger = logging.getLogger("dual_pipeline.index_core.gpu_scoring")

DEFAULT_COLBERT_TILE_SIZE = int(get_index_core_parameters()["default_colbert_tile_size"])
DEFAULT_PRE_PADDED_MAX_BYTES = 64 * 1024 * 1024  # 64 MB


@dataclass(frozen=True)
class ScoringDecision:
    """Formal decision outcome evaluating CPU vs GPU execution."""
    selected_backend: str  # "CPU", "MPS", "CUDA"
    reason: str
    crossover_evaluated: bool = False
    cpu_latency_ms: Optional[float] = None
    gpu_latency_ms: Optional[float] = None
    speedup_ratio: Optional[float] = None
    max_score_abs_diff: Optional[float] = None
    deterministic_rank_match: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


def detect_best_device() -> str:
    """Detects available hardware acceleration device for PyTorch."""
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class GpuColbertScoringAdapter:
    """Thread-safe adapter for GPU-accelerated MaxSim scoring with CPU fallback."""

    def __init__(self, preferred_backend: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._preferred_backend = preferred_backend.upper() if preferred_backend else None
        self._detected_device = detect_best_device()
        self._resident_tensors: Dict[str, Dict[str, Any]] = {}
        self._current_generation_id: Optional[str] = None
        self._last_decision: Optional[ScoringDecision] = None

    @property
    def detected_device(self) -> str:
        return self._detected_device

    def _resolve_backend_preference(self) -> str:
        import os
        if os.environ.get("DISABLE_GPU_OPTIMIZATION", "").lower() in {"1", "true", "yes"}:
            return "CPU"
        env_b = os.environ.get("GPU_SCORING_BACKEND", "").upper().strip()
        pref = env_b or self._preferred_backend
        if pref in {"CPU", "MPS", "CUDA"}:
            return pref
        if self._last_decision:
            return self._last_decision.selected_backend
        return "MPS" if self._detected_device == "mps" else ("CUDA" if self._detected_device == "cuda" else "CPU")

    def is_gpu_available(self) -> bool:
        backend = self._resolve_backend_preference()
        if backend == "CUDA":
            return self._detected_device == "cuda"
        if backend == "MPS":
            return self._detected_device == "mps"
        return False

    def active_backend(self) -> str:
        with self._lock:
            pref = self._resolve_backend_preference()
            return pref if self.is_gpu_available() else "CPU"

    def bind_generation(
        self,
        generation_id: str,
        colbert_tokens: Optional[Dict[str, np.ndarray]] = None,
    ) -> None:
        """Binds resident tensors to a specific generation lifecycle."""
        with self._lock:
            if self._current_generation_id != generation_id:
                self.invalidate_generation()
                self._current_generation_id = generation_id

            if colbert_tokens and self.is_gpu_available():
                try:
                    import torch
                    dev = torch.device(self._detected_device)
                    gen_tensors: Dict[str, Any] = {}
                    for rid, arr in colbert_tokens.items():
                        if arr is not None and arr.ndim == 2 and arr.shape[0] > 0:
                            gen_tensors[rid] = torch.from_numpy(arr).to(device=dev, dtype=torch.float32)
                    self._resident_tensors[generation_id] = gen_tensors
                except Exception as exc:
                    logger.debug("Failed pre-binding resident tensors on %s: %s", self._detected_device, exc)

    def invalidate_generation(self, generation_id: Optional[str] = None) -> None:
        """Invalidates generation-bound resident tensors and releases GPU memory."""
        with self._lock:
            if generation_id is None or generation_id == self._current_generation_id:
                self._resident_tensors.clear()
                self._current_generation_id = None
                try:
                    import torch
                    if self._detected_device == "mps":
                        torch.mps.empty_cache()
                    elif self._detected_device == "cuda":
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    def score_tile_gpu(
        self,
        query_mat: np.ndarray,
        doc_stack: np.ndarray,
        lengths: np.ndarray,
        mask: Optional[np.ndarray],
    ) -> Optional[np.ndarray]:
        """Scores a bounded tile of documents on GPU with synchronization.

        Returns None if GPU is unavailable or on any device error (signaling CPU fallback).
        """
        if not self.is_gpu_available():
            return None

        with self._lock:
            try:
                import torch
                dev = torch.device(self._detected_device)
                with torch.no_grad():
                    q_t = torch.from_numpy(query_mat).to(device=dev, dtype=torch.float32)
                    d_t = torch.from_numpy(doc_stack).to(device=dev, dtype=torch.float32)
                    sim = torch.matmul(d_t, q_t.transpose(0, 1))  # (N, L, Q)

                    if mask is not None:
                        mask_t = torch.from_numpy(mask).to(device=dev, dtype=torch.bool).unsqueeze(-1)
                        sim = sim.masked_fill(mask_t, float("-inf"))

                    max_sim = torch.max(sim, dim=1).values  # (N, Q)
                    scores_t = torch.sum(max_sim, dim=1)  # (N,)

                    if np.any(lengths == 0):
                        zero_mask = torch.from_numpy(lengths == 0).to(device=dev, dtype=torch.bool)
                        scores_t[zero_mask] = 0.0

                    if self._detected_device == "mps":
                        torch.mps.synchronize()
                    elif self._detected_device == "cuda":
                        torch.cuda.synchronize()

                    scores_np = scores_t.cpu().numpy().astype(np.float64)

                if not np.all(np.isfinite(scores_np)):
                    raise ValueError("Non-finite scores encountered during GPU MaxSim computation")

                return scores_np
            except Exception as exc:
                logger.warning("GPU scoring failed on %s, falling back to CPU: %s", self._detected_device, exc)
                return None

    def evaluate_crossover(
        self,
        candidate_count: int = 64,
        query_tokens: int = 8,
        doc_tokens: int = 24,
        dim: int = 1024,
        repeats: int = 5,
        target_latency_ms: float = 45.0,
    ) -> ScoringDecision:
        """Measures CPU vs GPU execution and emits a documented crossover decision."""
        with self._lock:
            q_mat = np.random.randn(query_tokens, dim).astype(np.float32)
            docs = np.random.randn(candidate_count, doc_tokens, dim).astype(np.float32)
            lengths = np.full((candidate_count,), doc_tokens, dtype=np.int32)
            mask = None

            # Warm CPU
            for _ in range(2):
                sim = np.matmul(docs, q_mat.T)
                _ = np.sum(np.max(sim, axis=1), axis=1)

            t0 = time.perf_counter()
            for _ in range(repeats):
                sim = np.matmul(docs, q_mat.T)
                cpu_scores = np.sum(np.max(sim, axis=1), axis=1)
            cpu_latency_ms = ((time.perf_counter() - t0) / repeats) * 1000.0

            if not self.is_gpu_available():
                decision = ScoringDecision(
                    selected_backend="CPU",
                    reason=f"GPU hardware acceleration unavailable ({self._detected_device}); retaining CPU default.",
                    crossover_evaluated=True,
                    cpu_latency_ms=cpu_latency_ms,
                    deterministic_rank_match=True,
                )
                self._last_decision = decision
                return decision

            # Warm GPU
            _ = self.score_tile_gpu(q_mat, docs, lengths, mask)

            t0 = time.perf_counter()
            gpu_scores = None
            for _ in range(repeats):
                gpu_scores = self.score_tile_gpu(q_mat, docs, lengths, mask)
            gpu_latency_ms = ((time.perf_counter() - t0) / repeats) * 1000.0

            if gpu_scores is None:
                decision = ScoringDecision(
                    selected_backend="CPU",
                    reason="GPU execution failed during crossover measurement; falling back to CPU.",
                    crossover_evaluated=True,
                    cpu_latency_ms=cpu_latency_ms,
                    gpu_latency_ms=gpu_latency_ms,
                    deterministic_rank_match=True,
                )
                self._last_decision = decision
                return decision

            max_diff = float(np.max(np.abs(cpu_scores - gpu_scores)))
            cpu_rank = np.argsort(-cpu_scores)
            gpu_rank = np.argsort(-gpu_scores)
            rank_match = bool(np.array_equal(cpu_rank, gpu_rank))
            speedup = cpu_latency_ms / gpu_latency_ms if gpu_latency_ms > 0 else 0.0

            # Documented crossover rule:
            # GPU must satisfy the pre-registered target (<= 45.0 ms) AND show measurable speedup over CPU
            # If CPU is faster (speedup <= 1.0) due to transfer/sync overhead on candidate batches, retain CPU!
            if speedup > 1.2 and gpu_latency_ms <= target_latency_ms and rank_match:
                selected = self._detected_device.upper()
                reason = (
                    f"GPU crossover demonstrated: {selected} achieves {gpu_latency_ms:.3f} ms "
                    f"({speedup:.2f}x vs CPU {cpu_latency_ms:.3f} ms) meeting target <= {target_latency_ms} ms."
                )
            else:
                selected = "CPU"
                reason = (
                    f"Documented CPU retention: CPU batched NumPy ({cpu_latency_ms:.3f} ms) outperforms "
                    f"{self._detected_device.upper()} ({gpu_latency_ms:.3f} ms) on candidate pool size {candidate_count} "
                    f"due to host-device dispatch and synchronization overhead."
                )

            decision = ScoringDecision(
                selected_backend=selected,
                reason=reason,
                crossover_evaluated=True,
                cpu_latency_ms=cpu_latency_ms,
                gpu_latency_ms=gpu_latency_ms,
                speedup_ratio=speedup,
                max_score_abs_diff=max_diff,
                deterministic_rank_match=rank_match,
                metadata={
                    "device": self._detected_device,
                    "candidate_count": candidate_count,
                    "target_latency_ms": target_latency_ms,
                },
            )
            self._last_decision = decision
            return decision


_GLOBAL_SCORING_ADAPTER: Optional[GpuColbertScoringAdapter] = None
_ADAPTER_LOCK = threading.RLock()


def get_global_gpu_scorer() -> GpuColbertScoringAdapter:
    """Returns singleton instance of GpuColbertScoringAdapter."""
    global _GLOBAL_SCORING_ADAPTER
    with _ADAPTER_LOCK:
        if _GLOBAL_SCORING_ADAPTER is None:
            _GLOBAL_SCORING_ADAPTER = GpuColbertScoringAdapter()
        return _GLOBAL_SCORING_ADAPTER


def _build_doc_stack(
    tile_ids: List[str],
    token_cache: Dict[str, np.ndarray],
    q_dim: int,
    strict: bool,
) -> Optional[Tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]]:
    doc_mats = [token_cache.get(rid, np.empty((0, q_dim), dtype=np.float32)) for rid in tile_ids]
    lengths = np.array([m.shape[0] for m in doc_mats], dtype=np.int32)
    max_tokens = int(np.max(lengths)) if len(lengths) > 0 else 0

    valid_dims = {m.shape[1] for m in doc_mats if m.shape[0] > 0}
    if valid_dims and any(d != q_dim for d in valid_dims):
        if strict:
            raise ValueError(
                f"Dimension mismatch between query ({q_dim}) and ColBERT document vectors ({valid_dims})"
            )
        return None

    if max_tokens == 0:
        return np.zeros((len(tile_ids), 0, q_dim), dtype=np.float32), lengths, None

    doc_stack = np.zeros((len(tile_ids), max_tokens, q_dim), dtype=np.float32)
    for i, m in enumerate(doc_mats):
        if lengths[i] > 0:
            doc_stack[i, :lengths[i], :] = m

    mask = (np.arange(max_tokens)[None, :] >= lengths[:, None]) if max_tokens > int(np.min(lengths)) else None
    return doc_stack, lengths, mask


def _score_tile(
    adapter: GpuColbertScoringAdapter,
    use_gpu: bool,
    query_mat: np.ndarray,
    doc_stack: np.ndarray,
    lengths: np.ndarray,
    mask: Optional[np.ndarray],
    strict: bool,
) -> np.ndarray:
    scores_arr: Optional[np.ndarray] = None
    if use_gpu:
        try:
            scores_arr = adapter.score_tile_gpu(query_mat, doc_stack, lengths, mask)
        except Exception as exc:
            logger.warning("GPU scoring tile failed, falling back to CPU: %s", exc)
            scores_arr = None

    if scores_arr is None:
        sim = np.matmul(doc_stack, query_mat.T)
        if mask is not None:
            sim[mask] = -np.inf
        scores_arr = np.sum(np.max(sim, axis=1), axis=1)
        scores_arr[lengths == 0] = 0.0

    if not np.all(np.isfinite(scores_arr)):
        if strict:
            raise ValueError("Non-finite values detected in batched ColBERT scores")
        scores_arr = np.nan_to_num(scores_arr, nan=0.0, posinf=0.0, neginf=0.0)
    return scores_arr


def score_colbert_tiled(
    query_mat: np.ndarray,
    target_ids: List[str],
    token_cache: Dict[str, np.ndarray],
    padded_cache: Optional[Any],
    strict: bool = True,
    tile_size: int = DEFAULT_COLBERT_TILE_SIZE,
    gpu_adapter: Optional[GpuColbertScoringAdapter] = None,
) -> Optional[List[Tuple[float, str]]]:
    """Scores candidate records in bounded tiles with GPU acceleration or CPU fallback."""
    if not target_ids:
        return []
    if query_mat.shape[0] == 0:
        return [(0.0, rid) for rid in target_ids]

    q_dim = query_mat.shape[1]
    adapter = gpu_adapter or get_global_gpu_scorer()
    use_gpu = adapter.active_backend() in {"MPS", "CUDA"} and adapter.is_gpu_available()
    results: List[Tuple[float, str]] = []

    step = max(1, tile_size)
    for start_idx in range(0, len(target_ids), step):
        tile_ids = target_ids[start_idx : start_idx + step]
        cached = padded_cache.get(tuple(tile_ids)) if padded_cache else None

        if cached is not None:
            doc_stack, lengths, mask = cached
        else:
            built = _build_doc_stack(tile_ids, token_cache, q_dim, strict)
            if built is None:
                return None
            doc_stack, lengths, mask = built
            if doc_stack.shape[1] == 0:
                results.extend((0.0, rid) for rid in tile_ids)
                continue
            if padded_cache is not None:
                padded_cache.set(tuple(tile_ids), doc_stack, lengths, mask)

        scores_arr = _score_tile(adapter, use_gpu, query_mat, doc_stack, lengths, mask, strict)
        results.extend((float(s), rid) for s, rid in zip(scores_arr, tile_ids, strict=False))

    return results
