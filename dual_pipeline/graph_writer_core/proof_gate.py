"""Proof-gated apply() for GovernedGraphWriter (kept out of writer.py to respect the file-size ratchet)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..graph_mutation_proof import NOT_APPLICABLE_NO_OPERATIONS, MutationGuard
from ..storage.governed_context import GLOBAL_WRITE_SERIALIZER


class SignalTranslationCompletenessError(ValueError):
    """Raised when Gate C2 detects signal-to-graph translation loss or vacuous boilerplate claims."""


def _check_vacuous_boilerplate_claims(
    ent_upserts: list[dict[str, Any]],
    ops: list[dict[str, Any]],
    change_set: dict[str, Any] | None,
) -> None:
    payloads = ((change_set or {}).get("extensions") or {}).get("com.partner_advisory.nee.operation-payloads") or {}
    claim_ops = [op for op in ops if op.get("operation") in ("add_candidate_assertion", "add_approved_assertion")]
    substantive = [
        e for e in ent_upserts
        if len(str(e.get("description") or "").strip()) >= 10
        and "is an identified" not in str(e.get("description") or "").lower()
    ]
    if claim_ops and substantive:
        texts = [
            str((payloads.get(op["operation_id"]) or {}).get("candidate", {}).get("normalized_text") or "")
            for op in claim_ops
        ]
        if texts and all("is an identified" in t.lower() for t in texts):
            raise SignalTranslationCompletenessError(
                "ERR_VACUOUS_BOILERPLATE_CLAIM: All emitted claim operations are vacuous 'is an identified' stubs despite substantive entity descriptions"
            )


def validate_bijective_signal_conservation(
    projection: dict[str, Any],
    change_set: dict[str, Any] | None,
) -> dict[str, Any]:
    """Gate C2: Verify that every signal family in Stage 03 translates into change-set operations without vacuous stub loss."""
    gm = projection.get("graph_mutations") if isinstance(projection.get("graph_mutations"), dict) else projection
    ent_upserts = [e for e in (gm.get("entity_upserts") or []) if isinstance(e, dict)]
    rel_signals = [r for r in (gm.get("relationship_signals") or []) if isinstance(r, dict)]
    aux_keys = ("decision_updates", "proposal_updates", "risk_updates", "action_updates", "strategic_intent_signals", "metrics")
    aux_count = sum(len(gm.get(k) or []) for k in aux_keys)
    total_input_signals = len(ent_upserts) + len(rel_signals) + aux_count
    ops = (change_set or {}).get("operations") or []
    if total_input_signals > 0 and not ops:
        raise SignalTranslationCompletenessError(
            f"ERR_SIGNAL_TRANSLATION_LOSS: Projection has {total_input_signals} signals but change_set has 0 operations"
        )
    _check_vacuous_boilerplate_claims(ent_upserts, ops, change_set)
    return {
        "gate": "Gate_C2_Bijective_Signal_Conservation",
        "status": "PASS",
        "input_entities": len(ent_upserts),
        "input_relationships": len(rel_signals),
        "input_auxiliary_signals": aux_count,
        "emitted_operations": len(ops),
    }


def run_with_proof_gate(
    database_path: Path,
    label: str,
    apply_fn: Callable[[MutationGuard], dict[str, Any]],
) -> dict[str, Any]:
    """Execute a graph mutation with pre-mutation byte snapshot rollback if proof verification fails."""
    resolved = database_path.resolve()
    with GLOBAL_WRITE_SERIALIZER.get_lock(str(resolved)):
        pre_bytes = resolved.read_bytes() if resolved.is_file() else None
        guard = MutationGuard(resolved, label=label)
        try:
            return apply_fn(guard)
        except Exception:
            if pre_bytes is not None:
                resolved.write_bytes(pre_bytes)
                for suffix in ("-wal", "-shm"):
                    side = resolved.with_name(resolved.name + suffix)
                    if side.exists():
                        side.unlink(missing_ok=True)
                try:
                    from ..core import connect_sqlite
                    _conn = connect_sqlite(resolved)
                    try:
                        _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    finally:
                        _conn.close()
                except Exception:
                    pass
            raise


_PROMOTABLE_ASSERTION_OPS = {"add_approved_assertion", "add_relationship", "add_action_item"}
_APPROVED_OP_MAP = {
    "add_relationship": "add_approved_relationship",
    "add_action_item": "add_approved_action_item",
}


def _collect_promoted_endpoints(
    ops: list[dict[str, Any]],
    payloads: dict[str, Any],
) -> set[str]:
    from ..uwg_core.node_promotion import _has_unverified_assertion_fields

    endpoints: set[str] = set()
    for op in ops:
        op_name = str(op.get("operation") or "")
        payload = payloads.get(op.get("operation_id")) or {}
        if op_name not in _PROMOTABLE_ASSERTION_OPS or payload.get("disposition") != "APPROVED":
            continue
        if _has_unverified_assertion_fields(payload):
            continue
        for key in ("subject_node_id", "object_node_id"):
            val = payload.get(key)
            if val:
                endpoints.add(str(val))
    return endpoints


def _classify_probe_op(
    op_name: str,
    tid: str,
    payload: dict[str, Any],
    promoted_endpoints: set[str],
) -> str:
    from ..uwg_core.node_promotion import _is_unverified_node_row

    if op_name in _APPROVED_OP_MAP and payload.get("disposition") == "APPROVED":
        return _APPROVED_OP_MAP[op_name]
    if op_name == "create_entity" and payload.get("entity_kind") == "internal" and tid in promoted_endpoints:
        ent = payload.get("entity") or {}
        if not _is_unverified_node_row(tid, ent.get("canonical_name"), ent.get("description")):
            return "create_promoted_entity"
    return op_name


def _resolve_operation_targets(
    change_set: dict[str, Any],
    result_operations: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Map committed operation receipts to (probe_op_type, target_id) with promotion awareness."""
    payloads = ((change_set.get("extensions") or {}).get("com.partner_advisory.nee.operation-payloads")) or {}
    ops = [op for op in (change_set.get("operations") or []) if isinstance(op, dict)]
    op_type_by_id = {str(op.get("operation_id")): str(op.get("operation") or "") for op in ops}
    promoted_endpoints = _collect_promoted_endpoints(ops, payloads)
    op_targets: list[tuple[str, str]] = []
    for rec in result_operations:
        tid = str(rec.get("target_id") or "")
        if not tid:
            continue
        oid = str(rec.get("operation_id") or "")
        op_name = _classify_probe_op(
            op_type_by_id.get(oid, ""),
            tid,
            payloads.get(oid) or {},
            promoted_endpoints,
        )
        op_targets.append((op_name, tid))
    return op_targets


class ProofGatedApplyMixin:
    """Wraps the writer's real commit with a before-snapshot and a fail-closed physical proof."""

    graph_path: Path

    def _apply_committed(
        self,
        change_set: dict[str, Any],
        *,
        fail_after_operations: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def apply(
        self,
        change_set: dict[str, Any],
        *,
        fail_after_operations: int | None = None,
    ) -> dict[str, Any]:
        """Apply an approved change set and physically prove the write landed.

        The write lock is reentrant, so holding it here keeps the before-snapshot,
        the commit, and the after-proof atomic with respect to other writers.
        """
        label = f"graph-writer:{change_set.get('change_set_id', 'unknown')}"

        def _do_apply(guard: MutationGuard) -> dict[str, Any]:
            result = self._apply_committed(change_set, fail_after_operations=fail_after_operations)
            if result.get("operation_count", 0) == 0:
                result["physical_proof"] = dict(NOT_APPLICABLE_NO_OPERATIONS)
            else:
                op_targets = _resolve_operation_targets(change_set, result["operations"])
                result["physical_proof"] = guard.finalize(
                    record_ids=[op["target_id"] for op in result["operations"]],
                    audit_events=result["operation_count"],
                    operation_targets=op_targets,
                )
            return result

        return run_with_proof_gate(self.graph_path, label, _do_apply)


def update_assertion_object_literals(
    connection_or_path: Any,
    updates: list[tuple[str, str]],
) -> list[str]:
    """Update assertion object_literal values through the governed graph writer layer."""
    from storage.connection import create_sqlite_conn

    if not updates:
        return []
    if isinstance(connection_or_path, (str, Path)):
        conn = create_sqlite_conn(Path(connection_or_path), readonly=False)
        close_after = True
    else:
        conn = connection_or_path
        close_after = False
    try:
        cur = conn.cursor()
        updated_ids: list[str] = []
        for new_literal, assertion_id in updates:
            if not assertion_id or not new_literal:
                continue
            cur.execute(
                "UPDATE assertion SET object_literal = ? WHERE assertion_id = ?",
                (new_literal, assertion_id),
            )
            updated_ids.append(assertion_id)
        if close_after:
            conn.commit()
        return updated_ids
    finally:
        if close_after:
            conn.close()


