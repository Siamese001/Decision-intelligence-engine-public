"""
dual_pipeline/canonical_pipeline/publish_helper.py

Publication, graph mutation, exit gate verification, and result assembly
helpers for CanonicalAgenticPipeline.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.canonical_pipeline.lifecycle import check_directory_compliance
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.transcript_metadata_helper import sanitize_metadata_for_persistence
from dual_pipeline.unified_agentic_pipeline import PipelineStage

logger = logging.getLogger("CanonicalAgenticPipeline.publish_helper")


def extract_proposed_entities(f3_path_str: str) -> list:
    """Extract proposed entity upserts from Output 03 graph file."""
    if not f3_path_str or not Path(f3_path_str).is_file():
        return []
    try:
        return json.loads(Path(f3_path_str).read_text(encoding="utf-8")).get("graph_mutations", {}).get("entity_upserts", [])
    except Exception as err:
        raise RuntimeError(f"Failed to extract proposed entities from '{f3_path_str}': {err}") from err


def _load_json_deliverable(p: Path, name: str) -> dict[str, Any]:
    if not p.is_file():
        raise FileNotFoundError(f"Missing required {name} file: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Corrupted {name} file: {err}") from err


def _verify_exception_receipt_signature(
    out_p: Path, d_data: Dict[str, Any], j_data: Dict[str, Any], metadata: Dict[str, Any]
) -> None:
    verdict = j_data.get("overall_verdict") or j_data.get("status")
    if d_data.get("status") != "PASS_AUTHORIZED_EXCEPTION" and verdict != "PASS_AUTHORIZED_EXCEPTION":
        return
    exc_rec = d_data.get("authorized_exception_receipt") or j_data.get("authorized_exception_receipt") or metadata.get("authorized_exception_receipt")
    exc_file = out_p / "authorized_exception_receipt.json"
    if exc_rec is None and exc_file.is_file():
        exc_rec = _load_json_deliverable(exc_file, "authorized exception receipt")
    if metadata.get("require_signed_exception_receipt") or isinstance(exc_rec, dict):
        sig = (exc_rec or {}).get("signature") or (exc_rec or {}).get("exception_receipt_signature") if isinstance(exc_rec, dict) else None
        if not sig or not str(sig).strip():
            raise RuntimeError(
                f"UNSIGNED_AUTHORIZED_EXCEPTION: GraphDB mutation blocked for '{out_p.name}': missing valid exception_receipt_signature."
            )


def _assert_authoritative_mutation_preconditions(out_p: Path, metadata: Dict[str, Any]) -> None:
    """Ensure synthesis and evaluation deliverables were not mock or short-circuited."""
    if metadata.get("is_mock") or metadata.get("provider") == "offline":
        raise RuntimeError(f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: GraphDB mutation blocked for '{out_p.name}'.")
    d_data = _load_json_deliverable(out_p / "final_release_decision.json", "release decision")
    if d_data.get("short_circuit") or d_data.get("is_mock") or d_data.get("mock"):
        raise RuntimeError(f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: GraphDB mutation blocked for '{out_p.name}'.")
    if d_data.get("status") not in ("PASS", "APPROVED", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"):
        raise RuntimeError(f"Fail-Fast: GraphDB mutation blocked for '{out_p.name}': status={d_data.get('status')}")
    j_data = _load_json_deliverable(out_p / "llm_judge_evaluation.json", "judge evaluation")
    if j_data.get("short_circuit") or j_data.get("is_mock") or j_data.get("mock") or j_data.get("evaluator_model") == "mock" or j_data.get("evaluation_mode") == "SKIPPED_DETERMINISTIC_QA_FAILURE":
        raise RuntimeError(f"MOCK_SYNTHESIS_NOT_AUTHORITATIVE: GraphDB mutation blocked for '{out_p.name}'.")
    verdict = j_data.get("overall_verdict") or j_data.get("status")
    if verdict not in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"):
        raise RuntimeError(f"Fail-Fast: GraphDB mutation blocked for '{out_p.name}': verdict={verdict}")
    _verify_exception_receipt_signature(out_p, d_data, j_data, metadata)
    d_manifest = d_data.get("artifact_manifest", {}) if isinstance(d_data, dict) else {}
    d_digest = d_manifest.get("judge_evaluation_digest") or d_data.get("judge_evaluation_digest")
    j_digest = j_data.get("evaluation_receipt_digest")
    if j_digest and d_digest and j_digest != d_digest:
        raise RuntimeError(
            f"Fail-Fast: GraphDB mutation blocked for '{out_p.name}': "
            f"decision judge digest '{d_digest}' != judge report digest '{j_digest}'"
        )


_PROJECTION_CATEGORIES = (
    "entity_upserts",
    "relationship_signals",
    "decision_updates",
    "proposal_updates",
    "risk_updates",
    "strategic_intent_signals",
)


def _format_projection_item(item: Dict[str, Any]) -> str:
    if "name" in item:
        n_str = str(item.get("name") or "").strip()
        d_str = str(item.get("description") or "").strip()
        return f"{n_str}\n{d_str}".strip() if d_str else n_str
    if "subject" in item or "predicate" in item:
        return f"{item.get('subject', '')} {item.get('predicate', '')} {item.get('object', '')}".strip()
    for key in ("title", "summary", "description", "text"):
        val = item.get(key)
        if val:
            return str(val).strip()
    return ""


def _collect_staging_records(sig_file: Path) -> List[Dict[str, Any]]:
    import hashlib
    records: List[Dict[str, Any]] = []
    muts = json.loads(sig_file.read_text(encoding="utf-8")).get("graph_mutations", {})
    for cat_key in _PROJECTION_CATEGORIES:
        for item in muts.get(cat_key) or []:
            if isinstance(item, dict):
                text = _format_projection_item(item)
                if text:
                    th = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    records.append({"record_id": item.get("id") or th[:16], "retrieval_text": text, "retrieval_text_hash": th})
    return records


def _prestage_intake_vectors_unlocked(
    out_p: Path, meta: Dict[str, Any], written: Dict[str, str], embedding_backend: Any
) -> None:
    """Pre-compute candidate retrieval embeddings in parallel before acquiring Stage 7 lock."""
    staged_db = out_p / "vector_staging.sqlite"
    if staged_db.is_file():
        written["vector_staging"] = str(staged_db)
        meta["vector_staging_path"] = str(staged_db)
        return
    sig_str = written.get("03_graph")
    sig_file = Path(str(sig_str)) if sig_str else None
    if not (sig_file and sig_file.is_file()):
        return
    try:
        from dual_pipeline.index_core.backends import get_backend
        from dual_pipeline.index_core.vector_staging import encode_and_stage_records

        records = _collect_staging_records(sig_file)
        if not records:
            return
        backend = (
            embedding_backend
            if (not isinstance(embedding_backend, str) and hasattr(embedding_backend, "backend_id"))
            else get_backend(str(embedding_backend or "bge-m3"), allow_dev_backend=True, allow_internal_backend=True)
        )
        if encode_and_stage_records(staged_db, backend, records) > 0:
            written["vector_staging"] = str(staged_db)
            meta["vector_staging_path"] = str(staged_db)
    except Exception as exc:
        logger.debug("Pre-staging intake vectors skipped: %s", exc)


def publish_graph_if_requested(
    mutate_graph: bool, intake_pkg_path: Path, metadata: Dict[str, Any],
    written: Dict[str, str], directive_id: Optional[str], reviewer: Optional[str],
    rationale: Optional[str], embedding_backend: str,
) -> Optional[Any]:
    """Execute graph publication if mutation is requested."""
    if not mutate_graph:
        return None
    _prestage_intake_vectors_unlocked(intake_pkg_path.parent, metadata, written, embedding_backend)
    return _guarded_graph_publication(
        intake_pkg_path, metadata, written, directive_id, reviewer, rationale,
        embedding_backend,
    )


def _validate_auth_and_runtime(
    out_p: Path, meta: Dict[str, Any], written: Dict[str, str], release_runner: Any,
) -> tuple[Dict[str, Any], Path, Optional[Dict[str, Any]]]:
    from dual_pipeline.canonical_pipeline.document_resume import is_document_request
    from .publication_bindings import canonical_publication_runtime

    is_replay = bool(
        meta.get("force_rerun")
        or meta.get("is_replay")
        or meta.get("regenerate")
        or meta.get("auto_regenerate_track1")
    )
    if is_replay:
        for r_key in ("approval_receipt", "mutation_approval_receipt"):
            r_val = meta.get(r_key)
            if isinstance(r_val, dict) and r_val.get("status") == "ACTIVATED":
                meta.pop(r_key, None)
        receipt_file = out_p / "mutation_approval_receipt.json"
        if receipt_file.is_file():
            try:
                rdata = json.loads(receipt_file.read_text(encoding="utf-8"))
                if rdata.get("status") == "ACTIVATED":
                    receipt_file.unlink(missing_ok=True)
            except Exception:
                pass

    from dual_pipeline.config.pipeline_settings import is_attachment_mutation_automation_enabled
    is_doc = is_document_request(meta) or (not written and not meta.get("meeting_name") and not meta.get("session_id"))
    requires_auth = (
        not is_doc
        or is_attachment_mutation_automation_enabled(meta)
        or meta.get("require_mutation_approval")
        or meta.get("require_approval")
        or bool(meta.get("approval_receipt"))
        or bool(meta.get("mutation_approval_receipt"))
        or bool(meta.get("approval_request_id"))
    )
    runtime = canonical_publication_runtime(release_runner.ROOT)
    requested = Path(meta["runtime_root"]).resolve() if meta.get("runtime_root") else runtime
    if requested != runtime:
        if canonical_publication_runtime(requested) == runtime or ".worktrees" in requested.parts:
            requested = runtime
            meta["runtime_root"] = str(runtime)
        else:
            raise RuntimeError("PUBLICATION_RUNTIME_MISMATCH: publication and maintenance runtime roots must match before publication can proceed")

    auth_info: Dict[str, Any] = {}
    if requires_auth:
        from dual_pipeline.hitl_governance_core.mutation_authorization import validate_mutation_authorization
        auth_info = validate_mutation_authorization(
            output_dir=out_p, metadata=meta, written=written, runtime_root=runtime,
        )
        if auth_info.get("idempotent") and auth_info.get("status") == "ACTIVATED":
            cov_file = out_p / "publication_fact_coverage_receipt.json"
            cov_failed = False
            if cov_file.is_file():
                try:
                    cdata = json.loads(cov_file.read_text(encoding="utf-8"))
                    if cdata.get("status") in ("FAILED", "FAIL", "UNVERIFIED"):
                        cov_failed = True
                except Exception:
                    cov_failed = True
            elif meta.get("require_fact_coverage"):
                cov_failed = True
            if cov_failed:
                raise RuntimeError(
                    f"PUBLICATION_COVERAGE_UNVERIFIED: Cannot short-circuit idempotent ACTIVATED receipt in '{out_p}' with failed or missing fact coverage receipt."
                )
            logger.info("Graph mutation already committed for this proposal (idempotent recovery).")
            return auth_info, Path(), {"status": "COMPLETE", "idempotent": True, "recovered": True}

    return auth_info, runtime, None


def _fetch_graph_nodes_for_reembed(g_path: Path, mutated_node_ids: Optional[List[str]] = None) -> List[Any]:
    from storage.connection import get_db_connection

    g_conn = get_db_connection(g_path, readonly=True)
    try:
        has_node = g_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='node'").fetchone()
        if not has_node:
            return []
        cols = {r[1] for r in g_conn.execute("PRAGMA table_info(node)").fetchall()}
        role_col = "role" if "role" in cols else ("node_type" if "node_type" in cols else "''")
        sum_col = "summary" if "summary" in cols else "''"
        if mutated_node_ids:
            ph = ",".join("?" for _ in mutated_node_ids)
            sql_in = "SELECT node_id, canonical_name, " + role_col + ", " + sum_col + " FROM node WHERE node_id IN (" + ph + ") OR canonical_name IN (" + ph + ")"
            return g_conn.execute(
                sql_in,
                [*mutated_node_ids, *mutated_node_ids],
            ).fetchall()
        sql_all = "SELECT node_id, canonical_name, " + role_col + ", " + sum_col + " FROM node"
        return g_conn.execute(sql_all).fetchall()
    finally:
        g_conn.close()


def _update_dense_vector_hashes(s_conn: Any, rows: List[Any]) -> int:
    import hashlib
    s_tables = {r[0] for r in s_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "dense_vector" not in s_tables:
        return 0
    dv_cols = {r[1] for r in s_conn.execute("PRAGMA table_info(dense_vector)").fetchall()}
    id_col = "object_id" if "object_id" in dv_cols else ("record_id" if "record_id" in dv_cols else None)
    sha_col = "text_sha256" if "text_sha256" in dv_cols else ("content_sha256" if "content_sha256" in dv_cols else None)
    if not id_col or not sha_col:
        return 0
    sql_upd = "UPDATE dense_vector SET " + sha_col + " = ? WHERE " + id_col + " = ?"
    for nid, cname, role_val, sum_val in rows:
        text_payload = f"{cname or ''} | {role_val or ''} | {sum_val or ''}".strip(" |")
        text_sha = hashlib.sha256(text_payload.encode("utf-8")).hexdigest()
        s_conn.execute(sql_upd, (text_sha, nid))
    return len(rows)


def _reembed_mutated_sidecar_nodes(
    g_path: Path, sidecar_p: Path, mutated_node_ids: Optional[List[str]] = None
) -> int:
    """Re-embeds mutated graph nodes into vector_sidecar.sqlite before updating graph_sha256."""
    from storage.connection import get_db_connection

    if not g_path.is_file() or not sidecar_p.is_file():
        return 0
    try:
        rows = _fetch_graph_nodes_for_reembed(g_path, mutated_node_ids)
        if not rows:
            return 0
        s_conn = get_db_connection(sidecar_p, readonly=False)
        try:
            reembedded = _update_dense_vector_hashes(s_conn, rows)
            s_conn.commit()
            return reembedded
        finally:
            s_conn.close()
    except Exception as exc:
        logger.debug("Sidecar node re-embedding skipped: %s", exc)
        return 0


def _sync_sidecar_for_reseal(
    rt_root: Path, g_path: Path, data: Dict[str, Any], m_data: Optional[Dict[str, Any]], actual_hash: str, mutated_node_ids: Optional[List[str]]
) -> None:
    from dual_pipeline.runtime_root import _compute_sha256
    from dual_pipeline.uwg_core.universal_write_gateway_sidecar import sync_sidecar_metadata_graph_hash

    sidecar_rel = data.get("sidecar_path")
    sidecar_p = (rt_root / sidecar_rel).resolve() if sidecar_rel else (g_path.parent / "vector_sidecar.sqlite")
    if not sidecar_p.is_file():
        sidecar_p = g_path.parent / "vector_sidecar.sqlite"
    if not sidecar_p.is_file():
        return
    try:
        _reembed_mutated_sidecar_nodes(g_path, sidecar_p, mutated_node_ids=mutated_node_ids)
        sync_sidecar_metadata_graph_hash(sidecar_p, actual_hash)
        data["sidecar_sha256"] = _compute_sha256(sidecar_p)
        if m_data is not None:
            m_data["sidecar_sha256"] = data["sidecar_sha256"]
    except Exception as s_exc:
        logger.warning("Could not sync sidecar metadata: %s", s_exc)


def _re_seal_active_pair_if_needed(
    rt_root: Path, g_path: Path, mutated_node_ids: Optional[List[str]] = None
) -> None:
    """Re-embeds mutated nodes into vector_sidecar.sqlite and re-seals active_pair.json if candidate graph hash drifted.
    Strictly forbids in-place re-sealing of immutable sealed generation databases under generations/<gen_id>/."""
    from dual_pipeline.storage.governed_context import is_sealed_generation_database

    if is_sealed_generation_database(g_path):
        raise RuntimeError(
            f"SEALED_GENERATION_RESEAL_FORBIDDEN: In-place re-sealing of active_pair.json is prohibited for sealed generation database '{g_path}'. Route through ReleaseActivationCoordinator."
        )
    pair_path = rt_root / "active_pair.json"
    if not pair_path.is_file() or not g_path.is_file():
        return
    data = json.loads(pair_path.read_text(encoding="utf-8"))
    if data.get("immutable_sealed") is True:
        raise RuntimeError(f"SEALED_GENERATION_RESEAL_FORBIDDEN: active_pair.json is marked immutable_sealed for '{g_path}'.")
    try:
        from dual_pipeline.runtime_root import _compute_sha256
        actual_hash = _compute_sha256(g_path)
        if data.get("graph_sha256") != actual_hash:
            data["graph_sha256"] = actual_hash
            manifest_p = g_path.parent / "pair_manifest.json"
            m_data = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.is_file() else None
            if m_data is not None:
                m_data["graph_sha256"] = actual_hash
            _sync_sidecar_for_reseal(rt_root, g_path, data, m_data, actual_hash, mutated_node_ids)
            pair_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            if m_data is not None and manifest_p.is_file():
                manifest_p.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
            logger.info("Re-sealed active_pair.json with updated graph_sha256: %s", actual_hash[:12])
    except Exception as exc:
        logger.warning("Could not re-seal active_pair.json: %s", exc)


def _apply_role_resolutions_to_graph(
    role_resolutions: Dict[str, Any],
    g_path: Path,
    rev_op: str,
    rt_root: Optional[Path] = None,
) -> None:
    """Applies role resolutions to candidate graph, forbidding writes to sealed generations."""
    from dual_pipeline.storage.governed_context import is_sealed_generation_database

    if is_sealed_generation_database(g_path):
        logger.info("Skipping role persistence on sealed generation database: %s", g_path)
        return

    from dual_pipeline.transcript_metadata_core.entity_resolver_write import persist_validated_role

    mutated_names: List[str] = []
    for name, res_val in role_resolutions.items():
        r_str = getattr(res_val, "resolved_role", None) or (
            res_val.get("resolved_role") if isinstance(res_val, dict) else str(res_val)
        )
        if r_str:
            try:
                persist_validated_role(
                    graph_db=g_path,
                    canonical_name=name,
                    selected_role=r_str,
                    operator_id=rev_op,
                )
                mutated_names.append(name)
            except Exception as p_err:
                logger.warning("Could not persist role resolution for '%s' to graph: %s", name, p_err)

    if rt_root:
        _re_seal_active_pair_if_needed(rt_root, g_path, mutated_node_ids=mutated_names)


def _pre_persist_role_resolutions_if_needed(
    meta: Dict[str, Any], written: Dict[str, str], rev_op: str
) -> None:
    """Persists validated role resolutions to candidate graph before release activation seals it."""
    role_resolutions = meta.get("hitl_role_resolutions")
    if not role_resolutions:
        return
    cand_graph = written.get("03_graph") or meta.get("graph_path")
    if not (cand_graph and Path(cand_graph).is_file() and str(cand_graph).endswith((".sqlite", ".db"))):
        cand_graph = meta.get("graph_path") or meta.get("candidate_graph_path")
        if not (cand_graph and Path(cand_graph).is_file() and str(cand_graph).endswith((".sqlite", ".db"))):
            try:
                from .publication_verifier import _resolve_graph_path
                out_dir = Path(meta.get("target_output_dir") or meta.get("deliverable_dir") or ".")
                resolved_g = _resolve_graph_path(meta, output_dir=out_dir)
                if resolved_g and Path(resolved_g).is_file():
                    cand_graph = str(resolved_g)
            except Exception as res_err:
                logger.warning("Could not resolve candidate graph path for role pre-persistence: %s", res_err)
    if cand_graph and Path(cand_graph).is_file() and str(cand_graph).endswith((".sqlite", ".db")):
        _apply_role_resolutions_to_graph(role_resolutions, Path(cand_graph), rev_op)


def _handle_publication_activation(
    pub_res: Any, written: Dict[str, str], auth_info: Dict[str, Any], meta: Dict[str, Any], out_p: Path,
) -> Any:
    from dual_pipeline.hitl_governance_core.mutation_authorization import mark_mutation_activated
    from .publication_verifier import record_publication_fact_coverage
    from .speaker_role_coverage import record_speaker_role_coverage
    res = pub_res or written.get("intake_publish")
    req_id = auth_info.get("request_id")
    gen_id = ""
    if isinstance(res, dict):
        gen_id = (
            res.get("generation_id")
            or res.get("active_pair", {}).get("generation_id")
            or res.get("ingestion_completion", {}).get("generation_id")
            or res.get("ingestion_completion", {}).get("generation_after")
            or res.get("release", {}).get("activation", {}).get("active_pair", {}).get("generation_id")
            or auth_info.get("active_generation")
            or ""
        )
    if req_id and res and gen_id:
        mark_mutation_activated(
            request_id=req_id, runtime_root=meta.get("runtime_root") or out_p,
            activated_generation_id=str(gen_id), activation_receipt=res if isinstance(res, dict) else {},
        )
    if res:
        role_resolutions = meta.get("hitl_role_resolutions")
        if role_resolutions:
            from .publication_verifier import _resolve_graph_path
            g_path = _resolve_graph_path(meta, output_dir=out_p)
            if g_path and Path(g_path).is_file() and str(g_path).endswith((".sqlite", ".db")):
                rev_op = auth_info.get("reviewer") or meta.get("reviewer") or "human_operator"
                rt_root = Path(meta.get("runtime_root") or out_p).resolve()
                _apply_role_resolutions_to_graph(role_resolutions, Path(g_path), rev_op, rt_root=rt_root)
        cov_rec = record_publication_fact_coverage(res, written, auth_info, meta, out_p, str(gen_id))
        if isinstance(res, dict):
            if cov_rec:
                res["publication_coverage_receipt"] = cov_rec
                res["publication_coverage_verified"] = (cov_rec.get("status") == "VERIFIED")
                res["publication_coverage_status"] = cov_rec.get("status")
        role_cov = record_speaker_role_coverage(res, written, auth_info, meta, out_p, str(gen_id))
        if isinstance(res, dict) and role_cov:
            res["speaker_role_coverage_receipt"] = role_cov
    return res


def resolve_next_directive_id(
    runtime_root: Path,
    directive_id: str,
) -> str:
    """Auto-increments directive revision (-v2, -v3) if directive already completed with different bindings."""
    if not directive_id:
        return directive_id

    import re
    match = re.search(r"-v(\d+)$", directive_id)
    if match:
        ver = int(match.group(1))
        base = directive_id[:match.start()]
    else:
        ver = 1
        base = directive_id

    rt_path = runtime_root if isinstance(runtime_root, Path) else Path(str(runtime_root))
    while True:
        cand_id = f"{base}-v{ver}"
        legacy_file = rt_path / "ingestion_directives" / f"{cand_id}.json"
        journal_dir = rt_path / "publication_journals" / cand_id
        journal_res = journal_dir / "result.json"

        is_done = False
        if legacy_file.exists():
            try:
                d = json.loads(legacy_file.read_text(encoding="utf-8"))
                if (
                    d.get("completion")
                    or d.get("terminal")
                    or d.get("status") in ("COMPLETE", "CANCELLED", "ROLLED_BACK", "BLOCKED")
                ):
                    is_done = True
            except Exception:
                is_done = True
        if journal_res.exists():
            is_done = True
        pub_j_result = rt_path / "publication_journal" / f"{cand_id}.result.json"
        if pub_j_result.exists():
            is_done = True
        pub_j_intent = rt_path / "publication_journal" / f"{cand_id}.intent.json"
        if pub_j_intent.exists():
            try:
                intent_d = json.loads(pub_j_intent.read_text(encoding="utf-8"))
                if intent_d.get("status") in ("ROLLED_BACK", "RESULT_RECORDED"):
                    is_done = True
            except Exception:
                is_done = True

        if not is_done:
            return cand_id
        ver += 1


def _guarded_graph_publication(
    intake_pkg_path: Path, metadata: Dict[str, Any], written: Dict[str, str],
    directive_id: Optional[str], reviewer: Optional[str], rationale: Optional[str],
    embedding_backend: str,
) -> Any:
    from dual_pipeline.credentials import CredentialManager
    from dual_pipeline.release_policy import verify_authoritative_release_gate
    from dual_pipeline.transcript_pipeline_core import release_runner
    from .publication_bindings import canonical_publication_runtime, publication_bindings, runtime_publication_context
    from .publication_journal import PublicationReconciliationRequired, guarded_publication

    meta = copy.deepcopy(metadata)
    target_out_p = Path(meta.get("target_output_dir") or meta.get("deliverable_dir") or intake_pkg_path.parent).resolve()
    out_p = intake_pkg_path.parent
    staged_vector_file = out_p / "vector_staging.sqlite" if (out_p / "vector_staging.sqlite").is_file() else (target_out_p / "vector_staging.sqlite")
    if staged_vector_file.is_file():
        written["vector_staging"] = str(staged_vector_file)
        meta["vector_staging_path"] = str(staged_vector_file)

    runtime_root = canonical_publication_runtime(release_runner.ROOT)
    is_replay = bool(
        meta.get("force_rerun")
        or meta.get("is_replay")
        or meta.get("regenerate")
        or meta.get("auto_regenerate_track1")
    )

    from .parallel_orchestrator import stage7_publication_mutex

    with stage7_publication_mutex(runtime_root):
        if directive_id and (not meta.get("resume") or is_replay):
            directive_id = resolve_next_directive_id(runtime_root, directive_id)
            meta["directive_id"] = directive_id
            if "directive_id" in written:
                written["directive_id"] = directive_id

        auth_dir = out_p if (out_p / "mutation_approval_receipt.json").is_file() else target_out_p
        auth_info, runtime, early = _validate_auth_and_runtime(auth_dir, meta, written, release_runner)
        if early:
            return early

        reviewer = (
            reviewer or (auth_info.get("subject") if isinstance(auth_info, dict) else None)
            or (auth_info.get("reviewer") if isinstance(auth_info, dict) else None)
            or CredentialManager.get_credential("NEE_REVIEWER") or meta.get("reviewer") or meta.get("author") or "human_operator"
        )
        meeting = meta.get("meeting_name") or meta.get("title") or "Meeting"
        date = meta.get("date") or meta.get("date_filename") or "Date"
        rationale = rationale or f"Ingest {meeting} ({date}) into {meta.get('project') or 'knowledge'} knowledge graph"

        def bindings() -> Dict[str, Any]:
            if not isinstance(reviewer, str) or not reviewer.strip():
                raise ValueError("Governed publication requires an explicit human reviewer")
            _assert_authoritative_mutation_preconditions(out_p, meta)
            check_directory_compliance(out_p, meta.get("project"))
            verify_authoritative_release_gate([intake_pkg_path])
            rev = "5617a9f61b028005a4858fdac845db406aefb181"
            return publication_bindings(
                intake_pkg_path, meta, directive_id=directive_id, reviewer=reviewer, rationale=rationale,
                embedding_backend=embedding_backend, runtime=runtime, repository_root=release_runner.ROOT,
                model_path=release_runner._resolve_model_asset(f"models/bge-m3/{rev}", "NEE_BGE_M3_MODEL_PATH"),
                model_manifest=release_runner._resolve_model_asset(f"models/bge-m3/bge-m3-manifest-{rev}.json", "NEE_BGE_M3_MODEL_MANIFEST"),
            )

        def publish() -> Any:
            from dual_pipeline.l0.ingestion_timing import timed_child_span
            pub_res = None
            activated = False
            with stage7_publication_mutex():
                try:
                    rev_op = auth_info.get("reviewer") or reviewer or "human_operator"
                    _pre_persist_role_resolutions_if_needed(meta, written, rev_op)
                    with timed_child_span("graph_transaction"):
                        pub_res = release_runner.publish_release_to_graph(
                            package_path=intake_pkg_path, metadata=meta, written=written, directive_id=directive_id,
                            reviewer=reviewer, rationale=rationale, embedding_backend=embedding_backend,
                        )
                    if isinstance(pub_res, dict) and pub_res.get("status") == "AWAITING_APPROVAL":
                        return pub_res
                    if isinstance(pub_res, dict) and (pub_res.get("receipt_path") or pub_res.get("activated_generation_id")):
                        activated = True
                    return _handle_publication_activation(pub_res, written, auth_info, meta, out_p)
                except Exception as pub_exc:
                    logger.error("Authoritative graph publication failed: %s", pub_exc, exc_info=True)
                    rollback_succeeded = False
                    if activated:
                        receipt_p = pub_res.get("receipt_path") if isinstance(pub_res, dict) else None
                        if receipt_p and Path(receipt_p).is_file():
                            try:
                                from dual_pipeline.release_core.manager import ReleaseManager
                                rm = ReleaseManager(
                                    runtime_root=runtime if isinstance(runtime, Path) else getattr(runtime, "runtime_root", out_p),
                                    graph_path=meta.get("graph_path") or (release_runner.ROOT / "graph" / "nee_graph.sqlite"),
                                )
                                rm.rollback(Path(receipt_p))
                                rollback_succeeded = True
                            except Exception as rb_exc:
                                logger.error("ReleaseManager rollback failed after publication error: %s", rb_exc, exc_info=True)
                                rollback_succeeded = False
                    else:
                        rollback_succeeded = True

                    try:
                        from .publication_journal import mark_publication_rolled_back
                        mark_publication_rolled_back(
                            runtime, directive_id, reason=str(pub_exc), zero_delta=rollback_succeeded
                        )
                    except Exception as j_exc:
                        logger.warning("Failed to record publication rollback journal: %s", j_exc)
                    raise

        try:
            return guarded_publication(runtime, directive_id, bindings, publish, lambda: runtime_publication_context(runtime, directive_id), allow_resume=True)
        except PublicationReconciliationRequired:
            if not directive_id:
                raise
            directive_id = resolve_next_directive_id(runtime_root, directive_id)
            meta["directive_id"] = directive_id
            return guarded_publication(runtime, directive_id, bindings, publish, lambda: runtime_publication_context(runtime, directive_id), allow_resume=True)


def check_idempotent_or_continue(
    tracker: Any, output_dir: str, meta: Dict[str, Any],
    input_hash: str, mutate_graph: bool,
) -> Optional[Dict[str, Any]]:
    """Check for idempotent run and complete U0 early if noop."""
    # Document resume reuses only sealed synthesis; all downstream gates rerun.
    if (meta.get("is_document") or meta.get("source_type") == "document") and (meta.get("resume") or meta.get("require_resume")):
        return None
    wants_mutation = bool(mutate_graph or meta.get("auto_mutate_on_pass"))
    from dual_pipeline.transcript_pipeline_core.caching import check_idempotent_meeting_run
    src_hash = meta.get("source_file_hash") or meta.get("file_hash")
    idempotent = check_idempotent_meeting_run(output_dir, meta, source_file_hash=src_hash, content_hash=input_hash)
    if not idempotent:
        return None
    if wants_mutation:
        rf = Path(output_dir) / "receipt.json"
        if not rf.is_file():
            nested_rfs = sorted(list(Path(output_dir).glob("**/receipt.json")), key=lambda p: len(p.parts))
            rf = nested_rfs[0] if nested_rfs else rf
        if not rf.is_file():
            pirfs = sorted(list(Path(output_dir).glob("**/pipeline_execution_receipt.json")), key=lambda p: len(p.parts))
            rf = pirfs[0] if pirfs else rf
        try:
            rd = json.loads(rf.read_text(encoding="utf-8")) if rf.is_file() else {}
            cov_f = Path(output_dir) / "publication_coverage_receipt.json"
            cov_ok = json.loads(cov_f.read_text(encoding="utf-8")).get("status") == "VERIFIED" if cov_f.is_file() else False
            outcome_ok = rd.get("terminal_outcome") in ("COMPLETE", "PASS") or (rd.get("terminal_outcome") == "PUBLISHED_UNVERIFIED" and cov_ok)
            has_graph = outcome_ok and bool(
                rd.get("graph_mutated") or rd.get("active_generation") or rd.get("details", {}).get("graph_mutated")
                or rd.get("stages", {}).get("EVALS_RELEASE", {}).get("details", {}).get("graph_mutated")
            )
        except Exception:
            has_graph = False
        if not has_graph:
            return None
    tracker.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details={"idempotent_noop": True})
    return idempotent


def verify_exit_qa(tracker: Any, out_p: Path, eval_result: Any, attempt: int, synthesis_result: Optional[Any] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Enforce X1-X3 gate clearance or raise failure."""
    from dual_pipeline.l0.ingestion_timing import timed_child_span
    with timed_child_span("validation", {"attempt": attempt, "stage": "X_EXIT_GATES"}):
        recap_text = ""
        f3_str = ""
        f1_text = ""
        f3_files = sorted(out_p.glob("03_*_canonical_signal_projection.json")) if (out_p and out_p.exists()) else []
        has_synth = synthesis_result and (
            getattr(synthesis_result, "raw_transcript", None)
            or getattr(synthesis_result, "executive_recap", None)
            or getattr(synthesis_result, "canonical_graph", None)
            or getattr(synthesis_result, "canonical_signal_projection", None)
            or getattr(synthesis_result, "output_1", None)
            or getattr(synthesis_result, "output_2", None)
            or getattr(synthesis_result, "output_3", None)
        )
        if has_synth:
            recap_text = getattr(synthesis_result, "output_2", None) or getattr(synthesis_result, "executive_recap", None) or ""
            f3_str = (
                getattr(synthesis_result, "output_3", None)
                or getattr(synthesis_result, "canonical_graph", None)
                or getattr(synthesis_result, "canonical_signal_projection", None)
                or ""
            )
            f1_text = getattr(synthesis_result, "output_1", None) or getattr(synthesis_result, "raw_transcript", None) or ""
        elif out_p and out_p.exists():
            f1_files = sorted(out_p.glob("01_*_hitl_resolved_transcript.md"))
            f2_files = sorted(out_p.glob("02_*_executive_recap.md"))
            if f3_files:
                f3_str = f3_files[0].read_text(encoding="utf-8")
            if f2_files:
                recap_text = f2_files[0].read_text(encoding="utf-8")
            if f1_files:
                f1_text = f1_files[0].read_text(encoding="utf-8")

        is_auth = bool(eval_result and eval_result.authoritative_write_authorized)
        if is_auth and isinstance(metadata, dict):
            metadata.pop("partial_deliverables_saved", None)
            metadata.pop("partial_deliverables_error", None)
        if has_synth or f3_str or is_auth:
            if not f3_str or not f3_str.strip():
                err_msg = "X1 FAIL: Output 03 canonical signal projection is missing or empty"
                tracker.complete_stage(PipelineStage.X_EXIT_GATES, status="FAIL", details={"error": err_msg})
                receipt = tracker.build_receipt(out_p, terminal_outcome="FAILED")
                receipt.write_to_dir(out_p)
                raise ValueError(err_msg)

            from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
            x_eval = XExitGatesEvaluator()
            meta_ctx = dict(metadata or {})
            if out_p and "output_dir" not in meta_ctx:
                meta_ctx["output_dir"] = str(out_p)
            if f3_files and "output_03_path" not in meta_ctx:
                meta_ctx["output_03_path"] = str(f3_files[0])
            try:
                sealed_manifest = (
                    (eval_result.final_decision or {}).get("artifact_manifest", {})
                    if eval_result and getattr(eval_result, "final_decision", None)
                    else {}
                )
                has_sealed_manifest = bool(
                    is_auth
                    and sealed_manifest.get("output_2_sha256")
                    and sealed_manifest.get("output_3_sha256")
                )
                x_pass = x_eval.execute(
                    recap=recap_text,
                    graph_json=f3_str,
                    metadata=meta_ctx,
                    transcript=f1_text,
                    auto_heal=not has_sealed_manifest,
                )
                if not x_pass:
                    raise ValueError("X1-X3 FAIL: Exit gates evaluation returned False")
                if out_p and out_p.exists() and synthesis_result is not None:
                    import hashlib
                    from dual_pipeline.release_policy import compute_decision_seal

                    healed_f2 = None
                    healed_f3 = None
                    f2_files = sorted(out_p.glob("02_*_executive_recap.md"))
                    if f2_files:
                        healed_f2 = f2_files[0].read_text(encoding="utf-8")
                        if not has_sealed_manifest:
                            if hasattr(synthesis_result, "output_2"):
                                try:
                                    synthesis_result.output_2 = healed_f2
                                except AttributeError:
                                    pass
                            if hasattr(synthesis_result, "executive_recap"):
                                try:
                                    synthesis_result.executive_recap = healed_f2
                                except AttributeError:
                                    pass
                    if f3_files:
                        healed_f3 = f3_files[0].read_text(encoding="utf-8")
                        if not has_sealed_manifest:
                            if hasattr(synthesis_result, "output_3"):
                                try:
                                    synthesis_result.output_3 = healed_f3
                                except AttributeError:
                                    pass
                            if hasattr(synthesis_result, "canonical_signal_projection"):
                                try:
                                    synthesis_result.canonical_signal_projection = healed_f3
                                except AttributeError:
                                    pass
                    if eval_result and isinstance(getattr(eval_result, "final_decision", None), dict):
                        manifest = eval_result.final_decision.get("artifact_manifest")
                        if isinstance(manifest, dict):
                            changed = False
                            if healed_f2 is not None:
                                h2 = hashlib.sha256(healed_f2.encode("utf-8")).hexdigest()
                                if manifest.get("output_2_sha256") != h2:
                                    manifest["output_2_sha256"] = h2
                                    changed = True
                            if healed_f3 is not None:
                                h3 = hashlib.sha256(healed_f3.encode("utf-8")).hexdigest()
                                if manifest.get("output_3_sha256") != h3:
                                    manifest["output_3_sha256"] = h3
                                    changed = True
                            if changed:
                                manifest["decision_seal_sha256"] = compute_decision_seal(manifest)
                                dec_path = out_p / "final_release_decision.json"
                                if dec_path.is_file():
                                    dec_path.write_text(json.dumps(eval_result.final_decision, indent=2, default=str), encoding="utf-8")
            except Exception as x_err:
                tracker.complete_stage(PipelineStage.X_EXIT_GATES, status="FAIL", details={"error": str(x_err)})
                receipt = tracker.build_receipt(out_p, terminal_outcome="FAILED")
                receipt.write_to_dir(out_p)
                raise

        tracker.complete_stage(PipelineStage.X_EXIT_GATES, status="PASS" if is_auth else "FAIL")
        if is_auth:
            return

    dec = getattr(eval_result, "final_decision", {}) or {}
    judge_rep = getattr(eval_result, "judge_report", {}) or {}
    status = dec.get("status") or judge_rep.get("status") or getattr(eval_result, "status", None)
    is_hitl = (
        status in ("REVIEW_REQUIRED", "HITL_PENDING", "PENDING_OPERATOR_DECISION")
        or dec.get("requires_hitl") is True
        or judge_rep.get("requires_hitl") is True
        or getattr(eval_result, "requires_hitl", False)
        or getattr(eval_result, "is_hitl_pending", False)
    )

    terminal_outcome = status if (status in ("REVIEW_REQUIRED", "HITL_PENDING", "PENDING_OPERATOR_DECISION")) else ("REVIEW_REQUIRED" if is_hitl else "FAILED")
    receipt = tracker.build_receipt(out_p, terminal_outcome=terminal_outcome)
    receipt.write_to_dir(out_p)

    fails: List[str] = []
    if eval_result and eval_result.qa_report:
        for qk in ("output_01_forensic_transcript", "output_02_executive_recap", "output_03_canonical_signal_projection", "cross_artifact_consistency"):
            fails.extend(eval_result.qa_report.get(qk, {}).get("failures", []))
    fail_details = "; ".join(fails) if fails else (eval_result.final_decision.get("status", "FAIL") if eval_result else "FAIL")

    if is_hitl:
        from dual_pipeline.exceptions import HITLRequiredError
        raise HITLRequiredError(
            f"Stage X_EXIT_GATES suspended for Human-In-The-Loop review ({terminal_outcome}): {fail_details}",
            hitl_type="review_required",
            prompt=f"Pipeline suspended at attempt {attempt} for operator verification.",
            evidence={"decision": dec, "judge_report": judge_rep, "attempt": attempt},
        )

    if eval_result and hasattr(eval_result, "final_decision") and not eval_result.final_decision.get("output_3_llm_score_pass", True):
        raise RuntimeError(f"Mandatory LLM Judge evaluation failed for Output 03 after {attempt} attempts: {fail_details}")
    raise RuntimeError(f"Mandatory QA Gate or Micro-Judge evaluation failed after {attempt} attempts: {fail_details}")


def _resolve_pipeline_status(mutation_result: Optional[Any], meta: Dict[str, Any]) -> str:
    if mutation_result is not None:
        if isinstance(mutation_result, dict):
            return str(mutation_result.get("status") or "COMPLETE")
        if hasattr(mutation_result, "status"):
            return str(mutation_result.status)
        return "COMPLETE"
    if (meta.get("mutate_graph") or meta.get("auto_mutate_on_pass")) and not meta.get("dry_run_graph"):
        return "SKIPPED_PUBLICATION"
    return "COMPLETE"


def _enrich_document_retrieval(res: Dict[str, Any], meta: Dict[str, Any]) -> None:
    res["output3_only"] = True
    res["retrieval_preflight"] = meta.get("retrieval_preflight")
    rc = meta.get("retrieval_completion", {"status": "NOT_REQUESTED"})
    res["retrieval_completion"] = rc
    if rc.get("status") not in {"NOT_REQUESTED", "VERIFIED", "UNCHANGED_TEXT", "NO_NEW_TEXT_UNVERIFIED"}:
        res["status"] = "RETRIEVAL_PENDING"


def _enrich_mutation_metadata(res: Dict[str, Any], meta: Dict[str, Any], mutation_result: Optional[Any]) -> None:
    if mutation_result is not None:
        res["graph_mutation"] = mutation_result
        if isinstance(mutation_result, dict):
            for k in ("mutation_summary", "table_deltas", "active_generation", "idempotent_replay"):
                if k in mutation_result:
                    res[k] = mutation_result[k]
    for k in ("worktree_dir", "worktree_branch"):
        if meta.get(k): res[k] = meta[k]


def _enrich_pipeline_result(
    res: Dict[str, Any], modality: CanonicalModality, meta: Dict[str, Any],
    artifacts_map: Dict[str, str], mutation_result: Optional[Any],
) -> None:
    if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT) and artifacts_map.get("01_transcript"):
        res["01_transcript"] = artifacts_map["01_transcript"]
    if modality != CanonicalModality.DOCUMENT and artifacts_map.get("02_recap"):
        res["02_recap"] = artifacts_map["02_recap"]
    if modality == CanonicalModality.DOCUMENT:
        _enrich_document_retrieval(res, meta)
    if artifacts_map.get("presentation_intake"):
        res["presentation_intake"] = artifacts_map["presentation_intake"]
    _enrich_mutation_metadata(res, meta, mutation_result)


def assemble_pipeline_result(
    modality: CanonicalModality, meta: Dict[str, Any], out_p: Path,
    artifacts_map: Dict[str, str], eval_result: Any,
    intake_pkg_path: Path, receipt_path: Path, mutation_result: Optional[Any],
) -> Dict[str, Any]:
    """Assemble final standardized execution result dictionary."""
    status = _resolve_pipeline_status(mutation_result, meta)
    rel_status = "NOT_EVALUATED"
    if eval_result and hasattr(eval_result, "final_decision") and eval_result.final_decision:
        rel_status = str(eval_result.final_decision.get("status", "UNKNOWN"))

    res: Dict[str, Any] = {
        "status": status,
        "modality": modality.value,
        "source_type": str(meta.get("source_type", modality.value)),
        "output_dir": str(out_p),
        "03_graph": artifacts_map.get("03_graph") or "",
        "output_03": artifacts_map.get("03_graph") or "",
        "source_notes": artifacts_map.get("source_notes") or "", "metadata": str(out_p / "metadata.json"),
        "qa_gates_report": str(out_p / "qa_gates_report.json"), "llm_judge_evaluation": str(out_p / "llm_judge_evaluation.json"),
        "final_release_decision": str(out_p / "final_release_decision.json"), "release_status": rel_status,
        "intake_package": str(intake_pkg_path), "execution_receipt": str(receipt_path),
    }
    _enrich_pipeline_result(res, modality, meta, artifacts_map, mutation_result)
    return res


def is_eval_verdict_pass(eval_result: Any) -> bool:
    if not eval_result or not getattr(eval_result, "authoritative_write_authorized", False):
        return False
    if getattr(eval_result, "final_decision", {}).get("status") not in (
        "PASS",
        "APPROVED",
        "PASS_ADVISORY",
        "PASS_AUTHORIZED_EXCEPTION",
    ):
        return False
    rep = getattr(eval_result, "judge_report", {})
    return (
        rep.get("overall_verdict") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
        or rep.get("status") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
        or rep.get("verdict") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
    )


def check_mutation_reviewer_and_preflight(
    modality: CanonicalModality, should_mutate: bool, meta: Dict[str, Any], reviewer: Optional[str]
) -> None:
    if modality == CanonicalModality.DOCUMENT and should_mutate:
        from dual_pipeline.document_pipeline_core.retrieval_visibility import preflight_document_clearance
        meta["retrieval_preflight"] = preflight_document_clearance(meta, required=True)
    effective_reviewer = (
        reviewer
        or meta.get("reviewer")
        or meta.get("author")
        or meta.get("organizer")
        or (meta.get("rbac_auth") or {}).get("subject")
        or (meta.get("approval_receipt") or {}).get("operator_origin")
        or "human_operator"
    )
    if should_mutate and (not effective_reviewer or not str(effective_reviewer).strip()):
        raise ValueError("Authoritative GraphDB mutation requires a valid operator identity.")


def record_retrieval_completion(
    meta: Dict[str, Any], mut_res: Dict[str, Any], should_mutate: bool, staging_dir: Path
) -> bool:
    if should_mutate:
        from dual_pipeline.canonical_pipeline.publication_bindings import canonical_publication_runtime
        from dual_pipeline.document_pipeline_core.retrieval_visibility import completion_from_publication
        from dual_pipeline.transcript_pipeline_core import release_runner
        runtime = Path(meta["runtime_root"]).resolve() if meta.get("runtime_root") else canonical_publication_runtime(release_runner.ROOT)
        meta["retrieval_completion"] = completion_from_publication(mut_res, runtime)
        retrieval_pending = meta["retrieval_completion"]["status"] not in {"VERIFIED", "UNCHANGED_TEXT"}
    else:
        meta["retrieval_completion"] = {"status": "PUBLICATION_NOT_REQUESTED" if meta.get("require_retrieval") else "NOT_REQUESTED"}
        retrieval_pending = bool(meta.get("require_retrieval"))
    saved_meta = sanitize_metadata_for_persistence(meta)
    (staging_dir / "metadata.json").write_text(json.dumps(saved_meta, indent=2, default=str), encoding="utf-8")
    return retrieval_pending


def prepare_run_metadata(
    forced_modality: Optional[CanonicalModality], provider_type: str,
    metadata: Optional[Dict[str, Any]], directive_id: Optional[str],
    presentation_path: Optional[Union[str, Path]], signal_envelope: Optional[Any],
    auto_mutate_on_pass: bool, mutate_graph: bool, kwargs: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool, bool]:
    meta = dict(metadata or {})
    if forced_modality == CanonicalModality.DOCUMENT:
        meta.setdefault("is_document", True)
    meta.setdefault("provider_type", provider_type)
    meta.setdefault("provider", provider_type)
    if directive_id:
        meta.setdefault("directive_id", directive_id)
    if kwargs:
        meta.update(kwargs)
    if presentation_path:
        meta["presentation_path"] = str(presentation_path)
    if signal_envelope:
        meta["signal_envelope"] = signal_envelope
    if meta.get("backtest"):
        meta["run_mode"] = "backtest"
        meta.setdefault("force_rerun", True)
        meta.setdefault("judge_tier", "fast")
        meta.setdefault("regenerate", "02,03")
    if meta.get("dry_run_graph") or meta.get("dry_run"):
        raise ValueError("Explicit dry-run or false mutation requests are prohibited under active-graph governance.")
    effective_mutate = bool(mutate_graph)
    effective_auto_mutate = bool(auto_mutate_on_pass) and effective_mutate
    meta["auto_mutate_on_pass"] = effective_auto_mutate
    return meta, effective_mutate, effective_auto_mutate


def resolve_pipeline_project_alias(meta: Dict[str, Any], output_dir: str) -> None:
    proj_cand = meta.get("project")
    if not proj_cand and output_dir and output_dir != "auto":
        out_parts = Path(output_dir).parts
        for idx, part in enumerate(out_parts[:-1]):
            if part == "output" and idx + 1 < len(out_parts):
                proj_cand = out_parts[idx + 1]
                break
    if proj_cand and str(proj_cand).lower() not in ("auto", "unresolved"):
        from dual_pipeline.transcript_pipeline_core.preflight_coordinator import _validate_project_alias
        meta["project"] = _validate_project_alias(str(proj_cand))


def handle_pipeline_run_exception(
    exc: Exception, meta: Dict[str, Any], current_stage: str, input_path: Union[str, Path, None]
) -> None:
    from dual_pipeline.canonical_pipeline.document_resume import is_document_request, record_document_failure
    from dual_pipeline.dlq import DeadLetterQueueManager

    if is_document_request(meta):
        try:
            record_document_failure(meta, current_stage, exc)
        except Exception as checkpoint_err:
            logger.error("Document checkpoint failure after %s: %s", current_stage, checkpoint_err)
    else:
        try:
            from dual_pipeline.canonical_pipeline.transcript_resume import record_transcript_failure
            record_transcript_failure(meta, current_stage, exc)
        except Exception as checkpoint_err:
            logger.error("Transcript checkpoint failure after %s: %s", current_stage, checkpoint_err)

    import re
    exc_str = str(exc).lower()
    is_quota = (
        any(k in exc_str for k in ("credit_balance_exhausted", "rate limit", "http 429", "status 429", "error 429"))
        or bool(re.search(r"\b429\b", exc_str))
        or type(exc).__name__ in ("RateLimitError", "RateLimitExceededError")
    )
    runtime_root = meta.get("runtime_root") or meta.get("evidence_root")
    try:
        dlq = DeadLetterQueueManager(runtime_root=runtime_root)
        dlq_id = dlq.enqueue(input_path=input_path, metadata=meta, error=exc, stage=current_stage)
        meta["dlq_id"] = dlq_id
        logger.error("Pipeline failure in stage '%s' recorded to DLQ as '%s': %s", current_stage, dlq_id, exc)
    except Exception as dlq_err:
        logger.error("Failed to enqueue into DLQ: %s", dlq_err)

    if is_quota:
        from dual_pipeline.transcript_stages_core.models import HITLRequiredError
        opts = [{"label": "Retry execution with fallback provider", "value": "fallback"}, {"label": "Abort run and notify admin", "value": "abort"}]
        raise HITLRequiredError(message=f"Provider quota or rate limit exhausted: {exc}", hitl_type="quota_exhausted", options=opts) from exc
    raise exc


def _evaluate_readback_verified(
    staging_dir: Path,
    mut_res: Dict[str, Any],
    is_published: bool,
) -> bool:
    if not is_published:
        return False
    cov_receipt_file = staging_dir / "publication_coverage_receipt.json"
    if cov_receipt_file.is_file():
        try:
            pcr_data = json.loads(cov_receipt_file.read_text(encoding="utf-8"))
            return pcr_data.get("status") == "VERIFIED"
        except Exception:
            return False
    if mut_res.get("publication_coverage_verified") is True:
        return True
    if mut_res.get("idempotent") or mut_res.get("mutation_outcome") == "IDEMPOTENT_NOOP":
        return bool(mut_res.get("publication_coverage_verified", True))
    return False


def _compute_terminal_outcome(
    should_mutate: bool,
    is_mutation_success: bool,
    retrieval_pending: bool,
    target_lifecycle_state_val: str,
    is_published: bool,
    is_readback_verified: bool,
    mut_status: Optional[str],
    mut_err: Optional[Any],
) -> str:
    if not should_mutate:
        return target_lifecycle_state_val
    if is_mutation_success and not retrieval_pending and target_lifecycle_state_val == "COMPLETE":
        return "COMPLETE"
    if is_published and not is_readback_verified:
        return "PUBLISHED_UNVERIFIED"
    return mut_status or ("DEGRADED_OUTPUTS_SAVED" if mut_err else "PUBLICATION_PENDING")


def resolve_release_completion_and_receipt(
    tracker: Any,
    staging_dir: Path,
    modality: CanonicalModality,
    meta: Dict[str, Any],
    mut_res: Dict[str, Any],
    mut_status: Optional[str],
    mut_err: Optional[Any],
    should_mutate: bool,
    scout_result: Any,
    intake_pkg_path: Path,
    artifacts_map: Dict[str, str],
) -> Any:
    from dual_pipeline.canonical_pipeline.operation_contracts import validate_completion_readiness

    retrieval_pending = False
    if modality == CanonicalModality.DOCUMENT:
        retrieval_pending = record_retrieval_completion(meta, mut_res, should_mutate, staging_dir)

    is_published = bool(should_mutate and mut_status in ("COMPLETE", "PASS") and not mut_err)
    is_readback_verified = _evaluate_readback_verified(staging_dir, mut_res, is_published)

    _, _, target_state = validate_completion_readiness(
        modality=modality,
        written_artifacts=artifacts_map,
        is_published=is_published,
        is_readback_verified=is_readback_verified,
    )

    is_mutation_success = not should_mutate or (is_published and is_readback_verified)
    stage_status = "PASS" if (is_mutation_success if should_mutate else True) and not retrieval_pending else "DEGRADED"

    tracker.complete_stage(
        PipelineStage.EVALS_RELEASE,
        status=stage_status,
        details={
            "tier1_scout": scout_result.to_dict() if hasattr(scout_result, "to_dict") else (scout_result if isinstance(scout_result, dict) else {}),
            "intake_package": str(intake_pkg_path),
            "graph_mutated": should_mutate and mut_res.get("status") == "COMPLETE" and not mut_res.get("idempotent_replay"),
            "mutation_error": mut_err,
            "mutation_status": mut_status,
            "retrieval_completion": meta.get("retrieval_completion"),
            "publication_coverage_verified": is_readback_verified,
            "lifecycle_state": target_state.value,
        },
    )

    evidence_dir = getattr(getattr(tracker, "timing", None), "evidence_directory", None)
    if evidence_dir and Path(evidence_dir).is_dir():
        from dual_pipeline.canonical_pipeline.evidence_capture import snapshot_run_artifacts
        snapshot_run_artifacts(tracker, staging_dir, evidence_dir, meta, modality)

    terminal_outcome = _compute_terminal_outcome(
        should_mutate=should_mutate,
        is_mutation_success=is_mutation_success,
        retrieval_pending=retrieval_pending,
        target_lifecycle_state_val=target_state.value,
        is_published=is_published,
        is_readback_verified=is_readback_verified,
        mut_status=mut_status,
        mut_err=mut_err,
    )

    receipt = tracker.build_receipt(staging_dir, terminal_outcome=terminal_outcome)
    receipt.write_to_dir(staging_dir)
    return receipt


def assemble_intake_publish_payload(
    directive_id: str,
    directive: dict[str, Any],
    classification: str,
    runs: list[dict[str, Any]],
    active_pair: dict[str, Any],
    status: str,
    mutation_outcome: Optional[str] = None,
    completion: Optional[dict[str, Any]] = None,
    release: Optional[dict[str, Any]] = None,
    worktree_dir: Optional[str] = None,
    worktree_branch: Optional[str] = None,
) -> dict[str, Any]:
    active_gen = active_pair.get("generation_id", "")
    if isinstance(completion, dict):
        completion["generation_id"] = active_gen
    payload: dict[str, Any] = {
        "status": status,
        "directive_id": directive_id,
        "generation_id": active_gen,
        "active_pair": active_pair,
        "mutation_summary": (release or completion or {}).get("mutation_summary"),
        "pipeline": "candidate_release",
        "classification": classification,
        "admission": directive["approval"],
        "required_embedding_target": directive.get("required_embedding_target"),
        "runs": runs,
        "ingestion_completion": completion or (release.get("ingestion_completion") if release else None),
        "idempotent_replay": False,
    }
    if mutation_outcome:
        payload["mutation_outcome"] = mutation_outcome
    if release is not None:
        payload["release"] = release
    if worktree_dir:
        payload["worktree_dir"] = worktree_dir
    if worktree_branch:
        payload["worktree_branch"] = worktree_branch
    return payload


from dual_pipeline.cli.intake_approval_helper import ensure_intake_publish_worktree_isolation  # noqa: E402, F401


