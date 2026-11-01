"""
tests/test_role_hint_and_ledger_gate_adversarial.py

Adversarial and edge-case tests for Wave 1 (Live Role-Hint Persistence,
Ledger-Bound Role Gate, and Question Hook Exemptions):
1. Invariant bypass attempt: Forged `hitl_role_resolutions` and `attendee_roles_confirmed_by_operator=True`
   without a backing ledger entry are rejected fail-closed under strict mode.
2. Boundary payload: Empty inputs, missing quotes without roles, null manifest candidates.
3. Malformed input: Corrupt speaker_hints.json, non-dict items in chat_attributions, parenthetical commas.
4. Exact production regression: Will Booth (`Managing Director`) downgraded to `Consultant` via
   parenthetical string filter mismatch and unbacked `hitl_role_resolutions`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.u0_hint_reconciler import (
    extract_manifest_roles,
    extract_role_hints_from_records,
)
from dual_pipeline.canonical_pipeline.u0_ingress import (
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
)
from dual_pipeline.cli.speaker_hint_handlers import handle_speaker_hint
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    verify_ledger_backed_role_resolutions,
)
from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import C05TitleCompletenessGate
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from tools.check_ask_user_question import check_hook_payload


def test_invariant_bypass_forged_hitl_role_resolutions_rejected(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt — forged hitl_role_resolutions without ledger receipt fails closed."""
    metadata: Dict[str, Any] = {
        "time": "01:00 PM - 01:30 PM ET",
        "attendees": "Unverified Person (Unknown)",
        "attendees_confirmed_by_operator": True,
        "attendee_roles_confirmed_by_operator": True,
        "require_ledger_role_receipt": True,
        "repo_root": str(tmp_path),
        "hitl_role_resolutions": {
            "Unverified Person": "Consultant",
        },
    }
    verified, rejected = verify_ledger_backed_role_resolutions(metadata, repo_root=tmp_path)
    assert verified == {}
    assert "Unverified Person" in rejected
    assert "Unverified Person" not in metadata["hitl_role_resolutions"]
    assert metadata["attendee_roles_confirmed_by_operator"] is False

    # Preflight batch check must flag attendee_roles as missing
    report = batch_validate_preflight_checks(metadata, raw_transcript="Unverified Person: Hello.")
    assert not report.is_valid
    assert "attendee_roles" in report.missing_fields

    # C0.5 and C0.6 must also reject forged hitl_role_resolutions when strict verification is active
    c05_meta: Dict[str, Any] = {
        "attendees": "Unverified Person (Unknown)",
        "require_ledger_role_receipt": True,
        "repo_root": str(tmp_path),
        "hitl_role_resolutions": {"Unverified Person": "Consultant"},
    }
    with pytest.raises(UnifiedPreflightCascadeError):
        C05TitleCompletenessGate(repo_root=tmp_path).execute({"metadata": c05_meta, "repo_root": tmp_path})

    c06_meta: Dict[str, Any] = {
        "attendees": "Unverified Person (Unknown)",
        "require_ledger_role_receipt": True,
        "repo_root": str(tmp_path),
        "hitl_role_resolutions": {"Unverified Person": "Consultant"},
    }
    with pytest.raises(HITLRequiredError):
        C06GraphDBRoleVerificationGate(repo_root=tmp_path).execute({"metadata": c06_meta, "repo_root": tmp_path})


def test_boundary_payloads_empty_and_null_inputs(tmp_path: Path) -> None:
    """Category: Boundary payload — empty strings, null manifests, and missing quote+role rejected cleanly."""
    collector = ChatAttributionCollector(attendees=[])
    assert collector.parse_message("") == []
    assert collector.parse_message("   ;  \n ") == []
    assert extract_manifest_roles(None, None) == {}
    assert extract_manifest_roles("", []) == {}
    assert extract_role_hints_from_records(None) == {}
    assert extract_role_hints_from_records([]) == {}

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    args_empty = argparse.Namespace(
        speaker="Will Booth",
        quote="",
        role="",
        org="",
        meeting_dir=str(meeting_dir),
        runtime_root=str(tmp_path),
        json=True,
        force=False,
        remove_attendee=None,
    )
    res = handle_speaker_hint(args_empty)
    assert res["status"] == "FAIL"
    assert res["error"] == "EMPTY_QUOTE"


def test_malformed_inputs_corrupt_files_and_nested_commas(tmp_path: Path) -> None:
    """Category: Malformed input — corrupt JSON files, non-dict elements, and nested commas inside parentheses."""
    meeting_dir = tmp_path / "corrupt_meeting"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    (meeting_dir / "speaker_hints.json").write_text("{corrupt json", encoding="utf-8")

    args = argparse.Namespace(
        speaker="Will Booth",
        quote="",
        role="Managing Director",
        org="BCG",
        meeting_dir=str(meeting_dir),
        runtime_root=str(tmp_path),
        json=True,
        force=False,
        remove_attendee=None,
    )
    res = handle_speaker_hint(args)
    assert res["status"] == "PASS"
    repaired = json.loads((meeting_dir / "speaker_hints.json").read_text(encoding="utf-8"))
    assert isinstance(repaired, list)
    assert repaired[0]["role"] == "Managing Director"

    # Malformed records list with non-dicts
    malformed_records = ["not_a_dict", 123, None, {"speaker": "John Snipes", "metadata": "not_a_dict", "role": "Managing Director"}]
    extracted = extract_role_hints_from_records(malformed_records)
    assert extracted == {"John Snipes": "Managing Director"}


def test_exact_production_regression_neet_0928_bcg_managing_director() -> None:
    """Category: Exact production regression — APEX_TRANS 09/28/2026 parenthetical roster string vs manifest dict."""
    cli_attendees_str = (
        "Alex Rivera (Technology Lead, APEX_TRANS), "
        "Peter Ashley (Utility Domain Lead, PartnerAdvisory), "
        "John Snipes (Managing Director, BCG), "
        "Will Booth (BCG)"
    )
    manifest_attendees = [
        {"name": "Alex Rivera", "role": "Technology Lead", "org": "APEX_TRANS"},
        {"name": "Peter Ashley", "role": "Utility Domain Lead", "org": "PartnerAdvisory"},
        {"name": "John Snipes", "role": "Managing Director", "org": "BCG"},
        {"name": "Will Booth", "role": "Managing Director", "org": "BCG"},
    ]
    roles = extract_manifest_roles(cli_attendees_str, manifest_attendees)
    assert roles["Will Booth"] == "Managing Director"
    assert roles["John Snipes"] == "Managing Director"
    assert roles["Alex Rivera"] == "Technology Lead"
    assert roles["Peter Ashley"] == "Utility Domain Lead"

    # Also verify C0.6 prompt text triggers non-waivable exemption in check_ask_user_question.py
    c06_hook_payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "question": "To prevent role hallucination in Output 01, please select or confirm Will Booth's verified project title:",
                        "options": [
                            "(Recommended) Assign enterprise directory title to Will Booth: 'Managing Director' (Confidence: HIGH (85%))",
                            "Assign Technical Architect to Will Booth (Confidence: MEDIUM (60%))",
                        ],
                    }
                ]
            },
        }
    }
    decision = check_hook_payload(json.dumps(c06_hook_payload))
    assert decision.get("decision") == "allow"
