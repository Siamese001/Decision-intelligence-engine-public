"""
tests/test_tier1_dedup_universal_guarantee.py

Universal Ingestion Guarantee Test Layer for Tier 1 GraphDB Deduplication.
Covers:
  - Wave 1: TestPreMutationDedupCorrectness (P6 explicit type, P7 empty desc, P8 ID format HITL)
  - Wave 2/3: TestGateInversionRegression (RC1 mutate_graph=False, RC3 phantom import, receipt contract)
  - Wave 4: 12-surface parametric ingestion matrix, concurrent ledger stress, post-merge gates
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.graph_scout_core.scout import GraphScout
from storage.database import sqlite3


def _init_test_graph_db(db_path: Path) -> None:
    """Initialize minimal schema for testing GraphScout."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                description TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT '2026-09-01T00:00:00Z',
                updated_at TEXT DEFAULT '2026-09-01T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                claim_text TEXT,
                valid_from TEXT DEFAULT '2026-09-01T00:00:00Z',
                recorded_at TEXT DEFAULT '2026-09-01T00:00:00Z',
                confidence_score REAL DEFAULT 0.90,
                currentness_state TEXT DEFAULT 'current',
                canonical_status TEXT DEFAULT 'canonical',
                active INTEGER DEFAULT 1
            )
            """
        )
        conn.execute("DELETE FROM node")
        conn.execute("DELETE FROM assertion")


        # Seed existing nodes
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, description, active) "
            "VALUES ('org_partner_advisory', 'PartnerAdvisory', 'org', '', 1)"
        )
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, description, active) "
            "VALUES ('person_jen', 'Jen', 'person', 'Lead Enterprise Architect', 1)"
        )
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, description, active) "
            "VALUES ('role_stakeholder_lead', 'Stakeholder Lead', 'stakeholder', 'Oversees delivery', 1)"
        )
        conn.commit()


# ── Wave 1: Pre-Mutation Correctness Unit Tests ───────────────────────────────

class TestPreMutationDedupCorrectness:
    """Verifies in-memory pre-mutation deduplication correctness (Wave 1)."""

    def test_id_format_divergence_forces_hitl(self, tmp_path: Path) -> None:
        """P8: When proposed uses colon ('org:partner_advisory') and existing uses underscore ('org_partner_advisory'),

        scout MUST force hitl_required=True to prevent unadjudicated taxonomy forks.
        """
        db_path = tmp_path / "test_graph.sqlite"
        _init_test_graph_db(db_path)

        scout = GraphScout(graph_path=db_path)
        proposed = [
            {
                "id": "org:partner_advisory",
                "canonical_name": "PartnerAdvisory",
                "type": "org",
                "description": "Global business and technology consulting firm",
            }
        ]

        with sqlite3.connect(str(db_path)) as conn:
            findings = scout.scan_proposed_entities(proposed, conn)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.node_id_a == "org:partner_advisory"
        assert finding.node_id_b == "org_partner_advisory"
        assert finding.similarity_score >= 0.99
        # P8 assertion: hitl_required MUST be True due to ID format divergence
        assert finding.hitl_required is True, (
            "P8 violation: colon vs underscore ID format divergence must force hitl_required=True"
        )

    def test_empty_description_does_not_inflate_confidence(self, tmp_path: Path) -> None:
        """P7: When both descriptions are empty, desc_sim must be 0.0, NOT 1.0.

        Confidence merge formula: c_merge = min(0.98, max(0.50, round(0.70*sim + 0.30*desc_sim, 2)))
        For sim=1.0 and desc_sim=0.0: c_merge = 0.70.
        If empty descriptions falsely evaluated to 1.0, c_merge would inflate to 1.00.
        """
        db_path = tmp_path / "test_graph.sqlite"
        _init_test_graph_db(db_path)

        scout = GraphScout(graph_path=db_path)
        # Existing org_partner_advisory has empty description
        proposed = [
            {
                "id": "org:partner_advisory_blank",
                "canonical_name": "PartnerAdvisory",
                "type": "org",
                "description": "",  # Empty description
            }
        ]

        with sqlite3.connect(str(db_path)) as conn:
            findings = scout.scan_proposed_entities(proposed, conn)

        assert len(findings) == 1
        finding = findings[0]
        # P7 assertion: confidence_merge must reflect desc_sim=0.0 (<= 0.72)
        assert finding.confidence_merge <= 0.72, (
            f"P7 violation: confidence_merge ({finding.confidence_merge}) was inflated by empty description"
        )

    def test_explicit_type_field_preferred_over_id_prefix(self, tmp_path: Path) -> None:
        """P6: Explicit 'type' field in entity dict must override ID prefix parsing."""
        db_path = tmp_path / "test_graph.sqlite"
        _init_test_graph_db(db_path)

        scout = GraphScout(graph_path=db_path)
        # Entity has colon ID 'role:lead' but explicit type 'stakeholder'
        # Existing node has type 'stakeholder'
        proposed = [
            {
                "id": "role:lead",
                "canonical_name": "Stakeholder Lead",
                "type": "stakeholder",  # Explicit type matches existing node
                "description": "Oversees delivery workstream",
            }
        ]

        with sqlite3.connect(str(db_path)) as conn:
            findings = scout.scan_proposed_entities(proposed, conn)

        assert len(findings) == 1
        finding = findings[0]
        # P6 assertion: node_type_a must match explicit type 'stakeholder', not parsed prefix 'role'
        assert finding.node_type_a == "stakeholder", (
            f"P6 violation: expected explicit type 'stakeholder', got '{finding.node_type_a}'"
        )


# ── Wave 2: Decoupled Scoped Scout Regression Tests ───────────────────────────

class TestGateInversionRegression:
    """Verifies that scout execution is decoupled from mutate_graph=True (RC1 fix)."""

    def test_scout_fires_when_mutate_graph_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """RC1 Regression Guard: Scout must execute and produce 15-field breakdown table

        even in pre-mutation / read-only runs.
        """
        monkeypatch.setenv("NEE_ALLOW_TEST_RUNTIME_ROOT", str(tmp_path))
        from dual_pipeline.transcript_pipeline_core.evals_coordinator import (
            _SIDECAR_TABLES,
            _SQLITE_TABLES,
            _execute_phase3_scoped_scout,
        )

        db_path = tmp_path / "test_graph.sqlite"
        _init_test_graph_db(db_path)

        # Set up mock governed runtime root with active_pair.json
        runtime_root = tmp_path / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        ap_file = runtime_root / "active_pair.json"
        ap_file.write_text(json.dumps({"graph_path": str(db_path)}), encoding="utf-8")

        output_dir = tmp_path / "deliverables"
        output_dir.mkdir(parents=True, exist_ok=True)

        proposed = [
            {
                "id": "org:partner_advisory_client",
                "canonical_name": "PartnerAdvisory",
                "type": "org",
                "description": "Technology consulting",
            }
        ]

        result = _execute_phase3_scoped_scout(
            written={},
            output_dir=output_dir,
            canon_runtime_root=runtime_root,
            dir_id="rc1-regression-directive",
            meta={"date_filename": "09-24-2026_test"},
            proposed_entities=proposed,
        )

        assert result["ran"] is True, f"Scout failed to run: {result}"
        assert result["findings_count"] >= 1, "Expected duplicate finding against org_partner_advisory"
        assert result["proposals_emitted"] >= 1, "Expected proposal emitted"

        # P11: Verify graph_mutation_breakdown.md was written and contains all 15 tables
        breakdown_file = output_dir / "graph_mutation_breakdown.md"
        assert breakdown_file.is_file(), "graph_mutation_breakdown.md was not written"
        content = breakdown_file.read_text(encoding="utf-8")
        assert "## Tier 1 Scoped Scout — GraphDB Mutation Breakdown" in content
        for tbl in _SQLITE_TABLES:
            assert f"`{tbl}`" in content, f"Missing table '{tbl}' in breakdown table"
        for tbl in _SIDECAR_TABLES:
            assert f"`{tbl}`" in content, f"Missing sidecar table '{tbl}' in breakdown table"

    def test_path_traversal_rejected_gracefully(self, tmp_path: Path) -> None:
        """P5: Runtime roots outside .local/dual_pipeline must be rejected via SecurityError

        without crashing the pipeline.
        """
        from dual_pipeline.transcript_pipeline_core.evals_coordinator import (
            _execute_phase3_scoped_scout,
        )

        output_dir = tmp_path / "deliverables"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Pass a rogue path outside governed boundary
        rogue_root = Path("/tmp/rogue_runtime_root")

        result = _execute_phase3_scoped_scout(
            written={},
            output_dir=output_dir,
            canon_runtime_root=rogue_root,
            dir_id="security-test",
            meta={},
            proposed_entities=[{"id": "org:test", "name": "Test"}],
        )

        assert result["ran"] is False
        assert result["skipped_reason"] is not None
        assert "SecurityError" in result["skipped_reason"], (
            f"Expected SecurityError in skipped_reason, got: {result['skipped_reason']}"
        )

    def test_dynamic_timeout_formula(self, tmp_path: Path) -> None:
        """P2: Dynamic timeout budget must scale with active node count."""
        from dual_pipeline.transcript_pipeline_core.evals_coordinator import (
            _compute_dynamic_timeout,
        )

        db_path = tmp_path / "test_graph.sqlite"
        _init_test_graph_db(db_path)

        timeout_sec = _compute_dynamic_timeout(db_path)
        # With 3 seeded nodes: budget_ms = min(500, max(100, 50 + 3*0.4)) = 100ms = 0.10s
        assert 0.09 <= timeout_sec <= 0.50, f"Expected timeout in [0.10, 0.50], got {timeout_sec}"


# ── Wave 3: CanonicalAgenticPipeline Stage EVALS Integration Tests ────────────

def _mock_pipeline_run(
    tmp_path: Path,
    modality: CanonicalModality = CanonicalModality.MEETING_TRANSCRIPT,
    mutate_graph: bool = False,
    source_text: str = "Test meeting discussion.",
) -> Dict[str, Any]:
    """Helper to execute CanonicalAgenticPipeline with hermetic mocks."""
    from unittest.mock import patch

    from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult

    out_p = tmp_path / "output"
    out_p.mkdir(parents=True, exist_ok=True)

    input_file = tmp_path / "input.txt"
    input_file.write_text(source_text, encoding="utf-8")

    meta_synth = {
        "meeting_name": "Test_Wave3", "date": "09-24-2026", "time": "09:00 AM - 09:30 AM ET",
        "attendees": "Amit Ayer, Reid Schenk", "classification": "internal",
    }
    from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
    offsynth = OfflineTranscriptSynthesizer()
    out1 = offsynth.synthesize_forensic("", f"<meeting_metadata>{json.dumps(meta_synth)}</meeting_metadata><raw_transcript>{source_text}</raw_transcript>")
    out2 = offsynth.synthesize_recap("", f"<meeting_metadata>{json.dumps(meta_synth)}</meeting_metadata><forensic_transcript>{out1}</forensic_transcript>")

    f3_graph = {
        "schema_version": "5.7",
        "meeting_id": "meeting-test-wave3",
        "classification": "internal",
        "audiences": ["internal_consulting"],
        "evidence_index": {},
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory_e2e", "name": "PartnerAdvisory", "canonical_name": "PartnerAdvisory", "type": "org", "description": "Consulting firm"},
                {"id": "person:amit_ayer", "name": "Amit Ayer", "canonical_name": "Amit Ayer", "type": "person", "description": "Consultant"},
                {"id": "person:reid_schenk", "name": "Reid Schenk", "canonical_name": "Reid Schenk", "type": "person", "description": "Engineer"},
            ],
            "assertion_upserts": [],
        },
    }
    synth = SynthesisResult(
        output_1=out1,
        output_2=out2,
        output_3=json.dumps(f3_graph),
    )

    from dual_pipeline.release_policy import build_artifact_manifest, compute_release_evaluation_digest

    j_rep = {"decision": "PASS", "status": "PASS", "rubric_version": "2.0"}
    eval_digest = compute_release_evaluation_digest(j_rep)
    j_rep["evaluation_receipt_digest"] = eval_digest
    manifest = build_artifact_manifest(
        f1_text=synth.output_1,
        f2_text=synth.output_2,
        f3_text=synth.output_3,
        model="mock-judge",
        reasoning_effort="medium",
        judge_eval_digest=eval_digest,
    )
    final_decision = {
        "status": "PASS",
        "final_pass": True,
        "authoritative_write_authorized": True,
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "artifact_manifest": manifest,
    }

    qa_eval = QAEvaluationResult(
        passed=True,
        qa_report={"output_1_pass": True, "output_2_pass": True, "output_3_pass": True},
        judge_report=j_rep,
        final_decision=final_decision,
        authoritative_write_authorized=True,
    )

    def _fake_build_intake_pkg(staging_dir: Path, *args: Any, **kwargs: Any) -> Path:
        pkg = staging_dir / "intake_package.json"
        pkg_data = {
            "package_version": "1.0.0",
            "source": "test_wave3",
            "status": "SEALED",
            "meeting": {
                "meeting_id": "meeting-test-wave3",
                "source_hash": hashlib.sha256(b"Test meeting discussion.").hexdigest(),
                "classification": "internal",
                "audiences": ["internal_consulting"],
            },
        }
        pkg.write_text(json.dumps(pkg_data), encoding="utf-8")
        return pkg

    with (
        patch("dual_pipeline.canonical_pipeline.pipeline.run_synthesis_loop", return_value=(1, synth, qa_eval)),
        patch("dual_pipeline.canonical_pipeline.pipeline.assert_no_u0_hitl_bypass"),
        patch("dual_pipeline.transcript_pipeline_core.caching.check_idempotent_meeting_run", return_value=None),
        patch("dual_pipeline.canonical_pipeline.pipeline.build_and_validate_intake_package", side_effect=_fake_build_intake_pkg),
    ):
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        import uuid

        return pipeline.run(
            input_path=str(input_file),
            output_dir=out_p,
            mutate_graph=mutate_graph,
            directive_id=f"dir-test-wave3-{uuid.uuid4().hex[:8]}",
            reviewer="Amit Ayer",
            metadata={
                "allow_duplicate": True,
                "reviewer": "Amit Ayer",
                "meeting_name": "Test_Wave3",
                "date": "09-24-2026",
                "time": "09:00 AM - 09:30 AM ET",
                "attendees": "Amit Ayer, Reid Schenk",
                "attendees_confirmed_by_operator": True,
                "attendee_confirmation_receipt": {
                    "status": "RESOLVED_HITL",
                    "confirmed_by": "Amit Ayer",
                    "attendees": ["Amit Ayer", "Reid Schenk"],
                    "subject_sha256": "subj_tier1_universal_09252026",
                },
            },
        )


class TestPipelineEvalsStageIntegration:
    """Verifies CanonicalAgenticPipeline Stage EVALS scout integration (Wave 3)."""

    def test_pipeline_receipt_contains_tier1_scout_contract(self, tmp_path: Path) -> None:
        """Verifies pipeline_execution_receipt.json always includes tier1_scout."""
        result = _mock_pipeline_run(tmp_path=tmp_path, mutate_graph=False)
        assert result["status"] == "COMPLETE"

        receipt_file = Path(result["execution_receipt"])
        assert receipt_file.is_file()
        receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))

        evals_stage = receipt_data.get("stages", {}).get("EVALS", {})
        details = evals_stage.get("details", {})
        assert "tier1_scout" in details, "Missing 'tier1_scout' field in EVALS stage details"
        scout_info = details["tier1_scout"]
        assert "ran" in scout_info
        assert "findings_count" in scout_info
        assert "proposals_emitted" in scout_info
        assert "elapsed_ms" in scout_info

    def test_mutate_graph_true_calls_publish_release_to_graph(self, tmp_path: Path) -> None:
        """RC3 Regression Guard: mutate_graph=True must invoke publish_release_to_graph

        without raising ImportError for nonexistent execute_graph_publication.
        """
        from unittest.mock import MagicMock, patch

        mock_publish = MagicMock(return_value={"graph_mutated": True, "generation": "gen-123", "status": "COMPLETE"})
        with patch(
            "dual_pipeline.hitl_governance_core.mutation_authorization.validate_mutation_authorization",
            return_value={"status": "AUTHORIZED", "approved_by": "Amit Ayer"},
        ), patch(
            "dual_pipeline.canonical_pipeline.publish_helper._assert_authoritative_mutation_preconditions",
        ), patch(
            "dual_pipeline.release_policy.verify_authoritative_release_gate",
        ), patch(
            "dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph",
            mock_publish,
        ):
            result = _mock_pipeline_run(tmp_path=tmp_path, mutate_graph=True)
            assert result["status"] == "COMPLETE"
            mock_publish.assert_called_once()
            assert result["graph_mutation"]["graph_mutated"] is True
            assert result["graph_mutation"]["generation"] == "gen-123"
            assert result["graph_mutation"]["status"] == "COMPLETE"

    def test_finalize_release_execution_runs_scout_unconditionally(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify that finalize_release_execution runs scout when mutate_graph=False."""
        monkeypatch.setenv("NEE_ALLOW_TEST_RUNTIME_ROOT", str(tmp_path))
        from dual_pipeline.transcript_pipeline_core.evals_coordinator import (
            _SIDECAR_TABLES,
            _SQLITE_TABLES,
        )
        from dual_pipeline.transcript_pipeline_core.release_runner import finalize_release_execution

        db_path = tmp_path / "test_graph.sqlite"
        _init_test_graph_db(db_path)

        runtime_root = tmp_path / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        ap_file = runtime_root / "active_pair.json"
        ap_file.write_text(json.dumps({"graph_path": str(db_path)}), encoding="utf-8")

        out_p = tmp_path / "deliverables"
        out_p.mkdir(parents=True, exist_ok=True)

        f3_path = out_p / "03_test_graph.json"
        f3_payload = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory_uncond", "canonical_name": "PartnerAdvisory", "type": "org", "description": "Consulting"}
                ]
            }
        }
        f3_path.write_text(json.dumps(f3_payload), encoding="utf-8")

        written = {"03_graph": str(f3_path)}
        from unittest.mock import MagicMock
        tracker = MagicMock()
        receipt_file = out_p / "pipeline_execution_receipt.json"
        receipt_file.write_text("{}", encoding="utf-8")
        mock_receipt = MagicMock()
        mock_receipt.write_to_dir.return_value = receipt_file
        tracker.build_receipt.return_value = mock_receipt

        receipt_str = finalize_release_execution(
            tracker=tracker,
            written=written,
            package_path=out_p / "intake_pkg.json",
            mutate_graph=False,
            metadata={"meeting_name": "Unconditional_Test", "date": "09-24-2026", "runtime_root": str(runtime_root)},
        )
        assert Path(receipt_str).is_file()
        assert written.get("tier1_scout_executed") is True
        assert "graph_mutation_breakdown" in written

        breakdown_file = Path(written["graph_mutation_breakdown"])
        assert breakdown_file.is_file()
        content = breakdown_file.read_text(encoding="utf-8")
        assert "## Tier 1 Scoped Scout — GraphDB Mutation Breakdown" in content
        for tbl in _SQLITE_TABLES + _SIDECAR_TABLES:
            assert f"`{tbl}`" in content


# ── Wave 4: 12-Surface Universal Ingestion Guarantee Matrix ───────────────────

_SAMPLE_TRANSCRIPT = (
    "**Amit Ayer:** [09:00:00]: Initiating Tier 1 deduplication audit across ingestion surfaces.\n\n"
    "**Reid Schenk:** [09:01:00]: Universal ingestion guarantee is verified.\n\n"
    "**Amit Ayer:** [09:02:00]: PartnerAdvisory architecture and engineering invariants confirmed.\n\n"
    "**Reid Schenk:** [09:03:00]: All telemetry and contracts passing.\n\n"
    "**Amit Ayer:** [09:04:00]: Concluding surface ingestion test.\n\n"
    "**Reid Schenk:** [09:05:00]: Session complete.\n"
)

_SAMPLE_NOTES = (
    "Meeting Notes: Architecture Review\n"
    "Date: 09/25/2026\n"
    "Attendees: Amit Ayer, Reid Schenk\n"
    "Summary: Universal ingestion guarantee ensures Tier 1 deduplication runs for notes modality.\n"
    "Action Items: Verify graph mutation breakdown generation.\n"
    "Discussion: The engineering team conducted a detailed architectural review of the ingestion surfaces. "
    "All components confirmed strict adherence to semantic hash integrity, ensuring zero duplicate nodes across sessions. "
    "Telemetry metrics and structured receipts will be recorded in durable audit logs upon every release cycle.\n"
)

_SAMPLE_DOCUMENT = (
    "# Architecture Specification: Tier 1 Deduplication\n"
    "Date: 09/25/2026\n"
    "System: Decision Intelligence Engine\n"
    "Scope: Universal deduplication guarantee across all documents and meeting artifacts.\n"
    "Entities: PartnerAdvisory, Decision Intelligence Engine, Ingestion Pipeline.\n"
    "Overview: This architectural specification provides the end-to-end deduplication guarantees "
    "governing document ingestion, entity resolution, and assertion generation across all multi-modal surfaces. "
    "All claims, decisions, and action items extracted from incoming specifications must conform to deterministic "
    "semantic hash indexing and persistent Merkle proof validation before release activation.\n"
)

_SAMPLE_EML = (
    "From: amit@example.com\n"
    "To: reid@example.com\n"
    "Date: Fri, 25 Sep 2026 09:00:00 -0400\n"
    "Subject: SOW Architecture Agreement\n\n"
    "This statement of work defines the Tier 1 deduplication guarantee for all incoming documents. "
    "Both parties formally ratify the ingestion architecture, acknowledging that entity resolution, "
    "provenance tracking, and graph mutation policies are enforced unconditionally before production releases. "
    "The agreed-upon deliverables and acceptance criteria remain governed by the authoritative release policy.\n"
)

_SAMPLE_VTT = (
    "WEBVTT\n\n"
    "00:00:01.000 --> 00:00:05.000\n"
    "<v Amit Ayer>Initiating Tier 1 deduplication audit across WebVTT caption surfaces.</v>\n\n"
    "00:00:05.500 --> 00:00:10.000\n"
    "<v Reid Schenk>WebVTT caption ingestion verified end to end.</v>\n\n"
    "00:00:10.500 --> 00:00:15.000\n"
    "<v Amit Ayer>PartnerAdvisory architecture and engineering invariants confirmed.</v>\n\n"
    "00:00:15.500 --> 00:00:20.000\n"
    "<v Reid Schenk>All telemetry and contracts passing successfully.</v>\n\n"
    "00:00:20.500 --> 00:00:25.000\n"
    "<v Amit Ayer>Concluding caption surface ingestion test.</v>\n\n"
    "00:00:25.500 --> 00:00:30.000\n"
    "<v Reid Schenk>Caption session complete and verified.</v>\n"
)

_SURFACE_CASES = [
    ("raw_minutes_txt", "meeting_minutes.txt", _SAMPLE_TRANSCRIPT, {}, []),
    ("raw_minutes_md", "meeting_minutes.md", _SAMPLE_TRANSCRIPT, {}, []),
    ("audio_wav", "recording.wav", _SAMPLE_TRANSCRIPT, {}, []),
    ("audio_m4a", "recording.m4a", _SAMPLE_TRANSCRIPT, {}, []),
    ("audio_mp4", "recording.mp4", _SAMPLE_TRANSCRIPT, {}, []),
    ("live_stream_whisper", "live_stream_transcript.md", _SAMPLE_TRANSCRIPT, {}, []),
    ("webvtt_vtt", "captions.vtt", _SAMPLE_VTT, {"time": "09:00 AM - 09:05 AM ET"}, []),
    ("meeting_with_image_collateral", "meeting_images.txt", _SAMPLE_TRANSCRIPT, {}, ["architecture_diagram.png"]),
    ("meeting_with_slide_deck_collateral", "meeting_deck.txt", _SAMPLE_TRANSCRIPT, {}, ["presentation_deck.pptx", "appendix.pdf"]),
    ("copilot_teams_notes", "copilot_notes.txt", _SAMPLE_NOTES, {"notes_mode": True}, []),
    ("sow_email_eml", "sow_agreement.eml", _SAMPLE_EML, {"document": True}, []),
    ("architecture_document_pdf", "architecture_spec.pdf", _SAMPLE_DOCUMENT, {"document": True}, []),
]


class TestUniversalIngestionGuaranteeMatrix:
    """Verifies Tier 1 Deduplication executes across all 12 ingestion surfaces (Wave 4)."""

    @pytest.mark.parametrize(
        "surface_id,filename,content,extra_meta,attachment_names",
        _SURFACE_CASES,
        ids=[c[0] for c in _SURFACE_CASES],
    )
    def test_tier1_dedup_universal_surface(
        self,
        surface_id: str,
        filename: str,
        content: str,
        extra_meta: Dict[str, Any],
        attachment_names: list,
        tmp_path: Path,
        tier1_scout_spy: Any,
    ) -> None:
        """Universal Ingestion Guarantee: Tier 1 deduplication scout MUST fire across all 12 surfaces."""
        from unittest.mock import MagicMock, patch

        from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline

        # Prepare source input
        src_file = tmp_path / filename
        ext = src_file.suffix.lower()
        if ext in {".wav", ".m4a", ".mp4"}:
            if ext == ".wav":
                src_file.write_bytes(
                    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
                    b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
                )
            else:
                src_file.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00" + b"\x00" * 8)
        elif ext == ".pdf":
            import io
            import pypdf
            writer = pypdf.PdfWriter()
            writer.add_blank_page(width=612, height=792)
            buf = io.BytesIO()
            writer.write(buf)
            src_file.write_bytes(buf.getvalue())
        else:
            src_file.write_text(content, encoding="utf-8")

        # Prepare attachments if any
        attachment_paths = []
        for att_name in attachment_names:
            att_path = tmp_path / att_name
            if att_name.lower().endswith(".pdf"):
                import io
                import pypdf
                writer = pypdf.PdfWriter()
                writer.add_blank_page(width=612, height=792)
                buf = io.BytesIO()
                writer.write(buf)
                att_path.write_bytes(buf.getvalue())
            elif att_name.lower().endswith(".pptx"):
                import io
                import zipfile
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                att_path.write_bytes(buf.getvalue())
            else:
                att_path.write_bytes(b"dummy_collateral_payload")
            attachment_paths.append(att_path)

        meeting_name = f"Surf_{surface_id}"[:35]
        out_dir = tmp_path / "output" / "nea" / "09-25-2026" / meeting_name
        out_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "project": "nea",
            "meeting_name": meeting_name,
            "date": "09/25/2026",
            "time": "09:00 AM - 09:30 AM ET",
            "attendees": "Amit Ayer, Reid Schenk",
            "classification": "internal",
            "allow_duplicate": True,
            "attendees_confirmed_by_operator": True,
            "attendee_confirmation_receipt": {
                "status": "RESOLVED_HITL",
                "confirmed_by": "Amit Ayer",
                "attendees": ["Amit Ayer", "Reid Schenk"],
                "subject_sha256": "subj_tier1_universal_09252026",
            },
        }
        meta.update(extra_meta)

        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = content

        with (
            patch("dual_pipeline.audio_ingestion.get_default_transcriber", return_value=mock_transcriber),
            patch(
                "dual_pipeline.document_pipeline_core.content_loader.load_document_content",
                return_value=(content, hashlib.sha256(content.encode()).hexdigest(), src_file),
            ),
        ):
            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=src_file,
                output_dir=str(out_dir),
                metadata=meta,
                attachments=attachment_paths if attachment_paths else None,
            )

        assert result["status"] in ("COMPLETE", "SKIPPED_PUBLICATION")

        # Verify receipt documents EVALS stage with tier1_scout details
        receipt_path = Path(result["execution_receipt"])
        assert receipt_path.is_file(), f"Execution receipt missing: {receipt_path}"
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))

        evals_stage = receipt_data.get("stages", {}).get("EVALS", {})
        assert evals_stage.get("status") == "PASS"
        details = evals_stage.get("details", {})
        assert "tier1_scout" in details, f"Surface {surface_id} missing tier1_scout in EVALS details"
        assert details["tier1_scout"]["ran"] is True, f"Surface {surface_id} tier1_scout did not run"

        # Verify graph_mutation_breakdown.md was created
        breakdown_file = out_dir / "graph_mutation_breakdown.md"
        assert breakdown_file.is_file(), f"Surface {surface_id} did not produce graph_mutation_breakdown.md"

        # tier1_scout_spy fixture will auto-assert that execute_tier1_scout_hook was called!


# ── Wave 4: High-Concurrency Storage Stress Test (P3) ─────────────────────────

class TestConcurrentLedgerStress:
    """Verifies GraphMutationLedger fcntl.flock concurrency under multi-threaded load."""

    def test_concurrent_pipeline_runs_no_ledger_data_loss(self, tmp_path: Path) -> None:
        """P3 Concurrency Invariant: 5 threads x 20 iterations = 100 mutations recorded

        with zero dropped writes or race conditions under fcntl.flock.
        """
        import concurrent.futures

        from dual_pipeline.graph_mutation_ledger import GraphMutationLedger

        ledger_file = tmp_path / "concurrent_ledger.json"
        ledger = GraphMutationLedger(ledger_path=ledger_file)

        num_threads = 5
        iterations_per_thread = 20
        total_expected = num_threads * iterations_per_thread

        def _worker(thread_idx: int) -> list:
            results = []
            for i in range(iterations_per_thread):
                run_id = f"run-thread-{thread_idx}-{i}"
                cnt = ledger.record_mutation(run_id=run_id, node_count=1, assertion_count=2)
                results.append(cnt)
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(_worker, t) for t in range(num_threads)]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        # Verify exact mutation counts and history
        assert ledger.get_total_mutations() == total_expected, (
            f"Expected {total_expected} total mutations, got {ledger.get_total_mutations()}"
        )

        data = ledger._load()
        assert data["cumulative_mutation_count"] == total_expected, (
            f"Expected cumulative_mutation_count={total_expected}, got {data['cumulative_mutation_count']}"
        )
        assert len(data["mutation_history"]) == total_expected, (
            f"Expected {total_expected} mutation history records, got {len(data['mutation_history'])}"
        )

        # Verify all run IDs are unique in history
        seen_runs = {entry["run_id"] for entry in data["mutation_history"]}
        assert len(seen_runs) == total_expected, "Duplicate run IDs detected in mutation ledger history"



