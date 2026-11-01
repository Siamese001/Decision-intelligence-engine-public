#!/usr/bin/env python3
"""Wave 3 end-to-end lifecycle tests for 13-Agent Hygiene Council, HITL clusters, and cloned remediation."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file
from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload

WORKTREE_ROOT = Path(__file__).resolve().parents[1]
SIDE_SCHEMA_SQL = (WORKTREE_ROOT / "dual_pipeline" / "index_core" / "schema.sql").read_text(encoding="utf-8")

_REGISTRY_TABLES = (
    "governance_approval",
    "node_type_registry",
    "edge_type_registry",
    "edge_type_domain_range",
    "assertion_predicate_registry",
    "controlled_value_registry",
    "property_registry",
)


def _clone_production_schema_and_registries(dst_graph: Path) -> None:
    """Create an empty graph DB with full production DDL, triggers, and controlled registries."""
    src_graph = ROOT / "graph" / "nee_graph.sqlite"
    sconn = connect_sqlite(src_graph, readonly=True)
    dconn = connect_sqlite(dst_graph)
    try:
        dconn.execute("PRAGMA foreign_keys = OFF")
        for (sql,) in sconn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '%_fts_%' ORDER BY rowid"
        ).fetchall():
            dconn.execute(sql)
        for tbl in _REGISTRY_TABLES:
            sel_sql = "SELECT * FROM " + tbl
            rows = sconn.execute(sel_sql).fetchall()
            if rows:
                ins_sql = "INSERT INTO " + tbl + " VALUES (" + ",".join("?" for _ in rows[0]) + ")"
                dconn.executemany(ins_sql, [tuple(r) for r in rows])
        dconn.execute("UPDATE node_type_registry SET example_node_id = NULL")
        for obj_type in ("index", "trigger"):
            for (sql,) in sconn.execute(
                "SELECT sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL ORDER BY rowid",
                (obj_type,),
            ).fetchall():
                dconn.execute(sql)
        dconn.commit()
        dconn.execute("PRAGMA foreign_keys = ON")
        assert dconn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        dconn.close()
        sconn.close()


def _seed_wave3_runtime_root(rt_root: Path) -> tuple[Path, Path]:
    gen_id = "generation-w3-baseline-0001"
    gen_dir = rt_root / "generations" / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = gen_dir / "graph.sqlite"
    sidecar_path = gen_dir / "sidecar.sqlite"
    _clone_production_schema_and_registries(graph_path)

    now_iso = "2026-10-29T12:00:00Z"
    app_ref = "AD-NEE-LIVE-ONBOARDING-CHARTERS-2026-09-25"
    conn = connect_sqlite(graph_path)
    try:
        conn.execute(
            "INSERT INTO source "
            "(source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at) "
            "VALUES ('src-w3-mtg', 'dual_pipeline_intake', 'Project Nexus Rules Alignment', '2026-10-29', 5, "
            "'derivative_internal', 'archive://w3/01', '', ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO source_version "
            "(source_version_id, source_id, version_number, source_fingerprint, content_hash, source_date_normalized, source_date_precision, source_date_status, authority_rank, scope_text, posture_text, created_at) "
            "VALUES ('sv-w3-01', 'src-w3-mtg', 1, ?, ?, '2026-10-29', 'day', 'exact', 5, '', '', ?)",
            ("a" * 64, "b" * 64, now_iso),
        )
        conn.execute(
            "INSERT INTO extraction_run "
            "(extraction_run_id, algorithm_version, baseline_graph_sha256, input_manifest_sha256, started_at, completed_at, status, deterministic) "
            "VALUES ('er-w3-01', 'v1', ?, ?, ?, ?, 'completed', 1)",
            ("0" * 64, "1" * 64, now_iso, now_iso),
        )
        nodes = [
            ("person_eduardo_more", "stakeholder", "Eduardo (Ed) Juan More", "Eduardo (Ed) Juan More", "Atlas Lead / Rule Specialist."),
            ("entity-08c09f0f6f80a785d75d5f4abc19", "stakeholder", "Ed Moore", "Ed Moore", "Engineering Director."),
            ("workstream_project_nexus", "workstream", "Project Nexus", "Project Nexus", "Rules modernization workstream."),
            ("cnode_w3_synthetic", "workstream", "Synthetic Fixture Node", "Synthetic Fixture Node", "Test leak."),
        ]
        for nid, ntype, cname, dname, desc in nodes:
            conn.execute(
                "INSERT INTO node "
                "(node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'canonical', 1, ?, ?)",
                (nid, ntype, cname, dname, desc, now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO node_state "
                "(node_id, lifecycle_state, validation_state, currentness_state, approval_state, rag_state, resolution_state, retrieval_eligibility, publication_state, review_state, updated_at) "
                "VALUES (?, 'active', 'accepted_legacy', 'current', 'approved', 'not_applicable', 'resolved', 'eligible', 'internal', 'reviewed', ?)",
                (nid, now_iso),
            )
            conn.execute(
                "INSERT INTO search_document "
                "(search_document_id, object_type, object_id, title, body, canonical_status, currentness_state, retrieval_eligible, source_id, updated_at) "
                "VALUES (?, 'node', ?, ?, ?, 'canonical', 'current', 1, 'src-w3-mtg', ?)",
                (f"sd-node-{nid}", nid, cname, f"{cname}: {desc}", now_iso),
            )

        stale_quals = json.dumps(
            {
                "dual_pipeline_candidate_id": "cand-stale-01",
                "dual_pipeline_candidate_type": "assertion",
                "dual_pipeline_disposition": "CANDIDATE",
                "retrieval_kind": "assertion",
                "answer_use": "candidate_hypothesis",
                "fact_promotion_eligible": False,
            }
        )
        citation_quals = json.dumps(
            {
                "dual_pipeline_candidate_id": "cand-cit-01",
                "dual_pipeline_candidate_type": "source_chunk",
                "retrieval_kind": "evidence_span",
                "answer_use": "citation_only",
                "fact_promotion_eligible": False,
            }
        )
        spec_rows = [
            # (aid, subj, text, spk, quals, make_canonical)
            ("asrt-w3-ed-moore-claim", "entity-08c09f0f6f80a785d75d5f4abc19", "Ed Moore reviewed deterministic validation schedules.", "Alex Sanchez", "{}", True),
            ("asrt-w3-placeholder", "workstream_project_nexus", "Participants listed on roster header.", "Participants", "{}", True),
            ("asrt-w3-missing-spk", "workstream_project_nexus", "Yeah I think you know we should tighten the rule.", None, "{}", True),
            ("asrt-w3-inline-mismatch", "workstream_project_nexus", "**Alex Sanchez:** Deterministic checks execute first.", "Eduardo (Ed) Juan More", "{}", True),
            ("asrt-w3-citation-leak", "workstream_project_nexus", "Raw citation excerpt leaked into canonical.", "Alex Sanchez", citation_quals, True),
            ("asrt-w3-stale-quals", "person_eduardo_more", "Eduardo More confirmed rule coverage exceeded ninety-eight percent.", "Alex Sanchez", stale_quals, True),
            ("asrt-w3-cand-undiarized", "workstream_project_nexus", "Yeah I mean we're missing the upstream check.", None, "{}", False),
            ("asrt-w3-cand-clean", "workstream_project_nexus", "Alex Sanchez proposed adding schema regression gates.", "Alex Sanchez", "{}", False),
        ]
        for idx, (aid, subj, text, spk, quals, make_canonical) in enumerate(spec_rows, start=1):
            ev_id = f"ev-w3-{idx:02d}"
            conn.execute(
                "INSERT INTO evidence_item "
                "(evidence_id, evidence_fingerprint, source_id, source_version_id, locator_kind, locator_value, direct_quote, evidence_directness, source_authority_rank, extraction_confidence, location_status, created_at) "
                "VALUES (?, ?, 'src-w3-mtg', 'sv-w3-01', 'section', '## Notes', ?, 'direct', 5, 0.93, 'exact', ?)",
                (ev_id, f"{idx:064x}", text, now_iso),
            )
            conn.execute(
                "INSERT INTO assertion "
                "(assertion_id, assertion_fingerprint, assertion_type, subject_node_id, predicate, object_literal, object_literal_type, "
                "scope_text, atomicity_state, qualifiers_json, origin_type, primary_source_id, extraction_run_id, extraction_method, speaker_or_author, "
                "recorded_at, date_precision, date_status, confidence_score, confidence_label, validation_state, currentness_state, "
                "resolution_state, canonical_status, canonical_eligible, retrieval_eligible, materiality, approval_reference, "
                "classification, allowed_audiences_json, hitl_required, legacy_record_type, legacy_record_ids_json, created_at, updated_at) "
                "VALUES (?, ?, 'decision', ?, 'HAS_CLAIM_TEXT', ?, 'text', ?, 'atomic', ?, 'human_source', 'src-w3-mtg', 'er-w3-01', 'llm', ?, "
                "?, 'day', 'exact', 0.93, 'high', 'unreviewed', 'current', 'resolved', 'candidate', 1, 0, 'high', ?, "
                "'internal', '[\"internal_consulting\"]', 0, 'claim', '[]', ?, ?)",
                (aid, f"{idx + 100:064x}", subj, text, text, quals, spk, now_iso, app_ref, now_iso, now_iso),
            )
            conn.execute(
                "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at) "
                "VALUES (?, ?, 'supports', 0, ?)",
                (aid, ev_id, now_iso),
            )
            conn.execute(
                "INSERT INTO search_document "
                "(search_document_id, object_type, object_id, title, body, canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
                "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, 'src-w3-mtg', ?, ?)",
                (aid, aid, text[:60], text, aid, now_iso),
            )
            if make_canonical:
                conn.execute(
                    "UPDATE assertion SET canonical_status = 'canonical', validation_state = 'accepted_legacy', "
                    "canonical_eligible = 1, retrieval_eligible = 1, updated_at = ? WHERE assertion_id = ?",
                    (now_iso, aid),
                )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    sconn = connect_sqlite(sidecar_path)
    try:
        sconn.executescript(SIDE_SCHEMA_SQL)
        g_sha = sha256_file(graph_path)
        for k, v in (
            ("generation_id", gen_id),
            ("graph_sha256", g_sha),
            ("bound_graph_sha256", g_sha),
            ("encoding_fingerprint_digest", "fp-w3-test"),
        ):
            sconn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (k, v))
        sconn.commit()
        sconn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        sconn.close()

    g_sha = sha256_file(graph_path)
    s_sha = sha256_file(sidecar_path)
    active_pair = {
        "generation_id": gen_id,
        "previous_generation_id": None,
        "graph_path": f"generations/{gen_id}/graph.sqlite",
        "sidecar_path": f"generations/{gen_id}/sidecar.sqlite",
        "graph_sha256": g_sha,
        "sidecar_sha256": s_sha,
        "encoding_fingerprint_digest": "fp-w3-test",
        "activated_at": now_iso,
    }
    (gen_dir / "pair_manifest.json").write_text(json.dumps(active_pair, indent=2) + "\n", encoding="utf-8")
    (rt_root / "active_pair.json").write_text(json.dumps(active_pair, indent=2) + "\n", encoding="utf-8")
    return graph_path, sidecar_path


class Wave3HygieneCouncilAndRemediationTests(unittest.TestCase):
    def test_end_to_end_council_clustering_question_validation_and_cloned_remediation(self) -> None:
        """Full Wave 3 lifecycle: CLI discovery -> HITL question validation -> cloned promotion -> 5-check proof."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            rt_root = Path(tmp) / "runtime_root"
            base_graph, _ = _seed_wave3_runtime_root(rt_root)
            base_sha = sha256_file(base_graph)
            pkg_path = Path(tmp) / "run-10-29-2026" / "hygiene_package.json"
            receipt_path = Path(tmp) / "run-10-29-2026" / "wave3_receipt.json"

            # 1. Verify GraphHygienePanel passes live conn into Agent 9 and filters defective candidates
            panel_pkg = GraphHygienePanel().run_full_audit(target_graph=base_graph)
            self.assertEqual(base_sha, sha256_file(base_graph))
            promo_findings = [f for f in panel_pkg.findings if f.proposed_fix == "batch_promote_candidates"]
            self.assertEqual(1, len(promo_findings))
            self.assertIn("asrt-w3-cand-clean", promo_findings[0].entity_references)
            self.assertNotIn("asrt-w3-cand-undiarized", promo_findings[0].entity_references)

            # 2. Run real CLI discovery entrypoint (run_hygiene_agent_cron.py)
            cron_cmd = [
                sys.executable,
                str(WORKTREE_ROOT / "tools" / "run_hygiene_agent_cron.py"),
                "--graph",
                str(base_graph),
                "--output",
                str(pkg_path),
            ]
            cron_res = subprocess.run(cron_cmd, cwd=WORKTREE_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(0, cron_res.returncode, msg=cron_res.stderr)
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))

            clusters_by_id = {c["cluster_id"]: c for c in pkg["hitl_clusters"]}
            for expected_cid in (
                "CLUSTER-01D-SPEAKER-ATTRIBUTION-HYGIENE",
                "CLUSTER-01B-LEAKED-CANDIDATES",
                "CLUSTER-01E-QUALIFIER-METADATA-SYNC",
                "CLUSTER-02A-STAKEHOLDER-NAMES",
                "CLUSTER-06-STAGING-PROMOTIONS",
                "CLUSTER-08-GOVERNANCE-AUDIT",
            ):
                self.assertIn(expected_cid, clusters_by_id)

            # Every generated HITL cluster question must pass validate_question_payload and pre-tool hook checks
            from tools.check_ask_user_question import verify_speaker_context

            for cid, c in clusters_by_id.items():
                q_payload = c["ready_to_ask_question"]
                vres = validate_question_payload(q_payload["question"], q_payload["options"])
                self.assertTrue(vres.is_valid, msg=f"{cid} failed question validation: {vres.errors}")
                self.assertIsNone(verify_speaker_context(q_payload["question"]), msg=f"{cid} triggered speaker assignment hook")

            # Verify Agent 9 elected person_eduardo_more as survivor over entity-08c09f0f6f80a785d75d5f4abc19
            c2a_finding = clusters_by_id["CLUSTER-02A-STAKEHOLDER-NAMES"]["findings"][0]
            self.assertEqual("person_eduardo_more", c2a_finding["raw_data"]["survivor_node_id"])
            self.assertEqual("entity-08c09f0f6f80a785d75d5f4abc19", c2a_finding["raw_data"]["retired_node_id"])

            # 3. Run real CLI cloned remediation entrypoint (apply_graph_hygiene_remediation.py)
            approved = ",".join(
                [
                    "CLUSTER-01D-SPEAKER-ATTRIBUTION-HYGIENE",
                    "CLUSTER-01B-LEAKED-CANDIDATES",
                    "CLUSTER-01E-QUALIFIER-METADATA-SYNC",
                    "CLUSTER-02A-STAKEHOLDER-NAMES",
                    "CLUSTER-08-GOVERNANCE-AUDIT",
                ]
            )
            apply_cmd = [
                sys.executable,
                str(WORKTREE_ROOT / "tools" / "apply_graph_hygiene_remediation.py"),
                "--package",
                str(pkg_path),
                "--runtime-root",
                str(rt_root),
                "--promote-generation",
                "--approved-clusters",
                approved,
                "--excluded-clusters",
                "CLUSTER-06-STAGING-PROMOTIONS",
                "--receipt",
                str(receipt_path),
            ]
            apply_res = subprocess.run(apply_cmd, cwd=WORKTREE_ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(0, apply_res.returncode, msg=f"STDOUT:\n{apply_res.stdout}\nSTDERR:\n{apply_res.stderr}")

            # Base generation must never be mutated in-place
            self.assertEqual(base_sha, sha256_file(base_graph))

            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual("PASS", receipt["terminal_outcome"])
            self.assertEqual("PASS", receipt["physical_proof"]["overall"])
            self.assertEqual("PASS", receipt["itemized_hitl_contract"]["status"])
            self.assertEqual(1.0, receipt["itemized_hitl_contract"]["coverage_ratio"])
            self.assertEqual(0, receipt["itemized_hitl_contract"]["failed_count"])

            # 4. Independent fresh read-only SQLite verification on promoted generation
            promoted_pair = json.loads((rt_root / "active_pair.json").read_text(encoding="utf-8"))
            self.assertNotEqual("generation-w3-baseline-0001", promoted_pair["generation_id"])
            prom_graph = (rt_root / promoted_pair["graph_path"]).resolve()

            rconn = connect_sqlite(prom_graph, readonly=True)
            try:
                # Ed Moore merged into person_eduardo_more
                ed_ret = rconn.execute(
                    "SELECT active, status FROM node WHERE node_id = 'entity-08c09f0f6f80a785d75d5f4abc19'"
                ).fetchone()
                self.assertEqual((0, "merged"), (ed_ret["active"], ed_ret["status"]))
                redir = rconn.execute(
                    "SELECT to_node_id, active FROM entity_redirect WHERE from_node_id = 'entity-08c09f0f6f80a785d75d5f4abc19'"
                ).fetchone()
                self.assertEqual(("person_eduardo_more", 1), (redir["to_node_id"], redir["active"]))

                # Synthetic node archived
                syn_node = rconn.execute("SELECT active FROM node WHERE node_id = 'cnode_w3_synthetic'").fetchone()
                self.assertEqual(0, syn_node["active"])

                # Speaker-defective claims quarantined without rewriting speaker_or_author or scope_text
                expected_spk = {
                    "asrt-w3-placeholder": "Participants",
                    "asrt-w3-missing-spk": None,
                    "asrt-w3-inline-mismatch": "Eduardo (Ed) Juan More",
                }
                for aid, orig_spk in expected_spk.items():
                    arow = rconn.execute(
                        "SELECT canonical_status, canonical_eligible, retrieval_eligible, validation_state, speaker_or_author "
                        "FROM assertion WHERE assertion_id = ?",
                        (aid,),
                    ).fetchone()
                    self.assertEqual("candidate", arow["canonical_status"])
                    self.assertEqual(0, arow["canonical_eligible"])
                    self.assertEqual(0, arow["retrieval_eligible"])
                    self.assertEqual("needs_review", arow["validation_state"])
                    self.assertEqual(orig_spk, arow["speaker_or_author"])
                    sd_row = rconn.execute(
                        "SELECT search_rowid, retrieval_eligible FROM search_document WHERE assertion_id = ?",
                        (aid,),
                    ).fetchone()
                    self.assertEqual(0, sd_row["retrieval_eligible"])
                    fts_docsize_cnt = rconn.execute(
                        "SELECT COUNT(*) FROM search_fts_docsize WHERE id = ?",
                        (sd_row["search_rowid"],),
                    ).fetchone()[0]
                    self.assertEqual(0, fts_docsize_cnt)
                fts_match_ids = {
                    r[0]
                    for r in rconn.execute(
                        "SELECT object_id FROM search_fts WHERE search_fts MATCH 'Rules OR Atlas OR Nexus OR Speaker'"
                    ).fetchall()
                }
                for aid in expected_spk:
                    self.assertNotIn(aid, fts_match_ids)

                # Stale candidate qualifiers synced on clean canonical assertion
                sq_row = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible, qualifiers_json FROM assertion WHERE assertion_id = 'asrt-w3-stale-quals'"
                ).fetchone()
                self.assertEqual("canonical", sq_row["canonical_status"])
                self.assertEqual(1, sq_row["retrieval_eligible"])
                synced_q = json.loads(sq_row["qualifiers_json"])
                self.assertEqual("APPROVED", synced_q["dual_pipeline_disposition"])
                self.assertEqual("factual_answer", synced_q["answer_use"])
                self.assertTrue(synced_q["fact_promotion_eligible"])

                # Excluded Cluster 06 candidate remained candidate and recorded HYGIENE_FINDING_EXCLUDED
                excl_cand = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible FROM assertion WHERE assertion_id = 'asrt-w3-cand-clean'"
                ).fetchone()
                self.assertEqual(("candidate", 0), (excl_cand["canonical_status"], excl_cand["retrieval_eligible"]))
                exc_cnt = rconn.execute(
                    "SELECT COUNT(*) FROM graph_quality_exception WHERE exception_type = 'HYGIENE_FINDING_EXCLUDED' AND active = 1"
                ).fetchone()[0]
                self.assertGreaterEqual(exc_cnt, 1)
            finally:
                rconn.close()

    def test_diarized_transcript_turn_recovery_and_undiarized_hold(self) -> None:
        """Verify diarized transcript turns recover speakers while undiarized transcripts and single-token fragments stay held."""
        from dual_pipeline.graph_maintenance_core.speaker_hygiene_rules import (
            _candidate_row_passes_promotion_safety,
            resolve_candidate_provenance_speaker,
        )

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            diarized_md = Path(tmp) / "01_Diarized_Session_hitl_resolved_transcript.md"
            diarized_md.write_text(
                "# Diarized Session\n\n### [00:17:28]\n\n**Amit Ayer:** So Atlas has 100% auditability on what failed and why.\n",
                encoding="utf-8",
            )
            undiarized_md = Path(tmp) / "01_Undiarized_Session_hitl_resolved_transcript.md"
            undiarized_md.write_text(
                "# Undiarized Session\n\n> **Diarization Limitation Notice:** metadata identifies a WAV audio source and explicitly classifies it as **UNDIARIZED**.\n\n### [00:00:58]\n\n**Jen Pan:** We are only keeping the records we can manually verify.\n",
                encoding="utf-8",
            )

            diarized_row = {
                "assertion_id": "asrt-diarized-01",
                "predicate": "HAS_CLAIM_TEXT",
                "scope_text": ": So Atlas has 100% auditability on what failed and why.",
                "speaker_or_author": "Forensic Context",
                "subject_node_id": "workstream_atlas_pilot_alr",
                "source_type": "dual_pipeline_intake",
                "source_name": "20260930 Lens Pipeline Review Sign Off",
                "cold_archive_ref": str(diarized_md),
                "transcript_timestamp": "00:17:28",
                "qualifiers_json": "{}",
            }
            spk, is_rec = resolve_candidate_provenance_speaker(diarized_row, {}, {})
            self.assertEqual(("Amit Ayer", True), (spk, is_rec))
            self.assertTrue(_candidate_row_passes_promotion_safety(diarized_row, {}, {}))

            undiarized_row = {
                "assertion_id": "asrt-undiarized-01",
                "predicate": "HAS_CLAIM_TEXT",
                "scope_text": "We are only keeping the records we can manually verify.",
                "speaker_or_author": "Forensic transcript participant roster",
                "subject_node_id": "workstream_lens",
                "source_type": "dual_pipeline_intake",
                "source_name": "20261001 Lens Sprint Review",
                "cold_archive_ref": str(undiarized_md),
                "transcript_timestamp": "[00:00:58]",
                "qualifiers_json": "{}",
            }
            u_spk, u_rec = resolve_candidate_provenance_speaker(undiarized_row, {}, {})
            self.assertFalse(u_rec)
            self.assertFalse(_candidate_row_passes_promotion_safety(undiarized_row, {}, {}))

            degen_row = dict(diarized_row, assertion_id="asrt-degen-01", scope_text="ESTABLISHED")
            self.assertFalse(_candidate_row_passes_promotion_safety(degen_row, {}, {}))


if __name__ == "__main__":
    unittest.main()
