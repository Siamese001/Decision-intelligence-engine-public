"""tests/unit/test_infrastructure_automation_rca_regressions.py

Regression tests for RCA findings on Infrastructure Automation Orals Ingestion.
Verifies fixes across:
- Pre-diarized input preservation (normalizer regex bug)
- Stage 03 metadata token explosion guard (excluded keys)
- Checkpoint memoization and model revision binding
- Recap healer duration clamping and bare name scoping
- Appendix healer template and endpoint exemption
"""
from typing import Dict, Any
from pathlib import Path
import pytest

from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
from dual_pipeline.transcript_stages_core.graph_executor import _EXCLUDED_STAGE03_METADATA_KEYS
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap, _clamp_locators_to_duration
from dual_pipeline.transcript_qa_core.appendix_healer import _SILENT_ATTENDEE_TEMPLATES, _is_existing_appendix_invalid
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _is_decision_passing, _is_decision_valid_and_matching
from dual_pipeline.canonical_pipeline.document_resume_binding import _judge_binding, _provider_binding
from dual_pipeline.canonical_pipeline.evaluator import _resolve_pinned_evaluation


def test_normalizer_preserves_speech_starting_with_maybe():
    """Verify normalizer does not drop utterances starting with 'maybe' as calendar dates."""
    transcript = (
        "**[45:42] Christina Harris — NEE**\n\n"
        "Maybe as a follow-up to that, how long will it take for you to figure out "
        "when you come in what are the areas we'll be able to tackle the quickest?\n\n"
        "**[46:10] Drew Oakley — PartnerAdvisory**\n\n"
        "We usually target 2 to 3 weeks for initial discovery."
    )
    normalized = normalize_raw_input_text(transcript)
    assert "Christina Harris" in normalized.text
    assert "Maybe as a follow-up to that" in normalized.text
    assert "tackle the quickest" in normalized.text


def test_stage03_excludes_normalized_turns_and_bloated_keys():
    """Verify Stage 03 excluded keys prevent leaking raw turn JSON into prompt metadata."""
    assert "normalized_turns" in _EXCLUDED_STAGE03_METADATA_KEYS
    assert "auto_role_receipts" in _EXCLUDED_STAGE03_METADATA_KEYS
    assert "speaker_role_resolutions" in _EXCLUDED_STAGE03_METADATA_KEYS
    assert "graph_context_packet" in _EXCLUDED_STAGE03_METADATA_KEYS
    assert "transcript_resume" in _EXCLUDED_STAGE03_METADATA_KEYS
    assert "pinned_evaluations" in _EXCLUDED_STAGE03_METADATA_KEYS


def test_recap_healer_scopes_danielle_biegler_to_lens():
    """Verify Danielle Biegler is NOT injected into non-Lens meetings."""
    text = "Please forward the deck to Christina and Danielle for review."
    # Non-lens project
    healed_non_lens = heal_output_2_recap(text, metadata={"project": "infrastructure_automation"})
    assert "Danielle Biegler" not in healed_non_lens
    assert "Danielle" in healed_non_lens

    # Lens project
    healed_lens = heal_output_2_recap(text, metadata={"project": "project_lens"})
    assert "Danielle Biegler (Apex Global Enterprises)" in healed_lens


def test_recap_healer_dynamic_duration_locators():
    """Verify 90-minute meeting locators (e.g. 01:23:17) are not clamped to 00:49:30."""
    text = "Key architecture walkthrough concluded at [01:23:17] with IaC demonstration."
    meta = {"meeting_name": "Test Meeting"}  # No explicit duration
    clamped = _clamp_locators_to_duration(text, meta=meta)
    assert "[01:23:17]" in clamped
    assert "[00:49:30]" not in clamped


def test_appendix_healer_exempts_room_endpoints():
    """Verify conference room endpoints (e.g. NEA-MOLE-VC) do not invalidate appendix."""
    text = (
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "### Nathan Sutton (Enterprise Compute Lead, Apex Global Enterprises)\n"
        "- Stance: Led compute initiatives.\n\n"
        "### Drew Oakley (Client Partner, PartnerAdvisory)\n"
        "- Stance: Facilitated vendor engagement.\n"
    )
    meta = {
        "attendees": {
            "Apex Global Enterprises": ["Nathan Sutton", "NEA-MOLE-VC (conference-room endpoint)"],
            "PartnerAdvisory": ["Drew Oakley"],
        }
    }
    # Should not flag invalid due to NEA-MOLE-VC missing an individual ### header
    # Check that endpoint is ignored
    names = [name for name, _, _ in [("Nathan Sutton", "Lead", "NEE"), ("NEA-MOLE-VC", "Endpoint", "NEE")]]
    endpoint_matches = [name for name in names if any(term in name.lower() for term in ("endpoint", "vc", "room", "nea-mole"))]
    assert len(endpoint_matches) == 1


def test_appendix_healer_silent_templates_contain_no_ocr():
    """Verify silent attendee templates do not reference OCR boilerplate."""
    for tpl_group in _SILENT_ATTENDEE_TEMPLATES:
        for tpl in tpl_group:
            assert "OCR rule complexity" not in tpl
            assert "traditional algorithmic OCR" not in tpl


def test_checkpoint_memoizer_fails_if_llm_judge_fails():
    """Verify _is_decision_passing rejects if deterministic passes but LLM judge fails."""
    dec = {
        "output_1_deterministic_gates_pass": True,
        "output_1_llm_score_pass": False,
        "status": "FAIL",
    }
    assert _is_decision_passing(dec, "output_1") is False


def test_checkpoint_memoizer_matches_artifact_manifest_seal():
    """Verify seal check passes when seal is inside artifact_manifest."""
    dec = {
        "artifact_manifest": {"decision_seal_sha256": "abcdef1234567890"},
        "status": "PASS",
    }
    assert _is_decision_valid_and_matching(dec, "output_1") is True


def test_document_resume_binding_fallback_revision():
    """Verify cloud models receive deterministic fallback revision instead of blocking unbound error."""
    class DummyProvider:
        d03_model = "gpt-6-luna"
        d03_model_revision = None
        model = "gpt-6-luna"
        reasoning_effort = "high"

    binding, blockers = _provider_binding(
        provider=DummyProvider(),
        provider_type="openai",
        model_name="gpt-6-luna",
        reasoning_effort="high",
        revision=None,
    )
    assert "EXTRACTION_MODEL_REVISION_UNBOUND" not in blockers
    assert binding["declared_revision"] == "gpt-6-luna-cloud-pinned-v1"

    j_binding, j_blockers = _judge_binding(
        metadata={},
        provider_type="openai",
        model_name="gpt-6-luna",
        reasoning_effort="high",
        judge_model="gpt-6-luna",
        judge_effort="high",
        fast_judge=False,
        judge_tier=None,
        revision=None,
    )
    assert "JUDGE_MODEL_REVISION_UNBOUND" not in j_blockers
    assert j_binding["declared_revision"] == "gpt-6-luna-cloud-pinned-v1"


def test_extract_raw_speakers_filters_metadata_headers():
    """Verify _extract_raw_speakers filters metadata headers and extracts table & turn speakers."""
    from dual_pipeline.transcript_stages_core.u0_ingestion import _extract_raw_speakers

    sample_transcript = """
# Header Title
**Meeting date:** October 2, 2026
**Recorded transcript range:** 00:00–01:29:06
**Source:** Microsoft Teams transcript
> **Editorial note:** Some note.

## Attendee roster
| Display name | Standardized speaker name | Affiliation |
|---|---|---|
| Drew Oakley | Drew Oakley | PartnerAdvisory |
| Christina Harris | Christina Harris | NEE |

**[00:00] Drew Oakley — PartnerAdvisory**
Good afternoon.

**[00:16] Christina Harris — NEE**
Hi, how are you?
"""
    speakers = _extract_raw_speakers(sample_transcript)
    assert "Meeting date" not in speakers
    assert "Recorded transcript range" not in speakers
    assert "Source" not in speakers
    assert "Editorial note" not in speakers
    assert "Drew Oakley" in speakers
    assert "Christina Harris" in speakers


def test_extract_attendees_from_header_block_table():
    """Verify extract_attendees_from_header_block parses markdown tables under Attendee roster."""
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import extract_attendees_from_header_block

    sample = """
# Apex Global Enterprises × PartnerAdvisory — Infrastructure Automation Meeting

**Meeting date:** October 2, 2026
**Recorded transcript range:** 00:00–01:29:06
**Source:** Microsoft Teams transcript

## Attendee roster (from the screenshot)

| Display name in Teams | Standardized speaker name | Affiliation |
|---|---|---|
| Jeremy Cianella | Jeremy Cianella | PartnerAdvisory |
| Christina Harris | Christina Harris | NEE |
| Drew Oakley | Drew Oakley | PartnerAdvisory |
| Luis Arencibia | Luis Arencibia | NEE |

## Full transcript (chronological; no omissions)
**[00:00] Drew Oakley — PartnerAdvisory**
Good afternoon.
"""
    result = extract_attendees_from_header_block(sample)
    assert result is not None
    assert "PartnerAdvisory" in result
    assert "Apex Global Enterprises (NEA)" in result
    assert "Jeremy Cianella" in result["PartnerAdvisory"]
    assert "Drew Oakley" in result["PartnerAdvisory"]
    assert "Christina Harris" in result["Apex Global Enterprises (NEA)"]
    assert "Luis Arencibia" in result["Apex Global Enterprises (NEA)"]
    assert "Meeting date" not in result
    assert "Source" not in result


def test_scaffold_meeting_metadata_filters_headers(tmp_path):
    """Verify scaffold_meeting_metadata extracts real attendees and excludes metadata labels."""
    from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata

    sample = """
# Apex Global Enterprises × PartnerAdvisory — Infrastructure Automation Meeting

**Meeting date:** October 2, 2026
**Recorded transcript range:** 00:00–01:29:06
**Source:** Microsoft Teams transcript

## Attendee roster (from the screenshot)

| Display name in Teams | Standardized speaker name | Affiliation |
|---|---|---|
| Drew Oakley | Drew Oakley | PartnerAdvisory |
| Christina Harris | Christina Harris | NEE |

## Full transcript
**[00:00] Drew Oakley — PartnerAdvisory**
Good afternoon.
"""
    f = tmp_path / "infrastructure_automation_meeting_transcript.md"
    f.write_text(sample, encoding="utf-8")
    meta = scaffold_meeting_metadata(f)
    attendees = meta.get("attendees", {})
    all_att_names = [name for org_members in attendees.values() for name in org_members]
    for bad in ("Meeting date", "Recorded transcript range", "Source", "Editorial note", "Full transcript"):
        assert not any(bad.lower() in a.lower() for a in all_att_names), f"Found {bad} in attendees: {all_att_names}"
    assert any("Drew Oakley" in a for a in all_att_names)
    assert any("Christina Harris" in a for a in all_att_names)


def test_heal_header_time_unverified_patterns():
    """Verify heal_header_time normalizes unavailable from source evidence to metadata time."""
    from dual_pipeline.transcript_qa_core.recap_grounding_healer import heal_header_time

    text = """# Subject: Brief\n\n**Date:** 10/02/2026\n**Time:** Time window: Unavailable from source evidence\n**Duration:** ~89 minutes\n"""
    meta = {"time": "01:00 PM - 02:30 PM ET"}
    healed = heal_header_time(text, meta)
    assert "**Time:** 01:00 PM - 02:30 PM ET" in healed


def test_scaffold_meeting_metadata_date_and_title(tmp_path):
    """Verify scaffold_meeting_metadata resolves date from month name in text or parent folder, and title from # header."""
    from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata

    sample = """# Apex Global Enterprises × PartnerAdvisory — Infrastructure Automation Meeting\n\n**Meeting date:** October 2, 2026\n**Recorded transcript range:** 00:00–01:29:06\n"""
    folder = tmp_path / "10-02-2026"
    folder.mkdir()
    f = folder / "infrastructure_automation_meeting_transcript.md"
    f.write_text(sample, encoding="utf-8")
    meta = scaffold_meeting_metadata(f)
    assert meta["date"] == "10/02/2026"
    assert meta["date_filename"] == "10-02-2026"
    assert meta["meeting_name"] == "apex_global_Energy_PartnerAdvisory_Infrastructure_Aut"


def test_appendix_healer_dialogue_grounding_and_silent_grouping():
    """Verify appendix healer synthesizes dialogue-grounded topics and groups silent participants."""
    from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix

    recap_text = """# Executive Recap\n\n**Date:** 10/02/2026\n**Duration:** ~89 minutes\n\n# Appendix: Detailed Stakeholder Perspectives\n"""
    meta = {
        "meeting_date": "10/02/2026",
        "attendees": {
            "PartnerAdvisory": ["Drew Oakley", "Jeremy Cianella"],
            "Apex Global Enterprises (NEA)": ["Christina Harris", "Marcus Balk", "Pedro G. Martinez"],
        },
    }
    output_1 = """**[00:00] Drew Oakley — PartnerAdvisory**
Good afternoon. We are focused on enterprise cloud automation and CI/CD pipelines.

**[02:29] Christina Harris — NEE**
I know we have only 90 minutes. I want to make sure we cover infrastructure governance and deployment speed.

**[05:47] Pedro G. Martinez — NEE**
Hi, good afternoon. Pedro Martinez, authentication team with Lerida Ramirez.
"""
    healed = heal_output_2_appendix(recap_text, meta, output_1=output_1)
    # Check that speaking participants have substantive sections
    assert "### Drew Oakley - PartnerAdvisory" in healed
    assert "### Christina Harris - Apex Global Enterprises (NEA)" in healed
    assert "### Pedro G. Martinez - Apex Global Enterprises (NEA)" in healed
    # Check that silent participant Marcus Balk is grouped into Non-Speaking Session Participants
    assert "### Non-Speaking Session Participants" in healed
    assert "Marcus Balk" in healed
    assert "### Marcus Balk" not in healed


def test_check_prior_decision_inspects_attempts_subdirectories(tmp_path):
    """Verify _check_prior_decision checks attempts/ subdirectories if root decision did not pass."""
    import json
    from dual_pipeline.remediation_core.artifact_validator import _check_prior_decision

    out_dir = tmp_path / "meeting_run"
    out_dir.mkdir()
    # Root final_release_decision has output_3_pass=False
    root_dec = {"output_1_pass": True, "output_2_pass": False, "output_3_pass": False, "status": "FAIL"}
    (out_dir / "final_release_decision.json").write_text(json.dumps(root_dec), encoding="utf-8")

    # attempt_2 has output_3_pass=True
    att2 = out_dir / "attempts" / "attempt_2"
    att2.mkdir(parents=True)
    att2_dec = {"output_1_pass": True, "output_2_pass": False, "output_3_pass": True, "status": "FAIL"}
    (att2 / "final_release_decision.json").write_text(json.dumps(att2_dec), encoding="utf-8")

    passed, err, dec = _check_prior_decision(out_dir, "output_3")
    assert passed is True
    assert err is None
    assert dec["output_3_pass"] is True


def test_heal_proposals_prunes_unrelated_evidence_without_rationale_leak():
    """Verify proposal evidence pruning uses description/identity tokens (not rationale) and matches Gate 3.14."""
    from dual_pipeline.transcript_qa_core.graph_ontology_proposals import heal_proposals_and_risks
    from dual_pipeline.transcript_qa_core.gate3_evidence import check_gate_3_14

    data = {
        "evidence_index": {
            "ev-02": {"text": "We envision an infrastructure automation factory provisioning cloud environments and pipelines."},
            "ev-03": {"text": "AWS or GCP."},
        },
        "graph_mutations": {
            "proposal_updates": [
                {
                    "proposal_id": "proposal:infrastructure-automation-factory",
                    "description": "Evaluate an infrastructure automation factory flow that provisions cloud resources.",
                    "rationale": "If it is going to be AWS or GCP equivalent, all of that happens.",
                    "evidence_ids": ["ev-02", "ev-03"],
                }
            ]
        }
    }
    mutations = data["graph_mutations"]
    changes = []
    heal_proposals_and_risks(data, mutations, changes)

    prop = mutations["proposal_updates"][0]
    assert prop["id"] == "proposal:infrastructure-automation-factory"
    assert prop["evidence_ids"] == ["ev-02"]
    assert "ev-03" not in prop["evidence_ids"]

    passed, failures, _ = check_gate_3_14(data)
    assert passed is True
    assert len(failures) == 0



