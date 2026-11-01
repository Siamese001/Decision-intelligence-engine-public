"""Helper functions for release verification, A2A finalization, and receipt sealing."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

from ..a2a_finalization import validated_ingress_finalization_reference
from ..core import ZERO_HASH, digest, sha256_bytes
from ..parameters import get_hitl_ambiguity_margin_threshold
from .constants import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    _A2A_SOURCE_ATTEST_CAPABILITY,
    _A2A_SOURCE_ATTEST_RESOURCES,
    FACT_PROMOTION_READY_STATUSES,
)


def _valid_a2a_finalization_binding(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _A2A_FINALIZATION_BINDING_FIELDS:
        return False
    if value.get("verification_state") != "finalization_archive_replayed":
        return False
    return all(
        isinstance(value.get(field), str) and re.fullmatch(r"[0-9a-f]{64}", value[field])
        for field in (
            "receipt_canonical_digest",
            "receipt_file_sha256",
            "package_file_sha256",
            "package_canonical_digest",
            "archive_binding_digest",
        )
    ) and all(
        isinstance(value.get(field), str) and value[field]
        for field in ("receipt_id", "archive_root_id")
    )


def _a2a_intake_authorization_attests_source(value: object) -> bool:
    """Return whether a sealed intake audit proves the A2A source-attest scope."""
    if not isinstance(value, dict):
        return False
    capabilities = value.get("capabilities")
    resource_names = value.get("authorized_resource_names")
    return (
        isinstance(capabilities, list)
        and _A2A_SOURCE_ATTEST_CAPABILITY in capabilities
        and isinstance(resource_names, list)
        and set(resource_names) >= _A2A_SOURCE_ATTEST_RESOURCES
    )


def _replayed_a2a_payload_governance(
    governance: object, reference: dict[str, Any]
) -> dict[str, Any]:
    """Project reconstructed A2A governance into the graph-writer payload form."""
    if not isinstance(governance, dict):
        raise RuntimeError("replayed A2A candidate has no retrieval governance")
    required = {
        "source_item_id",
        "retrieval_kind",
        "answer_use",
        "fact_promotion_eligible",
        "promotion_status",
    }
    result = {key: governance[key] for key in required}
    if governance.get("source_selection_bindings") is not None:
        result["a2a_source_span_bindings"] = governance["source_selection_bindings"]
    if governance.get("finalization_binding") is not None:
        result["a2a_finalization_binding"] = governance["finalization_binding"]
    result["a2a_finalization_reference"] = reference
    result["source_kind"] = "a2a_fact_vector_packet"
    return result


def _default_retrieval_governance(candidate_type: str) -> dict[str, Any]:
    """Return the fail-closed graph qualifier projection for an intake candidate."""
    if candidate_type == "action":
        retrieval_kind, answer_use = "guidance", "guidance"
    elif candidate_type == "source_chunk":
        retrieval_kind, answer_use = "evidence_span", "citation_only"
    elif candidate_type == "architecture_component":
        retrieval_kind, answer_use = "entity_facet", "navigation_only"
    elif candidate_type in {"relationship", "architecture_relationship"}:
        retrieval_kind, answer_use = "relationship_assertion", "candidate_hypothesis"
    else:
        retrieval_kind, answer_use = "assertion", "candidate_hypothesis"
    return {
        "retrieval_kind": retrieval_kind,
        "answer_use": answer_use,
        "fact_promotion_eligible": False,
    }


def _retrieval_governance_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and preserve source-bound retrieval qualifiers for graph write."""
    candidate = payload["candidate"]
    candidate_type = candidate["candidate_type"]
    default = _default_retrieval_governance(candidate_type)
    supplied = payload.get("retrieval_governance")
    if supplied is None:
        return default
    if not isinstance(supplied, dict):
        raise RuntimeError("candidate retrieval governance must be an object")
    required = {
        "source_item_id",
        "retrieval_kind",
        "answer_use",
        "fact_promotion_eligible",
        "promotion_status",
    }
    optional = {
        "a2a_source_span_bindings",
        "a2a_finalization_binding",
        "a2a_finalization_reference",
        "source_kind",
    }
    if not required <= set(supplied) or not set(supplied) <= required | optional:
        raise RuntimeError("candidate retrieval governance has an invalid shape")
    if (
        not isinstance(supplied["source_item_id"], str)
        or not supplied["source_item_id"].strip()
        or not isinstance(supplied["promotion_status"], str)
        or not supplied["promotion_status"].strip()
        or not isinstance(supplied["fact_promotion_eligible"], bool)
    ):
        raise RuntimeError("candidate retrieval governance has invalid values")
    expected = {
        "assertion": ("assertion", "candidate_hypothesis"),
        "action": ("guidance", "guidance"),
        "source_chunk": ("evidence_span", "citation_only"),
    }.get(candidate_type)
    if expected is None or (
        supplied["retrieval_kind"],
        supplied["answer_use"],
    ) != expected:
        raise RuntimeError("candidate retrieval governance does not match candidate type")
    if supplied["fact_promotion_eligible"] and (
        candidate_type != "assertion"
        or supplied["promotion_status"] not in FACT_PROMOTION_READY_STATUSES
    ):
        raise RuntimeError(
            "fact promotion eligibility requires a factual assertion with source-bound ready evidence"
        )
    source_kind = supplied.get("source_kind")
    bindings = supplied.get("a2a_source_span_bindings")
    finalization_binding = supplied.get("a2a_finalization_binding")
    finalization_reference = supplied.get("a2a_finalization_reference")
    if source_kind is not None and source_kind != "a2a_fact_vector_packet":
        raise RuntimeError("candidate retrieval governance has an unsupported source kind")
    if bindings is not None:
        required_binding = {
            "selection_ledger_sha256",
            "span_ledger_sha256",
            "reviewer",
            "decided_at",
            "human_attestation",
            "reviewer_authorization_reference",
            "fact_id",
            "decision",
            "span_proposal_id",
            "evidence_id",
            "source_id",
            "rationale",
            "quote",
            "quote_sha256",
            "visible_start_offset",
            "visible_end_offset",
            "raw_response_sha256",
            "visible_text_sha256",
            "canonical_locator",
        }
        if (
            source_kind != "a2a_fact_vector_packet"
            or not isinstance(bindings, list)
            or not bindings
            or any(
                not isinstance(binding, dict)
                or set(binding) != required_binding
                or not isinstance(binding["quote"], str)
                or not binding["quote"].strip()
                or binding["quote_sha256"]
                != sha256_bytes(binding["quote"].encode("utf-8"))
                or not isinstance(binding["canonical_locator"], dict)
                or binding["canonical_locator"].get("quote_digest")
                != binding["quote_sha256"]
                for binding in bindings
            )
        ):
            raise RuntimeError("A2A source-span governance is not quote-bound")
    if finalization_binding is not None and (
        source_kind != "a2a_fact_vector_packet"
        or not _valid_a2a_finalization_binding(finalization_binding)
    ):
        raise RuntimeError("A2A finalization receipt binding is invalid")
    if finalization_reference is not None:
        if source_kind != "a2a_fact_vector_packet":
            raise RuntimeError("A2A finalization evidence reference has an invalid source kind")
        try:
            finalization_reference = validated_ingress_finalization_reference(
                finalization_reference
            )
        except ValueError as exc:
            raise RuntimeError("A2A finalization evidence reference is invalid") from exc
        if (
            finalization_binding is None
            or finalization_reference["package_file_sha256"]
            != finalization_binding["package_file_sha256"]
            or finalization_reference["receipt_file_sha256"]
            != finalization_binding["receipt_file_sha256"]
            or finalization_reference["archive_root_id"]
            != finalization_binding["archive_root_id"]
        ):
            raise RuntimeError("A2A finalization evidence reference does not match its receipt")
    if source_kind == "a2a_fact_vector_packet" and supplied["fact_promotion_eligible"]:
        if (
            candidate_type != "assertion"
            or supplied["promotion_status"]
            != "candidate_evidence_selected_span_hitl_required"
            or bindings is None
            or not _valid_a2a_finalization_binding(finalization_binding)
            or finalization_reference is None
        ):
            raise RuntimeError(
                "A2A fact promotion eligibility requires replayed selected source-span governance"
            )
    return dict(supplied)


def _correlation(
    *,
    source_id: str | None = None,
    candidate_id: str | None = None,
    graph_operation_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "source_id": source_id,
        "candidate_id": candidate_id,
        "query_id": None,
        "graph_operation_id": graph_operation_id,
        "hitl_item_id": None,
    }


def _row_digest(connection: Any, table: str, key: str, value: str) -> str:
    row = connection.execute(f'SELECT * FROM "{table}" WHERE "{key}"=?', (value,)).fetchone()
    return ZERO_HASH if row is None else digest(dict(row))


def _seal_receipt(value: dict[str, Any]) -> dict[str, Any]:
    result = {k: v for k, v in value.items() if k != "receipt_digest"}
    result["receipt_digest"] = digest(result)
    return result


def _verify_receipt(value: dict[str, Any]) -> None:
    declared = value.get("receipt_digest")
    actual = digest({key: item for key, item in value.items() if key != "receipt_digest"})
    if declared != actual:
        raise ValueError("release receipt digest mismatch")
    if "next_pair" in value or "previous_pair" in value:
        next_pair, previous_pair = value.get("next_pair"), value.get("previous_pair")
        if not isinstance(next_pair, dict) or not isinstance(previous_pair, dict):
            raise ValueError("release receipt pair bindings are malformed")
        flags = (value.get("production_qualified"), next_pair.get("production_qualified"), previous_pair.get("production_qualified"))
        if any(not isinstance(flag, bool) for flag in flags) or len(set(flags)) != 1:
            raise ValueError("release receipt production policy bindings disagree")


def release_action_resources(receipt: dict[str, Any], action: str) -> dict[str, str]:
    """Return the exact signed resource bindings for a release transition."""
    if action not in {"activate", "rollback"}:
        raise ValueError("release action must be activate or rollback")
    _verify_receipt(receipt)
    generation_id = receipt.get("next_pair", {}).get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("release receipt has no target generation")
    return {
        "release_receipt": receipt["receipt_digest"],
        "release_generation": sha256_bytes(generation_id.encode("utf-8")),
        "release_action": sha256_bytes(action.encode("utf-8")),
    }


def _verify_activation_binding(activation: dict[str, Any], receipt: dict[str, Any]) -> None:
    _verify_receipt(activation)
    active_pair, next_pair = activation.get("active_pair"), receipt.get("next_pair", {})
    if (
        activation.get("status") != "ACTIVE"
        or activation.get("receipt_id") != receipt.get("receipt_id")
        or not isinstance(active_pair, dict)
        or active_pair.get("generation_id") != next_pair.get("generation_id")
        or active_pair.get("graph_sha256") != next_pair.get("graph_sha256")
        or active_pair.get("sidecar_sha256") != next_pair.get("sidecar_sha256")
    ):
        raise RuntimeError("activation receipt is not bound to the prepared pair")


def _verify_rollback_binding(rollback: dict[str, Any], receipt: dict[str, Any]) -> None:
    _verify_receipt(rollback)
    restored_pair = rollback.get("restored_pair")
    if (
        rollback.get("status") != "ROLLED_BACK"
        or rollback.get("receipt_id") != receipt.get("receipt_id")
        or rollback.get("rolled_back_generation_id") != receipt.get("next_pair", {}).get("generation_id")
        or not isinstance(restored_pair, dict)
        or restored_pair.get("generation_id") != receipt.get("previous_pair", {}).get("generation_id")
        or restored_pair.get("graph_sha256") != receipt.get("previous_pair", {}).get("graph_sha256")
    ):
        raise RuntimeError("rollback receipt is not bound to the prepared pair")


def _lookup_prior_mutex_winner(decision_store: Any, target: str, predicate: str) -> Optional[str]:
    """Check decision ledger for an existing human operator resolution."""
    if decision_store is not None and hasattr(decision_store, "get_history"):
        try:
            for rec in decision_store.get_history("mutex_lead_resolution"):
                meta = rec.get("metadata") or rec.get("payload") or {}
                if meta.get("target") == target and meta.get("predicate") == predicate:
                    return meta.get("selected_subject") or rec.get("selected_option")
        except Exception as e:
            logger.warning("Failed to retrieve mutex resolution history from decision store: %s", e)
    return None


def _prompt_or_interactively_resolve_mutex(
    target: str, predicate: str, candidates: list[dict[str, Any]],
    interactive_resolver: Optional[Any], decision_store: Optional[Any],
) -> str:
    """Interactively prompts or executes resolver to designate an exclusive lead."""
    from dual_pipeline.hitl_core.envelope import create_hitl_envelope
    from dual_pipeline.exceptions import HITLRequiredError  # noqa

    options = []
    for idx, cand in enumerate(candidates, 1):
        subj, c_val, is_rec = cand["subject"], cand["confidence"], (idx == 1)
        rec_lbl, tier = ("(Recommended) " if is_rec else ""), ("HIGH" if c_val >= 0.80 else "MEDIUM")
        options.append({
            "label": f"{rec_lbl}Designate {subj} as exclusive lead for {target} — Confidence: {tier} ({int(c_val * 100)}%)",
            "subject": subj, "assertion_id": cand["assertion_id"], "confidence": c_val,
            "confidence_score": c_val, "confidence_level": f"{tier} ({int(c_val * 100)}%)",
            "tier": tier, "is_recommended": is_rec,
        })
    if interactive_resolver is not None:
        envelope = create_hitl_envelope(
            category="mutex_lead_resolution", domain=target,
            question=(
                f"### HITL Decision Required (Workstream Mutex Lead Resolution)\n\n"
                f"**Target Asset:** {target}\n**Predicate:** {predicate}\n"
                f"Multiple competing leadership claims detected. Select the exclusive DRI to clear Gate 30:"
            ),
            options=options, store=decision_store, threshold=get_hitl_ambiguity_margin_threshold(),
        )
        try:
            sel = str(interactive_resolver(envelope))
            chosen = next((o["subject"] for o in options if o["subject"] == sel or o["label"] == sel or o.get("assertion_id") == sel), None)
            if not chosen and options:
                chosen = options[0]["subject"]
            if chosen and decision_store is not None and hasattr(decision_store, "record_decision"):
                decision_store.record_decision(
                    decision_id=f"mutex_{abs(hash(target)) % 100000}", category="mutex_lead_resolution",
                    prompt=f"Operator designated {chosen} as exclusive lead for {target}", options=options,
                    selected_option=chosen, rationale=f"Operator selected {chosen} as exclusive lead for {target} via Gate 30",
                    metadata={"target": target, "predicate": predicate, "selected_subject": chosen},
                )
            if chosen:
                return chosen
        except Exception as e:
            logger.warning("Interactive mutex resolver failed: %s", e)

    prompt = (
        f"### HITL Decision Required (Workstream Mutex Lead Resolution — Gate 30)\n\n"
        f"**Target Entity:** {target}\n**Predicate:** {predicate}\n"
        f"Release Gate 30 detected active multi-lead/delivery conflicts across {len(candidates)} entities "
        f"({', '.join(c['subject'] for c in candidates)}).\n\nPlease designate the authoritative, exclusive lead for this workstream:"
    )
    raise HITLRequiredError(
        prompt, hitl_type="mutex_lead_resolution", missing_fields=[f"lead:{target}"],
        options=options, prompt=prompt, evidence={"target": target, "predicate": predicate, "candidates": candidates},
    )


def _ensure_superseded_controlled_value(cur: Any) -> None:
    if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controlled_value_registry'").fetchone():
        cur.execute("INSERT OR IGNORE INTO controlled_value_registry VALUES ('currentness_state', 'historical', 'Historical assertion superseded by operator mutex resolution.', 1, 5)")
        cur.execute("INSERT OR IGNORE INTO controlled_value_registry VALUES ('canonical_status', 'superseded', 'Superseded by operator mutex resolution.', 1, 5)")


def _sync_mutex_sidecar_demotion(sidecar_path: Any, loser_ids: list[str]) -> None:
    if not (sidecar_path and loser_ids and Path(sidecar_path).is_file()):
        return
    from dual_pipeline.storage.governed_context import GovernedStorageContext
    conn_s = GovernedStorageContext.connect(sidecar_path, readonly=False)
    try:
        cur_s = conn_s.cursor()
        s_cols = {r[1] for r in cur_s.execute("PRAGMA table_info(retrieval_record)").fetchall()}
        if "lane" in s_cols:
            for lid in loser_ids:
                if "object_id" in s_cols:
                    cur_s.execute("UPDATE retrieval_record SET lane = 'historical' WHERE lane = 'approved' AND (object_id = ? OR record_id = ?)", (lid, lid))
                else:
                    cur_s.execute("UPDATE retrieval_record SET lane = 'historical' WHERE lane = 'approved' AND (record_id = ? OR record_id LIKE ?)", (lid, f"%{lid}%"))
            conn_s.commit()
            cur_s.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:
        logger.debug("Could not sync sidecar mutex demotion: %s", exc)
    finally:
        conn_s.close()


def _resolve_single_mutex_conflict(
    cur: Any, conflict: dict[str, Any], pre_counts: dict[tuple[str, str], int],
    pre_eval: Optional[dict[str, Any]], claim_expr: str, decision_store: Any, interactive_resolver: Any,
    sidecar_path: Optional[Any] = None,
) -> bool:
    target, predicate = conflict["target"], conflict["predicate"]
    cur.execute(
        f"SELECT assertion_id, subject_node_id, confidence_score, {claim_expr} FROM assertion "
        "WHERE currentness_state = 'current' AND canonical_status = 'canonical' "
        "AND predicate = ? AND object_node_id = ? ORDER BY confidence_score DESC",
        (predicate, target),
    )
    rows = cur.fetchall()
    if len(rows) <= 1:
        return False
    prior_winner = _lookup_prior_mutex_winner(decision_store, target, predicate)
    if not prior_winner:
        pre_map = {(c["target"], c["predicate"]): c for c in (pre_eval or {}).get("mutex_conflicts", [])}
        pre_entry = pre_map.get((target, predicate))
        pre_subjs = set(pre_entry.get("subjects") or []) if pre_entry else set()
        curr_subjs = {r[1] for r in rows}
        if (
            pre_eval is not None
            and len(rows) <= pre_counts.get((target, predicate), 0)
            and (not pre_subjs or curr_subjs.issubset(pre_subjs))
        ):
            return False
        candidates = [{"assertion_id": r[0], "subject": r[1], "confidence": r[2] or 0.85, "claim": r[3]} for r in rows]
        prior_winner = _prompt_or_interactively_resolve_mutex(
            target, predicate, candidates, interactive_resolver, decision_store
        )
    loser_ids = [r[0] for r in rows if r[1] != prior_winner]
    cur.execute(
        "UPDATE assertion SET currentness_state = 'historical', canonical_status = 'superseded', "
        "retrieval_eligible = 0, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE currentness_state = 'current' AND predicate = ? AND object_node_id = ? AND subject_node_id != ?",
        (predicate, target, prior_winner),
    )
    if loser_ids and cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'").fetchone():
        ph = ",".join("?" for _ in loser_ids)
        cur.execute(
            f"UPDATE search_document SET currentness_state = 'historical', canonical_status = 'superseded', retrieval_eligible = 0 WHERE object_id IN ({ph})",
            tuple(loser_ids),
        )
    _sync_mutex_sidecar_demotion(sidecar_path, loser_ids)
    return True


def _attempt_interactive_mutex_resolution(
    graph_path: Any, sidecar_path: Any, post_eval: dict[str, Any],
    interactive_resolver: Optional[Any] = None, store: Optional[Any] = None,
    repo_root: Optional[Path] = None, pre_eval: Optional[dict[str, Any]] = None,
) -> bool:
    """Interactively resolves competing leads in active workstreams/pods for Gate 30."""
    from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
    from dual_pipeline.storage.governed_context import GovernedStorageContext

    mutex_conflicts = post_eval.get("mutex_conflicts", [])
    if not mutex_conflicts:
        return False
    pre_counts = {(c["target"], c["predicate"]): c.get("count", 0) for c in (pre_eval or {}).get("mutex_conflicts", [])}
    decision_store = store
    if decision_store is None:
        try:
            decision_store = HITLDecisionStore()
        except Exception as e:
            logger.warning("Failed to initialize HITLDecisionStore for mutex resolution: %s", e)
    resolved_any = False
    conn = GovernedStorageContext.connect(graph_path, readonly=False)
    try:
        cur = conn.cursor()
        _ensure_superseded_controlled_value(cur)
        cols = {r[1] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
        claim_expr = "COALESCE(claim_text, '')" if "claim_text" in cols else "COALESCE(scope_text, object_literal, '')"
        for conflict in mutex_conflicts:
            if _resolve_single_mutex_conflict(
                cur, conflict, pre_counts, pre_eval, claim_expr, decision_store, interactive_resolver, sidecar_path=sidecar_path
            ):
                conn.commit()
                resolved_any = True
        if resolved_any:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return resolved_any


def _only_preexisting_mutex_conflicts(pre_eval: dict[str, Any], post_eval: dict[str, Any]) -> bool:
    pre_map = {(c["target"], c["predicate"]): c for c in pre_eval.get("mutex_conflicts", [])}
    for c in post_eval.get("mutex_conflicts", []):
        key = (c["target"], c["predicate"])
        pre_c = pre_map.get(key)
        if not pre_c or c.get("count", 0) > pre_c.get("count", 0):
            return False
        post_subjs = set(c.get("subjects") or [])
        pre_subjs = set(pre_c.get("subjects") or [])
        if post_subjs and pre_subjs and not post_subjs.issubset(pre_subjs):
            return False
    return True


def evaluate_gate30_non_degradation(
    graph_path: Any, sidecar_path: Any, active_graph_path: Any, active_sidecar_path: Any,
    interactive_resolver: Optional[Any] = None, store: Optional[Any] = None, repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Evaluate GS-10 Graph Signal Non-Degradation Gate (Gate 30)."""
    if not (active_graph_path and getattr(active_graph_path, "exists", lambda: False)()):
        return {}
    from ..graph_signal_core import evaluate_differential_signal, evaluate_graph_signal

    post_eval = evaluate_graph_signal(graph_path, sidecar_path)
    pre_eval = evaluate_graph_signal(active_graph_path, active_sidecar_path)
    eval_res = evaluate_differential_signal(pre_eval, post_eval)
    if eval_res.get("passed", False):
        return eval_res
    violations = eval_res.get("violations", ["Graph signal degraded"])
    if any("MUTEX_CONFLICTS_PRESENT" in v for v in violations):
        if _attempt_interactive_mutex_resolution(
            graph_path, sidecar_path, post_eval, interactive_resolver=interactive_resolver,
            store=store, repo_root=repo_root, pre_eval=pre_eval,
        ):
            post_eval = evaluate_graph_signal(graph_path, sidecar_path)
            eval_res = evaluate_differential_signal(pre_eval, post_eval)
            if eval_res.get("passed", False):
                return eval_res
            violations = eval_res.get("violations", ["Graph signal degraded"])
        if _only_preexisting_mutex_conflicts(pre_eval, post_eval):
            violations = [v for v in violations if "MUTEX_CONFLICTS_PRESENT" not in v]
            if not violations:
                return {**eval_res, "passed": True, "verdict": "PASS", "violations": []}
    raise RuntimeError(f"Release Gate 30 (Graph Signal Non-Degradation) FAILED: {'; '.join(violations)}")
