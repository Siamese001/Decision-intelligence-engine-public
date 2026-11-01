"""Property-based and stateful verification tests for top empirical hotspots.

Targets:
- tools/tier2_dedup_core/hygiene_promotion.py
- dual_pipeline/transcript_duration_helper.py
- dual_pipeline/document_pipeline_core/executor.py
- tools/shift_left_core/ast_framework.py
"""

from __future__ import annotations

import ast
import json
import sqlite3
from types import SimpleNamespace
from typing import Dict, List, Set

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dual_pipeline.document_pipeline_core.executor import (
    _build_document_user_content,
    _extract_manifest_file_categories,
    _quarantine_unsubstantiated_integrations,
    _quarantine_unsubstantiated_integrations_dict,
)
from dual_pipeline.transcript_duration_helper import (
    _extract_explicit_text_duration,
    _extract_meta_duration,
    _extract_text_timeline_duration,
    parse_time_duration_minutes,
    parse_transcript_timestamp_duration_minutes,
)
from tools.shift_left_core.ast_framework import SinglePassASTVisitor, _sanitized_git_env
from tools.tier2_dedup_core.hygiene_promotion import (
    _is_archivable_stale_item,
    remediate_auto_link_edges,
    remediate_orphan_nodes,
)


def _create_hygiene_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", timeout=5.0)
    conn.execute(
        "CREATE TABLE legacy_edge_assertion_map ("
        "edge_id TEXT PRIMARY KEY, assertion_id TEXT, "
        "migration_confidence REAL, migration_state TEXT, created_at TEXT)"
    )
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, active INTEGER, status TEXT, updated_at TEXT)")
    conn.execute(
        "CREATE TABLE node_state (node_id TEXT PRIMARY KEY, lifecycle_state TEXT, "
        "currentness_state TEXT, retrieval_eligibility TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE search_document (object_type TEXT, object_id TEXT, "
        "currentness_state TEXT, retrieval_eligible INTEGER, updated_at TEXT)"
    )
    return conn


@settings(max_examples=30, deadline=None)
@given(
    mins=st.integers(min_value=1, max_value=360),
    prefix=st.sampled_from(["Duration: ", "Duration: ~", "**Duration:** ~"]),
    suffix=st.sampled_from(["m", " min", " minutes"]),
)
def test_transcript_duration_explicit_roundtrip_property(mins: int, prefix: str, suffix: str) -> None:
    text = f"Meeting header\n{prefix}{mins}{suffix}\nBody content"
    extracted = _extract_explicit_text_duration(text)
    assert extracted == mins
    if "m" in suffix and not prefix.startswith("**"):
        parsed = parse_time_duration_minutes(f"01:00 PM - 02:00 PM ({prefix}{mins}m)")
        assert parsed == mins


@settings(max_examples=30, deadline=None)
@given(
    start_sec=st.integers(min_value=0, max_value=600),
    delta_min=st.integers(min_value=2, max_value=80),
)
def test_transcript_timestamp_span_monotonic_property(start_sec: int, delta_min: int) -> None:
    end_sec = start_sec + delta_min * 60
    s_m, s_s = divmod(start_sec, 60)
    e_m, e_s = divmod(end_sec, 60)
    mid_m, mid_s = divmod(start_sec + (delta_min * 30), 60)
    text = f"[{s_m:02d}:{s_s:02d}] Alice: Start\n[{mid_m:02d}:{mid_s:02d}] Bob: Mid\n[{e_m:02d}:{e_s:02d}] Alice: End"
    dur = parse_transcript_timestamp_duration_minutes(text)
    assert dur == delta_min


def test_transcript_duration_cross_midnight_and_meta_priority() -> None:
    assert parse_time_duration_minutes("11:45 PM - 12:15 AM ET") == 30
    assert _extract_text_timeline_duration("**Time:** 10:00 AM - 10:45 AM ET") == 45
    assert _extract_meta_duration({"duration_minutes": "42", "duration": 10}) == 42
    assert _extract_meta_duration({"duration_minutes": -5, "duration": "invalid"}) is None
    assert parse_transcript_timestamp_duration_minutes("[00:10] only one") is None


@settings(max_examples=25, deadline=None)
@given(
    valid_cues=st.lists(
        st.sampled_from(["integrates with SAP", "REST API endpoint", "Snowflake pipeline sync"]),
        min_size=0,
        max_size=4,
    ),
    invalid_cues=st.lists(
        st.sampled_from(["attended the weekly review", "discussed budget timeline", "approved charter"]),
        min_size=0,
        max_size=4,
    ),
)
def test_document_executor_integration_quarantine_property(
    valid_cues: List[str], invalid_cues: List[str]
) -> None:
    evidence_index: Dict[str, Dict[str, str]] = {}
    signals = []
    for idx, text in enumerate(valid_cues):
        eid = f"ev_valid_{idx}"
        evidence_index[eid] = {"exact_text": text}
        signals.append({"signal_id": f"sig_v_{idx}", "predicate": "INTEGRATES_WITH", "evidence_ids": [eid]})
    for idx, text in enumerate(invalid_cues):
        eid = f"ev_inv_{idx}"
        evidence_index[eid] = {"exact_text": text}
        signals.append({"signal_id": f"sig_i_{idx}", "predicate": "INTEGRATES_WITH", "evidence_ids": [eid]})
    signals.append({"signal_id": "sig_role", "predicate": "HAS_ROLE", "evidence_ids": []})

    proj = {"evidence_index": evidence_index, "graph_mutations": {"relationship_signals": signals}}
    out_json = _quarantine_unsubstantiated_integrations(json.dumps(proj))
    parsed = _quarantine_unsubstantiated_integrations_dict(json.loads(out_json))
    kept_ids = {s["signal_id"] for s in parsed["graph_mutations"]["relationship_signals"]}
    assert "sig_role" in kept_ids
    assert len(kept_ids) == len(valid_cues) + 1
    for idx in range(len(invalid_cues)):
        assert f"sig_i_{idx}" not in kept_ids


def test_document_executor_user_content_and_manifest_categories() -> None:
    meta = {
        "project": "project_nexus",
        "source_text": "MUST_BE_EXCLUDED",
        "is_visual_document": True,
        "document_ingestion_plan": {"target": "roster"},
    }
    content = _build_document_user_content("Body text", meta, remediation_directive="Fix roles")
    assert "MUST_BE_EXCLUDED" not in content
    assert "<visual_document_guidance>" in content
    assert "<remediation_directive>\nFix roles\n</remediation_directive>" in content

    manifest = {
        "sources": [
            {"path": "docs/summary_recap.md", "is_summary": True},
            {"filename": "raw_deck.pptx", "is_summary": False},
        ]
    }
    summaries, raws, ok = _extract_manifest_file_categories(manifest)
    assert ok is True
    assert summaries == {"summary_recap.md"}
    assert raws == {"raw_deck.pptx"}


@settings(max_examples=25, deadline=None)
@given(
    branch_count=st.integers(min_value=0, max_value=6),
    has_machine_path=st.booleans(),
)
def test_shift_left_ast_visitor_violations_and_complexity_property(
    branch_count: int, has_machine_path: bool
) -> None:
    lines = ["import sqlite3", "def sample(x):"]
    for i in range(branch_count):
        lines.append(f"    if x > {i}:")
        lines.append("        x += 1")
    if has_machine_path:
        root_tok = "/" + "Users"
        lines.append(f"    p = '{root_tok}/alice/secret/file.txt'")
    lines.append("    conn = sqlite3.connect(':memory:')")
    lines.append("    return x")
    src = "\n".join(lines)

    visitor = SinglePassASTVisitor("dual_pipeline/sample_mod.py", src)
    visitor.visit(ast.parse(src))
    assert len(visitor.functions) == 1
    assert visitor.functions[0].cognitive_complexity == branch_count
    categories = {v.category for v in visitor.ast_violations}
    assert "raw_sqlite_banned" in categories
    if has_machine_path:
        assert "machine_absolute_path" in categories

    env = _sanitized_git_env()
    assert "GIT_DIR" not in env and "GIT_INDEX_FILE" not in env


class HygienePromotionStateMachine(RuleBasedStateMachine):
    """Stateful model check for hygiene_promotion.py edge linking and orphan archival."""

    def __init__(self) -> None:
        super().__init__()
        self.conn = _create_hygiene_sqlite()
        self.linked_edges: Dict[str, str] = {}
        self.active_nodes: Set[str] = set()
        self.archived_nodes: Set[str] = set()

    @rule(
        edge_id=st.sampled_from(["edge_1", "edge_2", "edge_3", "edge_4"]),
        asrt_id=st.sampled_from(["asrt_a", "asrt_b", "asrt_c"]),
    )
    def link_edge(self, edge_id: str, asrt_id: str) -> None:
        pkg = {
            "findings": [
                {
                    "proposed_fix": "auto_link_edge",
                    "raw_data": {"edge_id": edge_id, "matching_assertion_id": asrt_id},
                }
            ]
        }
        sample_ids: List[str] = []
        cnt = remediate_auto_link_edges(self.conn, pkg, "2026-10-25T00:00:00Z", sample_ids)
        assert cnt == 1
        assert sample_ids == [edge_id]
        self.linked_edges[edge_id] = asrt_id

    @rule(node_id=st.sampled_from(["node_1", "node_2", "node_3", "node_4"]))
    def seed_and_archive_orphan(self, node_id: str) -> None:
        if node_id not in self.active_nodes and node_id not in self.archived_nodes:
            self.conn.execute("INSERT INTO node VALUES (?, 1, 'active', '2026-10-24')", (node_id,))
            self.conn.execute(
                "INSERT INTO node_state VALUES (?, 'active', 'current', 'eligible', '2026-10-24')",
                (node_id,),
            )
            self.conn.execute(
                "INSERT INTO search_document VALUES ('node', ?, 'current', 1, '2026-10-24')",
                (node_id,),
            )
        pkg = {"findings": [{"proposed_fix": "archive_node", "raw_data": {"node_id": node_id}}]}
        sample_ids: List[str] = []
        remediate_orphan_nodes(self.conn, pkg, "2026-10-25T00:00:00Z", sample_ids)
        self.active_nodes.discard(node_id)
        self.archived_nodes.add(node_id)

    @invariant()
    def check_db_consistency(self) -> None:
        rows = dict(
            self.conn.execute("SELECT edge_id, assertion_id FROM legacy_edge_assertion_map").fetchall()
        )
        assert rows == self.linked_edges
        for nid in self.archived_nodes:
            active_flag, status = self.conn.execute(
                "SELECT active, status FROM node WHERE node_id = ?", (nid,)
            ).fetchone()
            assert active_flag == 0 and status == "archived_orphan"
            elig = self.conn.execute(
                "SELECT retrieval_eligible FROM search_document WHERE object_id = ?", (nid,)
            ).fetchone()[0]
            assert elig == 0

    def teardown(self) -> None:
        self.conn.close()


TestHygienePromotionStateMachine = HygienePromotionStateMachine.TestCase


def test_is_archivable_stale_item_boundary() -> None:
    seen = {"asrt_seen"}
    item_ok = SimpleNamespace(
        finding_type="stale_candidate", details={"recommended_option": "ARCHIVE"}, target_id="asrt_new"
    )
    item_seen = SimpleNamespace(
        finding_type="stale_candidate", details={"recommended_option": "ARCHIVE"}, target_id="asrt_seen"
    )
    item_keep = SimpleNamespace(
        finding_type="stale_candidate", details={"recommended_option": "KEEP"}, target_id="asrt_keep"
    )
    assert _is_archivable_stale_item(item_ok, seen) == "asrt_new"
    assert _is_archivable_stale_item(item_seen, seen) is None
    assert _is_archivable_stale_item(item_keep, seen) is None
