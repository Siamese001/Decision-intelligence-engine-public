"""Copilot disk cache graph persistence and fast hydration."""
from __future__ import annotations

import logging
import pickle
from collections import defaultdict
from pathlib import Path
import hashlib
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from dual_pipeline.runtime_root import (
    ActiveGraphIntegrityMismatchError,
    resolve_active_pair,
    resolve_authoritative_runtime_root,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_cache_graph")

_LAST_GRAPH_LOAD_DIAGNOSTIC: Optional[str] = None


def get_last_copilot_graph_diagnostic() -> Optional[str]:
    """Returns the most recent graph loading diagnostic detail message."""
    return _LAST_GRAPH_LOAD_DIAGNOSTIC


def _set_last_copilot_graph_diagnostic(detail: Optional[str]) -> None:
    global _LAST_GRAPH_LOAD_DIAGNOSTIC
    _LAST_GRAPH_LOAD_DIAGNOSTIC = detail


class GraphLoadResult(tuple):
    """3-tuple subclass preserving backward compatibility while exposing diagnostic_detail."""

    status: Any
    in_mem: Optional[Any]
    retriever: Optional[Any]
    diagnostic_detail: Optional[str]

    def __new__(
        cls,
        status: Any,
        in_mem: Optional[Any] = None,
        retriever: Optional[Any] = None,
        diagnostic_detail: Optional[str] = None,
    ):
        inst = super().__new__(cls, (status, in_mem, retriever))
        inst.status = status
        inst.in_mem = in_mem
        inst.retriever = retriever
        inst.diagnostic_detail = diagnostic_detail
        return inst


def _is_repo_or_worktree_root(path: Path) -> bool:
    try:
        return (path / ".git").exists() or (path / "dual_pipeline").is_dir()
    except Exception:
        return False


def _resolve_root_dir(cand: Optional[Path]) -> Optional[Path]:
    """Resolves runtime root directory candidate or authoritative root.

    Rules:
    1. If cand is None: returns authoritative runtime root for the active repo.
    2. If cand contains active_pair.json directly: returns cand.
    3. If cand/.local/dual_pipeline contains active_pair.json: returns cand/.local/dual_pipeline.
    4. If cand is a repo/worktree root: resolves to authoritative runtime root.
    5. If cand is <repo_or_worktree>/.local/dual_pipeline: resolves to authoritative runtime root.
    6. Any other explicit candidate path lacking active_pair.json fails closed and returns None.
    """
    if cand is None:
        return resolve_authoritative_runtime_root(None)

    cand_resolved = Path(cand).resolve()
    if (cand_resolved / "active_pair.json").is_file():
        return cand_resolved
    if (cand_resolved / ".local" / "dual_pipeline" / "active_pair.json").is_file():
        return (cand_resolved / ".local" / "dual_pipeline").resolve()

    if _is_repo_or_worktree_root(cand_resolved):
        return resolve_authoritative_runtime_root(cand_resolved)

    if cand_resolved.name == "dual_pipeline" and cand_resolved.parent.name == ".local":
        repo_cand = cand_resolved.parent.parent
        if _is_repo_or_worktree_root(repo_cand):
            return resolve_authoritative_runtime_root(repo_cand)

    logger.warning("GraphLoadStatus.ABSENT: active_pair.json not found in %s", cand)
    _set_last_copilot_graph_diagnostic(f"active_pair.json not found in {cand_resolved}")
    return None


def _compute_file_sha256(path: Path) -> str:
    """Computes SHA256 hex digest of file directly."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_from_pair(
    pair_data: Dict[str, Any],
    active_pair_path: Optional[Path],
) -> Optional[Tuple[Optional[str], Path]]:
    """Resolves full graph path from active pair data."""
    rel_graph = pair_data.get("graph_path")
    graph_hash = pair_data.get("graph_sha256")
    if not rel_graph:
        logger.warning("GraphLoadStatus.ABSENT: active_pair.json missing graph_path")
        return None
    full_path = Path(rel_graph)
    if not full_path.is_absolute() and active_pair_path:
        full_path = (active_pair_path.parent / full_path).resolve()
    if not full_path.is_file():
        logger.warning("GraphLoadStatus.ABSENT: graph db not found at %s", full_path)
        return None
    return graph_hash, full_path


def _safe_resolve_active_pair(
    r_root: Path,
    graph_path: Optional[Union[str, Path]],
) -> Tuple[Dict[str, Any], Optional[Path], Optional[str]]:
    """Safely loads active pair with integrity mismatch handling."""
    try:
        data, path = resolve_active_pair(r_root, enforce_integrity=True)
        return data, path, None
    except ActiveGraphIntegrityMismatchError as exc:
        diag = str(exc)
        _set_last_copilot_graph_diagnostic(diag)
        try:
            data, path = resolve_active_pair(r_root, enforce_integrity=False)
            data["_integrity_error"] = diag
            return data, path, diag
        except Exception:
            return {"_integrity_error": diag}, None, diag
    except Exception as exc:
        diag = str(exc)
        _set_last_copilot_graph_diagnostic(diag)
        if not graph_path:
            logger.warning("GraphLoadStatus.ABSENT: %s", exc)
        return {}, None, None


def resolve_graph_paths_and_hash(
    runtime_root: Optional[Union[str, Path]],
    graph_path: Optional[Union[str, Path]],
) -> Tuple[Optional[Path], Optional[Path], Optional[str], Optional[Path], Dict[str, Any]]:
    """Resolves authoritative runtime root, active pair, full graph path, and expected hash."""
    _set_last_copilot_graph_diagnostic(None)
    cand = Path(runtime_root).resolve() if runtime_root else None
    r_root = _resolve_root_dir(cand)
    if not r_root:
        if not get_last_copilot_graph_diagnostic():
            _set_last_copilot_graph_diagnostic(f"Runtime root candidate could not be resolved: {cand}")
        return None, None, None, None, {}

    pair_data, active_pair_path, integrity_err = _safe_resolve_active_pair(r_root, graph_path)
    if not pair_data and not graph_path:
        return None, None, None, None, {}
    if integrity_err and not graph_path and not active_pair_path:
        return None, None, None, None, pair_data

    if graph_path:
        full_graph_path = Path(graph_path).resolve()
        if not full_graph_path.is_file():
            diag = f"graph db not found at {full_graph_path}"
            _set_last_copilot_graph_diagnostic(diag)
            logger.warning("GraphLoadStatus.ABSENT: %s", diag)
            return None, None, None, None, {}
        graph_hash = pair_data.get("graph_sha256") or _compute_file_sha256(full_graph_path)
        return r_root, active_pair_path, graph_hash, full_graph_path, pair_data

    resolved = _resolve_from_pair(pair_data, active_pair_path)
    if not resolved:
        if not get_last_copilot_graph_diagnostic():
            _set_last_copilot_graph_diagnostic(f"Failed to resolve graph path from active pair at {active_pair_path}")
        return None, None, None, None, pair_data
    graph_hash, full_graph_path = resolved
    return r_root, active_pair_path, graph_hash, full_graph_path, pair_data


def resolve_cache_file(
    active_pair_path: Optional[Path],
    r_root: Path,
    gen_id: Any,
    graph_hash: Optional[str],
    engagement_id: Optional[str] = None,
) -> Path:
    """Computes cache directory and generation-keyed snapshot filename."""
    cache_dir = (active_pair_path.parent / "cache") if active_pair_path else (r_root / "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    hash_slug = str(graph_hash)[:12] if graph_hash else "nohash"
    eng_slug = ""
    if engagement_id:
        norm = str(engagement_id).replace("project:", "").strip().lower()
        clean = re.sub(r"[^a-z0-9_-]", "_", norm)
        eng_slug = f"_{clean}" if clean else ""
    return cache_dir / f"copilot_graph_{gen_id}_{hash_slug}{eng_slug}.pkl"


def _read_cache_payload(
    cache_file: Path,
    graph_hash: Optional[str],
    expected_engagement_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Reads and validates cache snapshot payload from disk."""
    if not cache_file.is_file():
        return None

    try:
        with open(cache_file, "rb") as cf:
            cached_data = pickle.load(cf)
    except Exception as ce:
        logger.debug("Copilot graph cache read failed: %s", ce)
        return None

    if not isinstance(cached_data, dict) or "in_mem" not in cached_data:
        return None

    in_mem = cached_data["in_mem"]
    if graph_hash and getattr(in_mem, "graph_sha256", None) != graph_hash:
        return None
    if expected_engagement_id:
        cached_eng = cached_data.get("engagement_id")
        if cached_eng and str(cached_eng).lower() != str(expected_engagement_id).lower():
            return None
    return cached_data


def _init_retriever_from_cache(
    retriever: Any,
    in_mem: Any,
    cached_data: Dict[str, Any],
    attendees: Optional[List[Dict[str, str]]],
    seed_identities_fn: Any,
) -> None:
    """Populates index maps and seed identities from cached snapshot."""
    retriever._inverted_index = defaultdict(set, {k: set(v) for k, v in cached_data.get("inverted_index", {}).items()})
    retriever._token_idf = cached_data.get("token_idf", {})
    retriever._attendee_node_ids = set()
    retriever._attendee_tokens = set()
    retriever._salient_assertions = defaultdict(list, cached_data.get("salient_assertions", {}))
    retriever._allowed_nodes = set(cached_data.get("allowed_nodes", set()))
    retriever._claim_inverted_index = defaultdict(list, cached_data.get("claim_inverted_index", {}))
    retriever._indexed_assertion_records = cached_data.get("indexed_assertion_records", {})
    retriever._node_latest_epoch = cached_data.get("node_latest_epoch", {})
    retriever._reference_epoch = float(cached_data.get("reference_epoch", 0.0) or 0.0)
    retriever._scope_filtered_count = 0
    if attendees and getattr(in_mem, "nodes", None):
        seed_identities_fn(
            attendees,
            in_mem.nodes,
            retriever._inverted_index,
            retriever._attendee_tokens,
            retriever._attendee_node_ids,
            retriever._allowed_nodes,
            retriever._salient_assertions,
            getattr(in_mem, "assertions", None),
        )


def _attendee_signature(attendees: Optional[Any]) -> List[str]:
    if not attendees:
        return []
    out: List[str] = []
    for a in attendees:
        raw = (a.get("name") or "") if isinstance(a, dict) else (a or "")
        val = str(raw).strip().lower()
        if val:
            out.append(val)
    return sorted(out)


def _init_neighborhood_from_cache(
    retriever: Any,
    in_mem: Any,
    cached_data: Dict[str, Any],
    attendees: Optional[List[Dict[str, str]]],
    engagement_id: Optional[str],
    precompute_timeout_seconds: float,
    init_neighborhood_fn: Any,
    neighborhood_store_cls: Any,
) -> None:
    """Initializes neighborhood store from cached chains or falls back to computation."""
    req_att_sig = _attendee_signature(attendees)
    cached_att_sig = cached_data.get("attendees_sig", [])
    cached_eng = cached_data.get("engagement_id")
    eng_matches = not engagement_id or (cached_eng and str(cached_eng).lower() == str(engagement_id).lower())
    att_matches = not attendees or (req_att_sig == cached_att_sig)
    if "neighborhood_chains" in cached_data and cached_data["neighborhood_chains"] and eng_matches and att_matches:
        retriever.neighborhood_store = neighborhood_store_cls(
            chains=cached_data["neighborhood_chains"],
            status=cached_data.get("neighborhood_status", "COMPLETE"),
            engagement_id=engagement_id,
            permitted_engagements=retriever.permitted_engagements,
        )
    else:
        retriever.neighborhood_store = init_neighborhood_fn(
            in_mem, retriever, attendees, engagement_id, precompute_timeout_seconds
        )


def load_cached_retriever(
    cache_file: Path,
    graph_hash: Optional[str],
    gen_id: Any,
    pair_data: Dict[str, Any],
    active_pair_path: Optional[Path],
    attendees: Optional[List[str]],
    engagement_id: Optional[str],
    session_id: Optional[str],
    precompute_timeout_seconds: float,
    retriever_cls: Any,
    seed_identities_fn: Any,
    init_neighborhood_fn: Any,
    inherit_engagements_fn: Any,
    neighborhood_store_cls: Any,
    scope_binding_cls: Any,
) -> Tuple[Optional[Any], Optional[Any]]:
    """Loads cached topology and instantiates CopilotGraphRetriever from disk cache."""
    cached_data = _read_cache_payload(cache_file, graph_hash, expected_engagement_id=engagement_id)
    if not cached_data:
        return None, None

    in_mem = cached_data["in_mem"]
    rel_sidecar = pair_data.get("sidecar_path")
    full_sidecar_path = (active_pair_path.parent / rel_sidecar).resolve() if (rel_sidecar and active_pair_path) else None
    retriever = retriever_cls.__new__(retriever_cls)
    retriever._topology = in_mem
    retriever._attendees = attendees or []
    retriever.graph_sha256 = graph_hash
    retriever.generation = gen_id
    retriever.neighborhood_store = None
    retriever.engagement_id = engagement_id
    retriever.session_id = session_id
    retriever.sidecar_path = full_sidecar_path
    retriever.enable_cross_encoder = False
    retriever.enable_1hop_graph_expansion = True
    retriever.cross_encoder_fn = None
    retriever.embed_fn = None
    retriever._dense_matrix = None
    retriever._dense_record_ids = []
    retriever._record_id_to_idx = {}
    retriever._dense_record_to_assertion_id = {}
    retriever.permitted_engagements = set()
    retriever.topological_engagements = inherit_engagements_fn(in_mem, engagement_id) if engagement_id else set()
    if engagement_id or retriever.topological_engagements:
        base = {engagement_id} if engagement_id else set()
        retriever.permitted_engagements = base | retriever.topological_engagements
    retriever._scope_binding = scope_binding_cls(
        session_id=session_id or "", engagement_id=engagement_id or "", generation=gen_id or 0
    )
    _init_retriever_from_cache(retriever, in_mem, cached_data, attendees, seed_identities_fn)
    _init_neighborhood_from_cache(
        retriever,
        in_mem,
        cached_data,
        attendees,
        engagement_id,
        precompute_timeout_seconds,
        init_neighborhood_fn,
        neighborhood_store_cls,
    )
    logger.info("GraphLoadStatus.LOADED_ACTIVE_PAIR: loaded from cache snapshot %s", cache_file)
    return in_mem, retriever


def save_cached_retriever(cache_file: Path, in_mem: Any, retriever: Any) -> None:
    """Serializes in-memory topology, indexes, and chains to disk atomically."""
    try:
        cache_payload = {
            "in_mem": in_mem,
            "engagement_id": getattr(retriever, "engagement_id", None),
            "attendees_sig": _attendee_signature(getattr(retriever, "_attendees", None)),
            "inverted_index": dict(retriever._inverted_index),
            "token_idf": retriever._token_idf,
            "salient_assertions": dict(retriever._salient_assertions),
            "allowed_nodes": retriever._allowed_nodes,
            "claim_inverted_index": dict(retriever._claim_inverted_index),
            "indexed_assertion_records": retriever._indexed_assertion_records,
            "node_latest_epoch": getattr(retriever, "_node_latest_epoch", {}),
            "reference_epoch": getattr(retriever, "_reference_epoch", 0.0),
            "neighborhood_chains": retriever.neighborhood_store.chains if retriever.neighborhood_store else {},
            "neighborhood_status": retriever.neighborhood_store.status if retriever.neighborhood_store else "ABSENT",
        }
        tmp_cache = cache_file.with_suffix(".tmp")
        with open(tmp_cache, "wb") as cf:
            pickle.dump(cache_payload, cf, protocol=pickle.HIGHEST_PROTOCOL)
        tmp_cache.replace(cache_file)
        logger.info("Persisted copilot graph cache snapshot to %s", cache_file)
    except Exception as pe:
        logger.warning("Failed to persist copilot graph cache: %s", pe)


def execute_load_copilot_graph(
    attendees: Optional[List[Dict[str, str]]],
    runtime_root: Optional[Union[str, Path]],
    engagement_id: Optional[str],
    session_id: Optional[str],
    precompute_timeout_seconds: float,
    graph_path: Optional[Union[str, Path]],
    retriever_cls: Any,
    graph_load_status_cls: Any,
    seed_identities_fn: Any,
    init_neighborhood_fn: Any,
    inherit_engagements_fn: Any,
    neighborhood_store_cls: Any,
    scope_binding_cls: Any,
    bypass_cache: bool = False,
) -> Tuple[Any, Optional[Any], Optional[Any]]:
    """Loads active knowledge graph generation into memory with fail-closed verification."""
    try:
        from dual_pipeline.in_memory_graph import GraphIntegrityError, InMemoryGraphTopology

        r_root, active_pair_path, graph_hash, full_graph_path, pair_data = resolve_graph_paths_and_hash(
            runtime_root, graph_path
        )
        if pair_data.get("_integrity_error"):
            diag = str(pair_data["_integrity_error"])
            return GraphLoadResult(graph_load_status_cls.HASH_MISMATCH, None, None, diagnostic_detail=diag)

        if not r_root or not full_graph_path:
            diag = get_last_copilot_graph_diagnostic() or "active_pair or graph_path absent"
            return GraphLoadResult(graph_load_status_cls.ABSENT, None, None, diagnostic_detail=diag)

        gen_id = pair_data.get("generation_id") or pair_data.get("generation") or "gen"
        cache_file = resolve_cache_file(active_pair_path, r_root, gen_id, graph_hash, engagement_id=engagement_id)

        if not bypass_cache:
            in_mem, retriever = load_cached_retriever(
                cache_file=cache_file,
                graph_hash=graph_hash,
                gen_id=gen_id,
                pair_data=pair_data,
                active_pair_path=active_pair_path,
                attendees=attendees,
                engagement_id=engagement_id,
                session_id=session_id,
                precompute_timeout_seconds=precompute_timeout_seconds,
                retriever_cls=retriever_cls,
                seed_identities_fn=seed_identities_fn,
                init_neighborhood_fn=init_neighborhood_fn,
                inherit_engagements_fn=inherit_engagements_fn,
                neighborhood_store_cls=neighborhood_store_cls,
                scope_binding_cls=scope_binding_cls,
            )
            if in_mem is not None and retriever is not None:
                return GraphLoadResult(graph_load_status_cls.LOADED_ACTIVE_PAIR, in_mem, retriever, diagnostic_detail=None)

        try:
            in_mem = InMemoryGraphTopology.load_from_sqlite(full_graph_path, expected_sha256=graph_hash)
            rel_sidecar = pair_data.get("sidecar_path")
            full_sidecar_path = (
                (active_pair_path.parent / rel_sidecar).resolve() if (rel_sidecar and active_pair_path) else None
            )
            retriever = retriever_cls(
                in_mem,
                attendees=attendees,
                graph_sha256=graph_hash,
                generation=gen_id,
                engagement_id=engagement_id,
                session_id=session_id,
                sidecar_path=full_sidecar_path,
                enable_1hop_graph_expansion=True,
            )
            retriever.neighborhood_store = init_neighborhood_fn(
                in_mem, retriever, attendees, engagement_id, precompute_timeout_seconds
            )
            save_cached_retriever(cache_file, in_mem, retriever)
            logger.info("GraphLoadStatus.LOADED_ACTIVE_PAIR: successfully loaded active pair")
            return GraphLoadResult(graph_load_status_cls.LOADED_ACTIVE_PAIR, in_mem, retriever, diagnostic_detail=None)
        except GraphIntegrityError as e:
            diag = str(e)
            _set_last_copilot_graph_diagnostic(diag)
            logger.warning("GraphLoadStatus.HASH_MISMATCH: %s", e)
            return GraphLoadResult(graph_load_status_cls.HASH_MISMATCH, None, None, diagnostic_detail=diag)

    except Exception as e:
        diag = str(e)
        _set_last_copilot_graph_diagnostic(diag)
        logger.warning("GraphLoadStatus.ERROR: %s", e)
        return GraphLoadResult(graph_load_status_cls.ERROR, None, None, diagnostic_detail=diag)
