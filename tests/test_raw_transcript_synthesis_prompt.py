from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"
MIRROR_PROMPT = ROOT / "config" / "prompts" / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"


class RawTranscriptSynthesisPromptTests(unittest.TestCase):
    def test_prompt_files_exist_and_match(self) -> None:
        self.assertTrue(PROMPT.is_file(), f"Missing canonical prompt: {PROMPT}")
        self.assertTrue(MIRROR_PROMPT.is_file(), f"Missing mirror prompt: {MIRROR_PROMPT}")
        canonical_text = PROMPT.read_text(encoding="utf-8")
        mirror_text = MIRROR_PROMPT.read_text(encoding="utf-8")
        self.assertGreater(len(canonical_text), 10000)
        self.assertEqual(canonical_text, mirror_text, "Mirror prompt differs from canonical prompt")

    def test_three_artifact_delivery_contract(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        for required_artifact in (
            "01_hitl_resolved_transcript.md",
            "02_executive_recap.md",
            "03_canonical_signal_projection.json",
        ):
            self.assertIn(required_artifact, text)
        self.assertIn("downloadable links only", text.lower())
        self.assertIn("exactly three required artifacts", text.lower())

    def test_master_constraints_registry_complete(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        for c_id in (
            "C-01",
            "C-02",
            "C-03",
            "C-04",
            "C-05",
            "C-06",
            "C-07",
            "C-08",
            "C-09",
            "C-10",
            "C-11",
            "C-12",
            "C-13",
            "C-14",
            "C-15",
            "C-16",
            "C-17",
            "C-18",
            "C-19",
            "C-20",
            "C-21",
            "C-22",
            "C-23",
            "C-24",
            "C-25",
            "C-26",
        ):
            self.assertIn(c_id, text)

    def test_downstream_schema_alignment(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        for kind in ("summary", "decision", "risk", "dependency", "technical", "strategic"):
            self.assertIn(f"`{kind}`", text)
        for internal_cls in (
            "FACT",
            "ACTION",
            "DECISION",
            "RISK",
            "PROPOSAL",
            "ALIGNMENT",
            "COMMITMENT",
            "OPEN QUESTION",
            "INFERENCE",
        ):
            self.assertIn(f"`{internal_cls}`", text)

    def test_w3_temporal_observation_fields(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn('"observed_at"', text)
        self.assertIn('"stale_after"', text)

    def test_atomic_optionated_confidence_hitl_controls(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Atomic Presentation", text)
        self.assertIn("one decision at a time", text.lower())
        self.assertIn("Calibrated Confidence", text)
        self.assertIn("15% Margin Gating", text)
        self.assertNotIn("show the batched hitl questions inline", text.lower())

    def test_V5_5_structural_additions(self) -> None:
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("_audit_rationale", text)
        self.assertIn("Single Promotion Principle", text)
        self.assertIn("Risk Materiality Gate", text)
        self.assertIn("Negative Exclusion Filter", text)
        self.assertIn("GRAPH SIGNAL GATE & NEGATIVE EXCLUSION FILTER", text)
        self.assertIn('The "Who" Filter', text)
        self.assertIn("Prior Context Protocol (C-16)", text)
        self.assertIn("Hard ceiling (C-17)", text)
        self.assertIn("Idempotent Signal IDs (C-18)", text)
        self.assertIn("risk_scope", text)
        self.assertIn("GRAPH LIFECYCLE", text)
        self.assertIn("graph_gc.py", text)
        self.assertIn("Proposal Lifecycle State Machine", text)

    def test_agents_md_enforcement(self) -> None:
        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Raw transcript synthesis is contract-bound and artifact-exact", agents_text)
        self.assertIn("RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md", agents_text)
        self.assertIn("01_hitl_resolved_transcript.md", agents_text)
        self.assertIn("02_executive_recap.md", agents_text)
        self.assertIn("03_canonical_signal_projection.json", agents_text)

    def test_hooks_enforcement(self) -> None:
        hooks_data = json.loads((ROOT / ".agents" / "hooks.json").read_text(encoding="utf-8"))
        hook_ids = {h.get("id") for h in hooks_data.get("hooks", [])}
        self.assertIn("nee-transcript-synthesis-guard", hook_ids)

    def test_rules_enforcement(self) -> None:
        rule_path = ROOT / ".agents" / "rules" / "transcript-synthesis.md"
        self.assertTrue(rule_path.is_file(), f"Missing rule: {rule_path}")
        rule_text = rule_path.read_text(encoding="utf-8")
        self.assertIn("RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md", rule_text)

        conformance_text = (ROOT / ".agents" / "rules" / "conformance-map.md").read_text(encoding="utf-8")
        self.assertIn("RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md", conformance_text)
        self.assertIn("transcript-synthesis.md", conformance_text)
        self.assertIn("nee-transcript-synthesis-guard", conformance_text)

    def test_email_ready_executive_recap_structure(self) -> None:
        """Executive recap must be an email-ready distribution with required metadata and Meeting Objective."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Email Distribution Format", text)
        self.assertIn("Meeting Objective", text)
        self.assertIn("replaces Opening Context", text)
        self.assertIn("MM/DD/YYYY", text)
        self.assertIn("ET", text)
        self.assertIn("PartnerAdvisory", text)
        self.assertIn("NEA / Apex Global Enterprises", text)

    def test_actionability_gate_and_anti_embarrassment_guard(self) -> None:
        """Actions must be tangible and active; passive reflections like Conduit suspension are forbidden."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Strict Actionability Gate", text)
        self.assertIn("Anti-Embarrassment Gate", text)
        self.assertIn("Acknowledge Conduit suspension", text)
        self.assertIn("Active Imperative Verb", text)
        self.assertIn("Concrete Deliverable", text)
        self.assertIn("Attributed Ownership with Company Context", text)
        self.assertIn("Target Timing / Deadline", text)

    def test_parameterized_filenames_with_meeting_metadata(self) -> None:
        """Output filenames must incorporate meeting name, date, and time separated by underscores."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("<meeting_name>_<date>_<time>", text)
        self.assertIn("01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md", text)
        self.assertIn("02_<meeting_name>_<date>_<time>_executive_recap.md", text)
        self.assertIn("03_<meeting_name>_<date>_<time>_canonical_signal_projection.json", text)

    def test_h1_one_shot_exemplars_present(self) -> None:
        """H-1: BAD vs GOOD one-shot examples must be embedded across categories."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("One-Shot Exemplar: Standard Meeting Actions", text)
        self.assertIn("BAD (Anti-Pattern)", text)
        self.assertIn("Why it fails", text)
        self.assertIn("One-Shot Exemplar: Retrospective", text)
        self.assertIn("Catastrophic Anti-Pattern", text)
        self.assertIn("Why it works", text)
        self.assertIn("Acknowledge alignment on sprint priorities", text)
        self.assertIn("Locate and share Jenny Koch", text)

    def test_h2_signal_density_validation_gate(self) -> None:
        """H-2: Signal-density gate must enforce minimum action rows based on meeting duration."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Signal-Density Validation Gate", text)
        self.assertIn("floor(meeting_duration_minutes / 10)", text)
        self.assertIn("re-scan the transcript", text.lower())
        self.assertIn("Verbal commitments", text)
        self.assertIn("NEVER pad the table", text)

    def test_h3_meeting_type_awareness(self) -> None:
        """H-3: Retrospective meeting type guidance must be present with extraction rules."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("MEETING TYPE AWARENESS", text)
        self.assertIn("Retrospective / Lessons Learned / Post-Mortem", text)
        self.assertIn("Retrospective Meeting Extraction Rules", text)
        self.assertIn("forward-looking meta-actions", text)
        self.assertIn("NEVER produce a retrospective recap with ZERO forward-looking actions", text)

    def test_h4_actions_vs_findings_cross_validation(self) -> None:
        """H-4: Cross-validation gate must check findings vs. actions alignment."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Actions-vs-Findings Cross-Validation Gate", text)
        self.assertIn("corresponding row in the Next Steps table", text)
        self.assertIn("no action committed", text)

    def test_h5_tightened_social_coordination_boundary(self) -> None:
        """H-5: Social items must be excluded from Next Steps table."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Social & Coordination Boundary (Tightened)", text)
        self.assertIn("foreground the deliverable", text)
        self.assertIn("EXCLUDE from the Next Steps table entirely", text)

    def test_h6_owner_attribution_waterfall(self) -> None:
        """H-6: Owner attribution must follow 5-level waterfall precedence."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Owner Attribution Waterfall", text)
        self.assertIn("Explicit verbal acceptance", text)
        self.assertIn("Role-implied with escalation", text)
        self.assertIn("Request with affirmation", text)
        self.assertIn("Request without affirmation", text)
        self.assertIn("General suggestion without acceptance", text)

    # ---- v5.7 Hardening Tests (Wave 5) ----

    def test_balanced_meeting_taxonomy(self) -> None:
        """All 5 meeting categories must have extraction rules and density floors."""
        text = PROMPT.read_text(encoding="utf-8")
        for category in (
            "Forward-Looking",
            "Operational",
            "Collaborative",
            "Backward-Looking",
            "Informational",
        ):
            self.assertIn(category, text, f"Missing meeting category: {category}")
        self.assertIn("floor(meeting_duration_minutes / 10)", text)
        self.assertIn("floor(meeting_duration_minutes / 12)", text)
        self.assertIn("floor(meeting_duration_minutes / 15)", text)
        self.assertIn("floor(meeting_duration_minutes / 20)", text)
        self.assertIn("floor(meeting_duration_minutes / 30)", text)

    def test_no_hardcoded_company_in_template(self) -> None:
        """Recap template must not hardcode specific company names as mandatory bullet labels."""
        text = PROMPT.read_text(encoding="utf-8")
        template_section = text[text.index("## Structure (Email Distribution Format)"):text.index("## Signal Priority")]
        self.assertNotIn("- **PartnerAdvisory:**", template_section)
        self.assertNotIn("- **NEA / Apex Global Enterprises:**", template_section)
        self.assertIn("[Delivery Partner / Consulting Firm]", template_section)
        self.assertIn("[Client Organization]", template_section)

    def test_cross_output_consistency_gate_present(self) -> None:
        """Cross-output consistency validation must exist."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Cross-Output Consistency Gate", text)
        self.assertIn("must not contradict each other", text.lower())

    def test_schema_version_in_output_spec(self) -> None:
        """Output JSON schema must include schema_version field."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn('"schema_version": "5.7"', text)

    def test_changelog_separated_from_prompt(self) -> None:
        """Version summary should be extracted to CHANGELOG file."""
        text = PROMPT.read_text(encoding="utf-8")
        changelog_path = PROMPT.parent / "CHANGELOG_SYNTHESIS_PROMPT.md"
        self.assertTrue(changelog_path.is_file(), "Missing changelog file")
        version_section_start = text.index("# 13.")
        version_section = text[version_section_start:]
        self.assertLess(
            len(version_section.splitlines()),
            25,
            "Version section should be a short stub, not a full changelog"
        )

    def test_v57_version_stub(self) -> None:
        """v5.7 version stub must be present."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("v5.7", text)
        self.assertIn("full replacement for v5.6", text.lower())

    def test_governed_output_directory_hierarchy(self) -> None:
        """Outputs folder must be organized by project, then meeting date, then all outputs in each meeting."""
        text = PROMPT.read_text(encoding="utf-8")
        self.assertIn("Output Directory Organization", text)
        self.assertIn("output/<project_name>/<meeting_date>/", text)
        self.assertIn("nexus_reid_nea", text)
        self.assertIn("project_nexus", text)

        agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Governed Output Directory Hierarchy", agents_text)
        self.assertIn("output/<project_name>/<meeting_date>/", agents_text)
        self.assertIn("nexus_reid_nea", agents_text)
        self.assertIn("project_nexus", agents_text)

        rule_text = (ROOT / ".agents" / "rules" / "transcript-synthesis.md").read_text(encoding="utf-8")
        self.assertIn("Governed Output Directory Hierarchy", rule_text)
        self.assertIn("output/<project_name>/<meeting_date>/", rule_text)
        self.assertIn("nexus_reid_nea", rule_text)
        self.assertIn("project_nexus", rule_text)

    def test_output_filesystem_hierarchy_conformance(self) -> None:
        """Physical output/ directory must strictly pass hierarchy validation with 0 violations."""
        from tools.validate_output_hierarchy import validate_output_hierarchy
        violations = validate_output_hierarchy(ROOT / "output")
        self.assertEqual(violations, [], f"Output hierarchy violations detected: {violations}")

    def test_transcript_cache_anti_pollution_firewall(self) -> None:
        """Verify that any final deliverable (01, 02, 03) or receipt in transcript_cache is blocked."""
        import tempfile

        from tools.validate_output_hierarchy import validate_transcript_cache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "transcript_cache"
            cache_dir.mkdir()

            # Legitimate raw cache files should produce 0 violations
            (cache_dir / "meeting.transcript_cache.txt").write_text("raw whisper text", encoding="utf-8")
            (cache_dir / "meeting.segments.json").write_text("[]", encoding="utf-8")
            (cache_dir / "chunk_001.wav").write_text("", encoding="utf-8")
            self.assertEqual(validate_transcript_cache(cache_dir), [])

            # Injecting final deliverables or receipts must fail closed
            prohibited_samples = [
                "01_Meeting_10-02-2026_hitl_resolved_transcript.md",
                "02_Meeting_10-02-2026_executive_recap.md",
                "03_Meeting_10-02-2026_canonical_signal_projection.json",
                "qa_gates_report.json",
                "llm_judge_evaluation.json",
                "final_release_decision.json",
                "intake_package.json",
            ]
            for bad_file in prohibited_samples:
                nested_dir = cache_dir / "sub_project" / "10-02-2026"
                nested_dir.mkdir(parents=True, exist_ok=True)
                target = nested_dir / bad_file
                target.write_text("prohibited", encoding="utf-8")

                violations = validate_transcript_cache(cache_dir)
                self.assertTrue(
                    any("[TRANSCRIPT_CACHE_FINAL_OUTPUT_POLLUTION]" in v and bad_file in v for v in violations),
                    f"Expected violation for prohibited cache deliverable '{bad_file}', got: {violations}"
                )
                target.unlink()


if __name__ == "__main__":
    unittest.main()

