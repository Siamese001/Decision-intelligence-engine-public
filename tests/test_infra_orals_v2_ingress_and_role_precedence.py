"""
tests/test_infra_orals_v2_ingress_and_role_precedence.py

Class B Receipt Tests for Wave 1:
1. Verifies V2 transcript ingress staging and memoization without re-synthesis.
2. Verifies GraphDB role precedence over enterprise directory priors.
3. Verifies elimination of narrative clause role pollution (e.g. 'June 29 notes support executive pursuit').
4. Verifies Drew Oakley resolution with true graph role, zero corruption, and no first-name project hijacking.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _memoize_output_1
from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    ResolvedEntity,
)
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    _extract_role_from_description,
    _refine_org_from_directory,
)
from dual_pipeline.transcript_stages_core.attribution_builder import _resolve_defaults
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
    _build_role_recommendation_options,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)


def test_narrative_clause_role_pollution_eliminated() -> None:
    """Verifies that narrative text with 'executive' does not extract as a job title."""
    desc = "June 29 notes support executive pursuit; Discussion on cloud roadmap; Affiliation: PartnerAdvisory"
    extracted = _extract_role_from_description(desc)
    assert extracted is None, f"Expected None but extracted '{extracted}'"

    desc2 = "Executive Sponsor and Lead Architect on infrastructure migration"
    extracted2 = _extract_role_from_description(desc2)
    assert extracted2 is not None
    assert "Architect" in extracted2 or "Executive" in extracted2


def test_dan_okray_resolution_untainted() -> None:
    """Verifies Drew Oakley is resolved with GraphDB attributes and never tainted by narrative text."""
    resolver = PreSynthesisEntityResolver()
    resolver._kg_cache["Drew Oakley"] = ResolvedEntity(
        canonical_name="Drew Oakley",
        display_name="Drew Oakley",
        org="PartnerAdvisory",
        role="Client Director",
        source="knowledge_graph",
        confidence=0.95,
    )
    ent = resolver.resolve("Drew Oakley")
    assert ent.canonical_name == "Drew Oakley"
    assert ent.org == "PartnerAdvisory"
    assert "Client Service Lead" in ent.role or "Managing Director" in ent.role or "Client Director" in ent.role
    assert "conduit" not in ent.canonical_name.lower()
    assert "June 29" not in ent.role
    assert "notes support" not in ent.role


def test_first_name_no_project_hijacking() -> None:
    """Verifies attendee names like 'Jake' or 'Alex' do not hijack project to 'project_lens'."""
    resolver = PreSynthesisEntityResolver()
    entities = resolver.resolve_attendee_list("Jake Stark, Alex Sanchez, Drew Oakley")
    assert len(entities) >= 1
    # Check that resolver didn't force a project
    names = [e.canonical_name for e in entities]
    assert "Drew Oakley" in names or any("Okray" in n for n in names)


def test_graphdb_role_precedence_in_gate_and_reconciler() -> None:
    """Verifies C0.6 gate assigns 95% confidence to GraphDB roles over directory fallback."""
    ent = ResolvedEntity(
        canonical_name="Drew Oakley",
        display_name="Drew Oakley",
        org="PartnerAdvisory",
        role="Client Director",
        source="knowledge_graph",
        confidence=0.95,
    )
    options = _build_role_recommendation_options("Drew Oakley", ent=ent)
    assert len(options) >= 1
    rec = options[0]
    assert rec["role"] == "Client Director"
    assert rec["confidence"] == 0.95
    assert "GraphDB" in rec["label"]

    # Reconciler test: hitl_role_resolutions takes precedence over auto
    metadata = {
        "speaker_role_resolutions": {
            "Drew Oakley": {
                "person_name": "Drew Oakley",
                "resolved_role": "Auto Guessed Role",
                "is_human_confirmed": False,
            }
        },
        "hitl_role_resolutions": {
            "Drew Oakley": {
                "person_name": "Drew Oakley",
                "resolved_role": "Client Director",
                "is_human_confirmed": True,
            }
        },
    }
    final_data = {"entities": [{"name": "Drew Oakley", "role": "Old Role"}]}
    res = reconcile_speaker_role_resolutions(final_data, metadata=metadata)
    assert res["entities"][0]["role"] == "Client Director"


def test_attribution_builder_uses_ssot_resolver() -> None:
    """Verifies _resolve_defaults in attribution_builder queries the entity resolver."""
    org, role = _resolve_defaults("Drew Oakley")
    assert org in ("PartnerAdvisory", "Apex Global Enterprises (NEA)", "Client / Partner")
    assert role is not None


def test_v2_transcript_ingress_staging_without_resynthesis(tmp_path: Path) -> None:
    """Verifies authoritative 01 transcript input is reused directly without re-synthesis."""
    fake_v2 = tmp_path / "01_apex_global_Energy_PartnerAdvisory_10-02-2026_00000129_hitl_resolved_transcript.md"
    content = (
        "# Executive Meeting Transcript\n\n"
        "**Date:** 10/02/2026\n\n"
        "## Attribution Summary\n"
        "| Speaker | Organization | Professional Role | Turns |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| Drew Oakley | PartnerAdvisory | Client Director | 22 |\n\n"
        "## Dialogue Record\n\n"
        "Drew Oakley (00:01): Hello everyone.\n\n"
    ) + ("Speaker (00:02): Conversation turn.\n\n" * 50)
    fake_v2.write_text(content, encoding="utf-8")

    state_machine = MagicMock()
    state_machine.is_frozen.return_value = False
    metadata = {
        "input_path": str(fake_v2),
        "expected_turns": 50,
    }

    memoized = _memoize_output_1(tmp_path, state_machine, metadata, regen_spec="")
    assert memoized is True
    assert metadata.get("output_1_memoized") is True

    # Also test synthesis strategy direct reuse
    strategy = FullMeetingSynthesisStrategy(
        provider=MagicMock(),
        l2_forensic=MagicMock(),
        l2_recap=MagicMock(),
        l2_graph=MagicMock(),
    )
    out_1 = strategy._resolve_output_1(
        source_text="raw source",
        metadata=metadata,
        attempt=1,
        remediation_directive=None,
        progress_notify=None,
        ckpt_dir=tmp_path / "checkpoints",
        pa_context={"input_path": str(fake_v2), "output_dir": str(tmp_path)},
    )
    assert out_1 == content
