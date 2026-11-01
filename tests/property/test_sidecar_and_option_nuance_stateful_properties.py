"""Hypothesis Metamorphic & RuleBasedStateMachine Property Suite for Sidecar Recovery, Hygiene Promotion, Option Nuance & Watchdogs."""
from __future__ import annotations

import math
import sqlite3
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dual_pipeline.hitl_governance_core.bayesian import canonicalize_pattern_key
from dual_pipeline.learning_engine_core.option_nuance import (
    _build_synthesized_entry,
    _collect_option_stats,
    _format_healing_option_entry,
)
from tools.scheduled_task_core.adapters_core.watchdogs import (
    run_testing_gaps_assurance,
    run_timeout_lock_watchdog,
)
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.tier2_dedup_core.hygiene_promotion import (
    remediate_auto_link_edges,
    remediate_orphan_nodes,
)
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _deterministic_unit_vector,
    _ensure_missing_dense_vectors,
    _tokenize_retrieval_text,
)


@settings(max_examples=25, deadline=None)
@given(
    seed=st.text(min_size=1, max_size=80),
    dim=st.sampled_from([16, 32, 64, 128]),
)
def test_property_deterministic_unit_vector_norm_and_idempotence(seed: str, dim: int) -> None:
    """Property: _deterministic_unit_vector always produces L2 unit norm 1.0 and deterministic bytes."""
    blob1 = _deterministic_unit_vector(seed, dim=dim)
    blob2 = _deterministic_unit_vector(seed, dim=dim)
    assert blob1 == blob2
    assert len(blob1) == dim * 4
    vals = struct.unpack(f"<{dim}f", blob1)
    norm = math.sqrt(sum(v * v for v in vals))
    assert abs(norm - 1.0) < 1e-4


@settings(max_examples=25, deadline=None)
@given(
    words=st.lists(
        st.from_regex(r"[a-z0-9]{2,20}", fullmatch=True),
        min_size=1,
        max_size=30,
    )
)
def test_metamorphic_tokenizer_permutation_invariance(words: list[str]) -> None:
    """Metamorphic Property: Reordering input words yields identical token frequency Counter."""
    forward_text = " ".join(words)
    reversed_text = " ".join(reversed(words))
    c_fwd = _tokenize_retrieval_text(forward_text)
    c_rev = _tokenize_retrieval_text(reversed_text)
    assert c_fwd == c_rev
    assert all(2 <= len(tok) <= 40 for tok in c_fwd)


@settings(max_examples=25, deadline=None)
@given(
    decisions=st.lists(
        st.tuples(
            st.sampled_from(["human_op", "operator_1", "autonomous_engine", "auto"]),
            st.sampled_from(["Approve Wave", "Defer Wave", "Reject Plan"]),
            st.sampled_from(["AFFIRMED", "OVERRIDDEN"]),
        ),
        min_size=1,
        max_size=20,
    )
)
def test_metamorphic_option_nuance_history_permutation_invariance(
    decisions: list[tuple[str, str, str]],
) -> None:
    """Metamorphic Property: Permuting decision history order preserves affirmed/rejected counts and posteriors."""
    all_opts = ["Approve Wave", "Defer Wave", "Reject Plan"]
    history_fwd = [
        {
            "operator_id": op,
            "category": "governance",
            "selected_option": sel,
            "options": all_opts,
            "outcome_type": out,
        }
        for op, sel, out in decisions
    ]
    history_rev = list(reversed(history_fwd))

    stats_fwd = _collect_option_stats(history_fwd)
    stats_rev = _collect_option_stats(history_rev)
    assert set(stats_fwd.keys()) == set(stats_rev.keys())

    for k in stats_fwd:
        e_fwd = _build_synthesized_entry(k, stats_fwd[k])
        e_rev = _build_synthesized_entry(k, stats_rev[k])
        assert e_fwd is not None and e_rev is not None
        assert e_fwd["affirmed_count"] == e_rev["affirmed_count"]
        assert e_fwd["rejected_count"] == e_rev["rejected_count"]
        assert abs(e_fwd["posterior_confidence"] - e_rev["posterior_confidence"]) < 1e-9


@settings(max_examples=20, deadline=None)
@given(
    healed=st.integers(min_value=0, max_value=50),
    failed=st.integers(min_value=0, max_value=50),
    post_mean=st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False),
)
def test_property_healing_option_entry_tier_calibration(healed: int, failed: int, post_mean: float) -> None:
    """Property: _format_healing_option_entry assigns monotonic tier and exact sample_count."""
    total = healed + failed
    entry = _format_healing_option_entry(
        "repair_date",
        {
            "total_count": total,
            "success_count": healed,
            "failure_count": failed,
            "posterior_confidence": post_mean,
        },
    )
    if total == 0:
        assert entry is None
    else:
        assert entry is not None
        assert entry["sample_count"] == total
        expected_tier = "HIGH" if post_mean >= 0.85 else ("MEDIUM" if post_mean >= 0.50 else "LOW")
        assert entry["posterior_tier"] == expected_tier


class SidecarHygieneAndOptionStateMachine(RuleBasedStateMachine):
    """Stateful model checker for sidecar vector backfill, hygiene promotion, watchdogs, and option nuance."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = tempfile.TemporaryDirectory(prefix="eval_sandbox_stateful_")
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "stateful.sqlite"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, active INTEGER, status TEXT, updated_at TEXT);
            CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, retrieval_text_hash TEXT, artifact_json TEXT);
            CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB);
            CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token));
            CREATE TABLE legacy_edge_assertion_map (
                edge_id TEXT PRIMARY KEY, assertion_id TEXT, migration_confidence REAL,
                migration_state TEXT, created_at TEXT
            );
            """
        )
        self.expected_records: set[str] = set()
        self.expected_archived_nodes: set[str] = set()
        self.expected_linked_edges: set[str] = set()
        self.human_affirmed: int = 0
        self.history: list[dict] = []

    def teardown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    @rule(rid=st.from_regex(r"rec_[a-z0-9]{3,8}", fullmatch=True))
    def add_retrieval_record_and_backfill(self, rid: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO retrieval_record VALUES (?, ?, ?)",
            (rid, f"hash_{rid}", '{"retrieval_text": "stateful verification node"}'),
        )
        self.expected_records.add(rid)
        with patch(
            "tools.tier2_dedup_core.sidecar_vector_recovery.resolve_authoritative_runtime_root",
            autospec=True,
            side_effect=RuntimeError("sandbox"),
        ):
            _ensure_missing_dense_vectors(self.conn, [rid])

    @rule(nid=st.from_regex(r"node_[a-z0-9]{3,8}", fullmatch=True))
    def archive_orphan_node(self, nid: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO node VALUES (?, 1, 'canonical', '2026-01-01')",
            (nid,),
        )
        pkg = {"findings": [{"proposed_fix": "archive_node", "raw_data": {"node_id": nid}}]}
        remediate_orphan_nodes(self.conn, pkg, "2026-10-27T00:00:00Z", [])
        self.expected_archived_nodes.add(nid)

    @rule(eid=st.from_regex(r"edge_[a-z0-9]{3,8}", fullmatch=True))
    def link_legacy_edge(self, eid: str) -> None:
        pkg = {
            "findings": [
                {
                    "proposed_fix": "auto_link_edge",
                    "raw_data": {"edge_id": eid, "matching_assertion_id": f"asrt_{eid}"},
                }
            ]
        }
        remediate_auto_link_edges(self.conn, pkg, "2026-10-27T00:00:00Z", [])
        self.expected_linked_edges.add(eid)

    @rule(is_human=st.booleans())
    def record_hitl_feedback(self, is_human: bool) -> None:
        self.history.append(
            {
                "operator_id": "operator_human" if is_human else "autonomous_engine",
                "category": "governance",
                "selected_option": "Approve Wave 1",
                "options": ["Approve Wave 1", "Defer Wave 1"],
                "outcome_type": "AFFIRMED",
            }
        )
        if is_human:
            self.human_affirmed += 1

    @invariant()
    def check_state_invariants(self) -> None:
        dv_ids = {r[0] for r in self.conn.execute("SELECT record_id FROM dense_vector").fetchall()}
        assert self.expected_records.issubset(dv_ids)

        for nid in self.expected_archived_nodes:
            row = self.conn.execute("SELECT active, status FROM node WHERE node_id = ?", (nid,)).fetchone()
            assert row == (0, "archived_orphan")

        linked_ids = {r[0] for r in self.conn.execute("SELECT edge_id FROM legacy_edge_assertion_map").fetchall()}
        assert self.expected_linked_edges == linked_ids

        stats = _collect_option_stats(self.history)
        canon_approve = canonicalize_pattern_key("Approve Wave 1")
        if self.human_affirmed == 0:
            assert canon_approve not in stats
        else:
            assert stats[canon_approve]["affirmed_count"] == self.human_affirmed

        ctx = AuthorityContext(
            task_id="timeout_lock_watchdog",
            run_id="r_state",
            trigger_origin=TriggerOrigin.SCHEDULER_CRON,
            execution_mode=ExecutionMode.SCHEDULED,
            actor="cron",
            is_scheduled=True,
        )
        res_w = run_timeout_lock_watchdog(ctx, self.root / "clean", self.root / "run_w")
        res_t = run_testing_gaps_assurance(ctx, self.root / "clean", self.root / "run_t")
        assert res_w.terminal_outcome == "COMPLETE"
        assert res_t.terminal_outcome == "COMPLETE"


TestSidecarAndOptionStateful = SidecarHygieneAndOptionStateMachine.TestCase
TestSidecarAndOptionStateful.settings = settings(max_examples=15, stateful_step_count=10, deadline=None)
