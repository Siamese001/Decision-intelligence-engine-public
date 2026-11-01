#!/usr/bin/env python3
"""Wave 1 unit & adversarial tests for writer speaker/qualifier gates, fast-promotion, and recovery guards."""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import ROOT, ZERO_HASH, connect_sqlite, digest, sha256_file, stable_id
from dual_pipeline.fast_promotion import fast_promote_candidates
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.graph_writer_core.assertions import _resolve_speaker_or_author
from dual_pipeline.l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    has_non_promotable_qualifiers,
    has_stale_candidate_qualifiers,
    sync_canonical_qualifiers_json,
)
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _attach_synthesized_evidence,
    _normalize_assertion_and_search_states,
    dispatch_special_hygiene_cli,
)


def _make_approved_change_set(
    *,
    graph_path: Path,
    subject_id: str,
    evidence_id: str,
    text: str,
    speaker: str | None,
    source_meta: dict[str, object] | None = None,
    disposition: str = "APPROVED",
    target_id: str = "asrt-w1-test-001",
) -> dict[str, object]:
    src_id = "source-w1-meeting-001"
    sv_id = "sv-w1-001"
    ev_item = {
        "evidence_id": evidence_id,
        "source_id": src_id,
        "source_version_id": sv_id,
        "locator": {"kind": "section", "value": "## Decisions"},
        "quote": text,
        "quote_sha256": digest(text),
        "directness": "direct",
        "authority_rank": 5,
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "extraction_confidence": 0.92,
    }
    src = {
        "source_id": src_id,
        "source_version_id": sv_id,
        "name": "Project Nexus Rules Alignment",
        "title": "Project Nexus Rules Alignment",
        "package_digest": "a" * 64,
        "source_type": "dual_pipeline_intake",
        "source_posture": "derivative_internal",
        "authority_rank": 5,
        "date": "2026-10-29",
        "classification": "internal",
        "inline_canonical_promotion": True,
        "evidence": [ev_item],
        **(source_meta or {}),
    }
    candidate = {
        "candidate_id": f"cand-{target_id}",
        "candidate_type": "assertion",
        "assertion_type": "decision",
        "normalized_text": text,
        "materiality": "high",
        "classification": "internal",
        "audiences": ["nee-engineering"],
        "evidence_ids": [evidence_id],
        "extraction_confidence": 0.92,
        **({"speaker_or_author": speaker} if speaker is not None else {}),
    }
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
        run_id="run-w1-001",
        trace_id="b" * 32,
        status="approved" if disposition == "APPROVED" else "proposed",
        base_graph_snapshot=stable_id("graph-snapshot", g_hash, length=24),
        base_graph_hash=g_hash,
        approval_id="APP-W1-TEST-001",
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


class Wave1WriterAndRecoveryGateTests(unittest.TestCase):
    def test_speaker_resolution_never_falls_back_to_organizer_or_attendees(self) -> None:
        """Category 1: Organizer/attendee fallback is removed and placeholder headers strip to None."""
        source_with_org = {
            "organizer": "Eduardo (Ed) Juan More",
            "attendees": ["Eduardo (Ed) Juan More", "Alex Sanchez"],
        }
        self.assertIsNone(_resolve_speaker_or_author({}, {}, source_with_org))
        for bad_label in (
            "Participants",
            "Not specified",
            "Forensic Context",
            "Transcript Header",
            "Meeting Roster",
            "Speaker 2",
            "person:amit_ayer",
            "   ",
        ):
            self.assertIsNone(_resolve_speaker_or_author({"speaker_or_author": bad_label}, {}, source_with_org))
        self.assertEqual(
            "Alex Sanchez",
            _resolve_speaker_or_author({"speaker_or_author": "Alex Sanchez"}, {}, source_with_org),
        )

    def test_l0_defect_classifier_preserves_neet_stakeholders_and_catches_nexus_contamination(self) -> None:
        """Category 2: Real APEX_TRANS stakeholders stay valid in Insights Hub while cross-project/synthetic markers fail."""
        self.assertIsNone(
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="Alex Rivera confirmed the UAT training schedule for Insights Hub.",
                speaker_or_author="Alex Rivera",
                subject_node_id="workstream_insights_hub",
                source_type="dual_pipeline_intake",
                source_name="APEX_TRANS Insights Hub Weekly Sync",
            )
        )
        self.assertEqual(
            "cross_project_persona_contamination",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="Alex Rivera reviewed the rules engine.",
                speaker_or_author="Alex Rivera",
                subject_node_id="workstream_project_nexus",
                source_type="dual_pipeline_intake",
                source_name="Project Nexus Rules Alignment",
            ),
        )
        self.assertEqual(
            "inline_speaker_contradiction",
            classify_speaker_attribution_defect(
                predicate="HAS_CLAIM_TEXT",
                scope_text="**Alex Sanchez:** So we already built the deterministic rules check.",
                speaker_or_author="Eduardo (Ed) Juan More",
                subject_node_id="workstream_project_nexus",
                source_type="dual_pipeline_intake",
                source_name="Project Nexus Rules Alignment",
            ),
        )

    def test_governed_writer_rejects_defective_approved_writes_and_syncs_valid_qualifiers(self) -> None:
        """Category 3: Writer blocks placeholder/contradictory/undiarized APPROVED claims and syncs valid qualifiers."""
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
            for idx, (bad_text, bad_spk) in enumerate(
                [
                    ("The deterministic validation rule passed UAT.", "Participants"),
                    ("**Alex Sanchez:** We need explicit rules traceability.", "Eduardo (Ed) Juan More"),
                    (": Yeah I think you know we should ship it.", None),
                    ("Yeah I think you're right that we're missing the mapping.", None),
                ]
            ):
                cs_bad = _make_approved_change_set(
                    graph_path=graph_path,
                    subject_id=subj_id,
                    evidence_id=f"ev-bad-{idx}",
                    text=bad_text,
                    speaker=bad_spk,
                    target_id=f"asrt-bad-{idx}",
                )
                with self.assertRaisesRegex(RuntimeError, "speaker/purity defect"):
                    writer.apply(cs_bad)

            cs_good = _make_approved_change_set(
                graph_path=graph_path,
                subject_id=subj_id,
                evidence_id="ev-good-001",
                text="Alex Sanchez confirmed deterministic validation rules passed staging checks.",
                speaker="Alex Sanchez",
                target_id="asrt-good-001",
            )
            res = writer.apply(cs_good)
            self.assertEqual("COMMITTED", res["status"])

            rconn = connect_sqlite(graph_path, readonly=True)
            try:
                row = rconn.execute(
                    "SELECT canonical_status, speaker_or_author, qualifiers_json FROM assertion WHERE assertion_id = 'asrt-good-001'"
                ).fetchone()
                self.assertEqual("canonical", row["canonical_status"])
                self.assertEqual("Alex Sanchez", row["speaker_or_author"])
                quals = json.loads(row["qualifiers_json"])
                self.assertEqual("APPROVED", quals["dual_pipeline_disposition"])
                self.assertTrue(quals["fact_promotion_eligible"])
                self.assertEqual("factual_answer", quals["answer_use"])
            finally:
                rconn.close()

    def test_fast_promotion_blocks_defective_candidates_syncs_qualifiers_and_guards_active_pair(self) -> None:
        """Category 4: Fast promotion blocks speaker/qualifier defects, syncs qualifiers, and blocks live in-place writes."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            rt_root = Path(tmp) / "runtime"
            gen_dir = rt_root / "generations" / "generation-w1"
            gen_dir.mkdir(parents=True, exist_ok=True)
            live_db = gen_dir / "graph.sqlite"
            shutil.copy2(graph_src, live_db)
            (rt_root / "active_pair.json").write_text(
                json.dumps(
                    {
                        "generation_id": "generation-w1",
                        "graph_path": "generations/generation-w1/graph.sqlite",
                        "sidecar_path": "generations/generation-w1/sidecar.sqlite",
                    }
                ),
                encoding="utf-8",
            )
            before_live_sha = sha256_file(live_db)
            with self.assertRaisesRegex(RuntimeError, "promote_generation=True"):
                fast_promote_candidates(runtime_root=rt_root, promote_generation=False, dry_run=False)
            self.assertEqual(before_live_sha, sha256_file(live_db))

            sandbox_db = Path(tmp) / "sandbox_promote.sqlite"
            shutil.copy2(graph_src, sandbox_db)
            conn = connect_sqlite(sandbox_db)
            try:
                subj_id = str(conn.execute("SELECT node_id FROM node WHERE active = 1 LIMIT 1").fetchone()[0])
                ev_id, src_id = conn.execute("SELECT evidence_id, source_id FROM evidence_item LIMIT 1").fetchone()
                conn.execute("UPDATE source SET source_type = 'dual_pipeline_intake', source_name = 'Nexus Sync' WHERE source_id = ?", (src_id,))
                tmpl = dict(conn.execute("SELECT * FROM assertion LIMIT 1").fetchone())
                cols = list(tmpl.keys())
                ae_tmpl = dict(conn.execute("SELECT * FROM assertion_evidence WHERE evidence_relationship='supports' LIMIT 1").fetchone())
                ae_cols = list(ae_tmpl.keys())

                def _seed(aid: str, text: str, spk: str | None, qual: dict[str, object]) -> None:
                    r = dict(tmpl)
                    r.update(
                        {
                            "assertion_id": aid,
                            "assertion_fingerprint": digest(aid),
                            "subject_node_id": subj_id,
                            "predicate": "HAS_CLAIM_TEXT",
                            "object_node_id": None,
                            "object_literal": text,
                            "object_literal_type": "text",
                            "scope_text": text,
                            "speaker_or_author": spk,
                            "canonical_status": "candidate",
                            "canonical_eligible": 0,
                            "retrieval_eligible": 0,
                            "validation_state": "unreviewed",
                            "currentness_state": "current",
                            "superseded_by_assertion_id": None,
                            "confidence_score": 0.91,
                            "primary_source_id": src_id,
                            "qualifiers_json": json.dumps(qual, sort_keys=True),
                            "approval_reference": None,
                        }
                    )
                    ins_sql = "INSERT INTO assertion (" + ",".join(cols) + ") VALUES (" + ",".join("?" for _ in cols) + ")"
                    conn.execute(ins_sql, [r[c] for c in cols])
                    ae = dict(ae_tmpl)
                    ae.update({"assertion_id": aid, "evidence_id": ev_id, "evidence_relationship": "supports"})
                    ae_sql = "INSERT OR IGNORE INTO assertion_evidence (" + ",".join(ae_cols) + ") VALUES (" + ",".join("?" for _ in ae_cols) + ")"
                    conn.execute(ae_sql, [ae[c] for c in ae_cols])

                stale_cand_qual = {
                    "dual_pipeline_candidate_id": "cand-clean-1",
                    "dual_pipeline_candidate_type": "assertion",
                    "dual_pipeline_disposition": "CANDIDATE",
                    "retrieval_kind": "assertion",
                    "answer_use": "candidate_hypothesis",
                    "fact_promotion_eligible": False,
                }
                _seed("asrt-fp-clean", "Alex Sanchez verified the deterministic rule threshold.", "Alex Sanchez", stale_cand_qual)
                _seed("asrt-fp-placeholder", "Placeholder header text.", "Participants", stale_cand_qual)
                _seed("asrt-fp-contradict", "**Alex Sanchez:** This belongs to Alex.", "Eduardo More", stale_cand_qual)
                _seed("asrt-fp-undiarized", "Yeah I think you know we're blocked on access.", None, stale_cand_qual)
                _seed("asrt-fp-explicit-false", "Explicitly non-promotable draft.", "Alex Sanchez", {"fact_promotion_eligible": False})
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()

            res = fast_promote_candidates(sandbox_db, source_id=src_id, min_confidence=0.75, approval_reference="FP-W1-001")
            self.assertIn("asrt-fp-clean", res.promoted_ids)
            for bad_id in ("asrt-fp-placeholder", "asrt-fp-contradict", "asrt-fp-undiarized", "asrt-fp-explicit-false"):
                self.assertNotIn(bad_id, res.promoted_ids)

            rconn = connect_sqlite(sandbox_db, readonly=True)
            try:
                q_after = rconn.execute("SELECT qualifiers_json FROM assertion WHERE assertion_id = 'asrt-fp-clean'").fetchone()[0]
                self.assertFalse(has_stale_candidate_qualifiers(q_after))
                parsed = json.loads(q_after)
                self.assertEqual("APPROVED", parsed["dual_pipeline_disposition"])
                self.assertTrue(parsed["fact_promotion_eligible"])
                self.assertEqual("factual_answer", parsed["answer_use"])
            finally:
                rconn.close()

    def test_recovery_script_blocks_blanket_approval_cross_source_evidence_and_special_cli(self) -> None:
        """Category 5: Recovery helpers never stamp blanket approval, attach LIMIT 1 evidence, or allow ad-hoc CLI flags."""
        self.assertIsNone(
            dispatch_special_hygiene_cli(
                types.SimpleNamespace(
                    split_0925_google_lens=False,
                    promote_remaining_scopes=False,
                    promote_five_projects_sources=False,
                    promote_neet_sources=False,
                    promote_sherpa_sources=False,
                    promote_nexus_sources=False,
                    promote_dociq_sources=False,
                    promote_jarvis_sources=False,
                )
            )
        )
        for flag in ("promote_nexus_sources", "promote_remaining_scopes", "split_0925_google_lens"):
            with self.assertRaises(PermissionError):
                dispatch_special_hygiene_cli(types.SimpleNamespace(**{flag: True}))

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            db_path = Path(tmp) / "rec.sqlite"
            conn = connect_sqlite(db_path)
            try:
                conn.execute(
                    """CREATE TABLE assertion (
                        assertion_id TEXT PRIMARY KEY, canonical_status TEXT, canonical_eligible INTEGER,
                        retrieval_eligible INTEGER, currentness_state TEXT, superseded_by_assertion_id TEXT,
                        validation_state TEXT, approval_reference TEXT, primary_source_id TEXT, updated_at TEXT
                    )"""
                )
                conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT)")
                conn.execute(
                    "CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT, created_at TEXT)"
                )
                conn.execute("INSERT INTO evidence_item VALUES ('ev-other-src', 'src-other')")
                conn.execute(
                    "INSERT INTO assertion VALUES ('asrt-needs-rev', 'canonical', 1, 0, 'current', NULL, 'needs_review', NULL, 'src-other', '2026-10-01')"
                )
                conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-needs-rev', 'ev-other-src', 'supports', '2026-10-01')")
                tables = {"assertion", "evidence_item", "assertion_evidence"}
                _normalize_assertion_and_search_states(conn, tables, "2026-10-29T00:00:00Z")
                row = conn.execute(
                    "SELECT validation_state, approval_reference, retrieval_eligible FROM assertion WHERE assertion_id = 'asrt-needs-rev'"
                ).fetchone()
                self.assertEqual(("needs_review", None, 0), tuple(row))

                with self.assertRaisesRegex(RuntimeError, "no supporting evidence_item"):
                    _attach_synthesized_evidence(conn, "asrt-synth-1", "src-missing", True, True, "2026-10-29T00:00:00Z")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
