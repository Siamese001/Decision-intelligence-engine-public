"""
dual_pipeline/answer_ledger.py

Top-level facade for the Governed Answer Ledger and Feedback Capture.
Provides schema-backed persistence, cryptographic hash chain integrity, and operator feedback capture.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.answer_ledger")

from dual_pipeline.answer_ledger_core import (
    ANSWER_FEEDBACK_CONTRACT_ID,
    ANSWER_RECORD_CONTRACT_ID,
    GENESIS_PREV_DIGEST,
    AnswerFeedback,
    AnswerLedger,
    AnswerRecord,
    compute_canonical_digest,
    record_implicit_feedback,
    record_operator_feedback,
    sanitize_context_snapshot,
)

DEFAULT_LEDGER_REL_PATH = Path("learning") / "answer_ledger.sqlite"


def get_default_answer_ledger(runtime_root: Optional[Path] = None) -> AnswerLedger:
    """Returns an AnswerLedger instance at <runtime_root>/learning/answer_ledger.sqlite."""
    root = Path(runtime_root) if runtime_root else Path.cwd()
    return AnswerLedger(root / DEFAULT_LEDGER_REL_PATH)


def _parse_latency(val: Any) -> Optional[float]:
    """Parses numeric latency value safely."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_from_dict_keys(d: Dict[str, Any]) -> List[str]:
    for k in ("cited_assertion_ids", "assertions", "evidence"):
        val = d.get(k)
        if val and isinstance(val, (list, tuple, set)):
            return [str(cid) for cid in val if cid]
    return []


def _append_ids_from_item(item: Any, collected: List[str]) -> None:
    if not isinstance(item, dict):
        return
    for k in ("citations", "evidence_ids"):
        raw = item.get(k)
        if isinstance(raw, list):
            for x in raw:
                sx = str(x)
                if x and sx not in collected:
                    collected.append(sx)


def _extract_from_row_items(ans: Dict[str, Any]) -> List[str]:
    collected: List[str] = []
    fields = ("current_state", "opportunities", "pain_points", "guidance", "counterevidence")
    for f in fields:
        items = ans.get(f)
        if isinstance(items, list):
            for item in items:
                _append_ids_from_item(item, collected)
    return collected


def _extract_cited_ids(d: Dict[str, Any]) -> List[str]:
    """Extracts cited assertion ID strings from top-level or nested query result dicts."""
    found = _extract_from_dict_keys(d)
    if found:
        return found
    inner = d.get("result")
    if isinstance(inner, dict):
        found = _extract_from_dict_keys(inner)
        if found:
            return found
    ans = d.get("answer") or (inner.get("answer") if isinstance(inner, dict) else None)
    if isinstance(ans, dict):
        return _extract_from_row_items(ans)
    return []


def _extract_response_text(d: Dict[str, Any]) -> str:
    """Extracts response text from candidate dict fields, preferring formatted_markdown/markdown."""
    inner = d.get("result") if isinstance(d.get("result"), dict) else {}
    for k in ("formatted_markdown", "markdown", "clean_prose"):
        v = d.get(k) or inner.get(k)
        if isinstance(v, str) and v.strip():
            return v
    for k in ("answer", "response", "result", "text"):
        v = d.get(k)
        if v:
            return str(v)
    return ""


def _resolve_provenance(
    result: Dict[str, Any],
    graph_sha256: Optional[str],
    generation: Optional[Any],
    latency_ms: Optional[float],
    model_id: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[float], Optional[str]]:
    """Resolves provenance metadata from explicit args or nested result dictionary."""
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    active_pair = (
        result.get("active_pair")
        if isinstance(result.get("active_pair"), dict)
        else (inner.get("active_pair") if isinstance(inner.get("active_pair"), dict) else {})
    )
    g_sha = (
        graph_sha256
        or result.get("graph_sha256")
        or result.get("active_generation_sha256")
        or inner.get("graph_sha256")
        or active_pair.get("graph_sha256")
    )
    gen = (
        generation
        if generation is not None
        else (
            result.get("generation")
            or result.get("generation_id")
            or inner.get("generation")
            or inner.get("generation_id")
            or active_pair.get("generation_id")
        )
    )
    lat = (
        latency_ms
        if latency_ms is not None
        else _parse_latency(
            result.get("latency_ms")
            or result.get("elapsed_ms")
            or inner.get("latency_ms")
            or inner.get("elapsed_ms")
        )
    )
    mid = (
        model_id
        or result.get("model_id")
        or result.get("active_model")
        or inner.get("model_id")
        or inner.get("active_model")
    )
    return (
        str(g_sha) if g_sha is not None else None,
        str(gen) if gen is not None else None,
        lat,
        str(mid) if mid is not None else None,
    )


class QueryMetadata(tuple):
    """7-element tuple wrapper preserving backwards-compatible indexing and unpacking."""

    def __getitem__(self, idx: Any) -> Any:
        val = super().__getitem__(idx)
        if idx == 3 and val is None:
            return "UNVERIFIED"
        return val


def _extract_query_metadata(
    result: Any,
    graph_sha256: Optional[str] = None,
    generation: Optional[Any] = None,
    latency_ms: Optional[float] = None,
    model_id: Optional[str] = None,
) -> Tuple[List[str], str, str, Optional[str], Optional[str], Optional[float], Optional[str]]:
    """Extracts answer fields and metadata from raw result, defaulting to UNVERIFIED if ungrounded."""
    if not isinstance(result, dict):
        g_str = str(graph_sha256) if graph_sha256 is not None else None
        gen_str = str(generation) if generation is not None else None
        mid_str = str(model_id) if model_id is not None else None
        return QueryMetadata(([], str(result), "UNVERIFIED", g_str, gen_str, latency_ms, mid_str))

    cited_ids = _extract_cited_ids(result)
    resp_text = _extract_response_text(result)
    g_sha, gen, lat, mid = _resolve_provenance(result, graph_sha256, generation, latency_ms, model_id)
    raw_rigor = result.get("rigor")
    if not cited_ids or not g_sha:
        rigor = "UNVERIFIED"
    elif raw_rigor is not None:
        rigor = str(raw_rigor)
    else:
        rigor = "VERIFIED"
    return QueryMetadata((cited_ids, resp_text, rigor, g_sha, gen, lat, mid))  # type: ignore[return-value]


def _extract_traversal_attributes(record: Any) -> tuple[list[str], Optional[str], Optional[int]]:
    """Extract cited assertion IDs, graph sha, and hop count from record or dict."""
    from dual_pipeline.query_core.firewall import GraphDBRequiredError

    if hasattr(record, "cited_assertion_ids"):
        return (
            list(getattr(record, "cited_assertion_ids", [])),
            getattr(record, "graph_sha256", None),
            getattr(record, "traversal_hop_count", 1),
        )
    if isinstance(record, dict):
        return (
            _extract_cited_ids(record),
            record.get("graph_sha256"),
            record.get("traversal_hop_count", 1),
        )
    raise GraphDBRequiredError(f"INVALID_ANSWER_RECORD: Expected AnswerRecord or dict, got {type(record)}")


def _check_traversal_invariants(
    cited_ids: list[str],
    g_sha: Optional[str],
    hop_count: Optional[int],
    active_pair: Optional[Dict[str, Any]],
) -> None:
    """Enforce non-empty citations, present graph_sha256, active pair match, and hop count > 0."""
    from dual_pipeline.query_core.firewall import GraphDBRequiredError

    if not cited_ids:
        raise GraphDBRequiredError("ZERO_GRAPH_TRAVERSAL: Answer record contains zero cited assertions.")
    if not g_sha:
        raise GraphDBRequiredError("MISSING_GRAPH_SHA256: Answer record has no graph_sha256 binding.")
    if active_pair and active_pair.get("graph_sha256"):
        expected_sha = active_pair["graph_sha256"]
        if g_sha != expected_sha:
            raise GraphDBRequiredError(
                f"GRAPH_SHA256_MISMATCH: Record graph_sha256 '{g_sha}' does not match active pair '{expected_sha}'."
            )
    if hop_count is not None and hop_count <= 0:
        raise GraphDBRequiredError(f"ZERO_TRAVERSAL_HOPS: Traversal hop count must be > 0 (got {hop_count}).")


def _verify_assertions_in_db(cited_ids: list[str], conn: Any) -> None:
    """Verify cited assertion or claim IDs physically exist in SQLite store."""
    from dual_pipeline.query_core.firewall import GraphDBRequiredError

    try:
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in cited_ids)
        sql_asrt = "SELECT assertion_id FROM assertion WHERE assertion_id IN (" + placeholders + ")"
        cur.execute(sql_asrt, cited_ids)
        found = {r[0] for r in cur.fetchall()}
        missing = set(cited_ids) - found
        if missing:
            try:
                sql_claim = "SELECT claim_id FROM claim WHERE claim_id IN (" + placeholders + ")"
                cur.execute(sql_claim, cited_ids)
                found.update({r[0] for r in cur.fetchall()})
                missing = set(cited_ids) - found
            except Exception as exc:
                logger.debug("Fallback claim table lookup skipped: %s", exc)
        if missing:
            raise GraphDBRequiredError(f"NON_EXISTENT_ASSERTIONS: Cited assertions not found in GraphDB: {sorted(missing)}")
    except GraphDBRequiredError:
        raise
    except Exception as exc:
        raise GraphDBRequiredError(f"GRAPHDB_VERIFICATION_FAILED: Database verification error: {exc}") from exc


def verify_answer_graph_traversal(
    record: Any,
    active_pair: Optional[Dict[str, Any]] = None,
    conn: Optional[Any] = None,
) -> bool:
    """Verifies cryptographic GraphDB traversal provenance for an AnswerRecord or dict.

    Rejects any record with cited_assertion_ids=[], missing/mismatched graph_sha256,
    non-existent assertion IDs in SQLite, or zero traversal hops.
    """
    cited_ids, g_sha, hop_count = _extract_traversal_attributes(record)
    _check_traversal_invariants(cited_ids, g_sha, hop_count, active_pair)
    if conn is not None:
        _verify_assertions_in_db(cited_ids, conn)
    return True


def record_query_answer_safe(
    runtime_root: Optional[Path],
    question: str,
    result: Any,
    query_type: Optional[str] = None,
    *,
    graph_sha256: Optional[str] = None,
    generation: Optional[Any] = None,
    latency_ms: Optional[float] = None,
    model_id: Optional[str] = None,
) -> Optional[str]:
    """Records a query answer safely to AnswerLedger without raising or blocking caller."""
    try:
        ledger = get_default_answer_ledger(runtime_root)
        (
            cited_ids,
            resp_text,
            rigor,
            g_sha,
            gen,
            lat,
            mid,
        ) = _extract_query_metadata(result, graph_sha256, generation, latency_ms, model_id)

        rec = AnswerRecord(
            pipeline="query",
            intent_or_type=query_type or "exact_lookup",
            context_digest="",
            retrieval_seeds=[question],
            context_snapshot=[{"speaker": "User", "text": question}],
            cited_assertion_ids=cited_ids,
            rigor=rigor,
            response_text=resp_text,
            graph_sha256=g_sha,
            generation=gen,
            latency_ms=lat,
            model_id=mid,
        )
        saved = ledger.append_answer_safe(rec)
        return saved.answer_id if saved else None
    except Exception:
        return None


__all__ = [
    "ANSWER_FEEDBACK_CONTRACT_ID",
    "ANSWER_RECORD_CONTRACT_ID",
    "DEFAULT_LEDGER_REL_PATH",
    "GENESIS_PREV_DIGEST",
    "AnswerFeedback",
    "AnswerLedger",
    "AnswerRecord",
    "compute_canonical_digest",
    "get_default_answer_ledger",
    "record_implicit_feedback",
    "record_operator_feedback",
    "record_query_answer_safe",
    "sanitize_context_snapshot",
    "verify_answer_graph_traversal",
]
