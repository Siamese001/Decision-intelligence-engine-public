"""Candidate comparison, hydration, and semantic cache comparison context."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.core import connect_sqlite, digest, sha256_file
from dual_pipeline.hash_cache import cached_sha256_file
from dual_pipeline.index import CLASSIFICATION_RANK, HybridRetriever
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

_SEARCH_DOCUMENTS_CACHE: dict[str, list[dict[str, Any]]] = {}


def _load_search_documents(graph_path: Path) -> list[dict[str, Any]]:
    cache_key = f"{graph_path.resolve()}:{cached_sha256_file(graph_path)}"
    if cache_key in _SEARCH_DOCUMENTS_CACHE:
        return _SEARCH_DOCUMENTS_CACHE[cache_key]
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        raw_rows = connection.execute(
            """SELECT sd.search_document_id,sd.object_type,sd.object_id,sd.title,
                      sd.body,sd.canonical_status,sd.currentness_state,
                      sd.retrieval_eligible,sd.source_id,sd.assertion_id,
                      a.classification AS assertion_classification,
                      a.allowed_audiences_json AS assertion_audiences_json
               FROM search_document sd
               JOIN assertion a ON a.assertion_id=sd.assertion_id
               ORDER BY sd.search_document_id"""
        ).fetchall()
        docs = []
        for row in raw_rows:
            d = dict(row)
            try:
                d["_parsed_audiences"] = set(json.loads(d["assertion_audiences_json"]))
            except Exception:
                d["_parsed_audiences"] = set()
            doc_text = f"{d['title']} {d['body']}".casefold()
            d["_norm_doc"] = " ".join(doc_text.split()).strip(" .")
            d["_doc_tokens"] = set(re.findall(r"[a-z0-9]+", doc_text))
            docs.append(d)
        _SEARCH_DOCUMENTS_CACHE[cache_key] = docs
        return docs
    finally:
        connection.close()


def _filter_admitted_documents(
    documents: list[dict[str, Any]],
    candidate: dict[str, Any],
    comparison_audiences: set[str],
) -> list[dict[str, Any]]:
    admitted: list[dict[str, Any]] = []
    c_class = candidate["classification"]
    if c_class not in CLASSIFICATION_RANK:
        return admitted
    c_rank = CLASSIFICATION_RANK[c_class]

    for doc in documents:
        doc_class = doc["assertion_classification"]
        if doc_class not in CLASSIFICATION_RANK or CLASSIFICATION_RANK[doc_class] > c_rank:
            continue
        doc_audiences = doc.get("_parsed_audiences")
        if doc_audiences is None:
            try:
                doc_audiences = set(json.loads(doc["assertion_audiences_json"]))
            except (TypeError, json.JSONDecodeError):
                doc_audiences = set()
        if bool(comparison_audiences & doc_audiences):
            admitted.append(doc)
    return admitted


def _score_tokens(tokens: set[str], doc_tokens: set[str]) -> float:
    if not tokens or not doc_tokens:
        return 0.0
    inter = len(tokens & doc_tokens)
    if not inter:
        return 0.0
    jaccard = inter / max(1, len(tokens | doc_tokens))
    coverage = min(0.2, (inter / max(1, len(tokens))) * 0.2)
    return jaccard + coverage


def _strip_meeting_suffix(value: str) -> str:
    return re.sub(r"\s+in\s+[^.]+$", "", value).strip(" .")


def _normalize_doc_texts(doc: dict[str, Any]) -> tuple[str, str, str, str]:
    raw_title = str(doc.get("title") or "").strip()
    raw_body = str(doc.get("body") or "").strip()
    doc_text = raw_body.casefold() if (raw_title and raw_body.startswith(raw_title)) else f"{raw_title} {raw_body}".casefold()
    norm_doc = " ".join(doc_text.split()).strip(" .")
    norm_body = " ".join(raw_body.casefold().split()).strip(" .")
    lane = "approved" if doc["canonical_status"] == "canonical" and doc["retrieval_eligible"] else "candidate"
    return doc_text, norm_doc, norm_body, lane


def _is_exact_or_suffix_match(text: str, stripped_text: str, norm_doc: str, norm_body: str) -> bool:
    if text in (norm_doc, norm_body):
        return True
    stripped_body = _strip_meeting_suffix(norm_body)
    return bool(
        stripped_text
        and len(stripped_text) >= 12
        and stripped_text != text
        and stripped_body != norm_body
        and stripped_text == stripped_body
    )


_NUMERIC_VALUE_RE = re.compile(r"\$?\d+(?:\.\d+)?(?:\s*(?:m|b|k|%|ms|sec|s|days|months|years))?\b")
_NEGATION_TOKENS = frozenset({"not", "never", "no", "cancelled", "rejected", "deprecated", "disabled"})


def _has_semantic_contradiction(text: str, tokens: set[str], norm_doc: str, doc_tokens: set[str]) -> bool:
    if text == norm_doc or not tokens or not doc_tokens:
        return False
    shared = tokens & doc_tokens
    non_num_tokens = {t for t in tokens if not t.isdigit()}
    if len(shared) < 4 or (len(shared) / max(1, len(non_num_tokens))) < 0.65:
        return False
    cand_nums = set(_NUMERIC_VALUE_RE.findall(text))
    doc_nums = set(_NUMERIC_VALUE_RE.findall(norm_doc))
    if cand_nums and doc_nums and cand_nums != doc_nums:
        return True
    cand_neg = bool(tokens & _NEGATION_TOKENS)
    doc_neg = bool(doc_tokens & _NEGATION_TOKENS)
    return cand_neg != doc_neg and (len(shared) / max(1, len(non_num_tokens - _NEGATION_TOKENS))) >= 0.75


def _find_lexical_matches(
    text: str,
    admitted: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[float, dict[str, Any]]]]:
    tokens = set(re.findall(r"[a-z0-9]+", text))
    stripped_text = _strip_meeting_suffix(text)
    exact: list[dict[str, Any]] = []
    partial_overlaps: list[dict[str, Any]] = []
    scored: list[tuple[float, dict[str, Any]]] = []

    for doc in admitted:
        doc_text, norm_doc, norm_body, lane = _normalize_doc_texts(doc)
        doc_tokens = doc.get("_doc_tokens") or set(re.findall(r"[a-z0-9]+", doc_text))
        if _is_exact_or_suffix_match(text, stripped_text, norm_doc, norm_body):
            exact.append({
                "record_id": doc["search_document_id"],
                "object_id": doc["object_id"],
                "source_id": doc.get("source_id"),
                "lane": lane,
                "currentness_state": doc.get("currentness_state"),
            })
        elif (len(text) >= 24 and text in norm_doc) or _has_semantic_contradiction(text, tokens, norm_doc, doc_tokens):
            partial_overlaps.append({
                "record_id": doc["search_document_id"],
                "object_id": doc["object_id"],
                "lane": lane,
            })

        score = _score_tokens(tokens, doc_tokens)
        if score >= 0.08:
            scored.append((score, doc))

    exact.sort(key=lambda m: (m.get("currentness_state") == "historical", m.get("record_id") or ""))
    scored.sort(key=lambda item: (-item[0], item[1]["search_document_id"]))
    return exact, partial_overlaps, scored


def _is_lexically_decisive(scored: list[tuple[float, dict[str, Any]]]) -> bool:
    if not scored:
        return False
    s_top = scored[0][0]
    return s_top >= 0.85 or (
        s_top >= 0.70 and (len(scored) == 1 or (s_top - scored[1][0]) >= get_hitl_ambiguity_margin_threshold())
    )


def _resolve_semantic_matches(
    candidate: dict[str, Any],
    exact: list[dict[str, Any]],
    scored: list[tuple[float, dict[str, Any]]],
    comparison_audiences: set[str],
    retriever: HybridRetriever | None,
    cached: dict[str, Any] | None,
    encoded_queries: dict[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], str, str, str, str, bool, str]:
    if not exact and isinstance(cached, dict):
        return (
            list(cached["semantic_matches"]),
            cached["query_representation_digest"],
            cached["backend"],
            cached["model_id"],
            cached["model_revision"],
            cached["production_qualified"],
            "semantic_cache_replay",
        )
    if not exact and not _is_lexically_decisive(scored) and retriever is not None:
        hybrid = retriever.retrieve(
            candidate["normalized_text"],
            ["approved", "candidate"],
            classification=candidate["classification"],
            audiences=sorted(comparison_audiences),
            encoded_query=encoded_queries.get(candidate["candidate_id"]) if encoded_queries else None,
        )
        semantic_matches = [
            {
                "record_id": res["record_id"],
                "object_id": res["record"]["extensions"].get("com.partner_advisory.nee.graph-object-id"),
                "score": round(res["fused_score"], 12),
                "dense_rank": res["dense_rank"],
                "sparse_rank": res["sparse_rank"],
                "colbert_rank": res["colbert_rank"],
                "namespace": res["record"]["namespace"],
                "lane": lane,
                "retrieval_text_hash": res["record"]["retrieval_text_hash"],
            }
            for lane in ("approved", "candidate")
            for res in hybrid["lanes"][lane]["results"]
        ]
        rep_digest = digest({lane: hybrid["lanes"][lane]["query_representation_digest"] for lane in ("approved", "candidate")})
        backend = retriever.backend
        return (
            semantic_matches,
            rep_digest,
            backend.backend_id,
            backend.model_id,
            backend.model_revision,
            backend.production_qualified,
            "dense_sparse_colbert",
        )

    semantic_matches = [
        {
            "record_id": doc["search_document_id"],
            "object_id": doc["object_id"],
            "score": round(score, 6),
            "dense_rank": None,
            "sparse_rank": None,
            "colbert_rank": None,
            "namespace": doc["object_type"],
            "lane": "approved" if doc["canonical_status"] == "canonical" and doc["retrieval_eligible"] else "candidate",
            "retrieval_text_hash": digest(doc["body"]),
        }
        for score, doc in scored[:5]
    ]
    rep_digest = digest(candidate["normalized_text"])
    backend_id = "deterministic-lexical-preindex-comparison"
    return (
        semantic_matches,
        rep_digest,
        backend_id,
        backend_id,
        "1.0.0",
        False,
        "exact_or_lexical_fallback",
    )


def compare_candidates(
    bundle: dict[str, Any],
    graph_path: Path,
    *,
    retriever: HybridRetriever | None = None,
    retrieval_audiences: list[str] | None = None,
    cached_comparisons: dict[str, dict[str, Any]] | None = None,
    encoded_queries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Exact plus transient hybrid comparison; nothing is persisted to an index."""
    documents = _load_search_documents(graph_path)
    comparisons: dict[str, Any] = {}

    for candidate in bundle["candidates"]:
        common_audiences = (
            set(candidate["audiences"]) & set(retrieval_audiences)
            if retrieval_audiences is not None
            else set(candidate["audiences"])
        )
        comparison_audiences = common_audiences or set(candidate["audiences"])
        admitted = _filter_admitted_documents(documents, candidate, comparison_audiences)
        text = candidate["normalized_text"].casefold().strip(" .")
        exact, partial_overlaps, scored = _find_lexical_matches(text, admitted)

        cached = cached_comparisons.get(candidate["candidate_id"]) if cached_comparisons else None
        (
            semantic_matches,
            rep_digest,
            backend_id,
            model_id,
            model_revision,
            production_qualified,
            comparison_mode,
        ) = _resolve_semantic_matches(
            candidate, exact, scored, comparison_audiences, retriever, cached, encoded_queries
        )

        comparisons[candidate["candidate_id"]] = {
            "candidate_id": candidate["candidate_id"],
            "transient_only": True,
            "backend": backend_id,
            "model_id": model_id,
            "model_revision": model_revision,
            "production_qualified": production_qualified,
            "comparison_mode": comparison_mode,
            "query_representation_digest": rep_digest,
            "exact_matches": exact[:10],
            "partial_overlaps": partial_overlaps[:10],
            "semantic_matches": semantic_matches,
            "semantic_cache_replay": isinstance(cached, dict) and not exact,
            "persistent_write_count": 0,
        }

    return {
        "run_id": bundle["run_id"],
        "graph_sha256": sha256_file(graph_path),
        "comparisons": comparisons,
    }


def hydrate_comparison_context(comparison: dict[str, Any], graph_path: Path) -> dict[str, Any]:
    assertion_ids: set[str] = set()
    connection = connect_sqlite(graph_path, readonly=True)
    try:
        record_ids = {
            match["record_id"]
            for value in comparison["comparisons"].values()
            for key in ("exact_matches", "semantic_matches")
            for match in value[key]
        }
        if record_ids:
            placeholders = ",".join("?" for _ in record_ids)
            rows = connection.execute(
                f"SELECT search_document_id,assertion_id FROM search_document WHERE search_document_id IN ({placeholders})",
                sorted(record_ids),
            ).fetchall()
            assertion_ids.update(row["assertion_id"] for row in rows if row["assertion_id"])
        hydrated: list[dict[str, Any]] = []
        if assertion_ids:
            sorted_aids = sorted(assertion_ids)
            a_placeholders = ",".join("?" for _ in sorted_aids)
            a_rows = {
                row["assertion_id"]: dict(row)
                for row in connection.execute(
                    f"""SELECT a.assertion_id,a.subject_node_id,a.predicate,a.object_node_id,a.object_literal,
                              a.canonical_status,a.currentness_state,a.classification,a.allowed_audiences_json,
                              a.primary_source_id
                       FROM assertion a WHERE a.assertion_id IN ({a_placeholders})""",
                    sorted_aids,
                )
            }
            ev_by_aid: dict[str, list[dict[str, Any]]] = {aid: [] for aid in sorted_aids}
            for item in connection.execute(
                f"""SELECT ae.assertion_id, e.evidence_id,e.source_id,e.source_version_id,e.locator_kind,e.locator_value,
                          ae.evidence_relationship
                   FROM assertion_evidence ae JOIN evidence_item e ON e.evidence_id=ae.evidence_id
                   WHERE ae.assertion_id IN ({a_placeholders}) ORDER BY e.evidence_id""",
                sorted_aids,
            ):
                ev_dict = dict(item)
                aid = ev_dict.pop("assertion_id")
                ev_by_aid[aid].append(ev_dict)

            for aid in sorted_aids:
                if aid in a_rows:
                    hydrated.append(a_rows[aid] | {"evidence": ev_by_aid.get(aid, [])})
        return {
            "run_id": comparison["run_id"],
            "graph_sha256": comparison["graph_sha256"],
            "hydrated_assertions": hydrated,
            "record_count": len(hydrated),
        }
    finally:
        connection.close()


def _cached_comparison_output(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    comparison = value.get("comparison")
    if not isinstance(comparison, dict):
        return None
    return comparison


def _intake_cache_context(
    *,
    candidate: dict[str, Any],
    active_pair: dict[str, Any],
    retriever: HybridRetriever,
    retrieval_audiences: list[str] | None,
    authorization_context: AuthorizationContext | None,
) -> dict[str, Any]:
    return {
        "use_case": "intake",
        "cache_payload_kind": "sealed_candidate_comparison_v2",
        "route": "candidate_comparison",
        "candidate_type": candidate["candidate_type"],
        "classification": candidate["classification"],
        "audiences": sorted(candidate["audiences"]),
        "retrieval_audiences": sorted(retrieval_audiences or candidate["audiences"]),
        "authorization_context_digest": (
            authorization_context.context_digest if authorization_context else None
        ),
        "active_pair": {
            key: active_pair.get(key)
            for key in (
                "generation_id",
                "graph_snapshot",
                "graph_sha256",
                "sidecar_sha256",
                "embedding_backend",
                "model_id",
                "model_revision",
                "model_manifest_digest",
                "production_qualified",
            )
        },
        "retriever": {
            "embedding_backend": retriever.backend.backend_id,
            "model_id": retriever.backend.model_id,
            "model_revision": retriever.backend.model_revision,
            "model_manifest_digest": retriever.backend.model_manifest_digest,
            "production_qualified": retriever.backend.production_qualified,
        },
        "comparison_policy": "exact_then_lane_isolated_hybrid_v2",
    }


def _valid_cached_comparison(
    value: Any,
    *,
    candidate: dict[str, Any],
    retriever: HybridRetriever,
) -> dict[str, Any] | None:
    required = {
        "candidate_id",
        "backend",
        "model_id",
        "model_revision",
        "production_qualified",
        "query_representation_digest",
        "semantic_matches",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or value["backend"] != retriever.backend.backend_id
        or value["model_id"] != retriever.backend.model_id
        or value["model_revision"] != retriever.backend.model_revision
        or value["production_qualified"] != retriever.backend.production_qualified
        or not isinstance(value["semantic_matches"], list)
    ):
        return None
    return value | {"candidate_id": candidate["candidate_id"]}
