"""Automatic physical proof for governed graph writes (fail closed).

Usage inside a writer, while holding the graph write lock:

    guard = MutationGuard(graph_path, label="graph-writer:<change_set_id>")
    ... commit and WAL-checkpoint the write ...
    result["physical_proof"] = guard.finalize(record_ids=[...], audit_events=n)

finalize() raises GraphMutationProofError if any of the five checks fails, so a
write without physical proof is never reported as COMPLETED.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AUDIT_TABLE = "audit_event"


class GraphMutationProofError(RuntimeError):
    """Raised when a committed graph write fails its physical proof."""

    def __init__(self, message: str, receipt: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.receipt = receipt


def proof_directory(graph_path: Path) -> Path:
    """reports/graph_mutation_proofs under the repo root for in-repo graphs; else beside the graph."""
    resolved = graph_path.resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
        return _REPO_ROOT / "reports" / "graph_mutation_proofs"
    except ValueError:
        return resolved.parent / "graph_mutation_proofs"


class MutationGuard:
    """Snapshot before a write; prove the write physically landed afterwards."""

    def __init__(self, graph_path: Path | str, label: str) -> None:
        from tools.graph_mutation_proof_core import snapshot

        self.graph_path = Path(graph_path)
        self.label = label
        self.before = snapshot(self.graph_path, tables=self._tables())

    def _tables(self) -> list[str]:
        from tools.graph_mutation_proof_core.proof import table_names

        return table_names(self.graph_path, None)

    @staticmethod
    def _probe_for_target(
        idx: int,
        op_name: str,
        tid: str,
        has_node: bool,
        has_state: bool,
        has_asrt: bool,
    ) -> tuple[str, tuple[str, tuple[Any, ...]]] | None:
        if has_node and op_name == "create_promoted_entity":
            sql = (
                "SELECT 1 FROM node n JOIN node_state ns ON ns.node_id=n.node_id "
                "WHERE n.node_id=? AND n.status='canonical' AND ns.approval_state='approved'"
                if has_state
                else "SELECT 1 FROM node WHERE node_id=? AND status='canonical'"
            )
            return f"node_{idx}", (sql, (tid,))
        if has_node and op_name in {"create_entity", "create_external_concept", "link_existing_entity"}:
            return f"node_{idx}", ("SELECT 1 FROM node WHERE node_id=?", (tid,))
        if has_asrt and op_name in {
            "add_approved_assertion",
            "add_approved_relationship",
            "add_approved_action_item",
            "promote_assertion",
        }:
            return f"assertion_{idx}", (
                "SELECT 1 FROM assertion WHERE assertion_id=? AND canonical_status='canonical' AND canonical_eligible=1",
                (tid,),
            )
        if has_asrt and op_name in {
            "add_candidate_assertion",
            "add_relationship",
            "add_action_item",
            "supersede_assertion",
            "add_evidence",
        }:
            return f"assertion_{idx}", ("SELECT 1 FROM assertion WHERE assertion_id=?", (tid,))
        return None

    @classmethod
    def _add_operation_probes(
        cls,
        probes: Dict[str, tuple[str, tuple[Any, ...]]],
        operation_targets: Iterable[tuple[str, str]],
        row_counts: Dict[str, Any],
    ) -> None:
        has_node = "node" in row_counts
        has_state = "node_state" in row_counts
        has_asrt = "assertion" in row_counts
        for idx, (op_name, tid) in enumerate(operation_targets):
            if not tid:
                continue
            item = cls._probe_for_target(idx, op_name, tid, has_node, has_state, has_asrt)
            if item is not None:
                probes[item[0]] = item[1]

    def finalize(
        self,
        record_ids: Iterable[str],
        audit_events: int,
        operation_targets: Optional[Iterable[tuple[str, str]]] = None,
        expected_table_deltas: Optional[Dict[str, int]] = None,
        strict_delta: bool = False,
    ) -> Dict[str, Any]:
        from tools.graph_mutation_proof_core import prove, snapshot

        ids = sorted({i for i in record_ids if i})
        after = snapshot(self.graph_path, tables=list(self.before["row_counts"]))
        probes: Dict[str, tuple[str, tuple[Any, ...]]] = {
            f"audit_{n}": (f"SELECT 1 FROM {_AUDIT_TABLE} WHERE record_id=?", (rid,))
            for n, rid in enumerate(ids)
        }
        if operation_targets:
            self._add_operation_probes(probes, operation_targets, after["row_counts"])
        deltas = {_AUDIT_TABLE: audit_events}
        if expected_table_deltas:
            deltas.update(expected_table_deltas)
        receipt = prove(
            self.before,
            after,
            ids,
            deltas,
            probes,
            strict_delta=strict_delta,
        )
        receipt["label"] = self.label
        receipt["record_ids"] = ids
        path = self._write_receipt(receipt, after["sha256"])
        summary = {
            "overall": receipt["overall"],
            "receipt_path": str(path),
            "checks": {c["check"]: c["status"] for c in receipt["checks"]},
            "canonical_deltas": receipt.get("canonical_deltas", {}),
        }
        if receipt["overall"] != "PASS":
            failed = [c["check"] for c in receipt["checks"] if c["status"] != "PASS"]
            raise GraphMutationProofError(
                f"PHYSICAL_PROOF_FAILED for {self.label}: failed checks {failed}; receipt {path}",
                receipt,
            )
        return summary

    def _write_receipt(self, receipt: Dict[str, Any], after_sha: str) -> Path:
        directory = proof_directory(self.graph_path)
        directory.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.label)[:80]
        path = directory / f"{safe}_{after_sha[:12]}.json"
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        return path


NOT_APPLICABLE_NO_OPERATIONS: Dict[str, Any] = {
    "overall": "NOT_APPLICABLE",
    "reason": "change set contained no operations; no graph mutation was claimed",
}
