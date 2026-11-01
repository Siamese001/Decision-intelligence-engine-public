#!/usr/bin/env python3
"""Tests for Wave 2: Deterministic Sub-Second Backlog Fast-Promotion (nee promote-fast)."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cli.registry import get_default_registry
from dual_pipeline.core import ROOT, connect_sqlite, digest, sha256_file
from dual_pipeline.fast_promotion import (
    ensure_staged_candidates_promoted,
    fast_promote_candidates,
    has_unpromoted_candidates,
)
from dual_pipeline.graph_mutation_proof import GraphMutationProofError


def _seed_test_candidates(graph_path: Path) -> dict[str, str]:
    """Seed grounded, duplicate, and adversarial candidate assertions into an isolated sandbox DB."""
    conn = connect_sqlite(graph_path)
    try:
        node_row = conn.execute("SELECT node_id FROM node WHERE active = 1 LIMIT 3").fetchall()
        subj_id = str(node_row[0][0])
        obj_id = str(node_row[1][0]) if len(node_row) > 1 else subj_id
        inactive_id = str(node_row[2][0]) if len(node_row) > 2 else subj_id
        conn.execute("UPDATE node SET active = 0 WHERE node_id = ?", (inactive_id,))
        ev_row = conn.execute("SELECT evidence_id, source_id FROM evidence_item LIMIT 1").fetchone()
        ev_id = str(ev_row[0])
        src_id = str(ev_row[1])
        tmpl = dict(conn.execute("SELECT * FROM assertion LIMIT 1").fetchone())
        cols = list(tmpl.keys())
        col_list = ",".join(cols)
        ph_list = ",".join("?" for _ in cols)
        ae_tmpl = dict(conn.execute("SELECT * FROM assertion_evidence WHERE evidence_relationship='supports' LIMIT 1").fetchone())
        ae_cols = list(ae_tmpl.keys())
        ae_col_list = ",".join(ae_cols)
        ae_ph_list = ",".join("?" for _ in ae_cols)
        app_ref = str(conn.execute("SELECT approval_reference FROM governance_approval WHERE status='approved' AND active=1 LIMIT 1").fetchone()[0])

        def _ins_asrt(
            aid: str,
            *,
            subj: str = subj_id,
            pred: str = "HAS_CLAIM_TEXT",
            obj: str | None = None,
            text: str = "Valid grounded claim for fast promotion.",
            atype: str = "extracted_claim",
            conf: float = 0.80,
            qual: str = "{}",
            with_ev: bool = True,
            status: str = "candidate",
        ) -> None:
            row_dict = dict(tmpl)
            row_dict.update(
                {
                    "assertion_id": aid,
                    "assertion_fingerprint": digest(aid),
                    "assertion_type": atype,
                    "subject_node_id": subj,
                    "predicate": pred,
                    "object_node_id": obj,
                    "object_literal": None if obj else text,
                    "object_literal_type": None if obj else "text",
                    "scope_text": text,
                    "canonical_status": "candidate",
                    "canonical_eligible": 0,
                    "retrieval_eligible": 0,
                    "validation_state": "unreviewed",
                    "currentness_state": "current",
                    "superseded_by_assertion_id": None,
                    "superseded_at": None,
                    "confidence_score": conf,
                    "primary_source_id": src_id,
                    "qualifiers_json": qual,
                    "approval_reference": None,
                }
            )
            asrt_sql = "INSERT INTO assertion (" + col_list + ") VALUES (" + ph_list + ")"
            conn.execute(asrt_sql, [row_dict[c] for c in cols])
            if with_ev:
                ae_row = dict(ae_tmpl)
                ae_row["assertion_id"] = aid
                ae_row["evidence_id"] = ev_id
                ae_row["evidence_relationship"] = "supports"
                ae_sql = "INSERT OR IGNORE INTO assertion_evidence (" + ae_col_list + ") VALUES (" + ae_ph_list + ")"
                conn.execute(ae_sql, [ae_row[c] for c in ae_cols])
            if status == "canonical":
                conn.execute(
                    "UPDATE assertion SET canonical_status='canonical', canonical_eligible=1, retrieval_eligible=1, validation_state='accepted_legacy', approval_reference=? WHERE assertion_id=?",
                    (app_ref, aid),
                )

        # 1. Valid grounded candidate at 0.75 boundary
        _ins_asrt("asrt-fast-valid-075", text="Orion sprint canonical promotion latency is under 1 second.", conf=0.75)
        # 2. Duplicate of #1 with trailing meeting suffix and [6-hex] tag (lower confidence 0.76 vs 0.88)
        _ins_asrt("asrt-fast-dup-winner", text="Delta sidecar reuses unchanged BGE-M3 embeddings in Q4 Review.", conf=0.88)
        _ins_asrt("asrt-fast-dup-loser", text="Delta sidecar reuses unchanged BGE-M3 embeddings [a1b2c3]", conf=0.76)
        # 3. Valid hypothesis at 0.85 boundary
        _ins_asrt("asrt-fast-hyp-085", text="Hypothesis that batch size 64 saturates MPS.", atype="hypothesis", conf=0.85)
        # 4. Adversarial blocked candidates:
        # 4a. Claim below 0.75 (0.74)
        _ins_asrt("asrt-fast-low-074", text="Low confidence claim at 0.74.", conf=0.74)
        # 4b. Hypothesis below 0.85 (0.84)
        _ins_asrt("asrt-fast-hyp-084", text="Speculative hypothesis at 0.84.", atype="hypothesis", conf=0.84)
        # 4c. Open question
        _ins_asrt("asrt-fast-open-q", text="Who owns the legacy archive?", atype="open_question", conf=0.95)
        # 4d. No supporting evidence
        _ins_asrt("asrt-fast-no-ev", text="Ungrounded claim without evidence.", conf=0.95, with_ev=False)
        # 4e. Inactive subject node
        _ins_asrt("asrt-fast-inactive-subj", subj=inactive_id, text="Claim on inactive node.", conf=0.95)
        # 4f. Citation-only / A2A fact_promotion_eligible=false qualifier
        _ins_asrt("asrt-fast-ineligible-qual", text="Citation only span.", conf=0.95, qual='{"answer_use": "citation_only", "fact_promotion_eligible": false}')
        # 4g. Mutex cardinality conflict against an existing canonical REPORTS_TO assertion
        _ins_asrt("asrt-fast-canon-role", subj=subj_id, pred="REPORTS_TO", obj=obj_id, text="Canonical manager", conf=0.95, status="canonical")
        _ins_asrt("asrt-fast-mutex-conflict", subj=subj_id, pred="REPORTS_TO", obj=subj_id, text="Conflicting manager", conf=0.95)
        # 5. Additive HAS_ROLE_IN and structured card with placeholder speaker must promote
        _ins_asrt("asrt-fast-additive-role", subj=subj_id, pred="HAS_ROLE_IN", obj=subj_id, text="Additive role", conf=0.90)

        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"source_id": src_id, "subject_id": subj_id}
    finally:
        conn.close()


class FastCandidatePromotionTests(unittest.TestCase):
    def test_fast_promote_candidates_with_dedup_and_5point_proof(self) -> None:
        """Grounded candidates promote in <2s with normalized dedup, FTS sync, and 5-point physical proof."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_path = Path(tmp)
            graph_path = tmp_path / "graph.sqlite"
            shutil.copy2(graph_src, graph_path)
            seeded = _seed_test_candidates(graph_path)

            # Dry run must not mutate the database
            before_sha = sha256_file(graph_path)
            dry_res = fast_promote_candidates(
                graph_path,
                source_id=seeded["source_id"],
                min_confidence=0.75,
                dry_run=True,
            )
            self.assertEqual("DRY_RUN", dry_res.status)
            self.assertEqual(before_sha, sha256_file(graph_path))
            self.assertGreaterEqual(dry_res.promoted_count, 3)
            self.assertGreaterEqual(dry_res.deduped_count, 1)

            # Live fast promotion on sandbox DB
            res = fast_promote_candidates(
                graph_path,
                source_id=seeded["source_id"],
                min_confidence=0.75,
                approval_reference="FAST-PROMOTE-TEST-001",
            )
            self.assertEqual("PROMOTED", res.status)
            self.assertLess(res.duration_ms, 2000.0)
            self.assertEqual("PASS", res.physical_proof["overall"])
            for chk in ("file_changed", "raw_bytes", "integrity", "readback", "row_delta"):
                self.assertEqual("PASS", res.physical_proof["checks"][chk])

            self.assertIn("asrt-fast-valid-075", res.promoted_ids)
            self.assertIn("asrt-fast-dup-winner", res.promoted_ids)
            self.assertIn("asrt-fast-hyp-085", res.promoted_ids)
            self.assertIn("asrt-fast-additive-role", res.promoted_ids)
            self.assertIn("asrt-fast-dup-loser", res.deduped_ids)

            for blocked_id in (
                "asrt-fast-low-074",
                "asrt-fast-hyp-084",
                "asrt-fast-open-q",
                "asrt-fast-no-ev",
                "asrt-fast-inactive-subj",
                "asrt-fast-ineligible-qual",
                "asrt-fast-mutex-conflict",
            ):
                self.assertNotIn(blocked_id, res.promoted_ids)

            conn = connect_sqlite(graph_path, readonly=True)
            try:
                winner = conn.execute(
                    "SELECT canonical_status, canonical_eligible, retrieval_eligible, approval_reference "
                    "FROM assertion WHERE assertion_id = 'asrt-fast-dup-winner'"
                ).fetchone()
                self.assertEqual(("canonical", 1, 1, "FAST-PROMOTE-TEST-001"), tuple(winner))

                loser = conn.execute(
                    "SELECT canonical_status, canonical_eligible, retrieval_eligible, currentness_state, superseded_by_assertion_id "
                    "FROM assertion WHERE assertion_id = 'asrt-fast-dup-loser'"
                ).fetchone()
                self.assertEqual(
                    ("historical_candidate", 0, 0, "historical", "asrt-fast-dup-winner"),
                    tuple(loser),
                )

                sd_row = conn.execute(
                    "SELECT canonical_status, retrieval_eligible FROM search_document WHERE assertion_id = 'asrt-fast-valid-075'"
                ).fetchone()
                self.assertIsNotNone(sd_row)
                self.assertEqual(("canonical", 1), tuple(sd_row))
            finally:
                conn.close()

    def test_cli_promote_fast_and_generation_promotion(self) -> None:
        """CLI parser + registry dispatch + promote_generation=True atomically updates active_pair.json."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            rt_root = Path(tmp) / "runtime"
            gen_dir = rt_root / "generations" / "generation-base001"
            gen_dir.mkdir(parents=True, exist_ok=True)
            g_path = gen_dir / "graph.sqlite"
            s_path = gen_dir / "sidecar.sqlite"
            shutil.copy2(graph_src, g_path)
            _seed_test_candidates(g_path)

            sconn = connect_sqlite(s_path)
            try:
                sconn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
                sconn.execute("INSERT INTO metadata (key, value) VALUES ('generation_id', 'generation-base001')")
                sconn.commit()
                sconn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                sconn.close()

            active_pair = {
                "generation_id": "generation-base001",
                "graph_path": "generations/generation-base001/graph.sqlite",
                "sidecar_path": "generations/generation-base001/sidecar.sqlite",
                "graph_sha256": sha256_file(g_path),
                "sidecar_sha256": sha256_file(s_path),
            }
            (rt_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")

            parser = build_parser()
            args = parser.parse_args(
                [
                    "promote-fast",
                    "--runtime-root",
                    str(rt_root),
                    "--min-confidence",
                    "0.75",
                    "--approval-ref",
                    "CLI-FAST-001",
                    "--promote-generation",
                ]
            )
            handler = get_default_registry().get_handler("promote-fast")
            self.assertIsNotNone(handler)
            out = handler(args)
            self.assertEqual("PROMOTED", out["status"])
            self.assertIsNotNone(out["generation_id"])
            self.assertNotEqual("generation-base001", out["generation_id"])

            updated_pair = json.loads((rt_root / "active_pair.json").read_text(encoding="utf-8"))
            self.assertEqual(out["generation_id"], updated_pair["generation_id"])
            self.assertEqual("generation-base001", updated_pair["previous_generation_id"])

    def test_adversarial_malformed_inputs_and_proof_failure_rollback(self) -> None:
        """Categories 2 & 4: Invalid parameters fail closed; proof failure restores exact pre-mutation bytes."""
        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_path = Path(tmp)
            for bad_conf in (0.0, -0.5, 1.05):
                with self.assertRaises(ValueError):
                    fast_promote_candidates(graph_src, min_confidence=bad_conf)
            with self.assertRaises(FileNotFoundError):
                fast_promote_candidates(tmp_path / "nonexistent.sqlite")
            with self.assertRaises(ValueError):
                fast_promote_candidates(graph_src, approval_reference="   ")

            g_path = tmp_path / "rollback_test.sqlite"
            shutil.copy2(graph_src, g_path)
            _seed_test_candidates(g_path)
            before_sha = sha256_file(g_path)

            with patch(
                "dual_pipeline.graph_writer_core.fast_promotion.prove",
                return_value={"overall": "FAIL", "checks": [{"check": "raw_bytes", "status": "FAIL"}]},
            ):
                with self.assertRaises(GraphMutationProofError):
                    fast_promote_candidates(g_path, min_confidence=0.75)

            self.assertEqual(before_sha, sha256_file(g_path))

    def test_pre_query_dirty_check_and_auto_promotion_guard(self) -> None:
        """Pre-query guard promotes eligible staged rows before graphdb_read_query and runs in <2ms when clean."""
        import time
        from tools.mcp_graphdb_server import graphdb_read_query

        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_path = Path(tmp)
            graph_path = tmp_path / "pre_query_guard.sqlite"
            shutil.copy2(graph_src, graph_path)

            # 1. Seed staged candidates; dirty check detects promotable rows
            _seed_test_candidates(graph_path)
            self.assertTrue(has_unpromoted_candidates(graph_path))

            # 2. Calling graphdb_read_query (include_candidates=False) auto-promotes eligible staged rows
            raw_out = graphdb_read_query(
                "SELECT assertion_id, canonical_status, canonical_eligible FROM assertion WHERE assertion_id = 'asrt-fast-valid-075'",
                db_path=str(graph_path),
                include_candidates=False,
            )
            payload = json.loads(raw_out)
            self.assertEqual(1, payload["count"])
            self.assertEqual("canonical", payload["rows"][0]["canonical_status"])
            self.assertEqual(1, payload["rows"][0]["canonical_eligible"])

            # 3. Clean graph check on the promoted DB (even with skipped open_question rows) completes in <2ms
            t0 = time.perf_counter()
            self.assertFalse(has_unpromoted_candidates(graph_path))
            resolved_path, second_res = ensure_staged_candidates_promoted(graph_path, runtime_root=tmp_path)
            clean_ms = (time.perf_counter() - t0) * 1000.0
            self.assertEqual(graph_path.resolve(), resolved_path)
            self.assertIsNone(second_res)
            self.assertLess(clean_ms, 2.0)

    def test_all_pipelines_promote_nodes_and_assertions_to_canonical(self) -> None:
        """All pipelines (Inline GovernedGraphWriter & FastPromotion) promote nodes, node_state, and assertions to canonical."""
        from dual_pipeline.core import utc_now
        from dual_pipeline.uwg_core.node_promotion import promote_candidate_endpoint_nodes

        graph_src = ROOT / "graph" / "nee_graph.sqlite"
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_path = Path(tmp)
            graph_path = tmp_path / "node_and_assertion_promo.sqlite"
            shutil.copy2(graph_src, graph_path)

            now = utc_now()
            conn = connect_sqlite(graph_path)
            try:
                ev_row = conn.execute("SELECT evidence_id, source_id FROM evidence_item LIMIT 1").fetchone()
                ev_id, src_id = str(ev_row[0]), str(ev_row[1])
                tmpl = dict(conn.execute("SELECT * FROM assertion LIMIT 1").fetchone())
                cols = list(tmpl.keys())
                ae_tmpl = dict(conn.execute("SELECT * FROM assertion_evidence WHERE evidence_relationship='supports' LIMIT 1").fetchone())
                ae_cols = list(ae_tmpl.keys())

                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO node (node_id, node_type, canonical_name, status, active, created_at, updated_at) "
                    "VALUES ('entity-cand-node-01', 'workstream', 'Vanguard Canonical Promotion Workstream', 'candidate', 1, ?, ?)",
                    (now, now),
                )
                conn.execute(
                    "INSERT INTO node_state (node_id, lifecycle_state, validation_state, currentness_state, approval_state, "
                    "rag_state, resolution_state, retrieval_eligibility, publication_state, review_state, "
                    "legacy_status_text, intentional_orphan, intentional_orphan_reason, updated_at) "
                    "VALUES ('entity-cand-node-01', 'active', 'needs_review', 'current', 'candidate', 'amber', "
                    "'resolved', 'eligible', 'internal', 'reviewed', 'candidate', 0, NULL, ?)",
                    (now,),
                )
                row_dict = dict(tmpl)
                row_dict.update({
                    "assertion_id": "asrt-hist-cand-01",
                    "assertion_fingerprint": digest({"id": "asrt-hist-cand-01"}),
                    "subject_node_id": "entity-cand-node-01",
                    "predicate": "HAS_CLAIM_TEXT",
                    "object_node_id": None,
                    "scope_text": "Vanguard workstream achieved canonical promotion.",
                    "assertion_type": "extracted_claim",
                    "primary_source_id": src_id,
                    "confidence_score": 0.90,
                    "validation_state": "needs_review",
                    "currentness_state": "historical",
                    "canonical_status": "historical_candidate",
                    "canonical_eligible": 0,
                    "retrieval_eligible": 0,
                    "superseded_by_assertion_id": None,
                    "superseded_at": None,
                    "approval_reference": None,
                    "qualifiers_json": "{}",
                })
                ins_asrt_sql = "INSERT INTO assertion (" + ",".join(cols) + ") VALUES (" + ",".join("?" for _ in cols) + ")"
                conn.execute(ins_asrt_sql, [row_dict[c] for c in cols])
                ae_row = dict(ae_tmpl)
                ae_row["assertion_id"] = "asrt-hist-cand-01"
                ae_row["evidence_id"] = ev_id
                ins_ae_sql = "INSERT INTO assertion_evidence (" + ",".join(ae_cols) + ") VALUES (" + ",".join("?" for _ in ae_cols) + ")"
                conn.execute(ins_ae_sql, [ae_row[c] for c in ae_cols])
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                conn.close()

            self.assertTrue(has_unpromoted_candidates(graph_path))
            res = fast_promote_candidates(graph_path, min_confidence=0.75)
            self.assertEqual("PROMOTED", res.status)
            self.assertIn("asrt-hist-cand-01", res.promoted_ids)
            self.assertIn("entity-cand-node-01", res.promoted_node_ids)
            self.assertEqual("PASS", res.physical_proof["overall"])
            self.assertFalse(has_unpromoted_candidates(graph_path))

            rconn = connect_sqlite(graph_path, readonly=True)
            try:
                nrow = rconn.execute("SELECT status FROM node WHERE node_id='entity-cand-node-01'").fetchone()
                self.assertEqual("canonical", nrow["status"])
                nsrow = rconn.execute(
                    "SELECT validation_state, approval_state, rag_state, retrieval_eligibility FROM node_state WHERE node_id='entity-cand-node-01'"
                ).fetchone()
                self.assertEqual("accepted_legacy", nsrow["validation_state"])
                self.assertEqual("approved", nsrow["approval_state"])
                self.assertEqual("green", nsrow["rag_state"])
                self.assertEqual("eligible", nsrow["retrieval_eligibility"])
                sdrow = rconn.execute(
                    "SELECT canonical_status, retrieval_eligible FROM search_document WHERE object_type='node' AND object_id='entity-cand-node-01'"
                ).fetchone()
                self.assertIsNotNone(sdrow)
                self.assertEqual("canonical", sdrow["canonical_status"])
                self.assertEqual(1, sdrow["retrieval_eligible"])
            finally:
                rconn.close()


if __name__ == "__main__":
    unittest.main()
