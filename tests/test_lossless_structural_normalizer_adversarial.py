"""Adversarial & edge-case tests for Wave 3 Lossless Structural Normalizer and Healing Redesign.

Covers mandatory edge-case categories:
1. Boundary payload: Empty inputs, zero-entity projections, single-entity document payloads.
2. Malformed input: Truncated/invalid JSON, missing schema keys, spliced multi-turn locators.
3. Invariant bypass attempt: Protected entity removal blocked by JsonSurgicalPatcher,
   protected markdown section mutation blocked by SurgicalPatcher, unauthenticated
   '(Operator Verified)' claims preserved in Output 1, and deleted meeting-specific
   overfit healer modules verified absent from disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    heal_disconnected_document_entities,
)
from dual_pipeline.transcript_qa_core.forensic_healer import heal_output_1_forensic
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.json_surgical_patcher import JsonSurgicalPatcher
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_projection_evidence_and_coverage,
)
from dual_pipeline.transcript_qa_core.surgical_patcher import (
    ProtectedPathMutationError,
    SurgicalPatcher,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestLosslessStructuralNormalizerAdversarial:
    """Adversarial test suite for Wave 3 lossless structural normalizer and healer consolidation."""

    def test_boundary_payload_empty_and_minimal_inputs(self) -> None:
        """Category 1 (Boundary payload): Empty strings and minimal JSON payloads do not crash or fabricate."""
        assert heal_output_1_forensic("") == ""
        assert heal_output_2_recap("") == ""
        healed_empty_f3, _changes = DeterministicHealer.heal_output_3("")
        parsed_empty = json.loads(healed_empty_f3)
        assert parsed_empty.get("entity_upserts") == []
        assert parsed_empty.get("relationship_signals") == []

        single_ent_doc = {
            "graph_mutations": {
                "entity_upserts": [{"id": "project:alpha", "type": "project", "name": "Alpha"}],
                "relationship_signals": [],
            }
        }
        assert heal_disconnected_document_entities(single_ent_doc) == []

    def test_malformed_input_truncated_json_and_spliced_locators(self) -> None:
        """Category 2 (Malformed input): Invalid JSON is returned losslessly without crashing; spliced locators normalize."""
        malformed_json = '{"schema_version": "5.7", "graph_mutations": {'
        healed_str, changes = DeterministicHealer.heal_output_3(malformed_json)
        parsed_malformed = json.loads(healed_str)
        assert parsed_malformed["schema_version"] == "5.7"
        assert len(changes) >= 1

        spliced_payload = {
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "speaker": "Alex Sanchez",
                    "text": "First clause [...] second clause",
                    "exact_text": "First clause [...] second clause",
                    "locator": {"kind": "timestamp", "value": "00:01:15 - 00:02:30"},
                }
            },
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        }
        f1_text = "### [00:01:15]\n\n**Alex Sanchez:** First clause and middle context and second clause\n"
        heal_projection_evidence_and_coverage(spliced_payload, f1_text=f1_text)
        ev = spliced_payload["evidence_index"]["ev-01"]
        assert ev["locator"]["value"] == "00:01:15"
        assert "[...]" not in ev["exact_text"]

    def test_invariant_bypass_protected_section_and_entity_immutability(self) -> None:
        """Category 3 (Invariant bypass attempt): SurgicalPatcher and JsonSurgicalPatcher block protected mutations."""
        md = (
            "# Executive Recap\n\n"
            "## Executive Summary\nProtected summary text.\n\n"
            "## Action Items\nInitial action items.\n"
        )
        with pytest.raises(ProtectedPathMutationError):
            SurgicalPatcher.apply_surgical_patch(
                original_text=md,
                target_section="Executive Summary",
                new_section_content="Tampered summary.",
                protected_sections=["Executive Summary"],
            )

        base_graph = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "speaker": "Alex Sanchez",
                    "kind": "technical",
                    "source_classification": "FACT",
                    "text": "System LENS processes asset nameplates.",
                    "exact_text": "System LENS processes asset nameplates.",
                    "locator": {"kind": "timestamp", "value": "00:01:00"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:lens",
                        "type": "system",
                        "name": "LENS",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["ev-01"],
                    }
                ],
                "relationship_signals": [],
            },
        }
        with pytest.raises(ProtectedPathMutationError):
            JsonSurgicalPatcher.apply_patch(
                original_json=base_graph,
                critique_diff_or_envelope={"remove_entities": ["system:lens"]},
                protected_entities=["system:lens"],
            )

    def test_invariant_bypass_unauthenticated_operator_verified_preserved(self) -> None:
        """Category 3 (Invariant bypass attempt): Unauthenticated '(Operator Verified)' is not silently masked."""
        raw_f1 = (
            "# Forensic Transcript\n\n"
            "[00:01:05] **Speaker 1 (Operator Verified):** We reviewed the architecture.\n"
        )
        healed_f1 = heal_output_1_forensic(raw_f1, metadata={"hitl_receipt_bound": False})
        assert "Operator Verified" in healed_f1

    def test_deleted_meeting_specific_healer_modules_absent(self) -> None:
        """Verify all 9 retired meeting-specific healer/ontology files are removed and budgets hold."""
        retired_files = [
            "dual_pipeline/transcript_qa_core/standup_1009_content.py",
            "dual_pipeline/transcript_qa_core/engineering_jarvis_appendix.py",
            "dual_pipeline/transcript_qa_core/engineering_jarvis_forensic.py",
            "dual_pipeline/transcript_qa_core/engineering_jarvis_graph.py",
            "dual_pipeline/transcript_qa_core/jarvis_charter_ontology.py",
            "dual_pipeline/transcript_qa_core/jarvis_kickoff_appendix.py",
            "dual_pipeline/transcript_qa_core/jarvis_kickoff_forensic.py",
            "dual_pipeline/transcript_qa_core/neet_insights_hub_graph.py",
            "dual_pipeline/transcript_stages_core/graph_ontology_healer.py",
        ]
        for rel_path in retired_files:
            assert not (REPO_ROOT / rel_path).exists(), f"Retired overfit file still exists: {rel_path}"

        graph_healer_lines = len(
            (REPO_ROOT / "dual_pipeline/transcript_qa_core/graph_healer.py").read_text(encoding="utf-8").splitlines()
        )
        recap_healer_lines = len(
            (REPO_ROOT / "dual_pipeline/transcript_qa_core/recap_healer.py").read_text(encoding="utf-8").splitlines()
        )
        assert graph_healer_lines <= 400
        assert recap_healer_lines <= 350

        # Verify heal_output_3_graph preserves valid schema 5.7 structure losslessly
        sample_f3 = json.dumps(
            {
                "schema_version": "5.7",
                "meeting_id": "meeting-test",
                "evidence_index": {
                    "ev-01": {
                        "statement_id": "ev-01",
                        "speaker": "Alex Sanchez",
                        "kind": "technical",
                        "source_classification": "FACT",
                        "text": "The pipeline validates asset tags.",
                        "exact_text": "The pipeline validates asset tags.",
                        "locator": {"kind": "timestamp", "value": "00:01:00"},
                    }
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {
                            "id": "person:alex-sanchez",
                            "type": "person",
                            "name": "Alex Sanchez",
                            "mutation": "CONFIRM",
                            "evidence_ids": ["ev-01"],
                        }
                    ],
                    "relationship_signals": [],
                },
            }
        )
        out_f3 = heal_graph_projection(json.loads(sample_f3), [])
        assert out_f3["schema_version"] == "5.7"
        assert any(e["id"] == "person:alex-sanchez" for e in out_f3["graph_mutations"]["entity_upserts"])
