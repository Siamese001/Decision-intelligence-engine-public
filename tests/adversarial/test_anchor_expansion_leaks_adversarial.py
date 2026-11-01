"""Adversarial leak prevention tests for anchor expansion traversal (Wave 3)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.index_core.anchor_expansion import expand_retrieval_anchors


def _init_leak_test_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            node_type TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            speaker_or_author TEXT,
            valid_from TEXT,
            classification TEXT NOT NULL DEFAULT 'internal',
            allowed_audiences_json TEXT NOT NULL DEFAULT '["internal_consulting"]',
            canonical_status TEXT NOT NULL DEFAULT 'canonical',
            validation_state TEXT NOT NULL DEFAULT 'approved',
            currentness_state TEXT NOT NULL DEFAULT 'current',
            retrieval_eligible INTEGER NOT NULL DEFAULT 1,
            primary_source_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT NOT NULL,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        """
    )
    conn.execute("INSERT INTO node VALUES ('node_src', 'Source Entity', 'system', '2026-07-09')")
    conn.execute("INSERT INTO node VALUES ('node_tgt', 'Target Entity', 'system', '2026-07-09')")

    # 1. Valid approved assertion (baseline control)
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_valid', 'node_src', 'CONNECTS_TO', 'node_tgt', NULL,
            'Valid connection', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'current', 1, 'src_1'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_valid', 'ev_valid', 'supports')")

    # 2. Canonical assertion with needs_review (F4 leak cohort: 341 edges in live graph)
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_needs_review', 'node_src', 'CONNECTS_TO', 'node_tgt', NULL,
            'Unreviewed canonical fact', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'needs_review', 'current', 1, 'src_2'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_needs_review', 'ev_nr', 'supports')")

    # 3. Canonical assertion with retrieval_eligible=0 (F4 leak cohort: 38 edges in live graph)
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_ineligible_flag', 'node_src', 'CONNECTS_TO', 'node_tgt', NULL,
            'Ineligible flag fact', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'current', 0, 'src_3'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_ineligible_flag', 'ev_ineligible', 'supports')")

    # 4. Historical assertion
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_historical', 'node_src', 'CONNECTS_TO', 'node_tgt', NULL,
            'Deprecated legacy fact', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'historical', 1, 'src_4'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_historical', 'ev_hist', 'supports')")

    # 5. Assertion lacking supporting evidence
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_no_evidence', 'node_src', 'CONNECTS_TO', 'node_tgt', NULL,
            'Uncorroborated assertion', 'Operator', '2026-07-09', 'internal', '["internal_consulting"]',
            'canonical', 'approved', 'current', 1, 'src_5'
        )"""
    )
    # (no assertion_evidence row inserted)

    # 6. Multi-level classifications
    classifications = [
        ("asrt_pub", "public", '["external_public"]'),
        ("asrt_ext", "external_approved", '["external_public"]'),
        ("asrt_int", "internal", '["internal_consulting"]'),
        ("asrt_conf", "confidential", '["leadership_only"]'),
        ("asrt_rest", "restricted", '["security_council"]'),
    ]
    for aid, cl, auds in classifications:
        conn.execute(
            f"""INSERT INTO assertion VALUES (
                '{aid}', 'node_src', 'CLASSIFIED_LINK', 'node_tgt', NULL,
                'Classification level {cl}', 'Operator', '2026-07-09', '{cl}', '{auds}',
                'canonical', 'approved', 'current', 1, 'src_cl'
            )"""
        )
        conn.execute(f"INSERT INTO assertion_evidence VALUES ('{aid}', 'ev_{cl}', 'supports')")

    conn.commit()
    conn.close()


class TestAnchorExpansionLeaksAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "leaks_graph.sqlite"
        _init_leak_test_graph(self.graph_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _get_anchor(self) -> dict:
        return {
            "record_id": "profile_source",
            "lane": "approved",
            "fused_score": 0.90,
            "record": {
                "record_type": "entity_facet",
                "retrieval_kind": "entity_facet",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_src"]},
            },
        }

    def test_leak_prevention_unapproved_canonical(self) -> None:
        """Canonical assertions with needs_review must NEVER leak into candidate or approved lane."""
        anchor = self._get_anchor()
        # 1. Check approved lane
        exp_approved = expand_retrieval_anchors(
            [anchor], self.graph_path, lane="approved", classification="internal",
            audiences=["internal_consulting"],
        )
        aids_app = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in exp_approved if it.get("is_anchor_expanded")
        ]
        self.assertIn("asrt_valid", aids_app)
        self.assertNotIn("asrt_needs_review", aids_app, "LEAK: needs_review leaked into approved lane!")

        # 2. Check candidate lane: unapproved canonical must NOT leak into candidate lane!
        anchor_cand = dict(anchor)
        anchor_cand["lane"] = "candidate"
        exp_candidate = expand_retrieval_anchors(
            [anchor_cand], self.graph_path, lane="candidate", classification="internal",
            audiences=["internal_consulting"],
        )
        aids_cand = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in exp_candidate if it.get("is_anchor_expanded")
        ]
        self.assertNotIn("asrt_needs_review", aids_cand, "LEAK: needs_review canonical leaked into candidate lane!")

    def test_leak_prevention_retrieval_ineligible_flag(self) -> None:
        """Assertions with retrieval_eligible=0 must be filtered out by classify_retrieval_eligibility."""
        anchor = self._get_anchor()
        expanded = expand_retrieval_anchors(
            [anchor], self.graph_path, lane="approved", classification="internal",
            audiences=["internal_consulting"],
        )
        aids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in expanded if it.get("is_anchor_expanded")
        ]
        self.assertNotIn("asrt_ineligible_flag", aids, "LEAK: retrieval_eligible=0 leaked into traversal!")

    def test_leak_prevention_historical_assertions(self) -> None:
        """Historical assertions must be filtered out by classify_retrieval_eligibility."""
        anchor = self._get_anchor()
        expanded = expand_retrieval_anchors(
            [anchor], self.graph_path, lane="approved", classification="internal",
            audiences=["internal_consulting"],
        )
        aids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in expanded if it.get("is_anchor_expanded")
        ]
        self.assertNotIn("asrt_historical", aids, "LEAK: historical assertion leaked into traversal!")

    def test_leak_prevention_no_supporting_evidence(self) -> None:
        """Assertions lacking supporting evidence rows must be rejected."""
        anchor = self._get_anchor()
        expanded = expand_retrieval_anchors(
            [anchor], self.graph_path, lane="approved", classification="internal",
            audiences=["internal_consulting"],
        )
        aids = [
            it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
            for it in expanded if it.get("is_anchor_expanded")
        ]
        self.assertNotIn("asrt_no_evidence", aids, "LEAK: assertion without supporting evidence leaked!")

    def test_all_five_classification_levels_cross_clearance(self) -> None:
        """Zero crashes across all 5 classifications and strict cross-clearance boundary enforcement."""
        classifications = ["public", "external_approved", "internal", "confidential", "restricted"]
        for cl in classifications:
            anchor = {
                "record_id": f"profile_{cl}",
                "lane": "approved",
                "fused_score": 0.90,
                "record": {
                    "record_type": "entity_facet",
                    "retrieval_kind": "entity_facet",
                    "classification": cl,
                    "audiences": ["external_public", "internal_consulting", "leadership_only", "security_council"],
                    "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_src"]},
                },
            }
            # Must never raise an unhandled exception or KeyError
            expanded = expand_retrieval_anchors(
                [anchor], self.graph_path, lane="approved", classification=cl,
                audiences=["external_public", "internal_consulting", "leadership_only", "security_council"],
            )
            aids = [
                it["record"]["extensions"]["com.partner_advisory.nee.graph-assertion-ids"][0]
                for it in expanded if it.get("is_anchor_expanded")
            ]

            # Clearance assertions
            if cl in {"public", "external_approved"}:
                self.assertIn("asrt_pub", aids)
                self.assertIn("asrt_ext", aids)
                self.assertNotIn("asrt_int", aids, f"CLEARANCE VIOLATION: internal leaked into {cl}")
                self.assertNotIn("asrt_conf", aids, f"CLEARANCE VIOLATION: confidential leaked into {cl}")
                self.assertNotIn("asrt_rest", aids, f"CLEARANCE VIOLATION: restricted leaked into {cl}")
            elif cl == "internal":
                self.assertIn("asrt_pub", aids)
                self.assertIn("asrt_ext", aids)
                self.assertIn("asrt_int", aids)
                self.assertNotIn("asrt_conf", aids, "CLEARANCE VIOLATION: confidential leaked into internal")
                self.assertNotIn("asrt_rest", aids, "CLEARANCE VIOLATION: restricted leaked into internal")
            elif cl == "confidential":
                self.assertIn("asrt_conf", aids)
                self.assertNotIn("asrt_rest", aids, "CLEARANCE VIOLATION: restricted leaked into confidential")
            elif cl == "restricted":
                self.assertIn("asrt_rest", aids)


if __name__ == "__main__":
    unittest.main()
