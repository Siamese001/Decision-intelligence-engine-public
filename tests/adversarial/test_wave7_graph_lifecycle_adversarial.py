"""
tests/adversarial/test_wave7_graph_lifecycle_adversarial.py

Adversarial and boundary test suite for Wave 7: Graph Lifecycle & Maintenance.
Covers:
- Boundary conditions and invalid inputs in entity merge (identical IDs, missing nodes, cycles)
- Corrupted and malformed timestamps in TTL sweeps and time-travel versioning
- Extreme and negative queue age parameters in compaction
- Malformed strings, unescaped quotes, and unsupported formats in graph export
- Graph topology edge cases in orphan cleanup (nodes protected by assertions, all-orphan graphs)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from storage.database import (
    AssertionRecord,
    CanonicalGraphNode,
    DatabaseManager,
)


@pytest.fixture
def temp_db_manager() -> tuple[DatabaseManager, Path]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_w7_adversarial_"))
    mgr = DatabaseManager(
        base_dir=tmp_dir,
        allow_unencrypted_dev_fallback=True,
    )
    return mgr, tmp_dir


class TestWave7AdversarialMerge:
    """Adversarial scenarios for entity merge."""

    def test_merge_identical_nodes_raises_value_error(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_same", evidence_id="ev_same", type="SYSTEM", name="System Same"
        ))

        with pytest.raises(ValueError, match="surviving_id and absorbed_id must be distinct"):
            mgr.merge_nodes("node_same", "node_same")

    def test_merge_nonexistent_node_raises_key_error(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_exists", evidence_id="ev_ex", type="SYSTEM", name="Exists"
        ))

        with pytest.raises(KeyError, match="not found"):
            mgr.merge_nodes("node_exists", "node_ghost")

        with pytest.raises(KeyError, match="not found"):
            mgr.merge_nodes("node_ghost", "node_exists")

    def test_merge_with_malformed_and_special_character_properties(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_unicode_surv",
            evidence_id="ev_u1",
            type="SYSTEM",
            name="System Alpha ⚡️",
            properties={"key_special": "quotes: \"hello\" \n newlines \t and emojis 🚀"},
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_unicode_abs",
            evidence_id="ev_u2",
            type="SYSTEM",
            name="System Beta 🛡️",
            properties={"key_special": "should not overwrite", "nested": {"a": [1, 2, 3]}},
        ))

        res = mgr.merge_nodes("node_unicode_surv", "node_unicode_abs", reason="Special character test")
        assert res["status"] == "PASS"

        surv = mgr.get_canonical_node("node_unicode_surv")
        assert surv is not None
        assert "quotes: \"hello\"" in surv.properties["key_special"]
        assert surv.properties["nested"] == {"a": [1, 2, 3]}
        assert "System Beta 🛡️" in surv.properties["aliases"]


class TestWave7AdversarialCompaction:
    """Adversarial scenarios for database compaction."""

    def test_compaction_extreme_age_parameters(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        # Extreme negative age (purges all completed items)
        res_neg = mgr.compact_database(vacuum=True, max_queue_age_days=-100)
        assert res_neg["status"] == "PASS"
        assert res_neg["purged_queue_items"] == 0

        # Massive future age (purges nothing)
        res_pos = mgr.compact_database(vacuum=False, max_queue_age_days=1000000)
        assert res_pos["status"] == "PASS"
        assert res_pos["purged_queue_items"] == 0


class TestWave7AdversarialStaleSweep:
    """Adversarial scenarios for stale assertion retirement."""

    def test_sweep_with_malformed_stale_after_strings(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_malformed", evidence_id="ev_m", type="SYSTEM", name="Mal"
        ))

        # Insert assertions with malformed, random, and empty stale_after strings
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_corrupt_date",
            subject_node_id="node_malformed",
            predicate="STATUS",
            literal_value="Active",
            assertion_type="TECHNICAL",
            stale_after="not-a-valid-date-at-all",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_emoji_date",
            subject_node_id="node_malformed",
            predicate="STATUS",
            literal_value="Active",
            assertion_type="TECHNICAL",
            stale_after="📅 2026/99/99 🚀",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_valid_past",
            subject_node_id="node_malformed",
            predicate="STATUS",
            literal_value="Old",
            assertion_type="TECHNICAL",
            stale_after="2020-01-01",
        ))

        # Sweep with as_of_date
        res = mgr.retire_stale_assertions(as_of_date="2026-01-01")
        assert res["status"] == "PASS"
        # Only the truly valid past date should have been swept
        assert res["swept_count"] == 1
        assert res["swept_assertions"][0]["assertion_id"] == "assert_valid_past"

        # Corrupted assertions should remain CONFIRMED and not crash the system
        assertions = {a.assertion_id: a.status for a in mgr.query_assertions(subject_node_id="node_malformed")}
        assert assertions["assert_valid_past"] == "SUPERSEDED"
        assert assertions["assert_corrupt_date"] == "CONFIRMED"
        assert assertions["assert_emoji_date"] == "CONFIRMED"


class TestWave7AdversarialExport:
    """Adversarial scenarios for graph export."""

    def test_export_unsupported_format_raises_value_error(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        with pytest.raises(ValueError, match="Unsupported export format"):
            mgr.export_graph(format="xml")

        with pytest.raises(ValueError, match="Unsupported export format"):
            mgr.export_graph(format="yaml")

        with pytest.raises(ValueError, match="Unsupported export format"):
            mgr.export_graph(format="")

    def test_export_empty_graph(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        ttl = mgr.export_graph(format="turtle")
        assert "@prefix nee:" in ttl

        jsonld = mgr.export_graph(format="json-ld")
        doc = json.loads(jsonld)
        assert doc["@graph"] == []

        csv_out = mgr.export_graph(format="csv")
        assert "# SECTION: NODES" in csv_out

    def test_export_escaping_malicious_strings(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_injection_1",
            evidence_id="ev_inj",
            type="SYSTEM",
            name="System \"quoted\" with <angle_brackets> and \n newlines",
            strategic_value=0.5,
            confidence=0.5,
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_inj_1",
            subject_node_id="node_injection_1",
            predicate="CLAIM",
            literal_value="SELECT * FROM users WHERE '1'='1'; -- injection \"test\"",
            assertion_type="TECHNICAL",
        ))

        # Turtle should cleanly escape internal quotes
        ttl = mgr.export_graph(format="turtle")
        assert '\\"quoted\\"' in ttl

        # JSON-LD should be valid JSON
        jsonld = mgr.export_graph(format="json-ld")
        doc = json.loads(jsonld)
        assert len(doc["@graph"]) == 2

        # CSV should properly handle quotes
        csv_out = mgr.export_graph(format="csv")
        assert "injection_1" in csv_out


class TestWave7AdversarialTimeTravel:
    """Adversarial scenarios for time-travel versioning."""

    def test_time_travel_invalid_and_boundary_timestamps(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_tt_boundary",
            evidence_id="ev_tt",
            type="SYSTEM",
            name="Boundary Test",
        ))

        # Far future timestamp should return current node
        future_node = mgr.get_canonical_node("node_tt_boundary", as_of_timestamp="9999-12-31 23:59:59")
        assert future_node is not None
        assert future_node.name == "Boundary Test"

        # Far past timestamp before creation should return None
        past_node = mgr.get_canonical_node("node_tt_boundary", as_of_timestamp="1970-01-01 00:00:00")
        assert past_node is None


class TestWave7AdversarialOrphanCleanup:
    """Adversarial scenarios for orphan node cleanup."""

    def test_nodes_referenced_by_assertions_are_not_orphaned(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        # Node A has no edges, but is the subject of an assertion
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_subject_only", evidence_id="ev_s", type="POLICY", name="Security Policy"
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_p1", subject_node_id="node_subject_only", predicate="ENFORCES", literal_value="MFA", assertion_type="STRATEGIC"
        ))

        # Node B is an actual unreferenced orphan
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_pure_orphan", evidence_id="ev_o", type="DOCUMENT", name="Lonely File"
        ))

        orphans = mgr.cleanup_orphan_nodes(archive=True)
        assert "node_pure_orphan" in orphans
        # node_subject_only MUST NOT be treated as an orphan because it has assertion evidence!
        assert "node_subject_only" not in orphans
