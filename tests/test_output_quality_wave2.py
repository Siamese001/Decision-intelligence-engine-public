"""
tests/test_output_quality_wave2.py

Wave 2 Acceptance Suite: Collateral review provenance, anti-circular manifest
classification, weak keyword overlap rejection, authentic drop promotion,
Output 3 attachment gate, and attachment-free APEX_TRANS path.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, Dict

import pytest

from dual_pipeline.attachment_core.aligner import (
    _match_attachment_for_turn,
    align_attachments_with_transcript,
    inject_attachment_annotations,
)
from dual_pipeline.attachment_core.collateral_manifest import (
    build_collateral_manifest,
    write_collateral_manifest,
)
from dual_pipeline.attachment_core.detector import (
    _stage_candidate_attachment,
    _sync_collateral_drop_log,
    stage_attachments_into_output,
)
from dual_pipeline.attachment_core.prompt_builder import (
    _format_single_table,
    _select_decisive_claims,
    _select_decisive_table_rows,
    build_attachment_prompt_section,
)
from dual_pipeline.attachment_core.review_provenance import (
    evaluate_attachment_review_provenance,
    has_authenticated_mention,
    is_authenticated_drop,
    parse_hms_to_seconds,
)
from dual_pipeline.attachment_core.types import (
    AttachmentMention,
    AttachmentRecord,
    AttachmentType,
    CrossModalAttachmentAlignment,
    ExtractedAttachmentContent,
)
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
    _align_and_annotate_attachments,
)
from dual_pipeline.transcript_qa_core.collateral_healer import (
    _extract_cr_attachments,
    heal_collateral_graph_mutations,
)
from dual_pipeline.transcript_qa_core.consistency import (
    _check_collateral_evidence_propagation,
    run_cross_artifact_consistency,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_staging_and_circular_manifest_classification_rejection():
    """Staging candidate attachments defaults them to BACKGROUND without fabricating circular drop logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        ref_dir = out_dir / "referenced_artifacts"
        ref_dir.mkdir(parents=True)

        dummy_doc = out_dir / "Architecture_Draft.png"
        dummy_doc.write_bytes(b"dummy image content for wave 2 test")

        # Staging into output directory with no drop log
        records = stage_attachments_into_output([dummy_doc], out_dir)
        assert len(records) == 1
        rec = records[0]

        # Invariant: Discovered/staged attachments default to BACKGROUND
        assert rec.attribution_mode == "BACKGROUND"
        assert rec.dropped_at_elapsed is None

        # Invariant: _sync_collateral_drop_log must NEVER fabricate a synthetic drop log
        drop_log_file = out_dir / "collateral_drop_log.json"
        assert not drop_log_file.exists()

        # Invariant: Collateral manifest excludes BACKGROUND records
        manifest = build_collateral_manifest(records)
        assert len(manifest["items"]) == 0
        written_path = write_collateral_manifest(records, out_dir)
        assert written_path is None


def test_weak_keyword_overlap_never_injects_transcript_notes():
    """Weak keyword overlap (e.g. 'report', 'status') is rejected and never injects transcript annotations."""
    dummy_path = Path("/tmp/Delivery_Status_Report.pdf")
    rec = AttachmentRecord(
        attachment_id="att-01-delivery-status",
        original_path=dummy_path,
        staged_path=dummy_path,
        attachment_type=AttachmentType.DOCUMENT,
        filename="Delivery_Status_Report.pdf",
        file_size_bytes=1024,
        sha256="abc123456789",
        attribution_mode="BACKGROUND",
    )

    # Turn with generic keyword overlap ("report") and a generic visual cue
    turn = {
        "speaker": "harper Hoffman",
        "timestamp": "00:05:10",
        "turn_text": "Looking at the screen, we need to finalize the weekly status report for leadership.",
    }

    mention = _match_attachment_for_turn(turn, 1, rec, None)
    assert mention is not None
    # Must be marked as weak alignment and NOT speaker-authenticated
    assert mention.is_weak_alignment is True
    assert mention.is_speaker_authenticated is False

    alignment = CrossModalAttachmentAlignment(
        total_attachments=1,
        mentions=[mention],
    )

    transcript_text = (
        "### [00:05:10] harper Hoffman\n"
        "Looking at the screen, we need to finalize the weekly status report for leadership.\n"
    )

    # Invariant: inject_attachment_annotations MUST NOT inject annotations for weak keyword matches
    annotated = inject_attachment_annotations(transcript_text, alignment)
    assert "[Referenced Collateral:" not in annotated
    assert annotated == transcript_text


def test_authenticated_drop_log_promotion_and_temporal_plausibility():
    """Valid in-session drop log promotes attachment to COLLATERAL_REVIEWED; implausible drops fail."""
    session_meta = {"duration": "45m"}

    # 1. Authentic drop event with non-zero elapsed time and trusted source
    valid_drop = {
        "filename": "Lens_Architecture_v2.png",
        "dropped_at_elapsed": "00:15:30",
        "source": "copilot_drop",
        "sha256": "deadbeef1234",
    }
    assert is_authenticated_drop(valid_drop, session_meta) is True

    # 2. Synthetic circular drop with 00:00:00 and no provenance
    synthetic_drop = {
        "filename": "Lens_Architecture_v2.png",
        "dropped_at_elapsed": "00:00:00",
        "sha256": "deadbeef1234",
    }
    assert is_authenticated_drop(synthetic_drop, session_meta) is False

    # 3. Explicitly marked synthetic or background drop
    flagged_synthetic = {
        "filename": "Lens_Architecture_v2.png",
        "dropped_at_elapsed": "00:10:00",
        "synthetic": True,
    }
    assert is_authenticated_drop(flagged_synthetic, session_meta) is False

    # 4. Temporally implausible drop timestamp (drop at 1h20m in a 45m meeting)
    implausible_drop = {
        "filename": "Lens_Architecture_v2.png",
        "dropped_at_elapsed": "01:20:00",
        "source": "copilot_drop",
    }
    assert is_authenticated_drop(implausible_drop, session_meta) is False

    # Evaluate provenance helper
    rec = AttachmentRecord(
        attachment_id="att-01-lens-v2",
        original_path=Path("/tmp/Lens_Architecture_v2.png"),
        staged_path=Path("/tmp/Lens_Architecture_v2.png"),
        attachment_type=AttachmentType.IMAGE,
        filename="Lens_Architecture_v2.png",
        file_size_bytes=2048,
        sha256="deadbeef1234",
    )
    res_valid = evaluate_attachment_review_provenance(rec, drop_entry=valid_drop, session_metadata=session_meta)
    assert res_valid.is_reviewed is True
    assert res_valid.attribution_mode == "COLLATERAL_REVIEWED"
    assert res_valid.provenance_reason == "authenticated_drop"

    res_synthetic = evaluate_attachment_review_provenance(rec, drop_entry=synthetic_drop, session_metadata=session_meta)
    assert res_synthetic.is_reviewed is False
    assert res_synthetic.attribution_mode == "BACKGROUND"


def test_authenticated_source_mention_promotion():
    """Verified speaker mention of exact filename or component promotes attachment to COLLATERAL_REVIEWED."""
    dummy_path = Path("/tmp/LENS_Dev_Portal_Diagram.png")
    rec = AttachmentRecord(
        attachment_id="att-02-lens-portal",
        original_path=dummy_path,
        staged_path=dummy_path,
        attachment_type=AttachmentType.DIAGRAM,
        filename="LENS_Dev_Portal_Diagram.png",
        file_size_bytes=4096,
        sha256="feedface5678",
        attribution_mode="BACKGROUND",
    )

    extracted_content = ExtractedAttachmentContent(
        attachment_id="att-02-lens-portal",
        filename="LENS_Dev_Portal_Diagram.png",
        attachment_type=AttachmentType.DIAGRAM,
        sha256="feedface5678",
        components=[{"label": "Nameplate Validation Engine", "component_ref": "comp-01"}],
    )

    # Dialogue turn with exact component match
    turn = {
        "speaker": "Amit Ayer",
        "timestamp": "00:12:30",
        "turn_text": "If you look at the Nameplate Validation Engine on this diagram, the flow validates against SAP.",
    }

    mention = _match_attachment_for_turn(turn, 1, rec, extracted_content)
    assert mention is not None
    assert mention.is_speaker_authenticated is True
    assert mention.is_weak_alignment is False

    alignment = CrossModalAttachmentAlignment(
        total_attachments=1,
        mentions=[mention],
    )

    assert has_authenticated_mention(rec.attachment_id, rec.filename, alignment) is True

    # Preflight coordinator promotes background attachment upon verified mention
    records = [rec]
    metadata: Dict[str, Any] = {"attachments": []}
    transcript_text = "### [00:12:30]\n**Amit Ayer**: If you look at the Nameplate Validation Engine on this diagram, the flow validates against SAP.\n"
    annotated = _align_and_annotate_attachments(
        transcript_text=transcript_text,
        discovered_records=records,
        extracted_contents_map={rec.attachment_id: extracted_content},
        metadata=metadata,
    )

    assert records[0].attribution_mode == "COLLATERAL_REVIEWED"
    assert "[Referenced Collateral: `LENS_Dev_Portal_Diagram.png`" in annotated


def test_unsupported_review_assertion_and_source_linkage_rejection_in_gate_cross_5():
    """Gate Cross-5 rejects unsupported review claims on background files and unknown attachment locators."""
    meta = {
        "project": "lens-test",
        "duration": "30m",
        "attachments": [
            {
                "filename": "Background_Spec.pdf",
                "attribution_mode": "BACKGROUND",
            },
            {
                "filename": "Reviewed_Flow.png",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "dropped_at_elapsed": "00:10:00",
            },
        ],
    }

    # Case A: Output 3 asserts COLLATERAL_REVIEWED for Background_Spec.pdf (unsupported review assertion)
    f3_unsupported = {
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "document:background-spec",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "source_locators": ["attachment:Background_Spec.pdf"],
                },
                {
                    "id": "system:reviewed-flow",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "source_locators": ["attachment:Reviewed_Flow.png"],
                },
            ]
        }
    }
    ok_a, fails_a = _check_collateral_evidence_propagation(f3_unsupported, meta)
    assert ok_a is False
    assert any("Unsupported review assertion" in f for f in fails_a)

    # Case B: Output 3 references an unknown attachment with no source linkage
    f3_unknown = {
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:phantom",
                    "source_locators": ["attachment:Phantom_Nonexistent_Doc.pdf"],
                },
                {
                    "id": "system:reviewed-flow",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "source_locators": ["attachment:Reviewed_Flow.png"],
                },
            ]
        }
    }
    ok_b, fails_b = _check_collateral_evidence_propagation(f3_unknown, meta)
    assert ok_b is False
    assert any("Source linkage failure" in f for f in fails_b)

    # Case C: Temporal plausibility failure (drop timestamp 02:00:00 exceeds 30m duration)
    meta_implausible = {
        "duration": "30m",
        "attachments": [
            {
                "filename": "Reviewed_Flow.png",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "dropped_at_elapsed": "02:00:00",
            }
        ],
    }
    f3_valid = {
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:reviewed-flow",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "source_locators": ["attachment:Reviewed_Flow.png"],
                }
            ]
        }
    }
    ok_c, fails_c = _check_collateral_evidence_propagation(f3_valid, meta_implausible)
    assert ok_c is False
    assert any("Temporal plausibility violation" in f for f in fails_c)

    # Case D: Valid verified collateral passes cleanly
    ok_d, fails_d = _check_collateral_evidence_propagation(f3_valid, {
        "duration": "30m",
        "attachments": [
            {
                "filename": "Reviewed_Flow.png",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "dropped_at_elapsed": "00:10:00",
            }
        ],
    })
    assert ok_d is True
    assert len(fails_d) == 0


def test_attachment_free_neet_path_passes_cleanly():
    """APEX_TRANS / attachment-free sessions pass all collateral QA gates without fabricating artifacts."""
    meta_neet = {
        "meeting_name": "APEX_TRANS Architecture Sync",
        "project": "apex_trans",
        "duration": "30m",
        "attachments": [],
    }

    # Extract CR attachments returns empty list
    cr = _extract_cr_attachments(meta_neet)
    assert cr == []

    # Collateral healer makes zero mutations on attachment-free session
    f3_clean = {
        "graph_mutations": {
            "entity_upserts": [{"id": "project:apex_trans", "name": "APEX_TRANS"}],
            "relationship_signals": [],
        },
        "evidence_index": {},
    }
    changes: list[str] = []
    heal_collateral_graph_mutations(f3_clean, meta_neet, changes)
    assert len(changes) == 0
    assert len(f3_clean["graph_mutations"]["entity_upserts"]) == 1

    # Gate Cross-5 returns True with zero failures
    ok, fails = _check_collateral_evidence_propagation(f3_clean, meta_neet)
    assert ok is True
    assert fails == []

    # Prompt builder returns empty string
    prompt_sec = build_attachment_prompt_section([], {})
    assert prompt_sec == ""


def test_prompt_builder_omission_accounting_and_decisive_scoring_preservation():
    """_select_decisive_table_rows and _select_decisive_claims retain omission accounting and scoring."""
    # 15 table rows with decisive keywords and numerics
    rows = [
        ["Module A", "nominal", "10"],
        ["Module B", "critical failure detected", "404"],
        ["Module C", "high risk alert", "500"],
        ["Module D", "blocked by dependency", "1"],
        ["Module E", "offline remediation required", "911"],
        ["Module F", "nominal status", "0"],
        ["Module G", "nominal status", "0"],
        ["Module H", "nominal status", "0"],
        ["Module I", "nominal status", "0"],
        ["Module J", "nominal status", "0"],
        ["Module K", "nominal status", "0"],
        ["Module L", "nominal status", "0"],
        ["Module M", "nominal status", "0"],
        ["Module N", "nominal status", "0"],
        ["Module O", "nominal status", "0"],
    ]

    selected, omitted = _select_decisive_table_rows(rows, limit=5)
    assert len(selected) == 5
    assert omitted == 10

    # Ensure critical/high risk rows were prioritized
    assert any("critical failure" in " ".join(r) for r in selected)
    assert any("high risk" in " ".join(r) for r in selected)

    # Test single table formatting with omission accounting line
    table_dict = {
        "headers": ["System", "Status", "Metric"],
        "rows": rows,
    }
    table_lines = _format_single_table(table_dict)
    table_str = "\n".join(table_lines)
    assert "*[Omission accounting: 5 of 15 total rows omitted from prompt context" in table_str

    # Test decisive claims selection
    claims = [
        "System operates within normal operational parameters.",
        "Critical P0 variance discovered in core database schema.",
        "Routine documentation update completed.",
        "High risk security vulnerability flagged during audit.",
        "Minor cosmetic styling revision pending.",
    ]
    selected_claims, omitted_claims = _select_decisive_claims(claims, limit=2)
    assert len(selected_claims) == 2
    assert omitted_claims == 3
    assert any("Critical P0" in c for c in selected_claims)
    assert any("High risk" in c for c in selected_claims)


def test_mandy_and_conduit_false_review_rejection():
    """Realistic scenario: Conduit metadata with unreviewed background files rejects false review."""
    conduit_meta_path = REPO_ROOT / "inputs" / "transcripts" / "pd_conduit_831_metadata.json"
    assert conduit_meta_path.exists()
    conduit_meta = json.loads(conduit_meta_path.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        staged_file = out_dir / "Conduit_Architecture_Overview.png"
        staged_file.write_bytes(b"conduit architecture visual bytes")

        # Stage attachment into output
        records = stage_attachments_into_output([staged_file], out_dir)
        assert len(records) == 1
        rec = records[0]

        # Invariant: Unmentioned background file remains BACKGROUND
        assert rec.attribution_mode == "BACKGROUND"

        # Check Output 03 healer does not fabricate mutations
        f3_state: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [{"id": "project:conduit", "name": "Conduit"}],
                "relationship_signals": [],
            },
            "evidence_index": {},
        }
        changes: list[str] = []
        conduit_meta["attachments"] = [rec.to_dict()]
        heal_collateral_graph_mutations(f3_state, conduit_meta, changes)

        # Invariant: No collateral entity upserts or evidence created for background file
        assert len(changes) == 0
        assert not any("conduit-architecture" in e.get("id", "") for e in f3_state["graph_mutations"]["entity_upserts"])
        assert "attachment:" not in str(f3_state["evidence_index"])
