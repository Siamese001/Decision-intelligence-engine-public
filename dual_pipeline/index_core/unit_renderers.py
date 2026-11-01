"""Additive semantic unit renderers for evidence passages, propositions, and entity profiles."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import (
    connect_sqlite,
    normalize_iso_date,
    normalize_space,
    sha256_bytes,
    stable_id,
)
from dual_pipeline.label_quality import canonicalize_audiences

from .constants import (
    APPROVED_VALIDATION_STATES,
    ARTIFACT_ID_PATTERN,
    CLASSIFICATION_RANK,
    ENTITY_FACET_MAX_TEXT_CHARACTERS,
)
from .eligibility import classify_retrieval_eligibility
from .policy import (
    classify_policy_proposition_eligibility,
    load_semantic_unit_policy,
)


def _contract_ref(prefix: str, value: str) -> str:
    return value if ARTIFACT_ID_PATTERN.fullmatch(value) else stable_id(prefix, value, length=28)


def _classification_str(value: Any) -> str:
    val = str(value or "")
    return "public" if val == "external_approved" else (val if val in CLASSIFICATION_RANK else "internal")


def _audiences_list(raw: Any) -> list[str]:
    return list(canonicalize_audiences(str(raw or "[]")))


def _make_retrieval_record(
    record_id: str, record_type: str, lane: str, title: str, canonical_entities: list[str],
    retrieval_text: str, evidence_ids: list[str], graph_snapshot: str, classification: str,
    audiences: list[str], retrieval_kind: str, answer_use: str, effective_date: str | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cid = stable_id("evidence-cluster", evidence_ids, length=24)
    return create_artifact(
        "retrieval-record", record_id, record_id=record_id, record_type=record_type,
        namespace=f"nee.{lane}.{record_type}s", lane=lane, title=title,
        canonical_entities=canonical_entities, retrieval_text=retrieval_text,
        retrieval_text_hash=sha256_bytes(retrieval_text.encode("utf-8")),
        evidence_cluster_id=cid, evidence_ids=evidence_ids, graph_snapshot=graph_snapshot,
        classification=classification, audiences=audiences,
        lifecycle="current" if lane == "approved" else "candidate", effective_date=effective_date,
        action_status=None, retrieval_kind=retrieval_kind,
        epistemic_status="approved" if lane == "approved" else "candidate",
        provenance_kind="source_bound", answer_use=answer_use, extensions=extensions or {},
    )


def _decontextualize_proposition(row: Mapping[str, Any]) -> str:
    subj = normalize_space(str(row.get("subject_name") or ""))
    pred = normalize_space(str(row.get("predicate") or "").lower().replace("_", " "))
    claim = normalize_space(str(row.get("object_literal") or ""))
    scope = normalize_space(str(row.get("scope_text") or ""))
    speaker = normalize_space(str(row.get("speaker_or_author") or ""))
    date = normalize_space(str(row.get("valid_from") or ""))

    text = claim if claim.lower().startswith(subj.lower()) else (f"{subj} {pred}: {claim}" if pred else f"{subj}: {claim}")
    parts: list[str] = []
    if scope and scope.lower() not in text.lower():
        parts.append(f"Scope: {scope}")
    if speaker:
        parts.append(f"Speaker: {speaker}")
    if date:
        parts.append(f"Date: {date}")
    if parts:
        text = f"{text}. " + ". ".join(parts) + "."
    return normalize_space(text + ("" if text.endswith(".") else "."))


def render_evidence_passage_records(
    graph_path: Path,
    graph_snapshot: str,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Render windowed, evidence-bound passages adhering to semantic unit policy."""
    if policy is None:
        policy = load_semantic_unit_policy()

    pass_pol = policy.get("passage_windowing", {})
    min_chars = pass_pol.get("min_chars", 400)
    max_chars = pass_pol.get("max_chars", 1200)

    g_conn = connect_sqlite(graph_path, readonly=True)
    try:
        ev_items = g_conn.execute(
            """SELECT evidence_id, source_id, source_version_id, locator_kind, locator_value,
                      section_name, slide_number, transcript_timestamp, direct_quote
               FROM evidence_item WHERE direct_quote IS NOT NULL AND TRIM(direct_quote) != ''
               ORDER BY source_version_id,
                        CASE WHEN transcript_timestamp IS NOT NULL THEN transcript_timestamp ELSE locator_value END"""
        ).fetchall()
        ae_rows = g_conn.execute(
            "SELECT assertion_id, evidence_id FROM assertion_evidence WHERE evidence_relationship='supports'"
        ).fetchall()
        asrt_rows = g_conn.execute(
            """SELECT a.assertion_id, a.canonical_status, a.canonical_eligible, a.confidence_score,
                      a.validation_state,
                      a.classification, a.allowed_audiences_json, a.subject_node_id, a.object_node_id,
                      a.currentness_state, a.valid_from, a.valid_to, a.qualifiers_json, a.retrieval_eligible,
                      a.assertion_type, a.origin_type, a.extraction_run_id, a.hitl_required,
                      a.validation_priority, a.validation_owner, a.atomicity_state
               FROM assertion a WHERE a.currentness_state IN ('current', 'unknown')"""
        ).fetchall()
    finally:
        g_conn.close()

    ev_to_asrt: dict[str, list[str]] = defaultdict(list)
    for r in ae_rows:
        ev_to_asrt[r["evidence_id"]].append(r["assertion_id"])
    asrt_meta = {
        r["assertion_id"]: dict(r)
        for r in asrt_rows
        if classify_retrieval_eligibility(
            dict(r),
            has_supporting_evidence=True,
            allow_unapproved_canonical_as_candidate=True,
        )[0]
    }

    by_partition: dict[tuple[str, str, Any], list[dict[str, Any]]] = defaultdict(list)
    for r in ev_items:
        row = dict(r)
        sv_id = row["source_version_id"] or "unknown_sv"
        b_key = (sv_id, "slide", row["slide_number"]) if row.get("slide_number") is not None else (
            (sv_id, "section", row["section_name"]) if row.get("section_name") else (sv_id, "flow", None)
        )
        by_partition[b_key].append(row)

    passages: list[dict[str, Any]] = []
    seen_fps: set[str] = set()

    for boundary_key, items in by_partition.items():
        curr_quotes, curr_ev_ids, curr_len = [], [], 0

        def flush(quotes: list[str], ev_ids: list[str], b_key: tuple[Any, ...]) -> None:
            if not quotes:
                return
            text = normalize_space(" ".join(quotes))
            if not text:
                return
            fp = sha256_bytes(text.lower().encode("utf-8"))
            if fp in seen_fps:
                return
            seen_fps.add(fp)

            supported_aids = [
                aid for evid in ev_ids for aid in ev_to_asrt.get(evid, []) if aid in asrt_meta
            ]
            supported_aids = list(dict.fromkeys(supported_aids))
            supp_records = [asrt_meta[aid] for aid in supported_aids]
            if supp_records:
                classification = max(
                    (_classification_str(r["classification"]) for r in supp_records),
                    key=lambda v: CLASSIFICATION_RANK[v],
                )
                shared_auds: set[str] | None = None
                for sr in supp_records:
                    auds = set(_audiences_list(sr["allowed_audiences_json"]))
                    shared_auds = auds if shared_auds is None else shared_auds & auds
                if not shared_auds:
                    return
                audiences = sorted(shared_auds)
                all_appr = all(
                    r["canonical_status"] == "canonical"
                    and bool(r.get("canonical_eligible", 1))
                    and r["validation_state"] in APPROVED_VALIDATION_STATES
                    and float(r.get("confidence_score") if r.get("confidence_score") is not None else 1.0) >= 0.65
                    for r in supp_records
                )
                lane = "approved" if all_appr else "candidate"
            else:
                return

            ans_use = "citation_only" if lane == "approved" else "candidate_hypothesis"
            contract_ev_ids = [_contract_ref("evidence-ref", eid) for eid in sorted(set(ev_ids))]
            record_id = stable_id("passage", b_key[0], fp, length=28)

            can_ents = []
            for sr in supp_records:
                if sr.get("subject_node_id"):
                    can_ents.append(_contract_ref("entity-ref", sr["subject_node_id"]))
                if sr.get("object_node_id"):
                    can_ents.append(_contract_ref("entity-ref", sr["object_node_id"]))
            can_ents = list(dict.fromkeys(can_ents)) or [_contract_ref("entity-ref", "source")]

            b_desc = f"{b_key[1]}:{b_key[2]}" if b_key[2] is not None else b_key[1]
            passages.append(
                _make_retrieval_record(
                    record_id=record_id, record_type="source_chunk", lane=lane,
                    title=f"Passage: {b_key[0]} {b_desc}",
                    canonical_entities=can_ents, retrieval_text=text,
                    evidence_ids=contract_ev_ids, graph_snapshot=graph_snapshot,
                    classification=classification, audiences=audiences,
                    retrieval_kind="evidence_span", answer_use=ans_use,
                    extensions={
                        "com.partner_advisory.nee.graph-assertion-ids": sorted(supported_aids),
                        "com.partner_advisory.nee.graph-evidence-ids": sorted(set(ev_ids)),
                        "com.partner_advisory.nee.source-version-id": b_key[0],
                    },
                )
            )

        for item in items:
            q = normalize_space(item["direct_quote"] or "")
            if not q:
                continue
            if curr_len + len(q) > max_chars and curr_len >= min_chars:
                flush(curr_quotes, curr_ev_ids, boundary_key)
                curr_quotes, curr_ev_ids, curr_len = [], [], 0
            curr_quotes.append(q)
            curr_ev_ids.append(item["evidence_id"])
            curr_len += len(q)
            if curr_len >= max_chars:
                flush(curr_quotes, curr_ev_ids, boundary_key)
                curr_quotes, curr_ev_ids, curr_len = [], [], 0
        if curr_quotes:
            flush(curr_quotes, curr_ev_ids, boundary_key)

    return passages


def render_proposition_records(
    graph_path: Path,
    graph_snapshot: str,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Render self-contained proposition records for eligible assertions under policy."""
    if policy is None:
        policy = load_semantic_unit_policy()

    g_conn = connect_sqlite(graph_path, readonly=True)
    try:
        rows = g_conn.execute(
            """SELECT a.*, sn.canonical_name AS subject_name, onode.canonical_name AS object_name
               FROM assertion a JOIN node sn ON sn.node_id=a.subject_node_id
               LEFT JOIN node onode ON onode.node_id=a.object_node_id
               WHERE a.currentness_state IN ('current', 'unknown') ORDER BY a.assertion_id"""
        ).fetchall()
        ev_map: dict[str, list[str]] = defaultdict(list)
        for r in g_conn.execute(
            "SELECT assertion_id, evidence_id FROM assertion_evidence WHERE evidence_relationship='supports' ORDER BY evidence_id"
        ):
            ev_map[r["assertion_id"]].append(r["evidence_id"])
    finally:
        g_conn.close()

    propositions: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        aid = row["assertion_id"]
        ev_ids = ev_map.get(aid, [])
        clearance_ok, _ = classify_retrieval_eligibility(
            row,
            has_supporting_evidence=len(ev_ids) > 0,
            allow_unapproved_canonical_as_candidate=True,
        )
        if not clearance_ok:
            continue
        prop_ok, _ = classify_policy_proposition_eligibility(row, policy)
        if not prop_ok:
            continue

        decontextualized = _decontextualize_proposition(row)
        if not decontextualized:
            continue

        lane = (
            "approved"
            if (
                row["canonical_status"] == "canonical"
                and bool(row.get("canonical_eligible", 1))
                and row["validation_state"] in APPROVED_VALIDATION_STATES
                and float(row.get("confidence_score") if row.get("confidence_score") is not None else 1.0) >= 0.65
            )
            else "candidate"
        )
        ans_use = "factual_answer" if lane == "approved" else "candidate_hypothesis"
        contract_ev_ids = [_contract_ref("evidence-ref", e) for e in ev_ids] or [_contract_ref("evidence-ref", "source")]

        can_ents = [_contract_ref("entity-ref", row["subject_node_id"])]
        if row.get("object_node_id"):
            can_ents.append(_contract_ref("entity-ref", row["object_node_id"]))
        can_ents = list(dict.fromkeys(can_ents))

        record_id = stable_id("proposition", aid, length=28)
        claim_prev = normalize_space(str(row.get("object_literal") or ""))[:80]

        propositions.append(
            _make_retrieval_record(
                record_id=record_id, record_type="assertion", lane=lane,
                title=f"{row['subject_name']}: {claim_prev}", canonical_entities=can_ents,
                retrieval_text=decontextualized, evidence_ids=contract_ev_ids,
                graph_snapshot=graph_snapshot, classification=_classification_str(row["classification"]),
                audiences=_audiences_list(row["allowed_audiences_json"]), retrieval_kind="assertion",
                answer_use=ans_use, effective_date=normalize_iso_date(row["valid_from"]),
                extensions={
                    "com.partner_advisory.nee.graph-assertion-ids": [aid],
                    "com.partner_advisory.nee.graph-evidence-ids": ev_ids,
                    "com.partner_advisory.nee.source-id": row.get("primary_source_id"),
                    "com.partner_advisory.nee.predicate": row.get("predicate"),
                    "com.partner_advisory.nee.assertion-type": row.get("assertion_type"),
                },
            )
        )

    return propositions


def render_entity_profile_records(
    assertion_records: Iterable[dict[str, Any]],
    *,
    entity_names: dict[str, str],
    graph_snapshot: str,
    max_facts: int = 10,
) -> list[dict[str, Any]]:
    """Render reworked entity profiles without boilerplate prefix, maximizing semantic divergence."""
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in assertion_records:
        lane = record.get("lane")
        if lane not in {"approved", "candidate"}:
            continue
        ext = record.get("extensions")
        if not isinstance(ext, dict):
            continue
        graph_entity_ids = ext.get("com.partner_advisory.nee.graph-entity-ids")
        if not isinstance(graph_entity_ids, list):
            continue
        record_id = record.get("record_id")
        if not record_id:
            continue
        for entity_id in set(graph_entity_ids):
            if normalize_space(str(entity_names.get(entity_id, ""))):
                grouped[(lane, entity_id)][record_id] = record

    profiles: list[dict[str, Any]] = []
    for (lane, entity_id), by_record_id in sorted(grouped.items()):
        source_records = [by_record_id[rid] for rid in sorted(by_record_id)]
        shared_auds: set[str] | None = None
        for sr in source_records:
            auds = set(sr.get("audiences", []))
            shared_auds = auds if shared_auds is None else shared_auds & auds
        if not shared_auds:
            continue

        evidence_ids = sorted({eid for sr in source_records for eid in sr.get("evidence_ids", [])})
        graph_aids = sorted(
            {sr["extensions"]["com.partner_advisory.nee.graph-object-id"] for sr in source_records if "com.partner_advisory.nee.graph-object-id" in sr.get("extensions", {})}
        )
        graph_eids = sorted(
            {geid for sr in source_records for geid in sr.get("extensions", {}).get("com.partner_advisory.nee.graph-evidence-ids", [])}
        )
        if not evidence_ids or not graph_aids:
            continue

        ename = normalize_space(str(entity_names[entity_id]))
        fact_fragments = []
        profile_source_ids = []
        for sr in source_records[:max_facts]:
            txt = normalize_space(str(sr.get("retrieval_text", "")))
            if txt:
                fact_fragments.append(txt)
                profile_source_ids.append(sr.get("record_id", ""))

        if not fact_fragments or not profile_source_ids:
            continue

        profile_text = f"Entity: {ename}. Facts: " + " | ".join(fact_fragments)
        if len(profile_text) > ENTITY_FACET_MAX_TEXT_CHARACTERS:
            profile_text = profile_text[: ENTITY_FACET_MAX_TEXT_CHARACTERS - 3] + "..."

        classification = max((str(sr["classification"]) for sr in source_records), key=lambda v: CLASSIFICATION_RANK[v])
        record_id = stable_id("entity-profile", lane, entity_id, length=28)

        profiles.append(
            _make_retrieval_record(
                record_id=record_id, record_type="entity_facet", lane=lane,
                title=f"Entity Profile: {ename}", canonical_entities=[_contract_ref("entity-ref", entity_id)],
                retrieval_text=profile_text, evidence_ids=evidence_ids,
                graph_snapshot=graph_snapshot, classification=classification,
                audiences=sorted(shared_auds), retrieval_kind="entity_facet",
                answer_use="navigation_only",
                extensions={
                    "com.partner_advisory.nee.graph-entity-ids": [entity_id],
                    "com.partner_advisory.nee.graph-assertion-ids": graph_aids,
                    "com.partner_advisory.nee.graph-evidence-ids": graph_eids,
                    "com.partner_advisory.nee.entity-facet-source-record-ids": sorted(by_record_id),
                    "com.partner_advisory.nee.entity-facet-profile-record-ids": profile_source_ids,
                },
            )
        )

    return profiles
