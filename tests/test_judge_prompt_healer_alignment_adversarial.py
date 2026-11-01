"""
Adversarial & Edge-Case Verification for Wave 3: Prompt-Judge Contract Alignment & Healer Elimination.

Covers mandatory edge-case categories:
1. Exact production regression:
   - Silent roster participants (0 spoken turns) must NOT receive fabricated `stmt-spk-XX` dialogue rows.
   - `graph_post_helpers._ensure_meeting_attendees` must emit canonical `OWNS` (never `LEADS`).
   - `graph_post_risk_healer.prune_and_budget_signals` must emit `INTEGRATES_WITH` (never `CONTAINS`)
     and must not inject ungrounded `"pending enterprise access diligence"` prose.
2. Boundary payload & Malformed input:
   - `_resolve_utterance_matched_evidence_ids` with empty evidence dict, malformed items, or non-matching
     utterances falls back cleanly without crashing.
   - `_canonicalize_entity_id` maps `service:`, `database:`, and `queue:` to `system:` within the W2 8-prefix allowlist.
3. Invariant bypass attempt:
   - Attempting to pass a `PASS_HEURISTIC_PRE_CHECK` scorecard into `validate_micro_judge_scorecard` as an
     authoritative LLM Judge scorecard fails closed.
   - Prompts and `recap_executor.py` align on `#` single-hash section headers, bare `First Last` DRI names,
     standard zero-action confirmation sentence, and 0-point elapsed-only time window exemption.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from dual_pipeline.llm_judge_core.heuristic_pre_judge import evaluate_heuristic_pre_judge
from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    RELATIONSHIP_PRIORITY as EXTRACTOR_REL_PRIORITY,
)
from dual_pipeline.transcript_stages_core.graph_post_helpers import (
    _canonicalize_entity_id,
    _ensure_meeting_attendees,
    _resolve_utterance_matched_evidence_ids,
)
from dual_pipeline.transcript_stages_core.graph_post_risk_healer import (
    prune_and_budget_signals,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import (
    _append_roster_grounding_rows,
    _build_roster_grounding_row,
    _is_silent_roster_participant,
)
from dual_pipeline.transcript_stages_core.recap_executor import _build_remediation_block

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWave3PromptHealerAlignmentAdversarial(unittest.TestCase):
    def test_silent_roster_participant_never_fabricates_dialogue_turn(self) -> None:
        """Exact production regression: silent roster participant (0 spoken turns) must not get fake stmt-spk-XX."""
        meta = {
            "silent_attendees": ["Jen Pan"],
            "speaker_turn_counts": {"Amit Ayer": 12, "Jake Stark": 9, "Jen Pan": 0},
            "turns": [
                {"speaker": "Amit Ayer", "text": "Let's review the LENS architecture."},
                {"speaker": "Jake Stark", "text": "We tested the dual-LLM agreement tier."},
            ],
        }
        assert _is_silent_roster_participant("Jen Pan", meta) is True
        assert _is_silent_roster_participant("Amit Ayer", meta) is False

        silent_row = _build_roster_grounding_row(
            name="Jen Pan",
            matched_node_id="person:jen-pan",
            idx=3,
            is_text=True,
            is_silent=True,
        )
        assert "| None (0 spoken turns) |" in silent_row
        assert "0 independently attributed spoken turns" in silent_row
        assert "stmt-spk-" not in silent_row

        rows: list[str] = []
        mapped: set[str] = {"amit ayer", "jake stark"}
        _append_roster_grounding_rows(
            rows=rows,
            roster_names=["Amit Ayer", "Jake Stark", "Jen Pan"],
            mapped_attendees=mapped,
            entity_id_by_name={"jen pan": "person:jen-pan"},
            start_idx=3,
            is_text=True,
            metadata=meta,
        )
        assert len(rows) == 1
        assert "None (0 spoken turns)" in rows[0]
        assert "stmt-spk-03" not in rows[0]

    def test_graph_post_healer_canonical_prefixes_and_predicates(self) -> None:
        """Exact production regression: no non-canonical CONTAINS/LEADS predicates or service/database/queue prefixes."""
        assert _canonicalize_entity_id("service:auth-api", "system") == "system:auth-api"
        assert _canonicalize_entity_id("database:postgres-main", "system") == "system:postgres-main"
        assert _canonicalize_entity_id("queue:event-bus", "system") == "system:event-bus"

        assert "CONTAINS" not in EXTRACTOR_REL_PRIORITY

        mutations = {
            "entity_upserts": [{"id": "system:lens", "name": "LENS", "type": "system"}],
            "relationship_signals": [],
        }
        meta = {
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (LENS Lead)", "Jake Stark (Project Lead)"],
            }
        }
        _ensure_meeting_attendees(mutations, meta, ["ev-01"])
        predicates = {r["relationship"] for r in mutations["relationship_signals"]}
        assert "LEADS" not in predicates
        assert "OWNS" in predicates

    def test_system_integration_edges_never_inject_contains_or_ungrounded_diligence(self) -> None:
        """Exact production regression: dashboard/GIS links use INTEGRATES_WITH and never inject ungrounded diligence text."""
        mutations = {
            "entity_upserts": [
                {"id": "system:lens", "name": "LENS", "type": "system"},
                {"id": "system:gis", "name": "GIS", "type": "system"},
            ],
            "relationship_signals": [
                {
                    "signal_id": "rel-lens--gis--CONTAINS",
                    "from_id": "system:lens",
                    "to_id": "system:gis",
                    "relationship": "CONTAINS",
                    "predicate": "CONTAINS",
                    "evidence_ids": ["ev-02"],
                }
            ],
        }
        ev_index = {
            "ev-01": {"exact_text": "Opening roll call."},
            "ev-02": {"exact_text": "LENS integrates with GIS for spatial mapping."},
        }
        prune_and_budget_signals(mutations, fallback_ev=["ev-02"], is_document=False, ev_index=ev_index)
        for r in mutations["relationship_signals"]:
            assert r.get("relationship") != "CONTAINS"
            assert r.get("predicate") != "CONTAINS"
            assert "pending enterprise access diligence" not in str(r.get("description", ""))

    def test_utterance_matched_evidence_resolution_boundary_and_malformed(self) -> None:
        """Boundary & malformed input: _resolve_utterance_matched_evidence_ids matches lexical overlap and handles empty/malformed inputs."""
        assert _resolve_utterance_matched_evidence_ids(["Unifier migration"], None, ["ev-02"]) == ["ev-02"]
        assert _resolve_utterance_matched_evidence_ids(["Unifier migration"], {}, ["ev-01"]) == []

        ev_index = {
            "ev-01": {"speaker": "Alice", "exact_text": "Good morning everyone, welcome to the call."},
            "ev-02": {"speaker": "Bob", "exact_text": "Oracle Primavera P6 schedule integration is blocked by firewall rules."},
            "ev-03": {"speaker": "Carol", "exact_text": "We will review Documentum permissions tomorrow."},
        }
        matched = _resolve_utterance_matched_evidence_ids(
            ["Oracle Primavera P6 schedule integration blocked", "firewall rules"],
            ev_index,
            ["ev-01"],
        )
        assert matched == ["ev-02"]

    def test_heuristic_pre_judge_6_dimensions_and_fail_closed_scorecard_guard(self) -> None:
        """Invariant bypass attempt: heuristic pre-judge includes all 6 canonical dimensions and cannot pass validate_micro_judge_scorecard as an authoritative PASS."""
        f1 = "Amit Ayer discussed LENS architecture and dual-LLM pipeline confidence thresholds."
        f3 = '{"nodes": [{"id": "system:lens", "name": "LENS", "type": "system"}], "edges": []}'
        pre_res = evaluate_heuristic_pre_judge(f3, f1_text=f1)
        assert pre_res["passed_pre_check"] is True
        assert pre_res["is_heuristic_pre_check_only"] is True
        for dim_key in (
            "dimension_1_strategic_edge",
            "dimension_2_knowledge_layer_density",
            "dimension_3_semantic_atomicity",
            "dimension_4_predicate_specificity",
            "dimension_5_exclusion_of_action_items",
            "dimension_6_evidence_grounding_and_schema",
        ):
            assert dim_key in pre_res

        ok, errs = validate_micro_judge_scorecard(pre_res, output_num=3)
        assert ok is False
        assert any("Heuristic pre-judge" in e for e in errs)

    def test_prompt_and_executor_contract_alignment(self) -> None:
        """Verify Stage 02/03 prompts, judge prompts, and recap_executor have zero contradictory directives."""
        rem_xml = _build_remediation_block(
            metadata={"archetype": "ARCHITECTURE_DEEP_DIVE"},
            forensic_transcript="**[00:01:00] Amit Ayer:** Test.",
            directive="Fix Action Ledger",
            prior_candidate=None,
        )
        assert "No formal actions were established in this session; open initiatives remain unresolved questions." in rem_xml

        p02 = (REPO_ROOT / "config/prompts/synthesis/stages/02_executive_recap.md").read_text(encoding="utf-8")
        assert "Attributed Ownership with Company Context" not in p02
        assert "Delivery Partner (Jane Smith, Engineering Lead)" not in p02
        assert "leading persona anchor" in p02

        p02_arch = (REPO_ROOT / "config/prompts/synthesis/stages/02_architecture_deep_dive_override.md").read_text(encoding="utf-8")
        assert "## Meeting Objective" not in p02_arch
        assert "# Meeting Objective" in p02_arch
        assert "rather than assigned tasks" not in p02_arch

        p03_arch = (REPO_ROOT / "config/prompts/synthesis/stages/03_architecture_deep_dive_override.md").read_text(encoding="utf-8")
        assert "`service:`, `database:`, `queue:`" not in p03_arch

        p03 = (REPO_ROOT / "config/prompts/synthesis/stages/03_graph_extractor.md").read_text(encoding="utf-8")
        assert "OWNS_OR_LEADS" not in p03

        j02 = (REPO_ROOT / "governance/decision_intelligence/EXECUTIVE_RECAP_JUDGE_PROMPT.md").read_text(encoding="utf-8")
        assert "Elapsed-Only Time Window Exemption" in j02
        assert "0-point deduction" in j02


if __name__ == "__main__":
    unittest.main()
