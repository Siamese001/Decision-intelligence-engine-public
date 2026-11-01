#!/usr/bin/env python3
"""Wave 1 & Wave 2 unit and end-to-end verification tests for speaker attribution, qualifier hygiene, and candidate promotion."""
from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import ROOT, ZERO_HASH, connect_sqlite, digest, sha256_file, stable_id
from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import audit_safe_promotable_candidates
from dual_pipeline.graph_scout_core.speaker_and_qualifier_scans import scan_speaker_and_diarization_defects
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.graph_writer_core.assertions import _resolve_speaker_or_author
from dual_pipeline.l0.speaker_and_qualifier_policy import (
    clean_non_placeholder_speaker,
    resolve_deterministic_speaker_or_author,
    sync_canonical_qualifiers_json,
)
from dual_pipeline.normalization_core.meeting import normalize_evidence_envelope
from tools.apply_graph_hygiene_remediation import _remediate_candidate_promotions
from tools.tier2_dedup_core.sidecar_vector_recovery import _copy_real_vectors_from_ancestor
from tools.tier2_dedup_core.speaker_and_qualifier_remediation import remediate_speaker_and_qualifier_fixes


def _build_writer_change_set(
    *,
    graph_path: Path,
    subject_id: str,
    evidence_id: str,
    text: str,
    speaker: str | None,
    mentions: list[dict[str, object]] | None = None,
    disposition: str = "APPROVED",
    target_id: str = "asrt-w1-speaker-001",
) -> dict[str, object]:
    src_id = "source-w1-partner_advisory-gcp"
    sv_id = "sv-w1-partner_advisory-gcp-001"
    ev_item = {
        "evidence_id": evidence_id,
        "source_id": src_id,
        "source_version_id": sv_id,
        "locator": {"kind": "section", "value": "## Transcript"},
        "quote": text,
        "quote_sha256": digest(text),
        "directness": "direct",
        "authority_rank": 5,
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "extraction_confidence": 0.88,
    }
    src = {
        "source_id": src_id,
        "source_version_id": sv_id,
        "name": "20261027 Weekly PartnerAdvisory Gcp Alignment",
        "title": "20261027 Weekly PartnerAdvisory Gcp Alignment",
        "package_digest": "c" * 64,
        "source_type": "dual_pipeline_intake",
        "source_posture": "derivative_internal",
        "authority_rank": 5,
        "date": "2026-10-27",
        "classification": "internal",
        "inline_canonical_promotion": True,
        "evidence": [ev_item],
    }
    candidate: dict[str, object] = {
        "candidate_id": f"cand-{target_id}",
        "candidate_type": "assertion",
        "assertion_type": "decision",
        "normalized_text": text,
        "materiality": "high",
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "evidence_ids": [evidence_id],
        "extraction_confidence": 0.88,
    }
    if speaker is not None:
        candidate["speaker_or_author"] = speaker
    if mentions is not None:
        candidate["entity_mentions"] = mentions
    op_id = f"op-{target_id}"
    payload = {
        "subject_node_id": subject_id,
        "predicate": "HAS_CLAIM_TEXT",
        "disposition": disposition,
        "candidate": candidate,
    }
    operation = {
        "operation_id": op_id,
        "sequence": 1,
        "operation": "add_approved_assertion" if disposition == "APPROVED" else "add_candidate_assertion",
        "target_type": "assertion",
        "target_id": target_id,
        "before_state_hash": ZERO_HASH,
        "after_state_hash": digest(payload),
        "evidence_ids": [evidence_id],
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "candidate_id": candidate["candidate_id"],
    }
    g_hash = sha256_file(graph_path)
    cs_id = f"cs-{target_id}"
    return create_artifact(
        "graph-change-set",
        cs_id,
        change_set_id=cs_id,
        run_id="run-w1-speaker-001",
        trace_id="d" * 32,
        status="approved" if disposition == "APPROVED" else "proposed",
        base_graph_snapshot=stable_id("graph-snapshot", g_hash, length=24),
        base_graph_hash=g_hash,
        approval_id="APP-W1-SPEAKER-001",
        operations=[operation],
        transaction_required=True,
        rollback_required=True,
        extensions={
            "com.partner_advisory.nee.source": src,
            "com.partner_advisory.nee.external-concepts": [],
            "com.partner_advisory.nee.internal-entity-proposals": [],
            "com.partner_advisory.nee.edge-traits": {},
            "com.partner_advisory.nee.operation-payloads": {op_id: payload},
        },
    )


class SpeakerClaimPromotionHygieneGateTests(unittest.TestCase):
    def test_wave1_l0_and_normalization_resolve_speakers_and_strip_placeholders(self) -> None:
        """Wave 1: Normalization and L0 helpers resolve inline/mention speakers and strip placeholder roster labels."""
        self.assertIsNone(clean_non_placeholder_speaker("Meeting Roster"))
        self.assertIsNone(clean_non_placeholder_speaker("Participants"))
        self.assertEqual(
            "Alex Brannan",
            resolve_deterministic_speaker_or_author(
                raw_speaker="Meeting Roster",
                scope_text="**Alex Brannan:** Custom agents should only be built when Gemini Enterprise cannot solve the workflow.",
            ),
        )
        self.assertEqual(
            "Robson Silva",
            resolve_deterministic_speaker_or_author(
                raw_speaker="Meeting Roster",
                scope_text="Confirmed that NEE has a 30-to-40 million dollar commit with Google.",
                subject_canonical_name="Robson Silva",
                subject_node_type="stakeholder",
            ),
        )
        # Candidate with Speaker mention resolves speaker even when raw speaker is Meeting Roster
        cand_with_mention = {
            "normalized_text": "Custom agents should be the exception when Gemini Enterprise falls short.",
            "entity_mentions": [
                {
                    "mention_id": "m-1",
                    "reported_text": "Alex Brannan",
                    "stable_identifier": "person:alex_brannan",
                    "context": "Speaker in 20261027 Weekly PartnerAdvisory Gcp Alignment",
                }
            ],
        }
        self.assertEqual(
            "Alex Brannan",
            _resolve_speaker_or_author({"speaker_or_author": "Meeting Roster"}, cand_with_mention, {}),
        )

        # Normalize an Evidence Envelope intake package with a Meeting Roster speaker and inline speaker turn
        pkg = create_artifact(
            "evidence-envelope-intake",
            "env-w1-test-001",
            meeting={
                "meeting_id": "mtg-20261027-partner_advisory-gcp",
                "title": "20261027 Weekly PartnerAdvisory Gcp Alignment",
                "started_at": "2026-10-27T14:00:00Z",
                "source_location": "output/2026-10-27_partner_gcp_alignment/01_hitl_resolved_transcript.md",
                "source_hash": "e" * 64,
                "template_version": "5.7.0",
                "classification": "internal",
                "audiences": ["nee-engineering"],
                "attendees": ["Amit Ayer", "Alex Brannan", "Robson Silva"],
            },
            speakers=[
                {
                    "speaker_ref": "spk-roster",
                    "reported_name": "Meeting Roster",
                    "email": None,
                    "role_or_org": "PartnerAdvisory",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "strategic",
                            "text": "**Alex Brannan:** Custom agents should be the exception when Gemini Enterprise cannot solve the workflow.",
                            "locator": {"kind": "timestamp", "value": "00:11:02"},
                            "gemini_confidence": 0.91,
                        }
                    ],
                }
            ],
            actions=[],
            adapter={"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            extensions={
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "graph_mutations": {
                        "entity_upserts": [
                            {"id": "person:alex_brannan", "type": "person", "name": "Alex Brannan", "description": "PartnerAdvisory GCP AI Architect."},
                            {"id": "workstream:spark", "type": "workstream", "name": "Spark AI", "description": "Strategic AI initiative."},
                        ],
                        "relationship_signals": [
                            {
                                "signal_id": "rel-01",
                                "from_id": "person:alex_brannan",
                                "to_id": "workstream:spark",
                                "predicate": "CONTRIBUTES_TO",
                                "lineage": {"speaker": "Meeting Roster"},
                            }
                        ],
                    }
                }
            },
        )
        bundle = normalize_evidence_envelope(pkg, run_context="w1-test")
        stmt_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "assertion"]
        self.assertEqual(1, len(stmt_cands))
        spk_mentions = [m["reported_text"] for m in stmt_cands[0]["entity_mentions"] if m["context"].startswith("Speaker in ")]
        self.assertEqual(["Alex Brannan"], spk_mentions)
        self.assertNotIn("Meeting Roster", [m["reported_text"] for m in stmt_cands[0]["entity_mentions"]])

        rel_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "architecture_relationship"]
        self.assertEqual(1, len(rel_cands))
        rel_trait = bundle["extensions"]["com.partner_advisory.nee.edge-traits"][rel_cands[0]["candidate_id"]]
        self.assertIsNone(rel_trait["speaker_or_author"])

    def test_wave1_writer_binds_inline_or_mention_speaker_and_rejects_unattributed_dialogue(self) -> None:
        """Wave 1: GovernedGraphWriter binds deterministic speaker when placeholder is passed and blocks unattributed dialogue."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            graph_path = Path(tmp) / "graph.sqlite"
            shutil.copy2(graph_src, graph_path)
            conn = connect_sqlite(graph_path, readonly=True)
            try:
                subj_id = str(conn.execute("SELECT node_id FROM node WHERE active = 1 LIMIT 1").fetchone()[0])
            finally:
                conn.close()

            writer = GovernedGraphWriter(graph_path)
            # 1. Unattributed raw spoken dialogue with Meeting Roster and no speaker mention/inline prefix fails closed
            cs_bad = _build_writer_change_set(
                graph_path=graph_path,
                subject_id=subj_id,
                evidence_id="ev-w1-bad-01",
                text="Yeah I think you know we should build a custom agent.",
                speaker="Meeting Roster",
                target_id="asrt-w1-bad-01",
            )
            with self.assertRaisesRegex(RuntimeError, "speaker/purity defect"):
                writer.apply(cs_bad)

            # 2. Placeholder speaker with inline '**Alex Brannan:**' resolves to Alex Brannan and commits cleanly
            cs_inline = _build_writer_change_set(
                graph_path=graph_path,
                subject_id=subj_id,
                evidence_id="ev-w1-inline-01",
                text="**Alex Brannan:** Custom agents should only be built when Gemini Enterprise cannot solve the workflow.",
                speaker="Meeting Roster",
                target_id="asrt-w1-inline-01",
            )
            res = writer.apply(cs_inline)
            self.assertEqual("COMMITTED", res["status"])

            rconn = connect_sqlite(graph_path, readonly=True)
            try:
                row = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible, speaker_or_author, qualifiers_json "
                    "FROM assertion WHERE assertion_id = 'asrt-w1-inline-01'"
                ).fetchone()
                self.assertEqual("canonical", row["canonical_status"])
                self.assertEqual(1, row["retrieval_eligible"])
                self.assertEqual("Alex Brannan", row["speaker_or_author"])
                quals = json.loads(row["qualifiers_json"])
                self.assertTrue(quals["fact_promotion_eligible"])
            finally:
                rconn.close()

    def test_wave2_hygiene_rebinds_placeholder_speakers_promotes_candidates_and_recovers_sidecar_vectors(self) -> None:
        """Wave 2: Hygiene agent re-binds Meeting Roster claims to Alex Brannan and Robson Silva, syncs qualifiers, promotes, and copies BGE-M3 vectors."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            db_path = Path(tmp) / "w2_hygiene.sqlite"
            conn = connect_sqlite(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (
                        node_id TEXT PRIMARY KEY,
                        node_type TEXT,
                        canonical_name TEXT,
                        active INTEGER DEFAULT 1
                    );
                    CREATE TABLE source (
                        source_id TEXT PRIMARY KEY,
                        source_type TEXT,
                        source_name TEXT
                    );
                    CREATE TABLE evidence_item (
                        evidence_id TEXT PRIMARY KEY,
                        source_id TEXT,
                        locator_value TEXT,
                        transcript_timestamp TEXT
                    );
                    CREATE TABLE assertion (
                        assertion_id TEXT PRIMARY KEY,
                        subject_node_id TEXT,
                        predicate TEXT,
                        object_literal TEXT,
                        scope_text TEXT,
                        speaker_or_author TEXT,
                        primary_source_id TEXT,
                        canonical_status TEXT,
                        canonical_eligible INTEGER,
                        retrieval_eligible INTEGER,
                        currentness_state TEXT,
                        validation_state TEXT,
                        confidence_score REAL,
                        is_quarantined INTEGER DEFAULT 0,
                        qualifiers_json TEXT,
                        superseded_by_assertion_id TEXT,
                        approval_reference TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE assertion_evidence (
                        assertion_id TEXT,
                        evidence_id TEXT,
                        evidence_relationship TEXT
                    );
                    CREATE TABLE governance_approval (
                        approval_id TEXT PRIMARY KEY,
                        approval_reference TEXT,
                        status TEXT,
                        active INTEGER
                    );
                    CREATE TABLE search_document (
                        search_document_id TEXT PRIMARY KEY,
                        assertion_id TEXT,
                        canonical_status TEXT,
                        currentness_state TEXT,
                        retrieval_eligible INTEGER,
                        updated_at TEXT
                    );
                    """
                )
                conn.executemany(
                    "INSERT INTO node VALUES (?,?,?,1)",
                    [
                        ("person_alex_brannan", "stakeholder", "Alex Brannan"),
                        ("person_robson_silva", "stakeholder", "Robson Silva"),
                        ("workstream_spark_ai", "workstream", "Spark AI"),
                    ],
                )
                conn.execute(
                    "INSERT INTO source VALUES ('source-2e69e4176c8f71fef7972870', 'dual_pipeline_intake', '20261027 Weekly PartnerAdvisory Gcp Alignment')"
                )
                conn.executemany(
                    "INSERT INTO evidence_item VALUES (?,?,?,?)",
                    [
                        ("ev-alex-1102", "source-2e69e4176c8f71fef7972870", "00:11:02", "00:11:02"),
                        ("ev-robson-1305", "source-2e69e4176c8f71fef7972870", "00:13:05", "00:13:05"),
                        ("ev-entity-card", "source-2e69e4176c8f71fef7972870", "## Entities", None),
                        ("ev-undiarized", "source-2e69e4176c8f71fef7972870", "00:19:00", "00:19:00"),
                    ],
                )
                cand_qual_assertion = json.dumps(
                    {
                        "dual_pipeline_candidate_id": "cand-alex-01",
                        "dual_pipeline_candidate_type": "assertion",
                        "dual_pipeline_disposition": "CANDIDATE",
                        "retrieval_kind": "assertion",
                        "answer_use": "candidate_hypothesis",
                        "fact_promotion_eligible": False,
                    }
                )
                cand_qual_entity = json.dumps(
                    {
                        "dual_pipeline_candidate_id": "cand-ent-01",
                        "dual_pipeline_candidate_type": "entity_mention",
                        "dual_pipeline_disposition": "CANDIDATE",
                        "retrieval_kind": "assertion",
                        "answer_use": "candidate_hypothesis",
                        "fact_promotion_eligible": False,
                    }
                )
                rows = [
                    # 1. Alex Brannan stakeholder subject with 'Meeting Roster' and confidence 0.75
                    (
                        "asrt-alex-custom-agent",
                        "person_alex_brannan",
                        "HAS_CLAIM_TEXT",
                        "Custom agents should only be built when Gemini Enterprise cannot solve the workflow.",
                        "Custom agents should only be built when Gemini Enterprise cannot solve the workflow.",
                        "Meeting Roster",
                        "source-2e69e4176c8f71fef7972870",
                        "candidate",
                        0,
                        0,
                        "current",
                        "needs_review",
                        0.75,
                        0,
                        cand_qual_assertion,
                        None,
                        None,
                        "2026-10-27T00:00:00Z",
                    ),
                    # 2. Workstream subject at same timestamp/evidence as Robson Silva stakeholder claim
                    (
                        "asrt-robson-stakeholder",
                        "person_robson_silva",
                        "HAS_CLAIM_TEXT",
                        "Apex Global Enterprises has a 30-to-40 million dollar commit with Google.",
                        "Apex Global Enterprises has a 30-to-40 million dollar commit with Google.",
                        "Meeting Roster",
                        "source-2e69e4176c8f71fef7972870",
                        "candidate",
                        0,
                        0,
                        "current",
                        "needs_review",
                        0.75,
                        0,
                        cand_qual_assertion,
                        None,
                        None,
                        "2026-10-27T00:00:00Z",
                    ),
                    (
                        "asrt-spark-sibling-robson",
                        "workstream_spark_ai",
                        "HAS_CLAIM_TEXT",
                        "Google spend must consume existing commit via Gemini Enterprise seats.",
                        "Google spend must consume existing commit via Gemini Enterprise seats.",
                        "Meeting Roster",
                        "source-2e69e4176c8f71fef7972870",
                        "candidate",
                        0,
                        0,
                        "current",
                        "needs_review",
                        0.75,
                        0,
                        cand_qual_assertion,
                        None,
                        None,
                        "2026-10-27T00:00:00Z",
                    ),
                    # 3. Structured entity_mention card with 'Meeting Roster' -> clears speaker to None
                    (
                        "asrt-spark-entity-card",
                        "workstream_spark_ai",
                        "HAS_CLAIM_TEXT",
                        "Spark AI: Strategic initiative for Apex Global enterprise AI adoption.",
                        "Spark AI: Strategic initiative for Apex Global enterprise AI adoption.",
                        "Meeting Roster",
                        "source-2e69e4176c8f71fef7972870",
                        "candidate",
                        0,
                        0,
                        "current",
                        "needs_review",
                        0.85,
                        0,
                        cand_qual_entity,
                        None,
                        None,
                        "2026-10-27T00:00:00Z",
                    ),
                    # 4. Unresolvable raw dialogue on workstream with 'Meeting Roster' and no sibling stakeholder -> NOT promoted
                    (
                        "asrt-unresolvable-dialogue",
                        "workstream_spark_ai",
                        "HAS_CLAIM_TEXT",
                        "Yeah I think you know we should look at that next week.",
                        "Yeah I think you know we should look at that next week.",
                        "Meeting Roster",
                        "source-2e69e4176c8f71fef7972870",
                        "candidate",
                        0,
                        0,
                        "current",
                        "needs_review",
                        0.75,
                        0,
                        cand_qual_assertion,
                        None,
                        None,
                        "2026-10-27T00:00:00Z",
                    ),
                ]
                conn.executemany(
                    "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                conn.executemany(
                    "INSERT INTO assertion_evidence VALUES (?,?,'supports')",
                    [
                        ("asrt-alex-custom-agent", "ev-alex-1102"),
                        ("asrt-robson-stakeholder", "ev-robson-1305"),
                        ("asrt-spark-sibling-robson", "ev-robson-1305"),
                        ("asrt-spark-entity-card", "ev-entity-card"),
                        ("asrt-unresolvable-dialogue", "ev-undiarized"),
                    ],
                )
                for r in rows:
                    conn.execute(
                        "INSERT INTO search_document VALUES (?,?,'candidate','current',0,'2026-10-27T00:00:00Z')",
                        (r[0], r[0]),
                    )
                conn.commit()

                # Audit promotable candidates via speaker_hygiene_rules
                asrt_cols = {c[1] for c in conn.execute("PRAGMA table_info(assertion)").fetchall()}
                finding = audit_safe_promotable_candidates(
                    conn.cursor(),
                    asrt_cols,
                    set(),
                    lambda **kw: kw,
                )
                self.assertIsNotNone(finding)
                promotable_ids = set(finding["entity_references"])
                self.assertEqual(
                    {
                        "asrt-alex-custom-agent",
                        "asrt-robson-stakeholder",
                        "asrt-spark-sibling-robson",
                        "asrt-spark-entity-card",
                    },
                    promotable_ids,
                )
                self.assertNotIn("asrt-unresolvable-dialogue", promotable_ids)

                # Promote via _remediate_candidate_promotions
                pkg = {
                    "approval_reference": "HITL-2026-10-30",
                    "findings": [finding],
                }
                promoted_cnt = _remediate_candidate_promotions(conn, pkg, "2026-10-30T10:00:00Z")
                self.assertEqual(4, promoted_cnt)
                conn.commit()
            finally:
                conn.close()

            # Fresh read-only connection verification
            rconn = connect_sqlite(db_path, readonly=True)
            try:
                alex_row = rconn.execute(
                    "SELECT canonical_status, canonical_eligible, retrieval_eligible, speaker_or_author, qualifiers_json "
                    "FROM assertion WHERE assertion_id = 'asrt-alex-custom-agent'"
                ).fetchone()
                self.assertEqual(("canonical", 1, 1, "Alex Brannan"), (alex_row[0], alex_row[1], alex_row[2], alex_row[3]))
                self.assertTrue(json.loads(alex_row[4])["fact_promotion_eligible"])
                self.assertEqual("factual_answer", json.loads(alex_row[4])["answer_use"])

                rob_sib = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible, speaker_or_author FROM assertion WHERE assertion_id = 'asrt-spark-sibling-robson'"
                ).fetchone()
                self.assertEqual(("canonical", 1, "Robson Silva"), tuple(rob_sib))

                card_row = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible, speaker_or_author FROM assertion WHERE assertion_id = 'asrt-spark-entity-card'"
                ).fetchone()
                self.assertEqual(("canonical", 1, None), tuple(card_row))

                unres_row = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible, speaker_or_author FROM assertion WHERE assertion_id = 'asrt-unresolvable-dialogue'"
                ).fetchone()
                self.assertEqual(("candidate", 0, "Meeting Roster"), tuple(unres_row))
            finally:
                rconn.close()

            # Verify sidecar vector recovery from ancestor generation
            rt_root = Path(tmp) / "runtime"
            donor_dir = rt_root / "generations" / "generation-504b9ca4065b5ff924d200da3868"
            target_dir = rt_root / "generations" / "generation-target"
            donor_dir.mkdir(parents=True, exist_ok=True)
            target_dir.mkdir(parents=True, exist_ok=True)

            unit_blob = struct.pack("<1024f", 1.0, *([0.0] * 1023))
            real_blob = struct.pack("<1024f", 0.5, 0.5, *([0.0] * 1022))

            dconn = connect_sqlite(donor_dir / "sidecar.sqlite")
            try:
                dconn.executescript(
                    """
                    CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB);
                    CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL);
                    CREATE TABLE colbert_token (record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB);
                    """
                )
                dconn.execute("INSERT INTO dense_vector VALUES ('asrt-alex-custom-agent', 1024, ?)", (real_blob,))
                dconn.execute("INSERT INTO sparse_weight VALUES ('asrt-alex-custom-agent', 'gemini', 0.95)")
                dconn.commit()
            finally:
                dconn.close()

            sconn = connect_sqlite(target_dir / "sidecar.sqlite")
            try:
                sconn.executescript(
                    """
                    CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY);
                    CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB);
                    CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL);
                    CREATE TABLE colbert_token (record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB);
                    """
                )
                sconn.execute("INSERT INTO retrieval_record VALUES ('asrt-alex-custom-agent')")
                sconn.execute("INSERT INTO dense_vector VALUES ('asrt-alex-custom-agent', 1024, ?)", (unit_blob,))
                _copy_real_vectors_from_ancestor(
                    sconn,
                    unit_blob,
                    {"retrieval_record", "dense_vector", "sparse_weight", "colbert_token"},
                    runtime_root=rt_root,
                )
                sconn.commit()
            finally:
                sconn.close()

            ro_sconn = connect_sqlite(target_dir / "sidecar.sqlite", readonly=True)
            try:
                vec = ro_sconn.execute("SELECT vector FROM dense_vector WHERE record_id = 'asrt-alex-custom-agent'").fetchone()[0]
                self.assertEqual(real_blob, vec)
                sw = ro_sconn.execute("SELECT token, weight FROM sparse_weight WHERE record_id = 'asrt-alex-custom-agent'").fetchone()
                self.assertEqual(("gemini", 0.95), tuple(sw))
            finally:
                ro_sconn.close()

    def test_wave3_staging_hold_diarized_recovery_and_undiarized_degenerate_hitl_surfacing(self) -> None:
        """Wave 3: Staging hold candidates with None speaker and 0.45 quarantined confidence recover diarized speaker and promote, while undiarized and degenerate candidates surface in Cluster 1 for HITL archival."""
        from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            db_path = Path(tmp) / "w3_staging.sqlite"
            transcript_path = Path(tmp) / "01_2026-10-27_hitl_resolved_transcript.md"
            transcript_path.write_text(
                "# Diarized Transcript\n\n### [00:11:02]\n**Drew Oakley:** Yeah I think we need to finalize the quarterly budget by Friday.\n",
                encoding="utf-8",
            )
            undiarized_path = Path(tmp) / "01_2026-10-01_undiarized_transcript.md"
            undiarized_path.write_text(
                "# Transcript\nNote: This recording was recorded through a single input channel without native speaker diarization.\n\n### [00:05:00]\n**Meeting Roster:** We should review the sprint backlog.\n",
                encoding="utf-8",
            )
            conn = connect_sqlite(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (
                        node_id TEXT PRIMARY KEY,
                        node_type TEXT,
                        canonical_name TEXT,
                        status TEXT DEFAULT 'active',
                        created_at TEXT,
                        active INTEGER DEFAULT 1
                    );
                    CREATE TABLE source (
                        source_id TEXT PRIMARY KEY,
                        source_type TEXT,
                        source_name TEXT,
                        cold_archive_ref TEXT
                    );
                    CREATE TABLE evidence_item (
                        evidence_id TEXT PRIMARY KEY,
                        source_id TEXT,
                        direct_quote TEXT,
                        locator_value TEXT,
                        transcript_timestamp TEXT,
                        extraction_confidence REAL,
                        created_at TEXT
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
                        canonical_status TEXT,
                        canonical_eligible INTEGER,
                        retrieval_eligible INTEGER,
                        currentness_state TEXT,
                        validation_state TEXT,
                        confidence_score REAL,
                        is_quarantined INTEGER DEFAULT 0,
                        qualifiers_json TEXT,
                        superseded_by_assertion_id TEXT,
                        approval_reference TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    );
                    CREATE TABLE assertion_evidence (
                        assertion_id TEXT,
                        evidence_id TEXT,
                        evidence_relationship TEXT
                    );
                    CREATE TABLE governance_approval (
                        approval_id TEXT PRIMARY KEY,
                        approval_reference TEXT,
                        status TEXT,
                        active INTEGER
                    );
                    CREATE TABLE search_document (
                        search_document_id TEXT PRIMARY KEY,
                        assertion_id TEXT,
                        canonical_status TEXT,
                        currentness_state TEXT,
                        retrieval_eligible INTEGER,
                        updated_at TEXT
                    );
                    """
                )
                conn.executemany(
                    "INSERT INTO node VALUES (?,?,?,'active','2026-10-01T00:00:00Z',1)",
                    [
                        ("workstream_finance", "workstream", "Finance Transformation"),
                        ("workstream_lens", "workstream", "LENS Platform"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO source VALUES (?,?,?,?)",
                    [
                        ("src-diarized", "dual_pipeline_intake", "20261027 Budget Sync", str(transcript_path)),
                        ("src-undiarized", "dual_pipeline_intake", "20261001 Lens Sprint Review", str(undiarized_path)),
                    ],
                )
                conn.executemany(
                    "INSERT INTO evidence_item VALUES (?,?,?,?,?,?,?)",
                    [
                        ("ev-dan-01", "src-diarized", "Yeah I think we need to finalize the quarterly budget by Friday.", "00:11:02", "00:11:02", 0.75, "2026-10-27T00:00:00Z"),
                        ("ev-und-01", "src-undiarized", "We should review the sprint backlog.", "00:05:00", "00:05:00", 0.75, "2026-10-27T00:00:00Z"),
                        ("ev-deg-01", "src-diarized", "ESTABLISHED", "00:12:00", "00:12:00", 0.85, "2026-10-27T00:00:00Z"),
                    ],
                )
                q_quarantined = json.dumps(
                    {
                        "dual_pipeline_candidate_id": "cand-dan-01",
                        "dual_pipeline_candidate_type": "assertion",
                        "dual_pipeline_disposition": "CANDIDATE",
                        "extraction_confidence": 0.75,
                        "speaker_hygiene_quarantined": True,
                        "fact_promotion_eligible": False,
                    }
                )
                q_undiarized = json.dumps(
                    {
                        "dual_pipeline_candidate_id": "cand-und-01",
                        "dual_pipeline_candidate_type": "assertion",
                        "dual_pipeline_disposition": "CANDIDATE",
                        "extraction_confidence": 0.75,
                        "fact_promotion_eligible": False,
                    }
                )
                conn.executemany(
                    "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        # Cohort A: speaker_or_author is None, confidence_score lowered to 0.45, extraction_confidence=0.75, diarized transcript
                        (
                            "asrt-cohort-a-diarized",
                            "workstream_finance",
                            "HAS_CLAIM_TEXT",
                            None,
                            "Yeah I think we need to finalize the quarterly budget by Friday.",
                            "Yeah I think we need to finalize the quarterly budget by Friday.",
                            None,
                            "src-diarized",
                            "candidate",
                            0,
                            0,
                            "current",
                            "needs_review",
                            0.45,
                            0,
                            q_quarantined,
                            None,
                            None,
                            "2026-10-28T00:00:00Z",
                            "2026-10-28T00:00:00Z",
                        ),
                        # Cohort B: Undiarized transcript with Meeting Roster placeholder, recent (<30 days)
                        (
                            "asrt-cohort-b-undiarized",
                            "workstream_lens",
                            "HAS_CLAIM_TEXT",
                            None,
                            "Yeah I think we should review the sprint backlog.",
                            "Yeah I think we should review the sprint backlog.",
                            "Meeting Roster",
                            "src-undiarized",
                            "candidate",
                            0,
                            0,
                            "current",
                            "needs_review",
                            0.75,
                            0,
                            q_undiarized,
                            None,
                            None,
                            "2026-10-28T00:00:00Z",
                            "2026-10-28T00:00:00Z",
                        ),
                        # Cohort C: Degenerate single-token claim 'ESTABLISHED', recent (<30 days)
                        (
                            "asrt-cohort-c-degenerate",
                            "workstream_finance",
                            "HAS_CLAIM_TEXT",
                            None,
                            "ESTABLISHED",
                            "ESTABLISHED",
                            "Transcript participant roster",
                            "src-diarized",
                            "candidate",
                            0,
                            0,
                            "current",
                            "needs_review",
                            0.85,
                            0,
                            q_undiarized,
                            None,
                            None,
                            "2026-10-28T00:00:00Z",
                            "2026-10-28T00:00:00Z",
                        ),
                    ],
                )
                conn.executemany(
                    "INSERT INTO assertion_evidence VALUES (?,?,'supports')",
                    [
                        ("asrt-cohort-a-diarized", "ev-dan-01"),
                        ("asrt-cohort-b-undiarized", "ev-und-01"),
                        ("asrt-cohort-c-degenerate", "ev-deg-01"),
                    ],
                )
                for aid in ("asrt-cohort-a-diarized", "asrt-cohort-b-undiarized", "asrt-cohort-c-degenerate"):
                    conn.execute(
                        "INSERT INTO search_document VALUES (?,?,'candidate','current',0,'2026-10-28T00:00:00Z')",
                        (aid, aid),
                    )
                conn.commit()
            finally:
                conn.close()

            panel = GraphHygienePanel(graph_path=db_path)
            pkg = panel.run_full_audit(target_graph=db_path)
            clusters_by_id = {c["cluster_id"]: c for c in pkg.hitl_clusters}
            self.assertIn("CLUSTER-06-STAGING-PROMOTIONS", clusters_by_id)
            self.assertIn("CLUSTER-01-UNPROMOTED-STAGING-DRAFTS", clusters_by_id)
            self.assertIn("CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL", clusters_by_id)
            self.assertEqual(["asrt-cohort-a-diarized"], clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["entity_references"])
            self.assertEqual(
                ["asrt-cohort-c-degenerate"],
                clusters_by_id["CLUSTER-01-UNPROMOTED-STAGING-DRAFTS"]["entity_references"],
            )
            self.assertEqual(
                ["asrt-cohort-b-undiarized"],
                clusters_by_id["CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL"]["entity_references"],
            )
            self.assertTrue(clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["requires_isolated_scrutiny"])
            self.assertEqual(1, clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["wave_number"])
            self.assertTrue(clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["ready_to_ask_question"]["autonomous_permitted"])
            self.assertGreater(clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["ready_to_ask_question"]["margin"], 0.20)
            self.assertTrue(clusters_by_id["CLUSTER-01-UNPROMOTED-STAGING-DRAFTS"]["requires_isolated_scrutiny"])
            self.assertFalse(clusters_by_id["CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL"]["ready_to_ask_question"]["autonomous_permitted"])

            # Verify Cluster 6 question table shows itemized speaker and claim details, and Cluster 1F passes 5-turn speaker attribution validation
            from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload

            c1f_q = clusters_by_id["CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL"]["ready_to_ask_question"]["question"]
            c1f_opts = clusters_by_id["CLUSTER-01F-UNDIARIZED-SPEAKER-ATTRIBUTION-HITL"]["ready_to_ask_question"]["options"]
            self.assertIn("TARGET [00:05:00] **UNRESOLVED — Meeting Roster**:", c1f_q)
            self.assertTrue(validate_question_payload(c1f_q, c1f_opts).is_valid)

            c6_q = clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["ready_to_ask_question"]["question"]
            c6_opts = clusters_by_id["CLUSTER-06-STAGING-PROMOTIONS"]["ready_to_ask_question"]["options"]
            self.assertIn("Drew Oakley", c6_q)
            self.assertIn("quarterly budget", c6_q)
            self.assertTrue(validate_question_payload(c6_q, c6_opts).is_valid)

            bad_summary_q = (
                "| Dimension | Plain-English Status and Record Comparison | Source and Timing Context | 8-Agent Council Verdict |\n"
                "| :--- | :--- | :--- | :--- |\n"
                "| Source Review | Identified 49 verified high-confidence staging note(s) eligible for promotion. | 1 records (Priority Low) | Isolate for explicit boundary decision |\n"
                "Should we promote these verified high-confidence staging notes to active official records?"
            )
            bad_res = validate_question_payload(bad_summary_q, c6_opts)
            self.assertFalse(bad_res.is_valid)
            self.assertTrue(any("GENERIC_SUMMARY_ROW_PROHIBITED" in e for e in bad_res.errors))

            # Verify promotion restores confidence_score >= 0.75 and binds Drew Oakley
            wconn = connect_sqlite(db_path)
            try:
                promoted = _remediate_candidate_promotions(
                    wconn,
                    {"approval_reference": "HITL-2026-10-30", "findings": [f.__dict__ for f in pkg.findings]},
                    "2026-10-30T12:00:00Z",
                )
                self.assertEqual(1, promoted)
                wconn.commit()
            finally:
                wconn.close()

            rconn = connect_sqlite(db_path, readonly=True)
            try:
                row_a = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible, speaker_or_author, confidence_score "
                    "FROM assertion WHERE assertion_id = 'asrt-cohort-a-diarized'"
                ).fetchone()
                self.assertEqual(("canonical", 1, "Drew Oakley", 0.75), tuple(row_a))
            finally:
                rconn.close()

    def test_wave4_default_speaker_identification_for_undiarized_header_and_single_channel_memory(self) -> None:
        """Wave 4: Transcripts with UNDIARIZED header disclaimers recover explicit sub-turn speakers, and single-channel transcripts recover speakers from SpeakerMemory across the participant roster."""
        from dual_pipeline.graph_maintenance_core.transcript_turn_resolver import resolve_diarized_transcript_turn_speaker

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            # 1. Transcript with UNDIARIZED header notice but explicit speaker blocks and sub-paragraph timestamps
            sprint_md = Path(tmp) / "01_LENS_Sprint_Review_hitl_resolved_transcript.md"
            sprint_md.write_text(
                "# LENS Sprint Review\n"
                "**Participants:**\n"
                "- **Jen Pan** (Product Manager)\n"
                "- **Jake Stark** (UI and Prompting Lead)\n\n"
                "> **Diarization Limitation Notice:** Metadata identifies a WAV audio source and explicitly classifies it as **UNDIARIZED**.\n\n"
                "### [00:00:18] -- stmt-002\n\n"
                "**Jen Pan:** Sure, I can answer that.\n\n"
                "[00:00:58] And then the second kind of filter is we're only keeping the records that we can manually verify.\n\n"
                "### [00:12:33] -- stmt-026\n\n"
                "**Jake Stark:** Yeah, sure.\n\n"
                "[00:13:49] So we made some changes since then, which gives the user the ability to just edit or add a rule to any extraction.\n",
                encoding="utf-8",
            )
            self.assertEqual(
                "Jen Pan",
                resolve_diarized_transcript_turn_speaker(
                    str(sprint_md),
                    "[00:00:58]",
                    "And then the second kind of filter is we're only keeping the records that we can manually verify.",
                ),
            )
            self.assertEqual(
                "Jake Stark",
                resolve_diarized_transcript_turn_speaker(
                    str(sprint_md),
                    "[00:13:49]",
                    "So we made some changes since then, which gives the user the ability to just edit or add a rule to any extraction.",
                ),
            )

            # 2. Single-channel collapsed transcript refuses silent guessing and builds 5-turn HITL speaker attribution context
            from dual_pipeline.graph_maintenance_core.transcript_turn_resolver import build_undiarized_turn_hitl_context

            single_md = Path(tmp) / "01_Lens_catchup_single_channel_hitl_resolved_transcript.md"
            single_md.write_text(
                "# LENS CatchUp\n"
                "**Participants:**\n"
                "- **Jen Pan** (Participant, Unknown)\n"
                "- **Amit Ayer** (Lead Enterprise Architect, PartnerAdvisory)\n"
                "- **Mandy H** (Participant, NEA)\n\n"
                "> **Forensic Attribution Notice:** All spoken dialogue turns were recorded through a single input channel and labeled as Jen Pan.\n\n"
                "**[08:08] Jen Pan:** We anticipate that this is going to add four weeks to the timeline. Is that about where we're sitting?\n\n"
                "**[08:15] Jen Pan:** I think that's what we had estimated. I don't know how conservative we want to be with that.\n\n"
                "**[16:04] Jen Pan:** You have been working on this for 12 weeks.\n\n"
                "**[17:05] Jen Pan:** Yeah. I think that's where, and where I've kind of been thinking about the manufacturer, that maybe we constrain the domain for manufacturer.\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                resolve_diarized_transcript_turn_speaker(
                    str(single_md),
                    "00:08:08",
                    "We anticipate that this is going to add four weeks to the timeline. Is that about where we're sitting?",
                )
            )
            ctx = build_undiarized_turn_hitl_context(
                str(single_md),
                "00:16:04",
                "You have been working on this for 12 weeks.",
                "Jen Pan (recording channel label; actual speaker unresolved)",
                source_title="20260917 Lens Catchup Mandy Jen Amit",
            )
            self.assertIn("BEFORE 2 [00:08:08] **UNRESOLVED — Jen Pan**:", ctx["context_block"])
            self.assertIn("BEFORE 1 [00:08:15] **UNRESOLVED — Jen Pan**:", ctx["context_block"])
            self.assertIn("TARGET [00:16:04] **UNRESOLVED — Jen Pan**:", ctx["context_block"])
            self.assertIn("AFTER 1 [00:17:05] **UNRESOLVED — Jen Pan**:", ctx["context_block"])
            self.assertEqual(["Jen Pan", "Amit Ayer", "harper Hoffman"], ctx["roster_candidates"])

    def test_wave5_spoken_sow_charter_sources_single_token_names_and_synthetic_personas(self) -> None:
        """Wave 5: Spoken meetings named with SOW/Charter/Document tokens, single-token speaker names, inline speaker prefixes, and synthetic personas are detected and remediated."""
        from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import resolve_candidate_provenance_speaker
        from dual_pipeline.l0.speaker_and_qualifier_policy import (
            classify_speaker_attribution_defect,
            is_spoken_meeting_source,
            normalize_candidate_claim_text,
        )

        # 1. Spoken meeting sources with SOW/Charter/Document in title are recognized via cold_archive_ref
        self.assertTrue(
            is_spoken_meeting_source(
                "dual_pipeline_intake",
                "20261029 Vanguard Sow Review apex_trans Phase 2",
                "output/apex_trans/10-29-2026/01_Trident_Sow_Phase_2_hitl_resolved_transcript.md",
            )
        )
        self.assertEqual(
            "missing_meeting_speaker",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="The conversation is going to go one of three ways.",
                speaker_or_author=None,
                source_type="dual_pipeline_intake",
                source_name="20261029 Vanguard Sow Review apex_trans Phase 2",
                qualifiers_json=json.dumps({"dual_pipeline_candidate_type": "assertion"}),
                transcript_timestamp="00:00:22",
                cold_archive_ref="output/apex_trans/10-29-2026/01_Trident_Sow_Phase_2_hitl_resolved_transcript.md",
            ),
        )
        self.assertEqual(
            "missing_meeting_speaker",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="But the risk is four different stakeholders pulling in different directions.",
                speaker_or_author=None,
                source_type="dual_pipeline_intake",
                source_name="20261029 Outcome Based Services Strategy",
                qualifiers_json=json.dumps({"dual_pipeline_candidate_type": "risk"}),
                transcript_timestamp="00:22:46",
                cold_archive_ref="output/strategy/10-29-2026/01_Outcome_Based_Services_hitl_resolved_transcript.md",
            ),
        )

        # 2. Single-token speaker names and organization labels on structured cards vs dialogue
        self.assertEqual(
            "single_token_speaker_name",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="The agreed core team consists of Dan, Lan, Meg, and Andrew.",
                speaker_or_author="Dan",
                source_type="dual_pipeline_intake",
                source_name="Document 20261012 PartnerAdvisory Leader Meeting",
            ),
        )
        self.assertEqual(
            ("Drew Oakley", True),
            resolve_candidate_provenance_speaker(
                {
                    "speaker_or_author": "Dan",
                    "scope_text": "The agreed core team consists of Dan, Lan, Meg, and Andrew.",
                    "source_type": "dual_pipeline_intake",
                    "source_name": "Document 20261012 PartnerAdvisory Leader Meeting",
                },
                {},
                {},
            ),
        )
        self.assertEqual(
            "organization_or_unresolved_speaker",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="PartnerAdvisory: PartnerAdvisory account/delivery organization context for NEE/EDO stakeholders.",
                speaker_or_author="NEA Executive Engineering Leadership",
                source_type="dual_pipeline_intake",
                source_name="20260827 Nea Chakra Operating Model",
                qualifiers_json=json.dumps({"dual_pipeline_candidate_type": "entity_mention"}),
            ),
        )
        self.assertEqual(
            (None, True),
            resolve_candidate_provenance_speaker(
                {
                    "speaker_or_author": "NEA Executive Engineering Leadership",
                    "scope_text": "PartnerAdvisory: PartnerAdvisory account/delivery organization context for NEE/EDO stakeholders.",
                    "source_type": "dual_pipeline_intake",
                    "source_name": "20260827 Nea Chakra Operating Model",
                    "qualifiers_json": json.dumps({"dual_pipeline_candidate_type": "entity_mention"}),
                },
                {},
                {},
            ),
        )

        # 3. Inline speaker prefixes (including missing leading '**' and mid-line timestamp speaker tags) are stripped and resolved
        self.assertEqual(
            "But what is the first vertical slice of business value that we can deliver",
            normalize_candidate_claim_text("Miles Erickson:** But what is the first vertical slice of business value that we can deliver"),
        )
        self.assertEqual(
            "GIS on the back end and I don't,",
            normalize_candidate_claim_text("[00:15:48] **Mitch Garstka:** GIS on the back end and I don't,"),
        )
        self.assertEqual(
            "inline_speaker_contradiction",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="[00:15:48] **Mitch Garstka:** GIS on the back end and I don't,",
                speaker_or_author="Kaleena McHugo",
                source_type="dual_pipeline_intake",
                source_name="20261007 Jarvis Engineering Session",
            ),
        )

        # 4. Synthetic test personas are flagged as synthetic_fixture_marker across all predicates
        self.assertEqual(
            "synthetic_fixture_marker",
            classify_speaker_attribution_defect(
                predicate="HAS_ROLE_IN",
                scope_text="Elena Rostova HAS_ROLE Data Strategist.",
                speaker_or_author=None,
                subject_node_id="entity-elena-rostova",
            ),
        )


if __name__ == "__main__":
    unittest.main()
