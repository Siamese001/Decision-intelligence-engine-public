#!/usr/bin/env python3
"""Wave 2 unit & adversarial tests for Graph Scout speaker/qualifier scanners and multi-token person matching."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import connect_sqlite, sha256_file
from dual_pipeline.graph_scout_core.matching import _string_similarity
from dual_pipeline.graph_scout_core.scout import GraphScout


def _seed_wave2_sandbox_db(db_path: Path) -> None:
    conn = connect_sqlite(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                description TEXT,
                status TEXT DEFAULT 'canonical',
                active INTEGER DEFAULT 1
            );
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_type TEXT,
                source_name TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                speaker_or_author TEXT,
                primary_source_id TEXT,
                canonical_status TEXT DEFAULT 'canonical',
                canonical_eligible INTEGER DEFAULT 1,
                retrieval_eligible INTEGER DEFAULT 1,
                currentness_state TEXT DEFAULT 'current',
                validation_state TEXT DEFAULT 'accepted_legacy',
                is_quarantined INTEGER DEFAULT 0,
                qualifiers_json TEXT DEFAULT '{}',
                superseded_by_assertion_id TEXT
            );
            """
        )
        nodes = [
            ("person_eduardo_more", "stakeholder", "Eduardo (Ed) Juan More", "Eduardo (Ed) Juan More", "Role: Atlas Lead / Rule Specialist."),
            ("entity-08c09f0f6f80a785d75d5f4abc19", "stakeholder", "Ed Moore", "Ed Moore", "Engineering Director at Apex Global Enterprises (NEA)."),
            ("entity-amy-moore", "stakeholder", "Amy Moore", "Amy Moore", "Product Manager for Billing Journey."),
            ("entity-jimmy-moreno", "stakeholder", "Jimmy Guzman Moreno", "Jimmy Guzman Moreno", "Staff specialist."),
            ("workstream_project_nexus", "workstream", "Project Nexus", "Project Nexus", "Rules modernization workstream."),
            ("workstream_insights_hub", "workstream", "APEX_TRANS Insights Hub", "APEX_TRANS Insights Hub", "APEX_TRANS insights workstream."),
            ("cnode_synthetic_leak", "workstream", "Synthetic Node", "Synthetic Node", "Synthetic fixture node."),
        ]
        conn.executemany("INSERT INTO node (node_id, node_type, canonical_name, display_name, description) VALUES (?,?,?,?,?)", nodes)
        conn.executemany(
            "INSERT INTO source VALUES (?,?,?)",
            [
                ("src-nexus-mtg", "dual_pipeline_intake", "Project Nexus Rules Alignment"),
                ("src-apex_trans-mtg", "dual_pipeline_intake", "APEX_TRANS Insights Hub Weekly Sync"),
            ],
        )

        stale_cand_qual = json.dumps(
            {
                "dual_pipeline_candidate_id": "cand-sync-01",
                "dual_pipeline_candidate_type": "assertion",
                "dual_pipeline_disposition": "CANDIDATE",
                "retrieval_kind": "assertion",
                "answer_use": "candidate_hypothesis",
                "fact_promotion_eligible": False,
            }
        )
        citation_qual = json.dumps(
            {
                "dual_pipeline_candidate_id": "cand-chunk-01",
                "dual_pipeline_candidate_type": "source_chunk",
                "retrieval_kind": "evidence_span",
                "answer_use": "citation_only",
                "fact_promotion_eligible": False,
            }
        )
        assertions = [
            # Defective speaker/purity rows -> quarantine_undiarized_or_misattributed_claim
            ("asrt-placeholder", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Header roster line.", "Header roster line.", "Participants", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-hist-roster", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Historical roster line.", "Historical roster line.", "Meeting transcript participant roster", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-hist-hdr", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Historical underscore header.", "Historical underscore header.", "TRANSCRIPT_PARTICIPANT_HEADER", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-slug-spk", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Machine slug speaker.", "Machine slug speaker.", "person:amit_ayer", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-missing-spk", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Yeah I think you know we're missing the rule.", "Yeah I think you know we're missing the rule.", None, "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-inline-mismatch", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "**Alex Sanchez:** We already built the validator.", "**Alex Sanchez:** We already built the validator.", "Eduardo (Ed) Juan More", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-empty-colon", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, ": Right, agreed.", ": Right, agreed.", "Alex Sanchez", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-nexus-persona", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Alex Rivera approved Nexus rules.", "Alex Rivera approved Nexus rules.", "Alex Rivera", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            # Qualifier leak -> demote_leaked_candidate_assertion
            ("asrt-citation-leak", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Raw chunk excerpt.", "Raw chunk excerpt.", "Alex Sanchez", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, citation_qual),
            ("asrt-needs-review-leak", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Unapproved canonical row.", "Unapproved canonical row.", "Alex Sanchez", "src-nexus-mtg", "canonical", 1, 1, "current", "needs_review", 0, "{}"),
            # Stale candidate qualifiers on clean canonical row -> sync_promoted_qualifier_metadata
            ("asrt-stale-quals", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "Alex Sanchez confirmed rules engine latency is 40ms.", "Alex Sanchez confirmed rules engine latency is 40ms.", "Alex Sanchez", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, stale_cand_qual),
            # Valid clean rows -> must NOT be flagged
            ("asrt-clean-apex_trans", "workstream_insights_hub", "HAS_CLAIM_TEXT", None, "Alex Rivera confirmed APEX_TRANS UAT readiness.", "Alex Rivera confirmed APEX_TRANS UAT readiness.", "Alex Rivera", "src-apex_trans-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-clean-card", "person_eduardo_more", "HAS_CLAIM_TEXT", None, "Eduardo (Ed) Juan More: Role: Atlas Lead / Rule Specialist.", "Eduardo (Ed) Juan More: Role: Atlas Lead / Rule Specialist.", None, "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
            ("asrt-clean-inline-match", "workstream_project_nexus", "HAS_CLAIM_TEXT", None, "**Ed More:** Deterministic checks run first.", "**Ed More:** Deterministic checks run first.", "Eduardo (Ed) Juan More", "src-nexus-mtg", "canonical", 1, 1, "current", "accepted_legacy", 0, "{}"),
        ]
        conn.executemany(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, object_literal, scope_text,
                speaker_or_author, primary_source_id, canonical_status, canonical_eligible, retrieval_eligible,
                currentness_state, validation_state, is_quarantined, qualifiers_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            assertions,
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


class Wave2ScoutSpeakerQualifierAndMatchingTests(unittest.TestCase):
    def test_multi_token_parenthetical_and_phonetic_person_matching_with_negative_controls(self) -> None:
        """Categories 1 & 2: Multi-token/nickname/typo pairs score >=0.90 while distinct surnames/initials reject."""
        self.assertGreaterEqual(_string_similarity("Eduardo (Ed) Juan More", "Ed Moore"), 0.90)
        self.assertGreaterEqual(_string_similarity("Ed More", "Ed Moore"), 0.90)
        self.assertGreaterEqual(_string_similarity("Matthew (Matt) R. Miller", "Matt Miller"), 0.90)

        self.assertLess(_string_similarity("Ed Smith", "Eduardo More"), 0.50)
        self.assertLess(_string_similarity("Amy Moore", "Eduardo (Ed) Juan More"), 0.50)
        self.assertLess(_string_similarity("Jimmy Guzman Moreno", "Ed Moore"), 0.50)
        self.assertEqual(0.0, _string_similarity("John A. Smith", "John B. Smith"))
        self.assertEqual(0.0, _string_similarity("Eduardo Carlos More", "Eduardo Juan More"))
        self.assertEqual(0.0, _string_similarity("Project Atlas", "Project Nexus"))
        self.assertEqual(0.0, _string_similarity("John Hall", "John Hill"))
        self.assertLess(_string_similarity("LENS Rule Authoring Scope", "LENS Rule Asset Scope"), 0.85)
        self.assertLess(_string_similarity("HR PII Protection Requirement", "HR PII Handling Requirement"), 0.85)

    def test_scout_detects_ed_moore_duplicate_and_all_speaker_qualifier_defects_read_only(self) -> None:
        """Categories 3, 4 & 5: Full GraphScout scan detects all seeded defects with zero DB mutation."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            db_path = Path(tmp) / "w2_scout.sqlite"
            _seed_wave2_sandbox_db(db_path)
            before_sha = sha256_file(db_path)

            scout = GraphScout(db_path)
            with scout._connect_ro() as conn:
                dups = scout.scan_duplicate_entities(conn)
                antipatterns = scout.scan_retrieval_antipatterns(conn)

            self.assertEqual(before_sha, sha256_file(db_path))

            dup_pairs = {frozenset({d.node_id_a, d.node_id_b}): d for d in dups}
            ed_key = frozenset({"person_eduardo_more", "entity-08c09f0f6f80a785d75d5f4abc19"})
            self.assertIn(ed_key, dup_pairs)
            self.assertGreaterEqual(dup_pairs[ed_key].similarity_score, 0.90)
            for non_dup in ("entity-amy-moore", "entity-jimmy-moreno"):
                self.assertNotIn(frozenset({"person_eduardo_more", non_dup}), dup_pairs)
                self.assertNotIn(frozenset({"entity-08c09f0f6f80a785d75d5f4abc19", non_dup}), dup_pairs)

            by_target = {(f.target_id, f.action_recommended) for f in antipatterns}
            for spk_bad in (
                "asrt-placeholder",
                "asrt-hist-roster",
                "asrt-hist-hdr",
                "asrt-slug-spk",
                "asrt-missing-spk",
                "asrt-inline-mismatch",
                "asrt-empty-colon",
                "asrt-nexus-persona",
            ):
                self.assertIn((spk_bad, "quarantine_undiarized_or_misattributed_claim"), by_target)

            self.assertIn(("cnode_synthetic_leak", "archive_clutter"), by_target)
            self.assertIn(("asrt-citation-leak", "demote_leaked_candidate_assertion"), by_target)
            self.assertIn(("asrt-needs-review-leak", "demote_leaked_candidate_assertion"), by_target)
            self.assertIn(("asrt-stale-quals", "sync_promoted_qualifier_metadata"), by_target)

            flagged_ids = {f.target_id for f in antipatterns}
            for clean_id in ("asrt-clean-apex_trans", "asrt-clean-card", "asrt-clean-inline-match"):
                self.assertNotIn(clean_id, flagged_ids)


if __name__ == "__main__":
    unittest.main()
