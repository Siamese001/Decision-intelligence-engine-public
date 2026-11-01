"""End-to-end pipeline test for Output 03 signal-to-GraphDB mutation lifecycle.

Exercises:
1. Normalization of Output 03 entity_upserts and relationship_signals.
2. Generation and validation of entity intent profiles.
3. Identity resolution into CREATE_NEW_ENTITY_PROPOSAL / AUTO_LINK_EXACT.
4. Autonomous candidate disposition admission without quarantine trap.
5. Graph change set construction containing create_entity and add_relationship operations.
6. Governed graph writer execution against an isolated database.
7. Post-mutation SQLite verification of node, node_state, and assertion tables.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope


class TestOutput3GraphMutationE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        candidate_graphs = [
            ROOT / "graph" / "nee_graph.sqlite",
            ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite",
            ROOT.parent.parent / "graph" / "nee_graph.sqlite",
        ]
        self.orig_graph = next((g for g in candidate_graphs if g.is_file()), None)
        if not self.orig_graph:
            self.skipTest(f"Active baseline graph not found among {candidate_graphs}")

        self.db_path = Path(self.tmp_dir.name) / "test_isolated_graph.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_full_pipeline_output3_to_graph_mutation_e2e(self) -> None:
        """Verify full lifecycle from Output 03 payload to positive GraphDB mutations."""
        conn_pre = sqlite3.connect(self.db_path)
        node_count_pre = conn_pre.execute("SELECT COUNT(*) FROM node").fetchone()[0]
        assertion_count_pre = conn_pre.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
        conn_pre.close()

        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-e2e-mutation-test",
            "meeting": {
                "meeting_id": "meeting-e2e-mutation-test",
                "title": "Platform Scaling Architecture Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-e2e-mutation-test.md",
                "source_hash": "c" * 64,
                "attendees": ["Jen Bricker", "Carlos Salazar"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-jen-bricker",
                    "reported_name": "Jen Bricker",
                    "email": "jen.bricker@consulting.example.com",
                    "role_or_org": "Technical Services Dept",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "technical",
                            "text": "The Nexus high-throughput stream router processes cross-department workloads.",
                            "locator": {"kind": "timestamp", "value": "01:23"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [
                {
                    "action_id": "act-01",
                    "action": "Complete schema registry audit for the new stream router.",
                    "reported_owner": "Carlos Salazar",
                    "due_date": "2026-09-18",
                    "status": "planned",
                    "dependency": None,
                    "requested_follow_up": None,
                    "locator": {"kind": "timestamp", "value": "02:45"},
                }
            ],
            "adapter": {
                "name": "nee-evidence-envelope-intake",
                "version": "1.0.0",
            },
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "system:nexus_stream_router",
                            "type": "system",
                            "name": "Nexus Stream Router",
                            "description": "High-throughput stream router for cross-department workloads",
                            "mutation": "CREATE",
                        },
                        {
                            "id": "stakeholder:jen_bricker",
                            "type": "stakeholder",
                            "name": "Jen Bricker",
                            "description": "Technical Services Lead",
                            "mutation": "CONFIRM",
                        },
                        {
                            "id": "org:partner_advisory_build",
                            "type": "org",
                            "name": "PartnerAdvisory Build",
                            "description": "Engineering delivery team",
                            "mutation": "CREATE",
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-01",
                            "from_id": "stakeholder:jen_bricker",
                            "to_id": "system:nexus_stream_router",
                            "predicate": "OWNS_OR_LEADS",
                            "evidence_ids": ["stmt-01"],
                        },
                        {
                            "signal_id": "sig-02",
                            "from_id": "system:nexus_stream_router",
                            "to_id": "org:partner_advisory_build",
                            "predicate": "AFFILIATED_WITH",
                            "evidence_ids": ["stmt-01"],
                        },
                    ],
                }
            },
        }

        # Step 1: Seal and validate intake package
        sealed_pkg = seal_artifact(package)
        validate_artifact(sealed_pkg)

        # Step 2: Normalize
        bundle = normalize_evidence_envelope(sealed_pkg, run_context="e2e_run")
        validate_artifact(bundle)
        self.assertGreaterEqual(len(bundle["candidates"]), 6)
        profiles = bundle["extensions"].get("com.partner_advisory.nee.entity-intent-profiles", {})
        self.assertGreaterEqual(len(profiles), 3)

        # Step 3: Identity resolution
        resolutions = resolve_bundle(bundle, self.db_path)
        self.assertEqual(len(resolutions), len([m for c in bundle["candidates"] for m in c["entity_mentions"]]))

        # Step 4: Routing
        routes = [
            {
                "subject_id": c["candidate_id"],
                "intake_route": (
                    "NEW_RELATIONSHIP"
                    if c["candidate_type"] == "architecture_relationship"
                    else "NEW_ENTITY"
                    if c["candidate_type"] == "entity_mention"
                    else "ACTION_ITEM"
                    if c["candidate_type"] == "action"
                    else "NEW_ASSERTION"
                ),
                "reason_codes": [],
            }
            for c in bundle["candidates"]
        ]

        # Step 5: Candidate dispositions
        dispositions = build_dispositions(bundle, routes, resolutions)
        for d in dispositions:
            c = next(cand for cand in bundle["candidates"] if cand["candidate_id"] == d["candidate_id"])
            if c["candidate_type"] in ("entity_mention", "architecture_relationship"):
                self.assertTrue(d["identity_resolved"])
                self.assertIn(d["disposition"], ("CANDIDATE", "APPROVED"))

        # Step 6: Setup governance approval and build change set
        import json
        conn_gov = sqlite3.connect(self.db_path)
        conn_gov.execute(
            """INSERT OR REPLACE INTO governance_approval
               (approval_id, approval_reference, approval_type, scope_json,
                proposal_sha256, approval_statement_sha256, approved_at,
                status, approved_by, release_version, active, created_at)
               VALUES (?, ?, 'ingestion_promotion', ?,
                       ?, ?, '2026-09-10T10:00:00Z',
                       'approved', 'operator', '1.0.0', 1, '2026-09-10T10:00:00Z')""",
            (
                "approval-e2e-run",
                "approval-e2e-run",
                json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}),
                "0" * 64,
                "0" * 64,
            ),
        )
        conn_gov.commit()
        conn_gov.close()

        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=self.db_path,
        )
        self.assertIsNotNone(change_set)
        operations = change_set["operations"]
        self.assertGreater(len(operations), 0)

        create_ops = [op for op in operations if op["operation"] == "create_entity"]
        rel_ops = [op for op in operations if op["operation"] == "add_relationship"]
        self.assertGreaterEqual(len(create_ops), 2, "Expected create_entity ops for new entities")
        self.assertGreaterEqual(len(rel_ops), 2, "Expected add_relationship ops")

        # Step 7: Apply change set with GovernedGraphWriter
        change_set["status"] = "approved"
        change_set["approval_id"] = "approval-e2e-run"
        change_set = seal_artifact(change_set)
        validate_artifact(change_set)

        writer = GovernedGraphWriter(self.db_path)
        receipts = writer.apply(change_set)
        self.assertGreater(len(receipts), 0)

        # Step 8: Verify database state after mutation
        conn_post = sqlite3.connect(self.db_path)
        node_count_post = conn_post.execute("SELECT COUNT(*) FROM node").fetchone()[0]
        assertion_count_post = conn_post.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]

        # Verify positive table deltas
        node_delta = node_count_post - node_count_pre
        assertion_delta = assertion_count_post - assertion_count_pre
        self.assertGreater(node_delta, 0, f"Expected positive node delta, got {node_delta}")
        self.assertGreater(assertion_delta, 0, f"Expected positive assertion delta, got {assertion_delta}")

        # Verify specific nodes exist
        router_node = conn_post.execute(
            "SELECT node_id, node_type, canonical_name, status, active FROM node WHERE canonical_name='Nexus Stream Router'"
        ).fetchone()
        self.assertIsNotNone(router_node, "Nexus Stream Router must be inserted into node table")
        self.assertEqual(router_node[1], "technical_anchor")
        self.assertEqual(router_node[3], "candidate")
        self.assertEqual(router_node[4], 1)

        build_node = conn_post.execute(
            "SELECT node_id, node_type, canonical_name, status, active FROM node WHERE canonical_name='PartnerAdvisory Build'"
        ).fetchone()
        self.assertIsNotNone(build_node, "PartnerAdvisory Build must be inserted into node table")
        self.assertEqual(build_node[1], "org")
        self.assertEqual(build_node[3], "candidate")

        # Verify node_state entries exist
        router_state = conn_post.execute(
            "SELECT node_id, approval_state, currentness_state FROM node_state WHERE node_id=?",
            (router_node[0],),
        ).fetchone()
        self.assertIsNotNone(router_state)
        self.assertEqual(router_state[1], "candidate")
        self.assertEqual(router_state[2], "current")

        conn_post.close()

    def test_pipeline_run_auto_mutate_on_pass_triggers_publication(self) -> None:
        """Verify that CanonicalAgenticPipeline triggers graph publication when auto_mutate_on_pass=True."""
        from unittest.mock import patch

        from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline

        pipeline = CanonicalAgenticPipeline(provider_type="offline")

        with patch("dual_pipeline.canonical_pipeline.pipeline.publish_graph_if_requested") as mock_publish:
            mock_publish.return_value = {"status": "COMPLETE", "operations": 5}

            transcript_file = Path(self.tmp_dir.name) / "test_transcript.txt"
            transcript_file.write_text(
                "Jen Bricker [00:01:00]: The Nexus stream router handles all inter-department event dispatching.\n"
                "Carlos Salazar [00:02:00]: Confirmed, that is our primary routing engine.\n",
                encoding="utf-8",
            )
            out_dir = Path(self.tmp_dir.name) / "output"

            result = pipeline.run(
                input_path=str(transcript_file),
                output_dir=str(out_dir),
                auto_mutate_on_pass=True,
                reviewer="Test Reviewer",
                metadata={
                    "meeting_name": "Test Pipeline Run",
                    "time": "10:00 AM - 10:02 AM ET",
                    "date": "09/10/2026",
                    "date_filename": "09-10-2026",
                    "project": "project_nexus",
                    "attendee_roles_confirmed_by_operator": True,
                    "hitl_role_resolutions": {
                        "Jen Bricker": "Technical Services Dept",
                        "Carlos Salazar": "Stream Lead",
                    },
                    "roster": [
                        "Jen Bricker (Technical Services Dept)",
                        "Carlos Salazar (Stream Lead)",
                    ],
                },
            )

            self.assertTrue(mock_publish.called, "publish_graph_if_requested must be called")
            call_kwargs = mock_publish.call_args.kwargs
            self.assertTrue(
                call_kwargs.get("mutate_graph"),
                "Expected mutate_graph=True when auto_mutate_on_pass=True and judge passes",
            )
            self.assertIn("graph_mutation", result)

    def test_pipeline_run_mutation_without_approval_fails_closed(self) -> None:
        """Verify that attempting to mutate graph without an approved proposal fails closed."""
        from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline

        pipeline = CanonicalAgenticPipeline(provider_type="offline")

        transcript_file = Path(self.tmp_dir.name) / "test_unapproved.txt"
        transcript_file.write_text(
            "Jen Bricker [00:01:00]: The Nexus stream router handles all inter-department event dispatching.\n"
            "Carlos Salazar [00:02:00]: Confirmed, that is our primary routing engine.\n",
            encoding="utf-8",
        )
        out_dir = Path(self.tmp_dir.name) / "output_unapproved"

        with self.assertRaises(RuntimeError) as ctx:
            pipeline.run(
                input_path=str(transcript_file),
                output_dir=str(out_dir),
                mutate_graph=True,
                reviewer="Carlos Salazar",
                metadata={
                    "meeting_name": "Test Unapproved Run",
                    "time": "10:00 AM - 10:02 AM ET",
                    "date": "09/10/2026",
                    "date_filename": "09-10-2026",
                    "project": "project_nexus",
                    "attendee_roles_confirmed_by_operator": True,
                    "hitl_role_resolutions": {
                        "Jen Bricker": "Technical Services Dept",
                        "Carlos Salazar": "Stream Lead",
                    },
                    "roster": [
                        "Jen Bricker (Technical Services Dept)",
                        "Carlos Salazar (Stream Lead)",
                    ],
                },
            )
        self.assertIn("AUTHORIZATION_DENIED_MISSING_APPROVAL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
