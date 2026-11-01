"""
tests/end_to_end/test_document_high_signal_pipeline_e2e.py

End-to-End Test Suite for Multi-Slide High-Signal Extraction & Remediation:
1. Verifies that under-extracted 25-slide projections fail Gate 3 deterministically
   with DOCUMENT_SLIDE_COVERAGE_GAP and DOCUMENT_YIELD_DEFICIT.
2. Verifies that partial slide coverage (e.g. early + middle only) fails Gate 3.
3. Verifies that full multi-bucket coverage (early, middle, late) with >= 8 substantive
   mutations passes Gate 3 deterministically.
4. Verifies that additive deficits and slide coverage gaps cleanly bypass existing-object
   bounded patch in prepare_document_patch_or_handoff, setting document_patch_request to None
   so Attempt 2 performs a full Stage 03 re-extraction.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.canonical_pipeline.document_remediation import (
    prepare_document_patch_or_handoff,
)
from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_qa_gates,
)
from dual_pipeline.document_pipeline_core.quality import (
    validate_slide_deck_coverage_and_yield,
)


def _build_25_slide_fixture() -> str:
    """Build a synthetic 25-slide document source text with verifiable spans."""
    slides = []
    for i in range(1, 26):
        slides.append(
            f"## Page {i}\n\n"
            f"Slide {i} Header: Section {i} Analysis and Capabilities.\n"
            f"Verbatim text on slide {i} confirms material evidence for topic {i}.\n"
        )
    return "\n".join(slides)


class TestDocumentHighSignalPipelineE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.source_text = _build_25_slide_fixture()
        self.metadata = {
            "source_units": [{"locator": f"p{i}"} for i in range(1, 26)],
            "page_count": 25,
            "project": "internal_consulting",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
        }

    def test_under_extracted_25_slide_projection_fails_coverage_and_yield(self) -> None:
        """A 2-node projection citing only Slide 1 must fail Gate 3 with both gap errors."""
        under_extracted = {
            "schema_version": "5.7",
            "meeting_id": "document-20261007-ams-deck",
            "meeting_archetype": "DOCUMENT_MODE",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "expected_base_graph_version": "1.0.0",
            "evidence_index": {
                "ev-01": {
                    "speaker": "Author",
                    "source_classification": "FACT",
                    "exact_text": "Verbatim text on slide 1 confirms material evidence for topic 1.",
                    "locator": {"kind": "slide", "value": "p1"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "org:partner_advisory",
                        "type": "org",
                        "name": "PartnerAdvisory",
                        "mutation": "CREATE",
                        "evidence_ids": ["ev-01"],
                    },
                    {
                        "id": "system:partner_advisory-traverse",
                        "type": "system",
                        "name": "PartnerAdvisory Traverse",
                        "mutation": "CREATE",
                        "evidence_ids": ["ev-01"],
                    },
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }

        qa_report = evaluate_document_qa_gates(
            under_extracted,
            source_text=self.source_text,
            metadata=self.metadata,
        )
        self.assertEqual(qa_report["status"], "FAIL")
        q3 = qa_report["output_03_canonical_signal_projection"]
        self.assertFalse(q3["passed"])

        failures = q3.get("failures", [])
        gap_err = any("DOCUMENT_SLIDE_COVERAGE_GAP" in str(f) for f in failures)
        yield_err = any("DOCUMENT_YIELD_DEFICIT" in str(f) for f in failures)
        self.assertTrue(gap_err, f"Expected DOCUMENT_SLIDE_COVERAGE_GAP in failures: {failures}")
        self.assertTrue(yield_err, f"Expected DOCUMENT_YIELD_DEFICIT in failures: {failures}")

        telemetry = q3.get("document_slide_coverage", {})
        self.assertEqual(telemetry.get("page_count"), 25)
        self.assertIn("middle", telemetry.get("missing_buckets", []))
        self.assertIn("late", telemetry.get("missing_buckets", []))

    def test_partial_slide_coverage_fails_closed(self) -> None:
        """A projection with >= 8 mutations but citing only early and middle slides must fail Gate 3."""
        partial_projection = {
            "schema_version": "5.7",
            "meeting_id": "document-20261007-ams-deck",
            "meeting_archetype": "DOCUMENT_MODE",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "expected_base_graph_version": "1.0.0",
            "evidence_index": {
                "ev-01": {
                    "speaker": "Author",
                    "source_classification": "FACT",
                    "exact_text": "Verbatim text on slide 2 confirms material evidence for topic 2.",
                    "locator": {"kind": "slide", "value": "Slide 2"},
                },
                "ev-02": {
                    "speaker": "Author",
                    "source_classification": "FACT",
                    "exact_text": "Verbatim text on slide 12 confirms material evidence for topic 12.",
                    "locator": {"kind": "slide", "value": "Slide 12"},
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": f"entity:{i}", "type": "concept", "name": f"Concept {i}", "mutation": "CREATE", "evidence_ids": ["ev-01"]}
                    for i in range(1, 9)
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }

        qa_report = evaluate_document_qa_gates(
            partial_projection,
            source_text=self.source_text,
            metadata=self.metadata,
        )
        self.assertEqual(qa_report["status"], "FAIL")
        q3 = qa_report["output_03_canonical_signal_projection"]
        failures = q3.get("failures", [])
        self.assertTrue(any("DOCUMENT_SLIDE_COVERAGE_GAP" in str(f) for f in failures))
        telemetry = q3.get("document_slide_coverage", {})
        self.assertEqual(telemetry.get("missing_buckets"), ["late"])

    def test_full_multi_bucket_coverage_passes_gate_3(self) -> None:
        """A projection with >= 8 substantive mutations spanning early, middle, and late slides passes Gate 3."""
        full_projection = {
            "schema_version": "5.7",
            "meeting_id": "document-20261007-ams-deck",
            "meeting_archetype": "DOCUMENT_MODE",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "expected_base_graph_version": "1.0.0",
            "evidence_only_statement_ids": [],
            "evidence_index": {
                **{
                    "ev-01": {
                        "speaker": "Author",
                        "source_classification": "FACT",
                        "exact_text": "Verbatim text on slide 2 confirms material evidence for topic 2.",
                        "locator": {"kind": "slide", "value": "Slide 2"},
                    },
                    "ev-02": {
                        "speaker": "Author",
                        "source_classification": "FACT",
                        "exact_text": "Verbatim text on slide 12 confirms material evidence for topic 12.",
                        "locator": {"kind": "slide", "value": "Slide 12"},
                    },
                    "ev-03": {
                        "speaker": "Author",
                        "source_classification": "FACT",
                        "exact_text": "Verbatim text on slide 22 confirms material evidence for topic 22.",
                        "locator": {"kind": "slide", "value": "Slide 22"},
                    },
                },
                **{
                    f"ev-s{s:02d}": {
                        "speaker": "Author",
                        "source_classification": "FACT",
                        "exact_text": f"Verbatim text on slide {s} confirms material evidence for topic {s}.",
                        "locator": {"kind": "slide", "value": f"Slide {s}"},
                    }
                    for s in range(1, 26)
                    if s not in (2, 12, 22)
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CREATE", "evidence_ids": ["ev-01"] + [f"ev-s{s:02d}" for s in range(1, 26) if s not in (2, 12, 22)]},
                    {"id": "system:partner_advisory-traverse", "type": "system", "name": "PartnerAdvisory Traverse", "mutation": "CREATE", "evidence_ids": ["ev-01"]},
                    {"id": "workstream:agentic-managed-services", "type": "workstream", "name": "Agentic Managed Services", "mutation": "CREATE", "evidence_ids": ["ev-01"]},
                    {"id": "role:practice-lead", "type": "role", "name": "Practice Lead", "mutation": "CREATE", "evidence_ids": ["ev-02"]},
                    {"id": "person:anthony", "type": "person", "name": "Anthony Marquardt", "mutation": "CREATE", "evidence_ids": ["ev-02"]},
                    {"id": "project:client-pilot", "type": "project", "name": "Client Pilot", "mutation": "CREATE", "evidence_ids": ["ev-03"]},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-01",
                        "from_id": "person:anthony",
                        "predicate": "MEMBER_OF",
                        "to_id": "org:partner_advisory",
                        "mutation": "ASSERT",
                        "evidence_ids": ["ev-02"],
                    },
                    {
                        "signal_id": "sig-02",
                        "from_id": "org:partner_advisory",
                        "predicate": "OWNS",
                        "to_id": "system:partner_advisory-traverse",
                        "mutation": "ASSERT",
                        "evidence_ids": ["ev-03"],
                    },
                ],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }

        qa_report = evaluate_document_qa_gates(
            full_projection,
            source_text=self.source_text,
            metadata=self.metadata,
        )
        self.assertEqual(qa_report["status"], "PASS")
        q3 = qa_report["output_03_canonical_signal_projection"]
        self.assertTrue(q3["passed"])
        self.assertEqual(q3.get("failures", []), [])
        telemetry = q3.get("document_slide_coverage", {})
        self.assertTrue(telemetry.get("passed"))
        self.assertEqual(telemetry.get("missing_buckets"), [])
        self.assertEqual(telemetry.get("covered_buckets"), ["early", "late", "middle"])

    def test_additive_deficits_bypass_bounded_patch(self) -> None:
        """Additive errors (slide coverage gap, missing entities) must set document_patch_request to None."""
        candidate = {
            "schema_version": "5.7",
            "meeting_id": "document-20261007-ams-deck",
            "evidence_index": {
                "ev-01": {"exact_text": "Verbatim text on slide 1 confirms material evidence for topic 1.", "locator": "Slide 1"}
            },
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]}],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }
        candidate_str = json.dumps(candidate)
        judge_report = {
            "status": "FAIL",
            "failures": [
                "DOCUMENT_SLIDE_COVERAGE_GAP: Document with 25 pages requires citations across early, middle, and late buckets",
                "DOCUMENT_YIELD_DEFICIT: Multi-slide document requires at least 8 substantive graph mutations",
            ],
            "output_3_evaluation": {
                "verdict": "FAIL",
                "actionable_remediation_diff": "ERR_OMITTED_HIGH_SIGNAL_ENTITIES: Extract core system:partner_advisory-traverse and late slide case studies",
            },
        }

        meta = dict(self.metadata)
        with tempfile.TemporaryDirectory() as tmp_dir:
            res = prepare_document_patch_or_handoff(
                meta,
                candidate=candidate_str,
                judge_report=judge_report,
                source_text=self.source_text,
                directive="Re-extract with slide coverage across early, middle, late: ERR_OMITTED_HIGH_SIGNAL_ENTITIES",
                output_dir=Path(tmp_dir),
                attempt=1,
            )
            self.assertTrue(res)
            self.assertIsNone(meta.get("document_patch_request"))
            handoff_path = Path(tmp_dir) / "attempts" / "attempt_1" / "document_remediation_handoff.json"
            self.assertFalse(handoff_path.exists(), "Additive deficit should bypass review handoff and route to full re-extraction")

    def test_short_document_exempt_from_slide_buckets(self) -> None:
        """Documents with < 10 pages/slides are exempt from multi-bucket coverage enforcement."""
        short_source = "## Page 1\nShort memo text on topic.\n\n## Page 2\nSecond page content."
        short_projection = {
            "schema_version": "5.7",
            "meeting_id": "document-20261007-short-memo",
            "meeting_archetype": "DOCUMENT_MODE",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "expected_base_graph_version": "1.0.0",
            "evidence_only_statement_ids": [],
            "evidence_index": {
                "ev-01": {
                    "speaker": "Author",
                    "source_classification": "FACT",
                    "exact_text": "Short memo text on topic.",
                    "locator": {"kind": "section", "value": "p1"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CREATE", "evidence_ids": ["ev-01"]}
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }
        meta = {"is_document": True, "page_count": 2, "source_units": [{"locator": "p1"}, {"locator": "p2"}]}
        passed, errors, telemetry = validate_slide_deck_coverage_and_yield(
            short_projection,
            source_text=short_source,
            metadata=meta,
            mutation_count=1,
        )
        self.assertTrue(passed)
        self.assertEqual(errors, [])
        self.assertEqual(telemetry, {})

    def test_text_based_evidence_locator_page_resolution(self) -> None:
        """Evidence without explicit slide numbers resolves slide buckets via source_text exact_text matching."""
        source_text = (
            "## Page 1\nIntro on slide 1 with unique introductory marker alpha.\n\n"
            "## Page 6\nMiddle section with unique middle marker beta.\n\n"
            "## Page 12\nLate conclusion with unique conclusion marker gamma.\n"
        )
        projection = {
            "evidence_index": {
                "ev-01": {"exact_text": "unique introductory marker alpha", "locator": {"kind": "section", "value": "Intro"}},
                "ev-02": {"exact_text": "unique middle marker beta", "locator": {"kind": "section", "value": "Details"}},
                "ev-03": {"exact_text": "unique conclusion marker gamma", "locator": {"kind": "section", "value": "Conclusion"}},
            },
            "graph_mutations": {},
        }
        meta = {"page_count": 12, "is_document": True}
        passed, errors, telemetry = validate_slide_deck_coverage_and_yield(
            projection,
            source_text=source_text,
            metadata=meta,
            mutation_count=8,
        )
        self.assertTrue(passed, f"Expected pass, got errors: {errors}")
        self.assertEqual(telemetry["covered_buckets"], ["early", "late", "middle"])

    def test_wave2_per_slide_unit_decomposition_and_batch_merge(self) -> None:
        """Wave 2: Verify 25-slide + Executive Overview decomposition and deterministic batch merge."""
        from dual_pipeline.document_pipeline_core.unit_decomposition import (
            extract_slide_and_section_units,
            merge_unit_batch_projections,
            partition_units_into_batches,
        )

        deck_with_overview = (
            "## Executive Overview\n\n"
            "High-level executive overview of Agentic Managed Services.\n\n"
            + self.source_text
        )
        units = extract_slide_and_section_units(deck_with_overview, "abcdef1234567890", "md")
        self.assertEqual(len(units), 26)
        self.assertEqual(units[0].locator, "section:executive_overview")
        self.assertTrue(units[0].metadata["is_summary_unit"])
        self.assertFalse(units[0].metadata["is_substantive"])

        slide_units = [u for u in units if u.unit_type == "slide"]
        self.assertEqual(len(slide_units), 25)
        self.assertEqual(slide_units[0].locator, "slide:1")
        self.assertEqual(slide_units[-1].locator, "slide:25")

        batches = partition_units_into_batches([u.to_dict() for u in units], batch_size=9)
        self.assertEqual(len(batches), 3)
        self.assertEqual(sum(len(b) for b in batches), 25)

        b1 = {
            "evidence_index": {
                "ev-01": {"exact_text": "Verbatim text on slide 1 confirms material evidence for topic 1.", "locator": "slide:1"},
            },
            "graph_mutations": {
                "entity_upserts": [{"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]}],
                "relationship_signals": [],
            },
        }
        b2 = {
            "evidence_index": {
                "ev-01": {"exact_text": "Verbatim text on slide 12 confirms material evidence for topic 12.", "locator": "slide:12"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
                    {"id": "org:qurrent", "type": "org", "name": "Qurrent", "evidence_ids": ["ev-01"]},
                ],
                "relationship_signals": [
                    {"from_id": "org:partner_advisory", "predicate": "PARTNERS_WITH", "to_id": "org:qurrent", "evidence_ids": ["ev-01"]}
                ],
            },
        }
        merged = merge_unit_batch_projections([b1, b2])
        self.assertEqual(set(merged["evidence_index"].keys()), {"ev-01", "ev-02"})
        ents = {e["id"]: e for e in merged["graph_mutations"]["entity_upserts"]}
        self.assertEqual(ents["org:partner_advisory"]["evidence_ids"], ["ev-01", "ev-02"])
        self.assertEqual(ents["org:qurrent"]["evidence_ids"], ["ev-02"])

    def test_wave5_additive_unit_gap_remediation_and_prefix_type_canonicalization(self) -> None:
        """Wave 5: Additive unit-gap remediation merges missing slides without dropping prior slides; prefix/type conflicts canonicalize."""
        from dual_pipeline.canonical_pipeline.document_remediation import (
            build_additive_unit_gap_directive,
            merge_additive_unit_remediation,
        )
        from dual_pipeline.document_pipeline_core.bounded_patch import (
            build_bounded_patch_request,
            validate_patch_result,
        )
        from dual_pipeline.normalization_core.meeting_entities import process_projection_signals

        partial_cand = {
            "evidence_index": {
                f"ev-{s:02d}": {
                    "exact_text": f"Verbatim text on slide {s} confirms material evidence for topic {s}.",
                    "locator": {"kind": "slide", "value": f"Slide {s}"},
                }
                for s in range(1, 20)
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-02"]},
                ],
                "relationship_signals": [],
            },
        }
        directive_str, uncovered = build_additive_unit_gap_directive(
            self.source_text, partial_cand, {"status": "FAIL"}, base_directive="Fix coverage gap."
        )
        self.assertEqual(uncovered, [20, 21, 22, 23, 24, 25])
        self.assertIn("Slide 20", directive_str)
        self.assertIn("Slide 25", directive_str)

        gap_cand = {
            "evidence_index": {
                f"ev-gap-{s:02d}": {
                    "exact_text": f"Verbatim text on slide {s} confirms material evidence for topic {s}.",
                    "locator": {"kind": "slide", "value": f"Slide {s}"},
                }
                for s in range(20, 26)
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:qurrent", "type": "system", "name": "Qurrent", "evidence_ids": ["ev-gap-20"]},
                ],
                "relationship_signals": [
                    {"from_id": "org:partner_advisory", "predicate": "PARTNERS_WITH", "to_id": "org:qurrent", "evidence_ids": ["ev-gap-20"]},
                ],
            },
        }
        merged = merge_additive_unit_remediation(partial_cand, gap_cand)
        passed, errors, cov = validate_slide_deck_coverage_and_yield(
            merged, source_text=self.source_text, metadata=self.metadata, mutation_count=10
        )
        self.assertTrue(passed, f"Expected merged projection to pass coverage, got: {errors}")
        self.assertEqual(cov["uncovered_substantive_units"], [])
        self.assertEqual(cov["substantive_coverage_ratio"], 1.0)

        # Prefix-type mismatch canonicalization in process_projection_signals (org:qurrent + type="system" -> system:qurrent)
        candidates: list = []
        evidence = [{"evidence_id": "ev-01", "locator": {"kind": "slide", "value": "Slide 20"}}]
        profiles, _ = process_projection_signals(
            projection=gap_cand,
            candidates=candidates,
            evidence=evidence,
            evidence_by_identifier={"ev-gap-20": "ev-01"},
            run_id="run-w5",
            meeting_title="AMS 101 Deck",
            classification="internal",
            default_audiences=["internal_consulting"],
        )
        stable_ids = {p["stable_identifier"] for p in profiles.values()}
        self.assertIn("system:qurrent", stable_ids)
        self.assertNotIn("org:qurrent", stable_ids)

        # Bounded patch must reject mutating entity type away from ID namespace prefix
        bp_source = "Qurrent is an AI agent orchestration platform."
        bp_cand = {
            "schema_version": "5.7",
            "meeting_id": "doc-bp",
            "classification": "internal",
            "audiences": ["internal"],
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "speaker": "Doc",
                    "source_classification": "FACT",
                    "kind": "technical",
                    "text": bp_source,
                    "exact_text": bp_source,
                    "locator": {"kind": "section", "value": "Body"},
                }
            },
            "evidence_only_statement_ids": [],
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:qurrent", "type": "org", "name": "Qurrent", "description": "Old.", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]}
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "action_updates": [],
            },
        }
        bp_req = build_bounded_patch_request(
            bp_cand,
            {"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL", "failed_claims": [{"collection": "entity_upserts", "id": "org:qurrent", "reason": "Fix type"}]}},
            bp_source,
        )
        bp_change = dict(bp_req["allowed_objects"][0])
        bp_change["value"] = dict(bp_change["value"], type="system")
        bp_resp = {k: bp_req[k] for k in ("contract_version", "candidate_sha256", "source_sha256", "request_sha256")}
        bp_resp["changes"] = [bp_change]
        with self.assertRaisesRegex(ValueError, "conflicts with ID prefix"):
            validate_patch_result(bp_cand, bp_req, bp_resp, bp_source)

    def test_wave6_full_25_slide_ams_deck_per_unit_coverage_and_graphdb_materialization(self) -> None:
        """Wave 6 E2E: Full 25-slide AMS 101 deck verifies >=90% substantive slide coverage, compound roles, deals, competitors, platforms, and L0/C0 normalization."""
        from dual_pipeline.normalization_core.meeting_entities import process_projection_signals
        from dual_pipeline.transcript_duration_helper import resolve_duration_mutation_budget
        from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection

        ams_slides = {
            1: "Agentic Managed Services (AMS) 101",
            2: "AMS Value Proposition: 60% of enterprise AI pilots stall before production without managed operations.",
            3: "Definition of Agentic Managed Services: Continuous tuning, observability, and human-in-the-loop governance.",
            4: "The Business Opportunity: $50B+ global market for managed AI & agentic operations.",
            5: "Target Buyer Personas: CIO, CTO, VP of AI & Data Platform.",
            6: "Key Pain Points: Prompt drift, hallucination risk, and multi-agent orchestration bottlenecks.",
            7: "AMS Service Tiers: Foundation, Accelerator, and Enterprise Autonomous Operations.",
            8: "Where We Are Winning: State of Iowa ($1.5M initial -> $6M follow-on), Disney, RBI ($670K initial -> $3M follow-on), Madrigal ($280K -> $1.2M).",
            9: "Delivery Pod Structure: AI & Data Engineers, Agent Ops Specialists, and Domain Architects.",
            10: "PartnerAdvisory Traverse Platform: Integrated observability and policy enforcement engine.",
            11: "Commercial & Pricing Models: Outcome-based pricing, pod subscription, and token consumption tiers.",
            12: "Governance & Guardrails: Automated evaluation gates and policy-before-retrieval enforcement.",
            13: "Transition from Build to Run: 30-60-90 day onboarding playbook for production agents.",
            14: "SLAs & Operational Metrics: Latency, groundedness score, and escalation resolution rate.",
            15: "Appendix",
            16: "Competitive Landscape (Services): Accenture, Cognizant, Capgemini, and Genpact.",
            17: "Competitive Landscape (Platforms & Boutiques): UiPath, Neurons Lab, JADA, and Kanerika.",
            18: "Technology Partner Ecosystem: Qurrent, ServiceNow, Workday, Salesforce, Google Cloud, and AWS.",
            19: "Detailed Service Catalog: Prompt engineering, model routing, and vector index maintenance.",
            20: "Security & Compliance: SOC2, HIPAA, and zero-trust tenant isolation.",
            21: "Client Readiness Assessment: Data maturity and API instrumentation checklist.",
            22: "Objection Handling: Why clients choose PartnerAdvisory AMS over internal DIY maintenance.",
            23: "Case Study Deep Dive: Multi-agent customer support transformation and cost reduction.",
            24: "AMS Core Team: Anthony Marquardt (Practice Lead), Tauseefistiaq (Data & Solution Lead), and Ali Imam (GTM Enablement & Operations Lead).",
            25: "Next Steps & Enablement Resources: Reach out to the AMS Core Team for client discovery.",
        }
        ams_text = "\n\n".join(f"## Slide {s}\n{txt}" for s, txt in ams_slides.items())
        meta = {
            "is_document": True,
            "output3_only": True,
            "page_count": 25,
            "source_units": [{"slide": s, "locator": f"slide:{s}"} for s in range(1, 26)],
        }
        budget = resolve_duration_mutation_budget(meta, text=ams_text)
        self.assertGreaterEqual(budget, 100)

        ev_index = {
            f"ev-{s:02d}": {
                "statement_id": f"ev-{s:02d}",
                "speaker": "Document Author",
                "source_classification": "FACT",
                "text": txt,
                "exact_text": txt,
                "locator": {"kind": "slide", "value": f"Slide {s}"},
            }
            for s, txt in ams_slides.items()
            if s not in (1, 15)
        }
        entity_upserts = [
            {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CREATE", "evidence_ids": ["ev-02", "ev-22"]},
            {"id": "workstream:agentic-managed-services", "type": "workstream", "name": "Agentic Managed Services", "mutation": "CREATE", "evidence_ids": ["ev-02", "ev-03", "ev-04", "ev-05", "ev-06", "ev-07", "ev-09", "ev-11", "ev-12", "ev-13", "ev-14", "ev-19", "ev-20", "ev-21", "ev-23", "ev-25"]},
            {"id": "system:partner_advisory-traverse", "type": "system", "name": "PartnerAdvisory Traverse", "mutation": "CREATE", "evidence_ids": ["ev-10"]},
            {"id": "org:state-of-iowa", "type": "org", "name": "State of Iowa", "mutation": "CREATE", "evidence_ids": ["ev-08"]},
            {"id": "org:disney", "type": "org", "name": "Disney", "mutation": "CREATE", "evidence_ids": ["ev-08"]},
            {"id": "org:rbi", "type": "org", "name": "RBI", "mutation": "CREATE", "evidence_ids": ["ev-08"]},
            {"id": "org:madrigal", "type": "org", "name": "Madrigal", "mutation": "CREATE", "evidence_ids": ["ev-08"]},
            {"id": "org:accenture", "type": "org", "name": "Accenture", "mutation": "CREATE", "evidence_ids": ["ev-16"]},
            {"id": "org:cognizant", "type": "org", "name": "Cognizant", "mutation": "CREATE", "evidence_ids": ["ev-16"]},
            {"id": "org:capgemini", "type": "org", "name": "Capgemini", "mutation": "CREATE", "evidence_ids": ["ev-16"]},
            {"id": "org:genpact", "type": "org", "name": "Genpact", "mutation": "CREATE", "evidence_ids": ["ev-16"]},
            {"id": "org:uipath", "type": "org", "name": "UiPath", "mutation": "CREATE", "evidence_ids": ["ev-17"]},
            {"id": "org:neurons-lab", "type": "org", "name": "Neurons Lab", "mutation": "CREATE", "evidence_ids": ["ev-17"]},
            {"id": "org:jada", "type": "org", "name": "JADA", "mutation": "CREATE", "evidence_ids": ["ev-17"]},
            {"id": "org:kanerika", "type": "org", "name": "Kanerika", "mutation": "CREATE", "evidence_ids": ["ev-17"]},
            {"id": "system:qurrent", "type": "system", "name": "Qurrent", "mutation": "CREATE", "evidence_ids": ["ev-18"]},
            {"id": "system:servicenow", "type": "system", "name": "ServiceNow", "mutation": "CREATE", "evidence_ids": ["ev-18"]},
            {"id": "system:workday", "type": "system", "name": "Workday", "mutation": "CREATE", "evidence_ids": ["ev-18"]},
            {"id": "system:salesforce", "type": "system", "name": "Salesforce", "mutation": "CREATE", "evidence_ids": ["ev-18"]},
            {"id": "system:google-cloud", "type": "system", "name": "Google Cloud", "mutation": "CREATE", "evidence_ids": ["ev-18"]},
            {"id": "system:aws", "type": "system", "name": "AWS", "mutation": "CREATE", "evidence_ids": ["ev-18"]},
            {"id": "person:anthony-marquardt", "type": "person", "name": "Anthony Marquardt", "mutation": "CREATE", "evidence_ids": ["ev-24"]},
            {"id": "person:tauseefistiaq", "type": "person", "name": "Tauseefistiaq", "mutation": "CREATE", "evidence_ids": ["ev-24"]},
            {"id": "person:ali-imam", "type": "person", "name": "Ali Imam", "mutation": "CREATE", "evidence_ids": ["ev-24"]},
            {"id": "role:practice-lead", "type": "role", "name": "Practice Lead", "mutation": "CREATE", "evidence_ids": ["ev-24"]},
            {"id": "role:data-and-solution-lead", "type": "role", "name": "Data & Solution Lead", "mutation": "CREATE", "evidence_ids": ["ev-24"]},
            {"id": "role:gtm-enablement-and-operations-lead", "type": "role", "name": "GTM Enablement & Operations Lead", "mutation": "CREATE", "evidence_ids": ["ev-24"]},
        ]
        relationship_signals = [
            {"signal_id": "sig-01", "from_id": "org:partner_advisory", "predicate": "DELIVERS", "to_id": "workstream:agentic-managed-services", "evidence_ids": ["ev-02"]},
            {"signal_id": "sig-02", "from_id": "org:partner_advisory", "predicate": "OWNS", "to_id": "system:partner_advisory-traverse", "evidence_ids": ["ev-10"]},
            {"signal_id": "sig-03", "from_id": "workstream:agentic-managed-services", "predicate": "DELIVERS", "to_id": "org:state-of-iowa", "evidence_ids": ["ev-08"]},
            {"signal_id": "sig-04", "from_id": "workstream:agentic-managed-services", "predicate": "DELIVERS", "to_id": "org:disney", "evidence_ids": ["ev-08"]},
            {"signal_id": "sig-05", "from_id": "workstream:agentic-managed-services", "predicate": "DELIVERS", "to_id": "org:rbi", "evidence_ids": ["ev-08"]},
            {"signal_id": "sig-06", "from_id": "workstream:agentic-managed-services", "predicate": "DELIVERS", "to_id": "org:madrigal", "evidence_ids": ["ev-08"]},
            {"signal_id": "sig-07", "from_id": "workstream:agentic-managed-services", "predicate": "INTEGRATES_WITH", "to_id": "system:qurrent", "evidence_ids": ["ev-18"]},
            {"signal_id": "sig-08", "from_id": "workstream:agentic-managed-services", "predicate": "INTEGRATES_WITH", "to_id": "system:servicenow", "evidence_ids": ["ev-18"]},
            {"signal_id": "sig-09", "from_id": "workstream:agentic-managed-services", "predicate": "INTEGRATES_WITH", "to_id": "system:workday", "evidence_ids": ["ev-18"]},
            {"signal_id": "sig-10", "from_id": "workstream:agentic-managed-services", "predicate": "INTEGRATES_WITH", "to_id": "system:salesforce", "evidence_ids": ["ev-18"]},
            {"signal_id": "sig-11", "from_id": "workstream:agentic-managed-services", "predicate": "INTEGRATES_WITH", "to_id": "system:google-cloud", "evidence_ids": ["ev-18"]},
            {"signal_id": "sig-12", "from_id": "workstream:agentic-managed-services", "predicate": "INTEGRATES_WITH", "to_id": "system:aws", "evidence_ids": ["ev-18"]},
            {"signal_id": "sig-13", "from_id": "person:anthony-marquardt", "predicate": "HAS_ROLE", "to_id": "role:practice-lead", "evidence_ids": ["ev-24"]},
            {"signal_id": "sig-14", "from_id": "person:tauseefistiaq", "predicate": "HAS_ROLE", "to_id": "role:data-and-solution-lead", "evidence_ids": ["ev-24"]},
            {"signal_id": "sig-15", "from_id": "person:ali-imam", "predicate": "HAS_ROLE", "to_id": "role:gtm-enablement-and-operations-lead", "evidence_ids": ["ev-24"]},
        ]
        projection = {
            "schema_version": "5.7",
            "meeting_id": "document-20261007-ams-101-full",
            "meeting_archetype": "DOCUMENT_MODE",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "expected_base_graph_version": "1.0.0",
            "evidence_only_statement_ids": [],
            "evidence_index": ev_index,
            "graph_mutations": {
                "entity_upserts": entity_upserts,
                "relationship_signals": relationship_signals,
                "decision_updates": [],
                "proposal_updates": [],
                "risk_updates": [],
                "strategic_intent_signals": [],
            },
        }
        changes: list[str] = []
        heal_graph_projection(projection, changes, metadata=meta, output_1=ams_text)
        qa_report = evaluate_document_qa_gates(projection, source_text=ams_text, metadata=meta)
        self.assertEqual(qa_report["status"], "PASS", f"Expected PASS, got failures: {qa_report}")
        cov = qa_report["output_03_canonical_signal_projection"]["document_slide_coverage"]
        self.assertGreaterEqual(cov["substantive_coverage_ratio"], 0.90)
        self.assertEqual(cov["uncovered_substantive_units"], [])
        self.assertIn(1, cov["exempt_structural_units"])
        self.assertIn(15, cov["exempt_structural_units"])

        surviving_names = {e["id"]: e["name"] for e in projection["graph_mutations"]["entity_upserts"]}
        self.assertEqual(surviving_names["role:data-and-solution-lead"], "Data & Solution Lead")
        self.assertEqual(surviving_names["role:gtm-enablement-and-operations-lead"], "GTM Enablement & Operations Lead")
        for req_id in (
            "org:state-of-iowa", "org:disney", "org:rbi", "org:madrigal",
            "org:accenture", "org:cognizant", "org:capgemini", "org:genpact",
            "org:uipath", "org:neurons-lab", "org:jada", "org:kanerika",
            "system:qurrent", "system:servicenow", "system:workday", "system:salesforce", "system:google-cloud", "system:aws",
        ):
            self.assertIn(req_id, surviving_names)

        candidates: list = []
        norm_ev = [{"evidence_id": k, "locator": v["locator"]} for k, v in ev_index.items()]
        profiles, edge_traits = process_projection_signals(
            projection=projection,
            candidates=candidates,
            evidence=norm_ev,
            evidence_by_identifier={k: k for k in ev_index},
            run_id="run-ams-w6",
            meeting_title="Agentic Managed Services (AMS) 101",
            classification="internal",
            default_audiences=["internal_consulting"],
        )
        self.assertEqual(len(candidates), len(entity_upserts) + len(relationship_signals))
        self.assertEqual(len(edge_traits), len(relationship_signals))



if __name__ == "__main__":
    unittest.main()
