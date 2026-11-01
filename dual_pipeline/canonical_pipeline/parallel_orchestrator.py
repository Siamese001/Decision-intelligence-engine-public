"""L5 Parallel Orchestration Coordinator for MacBook M5 (48 GB Unified RAM).

Strictly gates parallel execution to two governed workflows:
1. Mode 1 (Cached Audio Post-ASR): A .wav/audio file whose Stage 0 ASR transcript
   is already completed and verified in cache, producing Outputs 01, 02, 03 and
   mutating the knowledge graph (`mutate_graph=True`).
2. Mode 2 (Document Ingestion): A document ingested via NEE producing Output 03
   only (`output3_only=True`, `outputs=['03']`) and mutating the knowledge graph
   (`mutate_graph=True`).

All other routes (live recording, cold un-cached ASR, notes/copilot, package-only
or non-mutating runs) deterministically bypass parallel orchestration.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple, TypeVar

from config.credentials import CredentialManager
from dual_pipeline.transcript_pipeline_core.cache_seal import (
    CacheIntegrityViolationError,
    verify_cache_seal,
)
from dual_pipeline.transcript_pipeline_core.caching import get_cached_audio_transcript
from dual_pipeline.transcript_pipeline_core.constants import AUDIO_EXTENSIONS
from dual_pipeline.transcript_pipeline_core.source_modality import (
    _is_audio_derived,
    _is_notes_mode,
    distinguish_audio_capture_type,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

MODE_1_CACHED_AUDIO_FULL_SYNTHESIS = "MODE_1_CACHED_AUDIO_FULL_SYNTHESIS"
MODE_2_DOCUMENT_OUTPUT3_INGESTION = "MODE_2_DOCUMENT_OUTPUT3_INGESTION"

_HITL_SURFACING_LOCK = threading.RLock()
_STAGE7_PUBLICATION_LOCK = threading.RLock()


@dataclass(frozen=True)
class M5HardwareProfile:
    """Resource budget tuned for Apple Silicon M5 Pro with 48 GB unified memory."""

    cpu_workers: int = 8
    llm_concurrency_limit: int = 12
    l2_fork_workers: int = 2
    recap_dag_workers: int = 3
    mps_batch_size: int = 64
    mps_streaming_window_size: int = 128
    sqlite_mmap_size_bytes: int = 1073741824  # 1 GB unified RAM mmap
    logical_cores: int = 18
    performance_cores: int = 12
    efficiency_cores: int = 6
    unified_memory_gb: int = 48
    parallel_ingest_workers: int = 8
    max_parallel_ingest_workers: int = 16
    per_worker_memory_budget_gb: float = 2.5


DEFAULT_M5_PROFILE = M5HardwareProfile()


def get_m5_hardware_profile() -> M5HardwareProfile:
    """Return active M5 hardware concurrency profile with optional env overrides."""
    cpu_raw = CredentialManager.get_credential("NEE_M5_CPU_WORKERS", "").strip()
    llm_raw = CredentialManager.get_credential("NEE_LLM_MAX_CONCURRENT_REQUESTS", "").strip()
    batch_raw = CredentialManager.get_credential("NEE_MPS_BATCH_SIZE", "").strip()
    ingest_raw = CredentialManager.get_credential("NEE_PARALLEL_INGEST_WORKERS", "").strip()
    cpu_workers = max(1, min(int(cpu_raw), 16)) if cpu_raw.isdigit() else DEFAULT_M5_PROFILE.cpu_workers
    llm_limit = max(1, min(int(llm_raw), 32)) if llm_raw.isdigit() else DEFAULT_M5_PROFILE.llm_concurrency_limit
    mps_batch = max(8, min(int(batch_raw), 256)) if batch_raw.isdigit() else DEFAULT_M5_PROFILE.mps_batch_size
    ingest_workers = (
        max(1, min(int(ingest_raw), 16))
        if ingest_raw.isdigit()
        else DEFAULT_M5_PROFILE.parallel_ingest_workers
    )
    return M5HardwareProfile(
        cpu_workers=cpu_workers,
        llm_concurrency_limit=llm_limit,
        mps_batch_size=mps_batch,
        parallel_ingest_workers=ingest_workers,
    )


def resolve_optimal_parallel_workers(
    requested_workers: Optional[int] = None,
    task_count: Optional[int] = None,
) -> int:
    """Resolve optimal parallel ingestion worker count for M5 Pro 48 GB unified RAM."""
    profile = get_m5_hardware_profile()
    max_cap = profile.max_parallel_ingest_workers

    if requested_workers is not None:
        clamped = max(1, min(int(requested_workers), max_cap))
        if task_count is not None and task_count > 0:
            return max(1, min(clamped, int(task_count)))
        return clamped

    avail_gb = 38.0
    try:
        from dual_pipeline.runtime.memory_governor import MemoryGovernor
        gov = MemoryGovernor(auto_detect=True)
        avail_gb = max(4.0, float(gov.available_memory_gb))
    except Exception:
        pass

    mem_safe_workers = max(1, int(avail_gb // max(0.5, profile.per_worker_memory_budget_gb)))
    target_workers = min(
        max_cap,
        mem_safe_workers,
        max(profile.parallel_ingest_workers, profile.performance_cores),
    )
    optimal = max(1, min(target_workers, max_cap))
    if task_count is not None and task_count > 0:
        return max(1, min(optimal, int(task_count)))
    return optimal


def _has_verified_warm_audio_cache(input_p: Path, metadata: Dict[str, Any]) -> bool:
    """Verify whether an audio file already has a warm, uncorrupted Stage 0 ASR cache."""
    # 1. Resume checkpoint with pre-loaded U0 source text or structured segments
    if bool(metadata.get("resume")):
        cached_text = str(metadata.get("clean_transcript") or metadata.get("source_text") or "").strip()
        if len(cached_text) > 50 or bool(metadata.get("structured_segments")):
            return True

    # 2. Explicit pre-computed transcript path with mtime freshness check
    pre_tx = metadata.get("transcript_cache_path") or metadata.get("raw_transcript_path")
    if pre_tx:
        pre_path = Path(str(pre_tx)).expanduser()
        if pre_path.is_file():
            try:
                if (
                    input_p.stat().st_mtime <= pre_path.stat().st_mtime
                    and len(pre_path.read_text(encoding="utf-8", errors="replace").strip()) > 50
                ):
                    return True
            except OSError:
                pass

    # 3. Content-addressed SHA-256 transcript cache
    cache_dir_raw = metadata.get("transcript_cache_dir")
    cache_dir = Path(str(cache_dir_raw)) if cache_dir_raw else None
    cached_tx = get_cached_audio_transcript(input_p, audio_sha=None, cache_dir=cache_dir)
    if not cached_tx:
        return False

    # If a cryptographic sidecar seal exists alongside the audio or cache, verify it
    seal_candidates = [
        input_p.with_suffix(".cache_seal.json"),
        input_p.parent / f"{input_p.stem}.cache_seal.json",
    ]
    for seal_path in seal_candidates:
        if seal_path.is_file():
            seg_path = seal_path.with_name(seal_path.name.replace(".cache_seal.json", ".segments.json"))
            if seg_path.is_file():
                try:
                    verify_cache_seal(seg_path)
                except CacheIntegrityViolationError:
                    return False
    return True


def _normalize_requested_outputs(metadata: Dict[str, Any], is_doc_mode: bool) -> List[str]:
    """Normalize requested output list from metadata flags."""
    if metadata.get("output3_only") or is_doc_mode and "outputs" not in metadata:
        if not metadata.get("include_output_1") and not metadata.get("include_output_2"):
            return ["03"]
    raw_outputs = metadata.get("outputs")
    if isinstance(raw_outputs, (list, tuple)):
        normalized = [str(item).zfill(2) if str(item).isdigit() else str(item) for item in raw_outputs]
        return normalized
    if metadata.get("skip_output_1") and metadata.get("skip_output_2"):
        return ["03"]
    return ["01", "02", "03"]


def _resolve_input_path(input_path: str) -> Tuple[Optional[Path], str]:
    if not input_path:
        return None, ""
    p = Path(input_path).expanduser().resolve() if os.path.exists(input_path) else Path(input_path).expanduser()
    return p, p.suffix.lower()


def _check_parallel_exclusions(
    input_p: Optional[Path], input_ext: str, meta: Dict[str, Any], mutate_graph: bool
) -> Optional[str]:
    if not mutate_graph or meta.get("mutate_graph") is False or bool(meta.get("package_only")):
        return "GRAPH_MUTATION_DISABLED"
    if bool(meta.get("dry_run")):
        return "DRY_RUN_EXCLUDED"
    if _is_notes_mode(meta) or bool(meta.get("notes") or meta.get("is_notes") or meta.get("copilot")):
        return "NOTES_OR_COPILOT_MODE_EXCLUDED"
    cap_mode = str(meta.get("capture_mode") or meta.get("audio_capture_type") or "").strip().upper()
    if (
        distinguish_audio_capture_type("", meta, input_ext) == "LIVE_CAPTURE"
        or cap_mode.startswith("LIVE")
        or bool(meta.get("live_capture") or meta.get("is_live_capture"))
    ):
        return "LIVE_CAPTURE_EXCLUDED"
    if input_p is not None and input_p.is_file() and input_p.with_name(f".{input_p.name}.flock").exists():
        return "ACTIVE_FILE_LOCK_EXCLUDED"
    return None


def should_enable_parallel_orchestration(
    input_path: str,
    metadata: Optional[Dict[str, Any]] = None,
    mutate_graph: bool = True,
) -> Tuple[bool, str]:
    """Fail-closed predicate gating parallel orchestration to the two approved routes."""
    meta = dict(metadata or {})
    input_p, input_ext = _resolve_input_path(input_path)
    exclusion = _check_parallel_exclusions(input_p, input_ext, meta, mutate_graph)
    if exclusion is not None:
        return False, exclusion

    mod_upper = str(meta.get("canonical_modality") or meta.get("source_modality") or "").upper()
    is_doc_mode = bool(meta.get("is_document") or meta.get("document") or mod_upper == "DOCUMENT" or meta.get("output3_only"))
    outputs_req = _normalize_requested_outputs(meta, is_doc_mode)
    is_valid_file = input_p is not None and input_p.is_file()

    if is_valid_file and input_ext in AUDIO_EXTENSIONS and not is_doc_mode and outputs_req == ["01", "02", "03"]:
        assert input_p is not None
        return (True, MODE_1_CACHED_AUDIO_FULL_SYNTHESIS) if _has_verified_warm_audio_cache(input_p, meta) else (False, "COLD_AUDIO_ASR_REQUIRED")

    if is_doc_mode and outputs_req == ["03"] and not meta.get("skip_output_3") and input_ext not in AUDIO_EXTENSIONS and not _is_audio_derived(input_ext, meta, ""):
        return (True, MODE_2_DOCUMENT_OUTPUT3_INGESTION) if is_valid_file else (False, "MISSING_DOCUMENT_SOURCE")

    return False, "UNSUPPORTED_PARALLEL_ROUTE"


@contextmanager
def atomic_hitl_surfacing_barrier() -> Iterator[None]:
    """Serialize HITL prompt surfacing so concurrent workers never bundle prompts."""
    with _HITL_SURFACING_LOCK:
        yield


@contextmanager
def stage7_publication_mutex(runtime_root: Optional[Path] = None) -> Iterator[None]:
    """Serialize Stage 7 graph publication across threads and OS processes."""
    with _STAGE7_PUBLICATION_LOCK:
        if runtime_root is not None:
            from dual_pipeline.core import file_lock
            rt_path = runtime_root if isinstance(runtime_root, Path) else Path(str(runtime_root))
            lock_file = rt_path.resolve() / "stage7_publication.lock"
            with file_lock(lock_file, timeout_seconds=600.0):
                yield
        else:
            yield


def run_indexed_parallel(
    items: Sequence[T],
    worker_fn: Callable[[int, T], R],
    *,
    max_workers: Optional[int] = None,
) -> List[R]:
    """Execute worker_fn across items in parallel and return results in exact input order."""
    total = len(items)
    if total == 0:
        return []
    eff_workers = max_workers if max_workers is not None else get_m5_hardware_profile().cpu_workers
    if total == 1 or eff_workers <= 1:
        return [worker_fn(0, items[0])]

    bounded_workers = min(eff_workers, total)
    results: List[Optional[R]] = [None] * total
    import contextvars
    from config.request_deadline import remaining_timeout
    t_budget = remaining_timeout(300.0)
    with ThreadPoolExecutor(max_workers=bounded_workers, thread_name_prefix="m5_idx_worker") as pool:
        futures: List[Tuple[int, Future[R]]] = [
            (idx, pool.submit(contextvars.copy_context().run, worker_fn, idx, item)) for idx, item in enumerate(items)
        ]
        first_exc: Optional[BaseException] = None
        for idx, fut in futures:
            try:
                results[idx] = fut.result(timeout=t_budget)
            except BaseException as exc:
                if first_exc is None:
                    first_exc = exc
        if first_exc is not None:
            raise first_exc
    return [res for res in results]  # type: ignore[misc]


def run_named_tasks_parallel(
    tasks: Dict[str, Callable[[], Any]],
    *,
    max_workers: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute independent read-only callables concurrently and return a keyed dict."""
    if not tasks:
        return {}
    eff_workers = max_workers if max_workers is not None else get_m5_hardware_profile().cpu_workers
    if len(tasks) == 1 or eff_workers <= 1:
        return {name: fn() for name, fn in tasks.items()}

    bounded_workers = min(eff_workers, len(tasks))
    outcomes: Dict[str, Any] = {}
    import contextvars
    from config.request_deadline import remaining_timeout
    t_budget = remaining_timeout(300.0)
    with ThreadPoolExecutor(max_workers=bounded_workers, thread_name_prefix="m5_named_task") as pool:
        future_map = {name: pool.submit(contextvars.copy_context().run, fn) for name, fn in tasks.items()}
        first_exc: Optional[BaseException] = None
        for name in tasks:
            try:
                outcomes[name] = future_map[name].result(timeout=t_budget)
            except BaseException as exc:
                if first_exc is None:
                    first_exc = exc
        if first_exc is not None:
            raise first_exc
    return outcomes


def load_cross_and_graph_context_parallel(
    metadata: Dict[str, Any],
    prior_context_blocks: List[str],
    transcript_text: Optional[str] = None,
) -> None:
    """Concurrently fetch 7-day cross-meeting context and C-16 active graph prior context."""
    def _fetch_cross_meeting() -> Tuple[Optional[str], Any]:
        try:
            from dual_pipeline.transcript_pipeline_core.cross_meeting_context import (
                CrossMeetingContextLoader,
            )
            cm_loader = CrossMeetingContextLoader()
            return cm_loader.build_cross_meeting_packet(
                project_name=metadata.get("project_name") or metadata.get("project") or "",
                current_date_str=metadata.get("date") or "",
                current_meeting_name=metadata.get("meeting_name"),
            )
        except Exception as exc:
            logger.debug("Cross-meeting context loading skipped/failed: %s", exc)
            return None, None

    def _fetch_graph_context() -> Tuple[Any, Optional[Exception]]:
        try:
            from dual_pipeline.query_core.offline_retriever import OfflineGraphContextRetriever
            retriever = OfflineGraphContextRetriever()
            t_text = transcript_text or metadata.get("clean_transcript") or metadata.get("transcript_text")
            packet = retriever.retrieve_meeting_prior_context(
                project_name=metadata.get("project_name") or metadata.get("project"),
                attendees=metadata.get("attendees") or metadata.get("roster"),
                date_str=metadata.get("date"),
                transcript_text=t_text,
            )
            return packet, None
        except Exception as exc:
            return None, exc

    results = run_named_tasks_parallel(
        {"cross_meeting": _fetch_cross_meeting, "graph_context": _fetch_graph_context},
        max_workers=2,
    )
    cm_xml, cm_registry = results["cross_meeting"]
    if cm_xml:
        metadata["cross_meeting_context"] = cm_xml
        metadata["cross_meeting_entity_registry"] = cm_registry
        prior_context_blocks.append(f"--- CROSS-MEETING PROJECT CONTEXT ---\n{cm_xml}")

    packet, graph_exc = results["graph_context"]
    if graph_exc is not None:
        logger.warning("GRAPH_CONTEXT_DEGRADED: Offline graph context retrieval failed: %s", graph_exc)
        metadata["graph_context_degraded"] = True
        metadata["graph_context_degradation_reason"] = str(graph_exc)
    elif packet is not None and not packet.is_empty:
        metadata["graph_context_packet"] = packet.to_dict()
        metadata["graph_lexical_anchors"] = packet.lexical_terms
        metadata["known_graph_entities"] = [
            {
                "node_id": e.node_id, "canonical_name": e.canonical_name, "display_name": e.display_name,
                "node_type": e.node_type, "organization": e.organization, "role": e.role,
            }
            for e in packet.entities
        ]
        metadata["prior_graph_decisions"] = [
            {
                "assertion_id": d.assertion_id, "predicate": d.predicate,
                "subject_node_id": d.subject_node_id, "claim_text": d.claim_text, "observed_at": d.observed_at,
            }
            for d in packet.decisions
        ]
        metadata["prior_graph_risks"] = [
            {
                "assertion_id": r.assertion_id, "predicate": r.predicate,
                "subject_node_id": r.subject_node_id, "claim_text": r.claim_text,
            }
            for r in packet.risks
        ]
        if packet.formatted_prior_context:
            prior_context_blocks.append(f"--- CANONICAL GRAPH PRIOR CONTEXT ---\n{packet.formatted_prior_context}")


def bind_verified_stage_checkpoint_pins(
    metadata: Dict[str, Any],
    forensic_transcript: str,
    executive_recap: str,
    graph_json: str,
) -> Dict[str, str]:
    """Compute SHA-256 digests of Stage 01/02/03 outputs so QA skips duplicate gate runs."""
    import hashlib
    hashes: Dict[str, str] = {}
    for key, text in (
        ("output_1", forensic_transcript),
        ("output_2", executive_recap),
        ("output_3", graph_json),
    ):
        if text:
            hashes[key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    metadata["verified_stage_checkpoint_hashes"] = hashes
    return hashes


def finalize_document_output3_parallel(
    out_3: str,
    metadata: Dict[str, Any],
    ckpt_3: Optional[Path] = None,
) -> str:
    """Write document Output 03 checkpoint and bind Stage 03 digest concurrently."""
    def _write_ckpt() -> None:
        if ckpt_3 is not None:
            ckpt_3.parent.mkdir(parents=True, exist_ok=True)
            ckpt_3.write_text(out_3, encoding="utf-8")

    def _bind_pin() -> Dict[str, str]:
        return bind_verified_stage_checkpoint_pins(metadata, "", "", out_3)

    run_named_tasks_parallel({"ckpt": _write_ckpt, "pin": _bind_pin}, max_workers=2)
    return out_3


