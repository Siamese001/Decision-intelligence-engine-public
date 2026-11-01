#!/usr/bin/env python3
"""Acceptance for deterministic L1 query coverage compilation."""
from __future__ import annotations

import unittest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.query import (
    MAX_L2_INPUT_RECORDS_PER_LANE,
    MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES,
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    filter_packet_for_l2,
    render_retrieval_evidence_markdown,
)


def _plan(query_type: str, lanes: list[str]) -> dict[str, object]:
    return {
        "run_id": "run-query-coverage-test",
        "query_type": query_type,
        "allowed_lanes": lanes,
        "materiality": "high",
        "primary_entities": ["Project Beacon"],
        "evidence_requirements": [
            "Approved internal facts for current-state statements",
            "Resolvable evidence and source locators for material claims",
        ],
        "subquestions": [
            "Which workstreams match?",
            "What patterns recur?",
            "What gaps remain?",
        ],
        "canonical_digest": {"value": "2" * 64},
    }


ACTIVE = {
    "graph_snapshot": "graph-snapshot-coverage-test",
    "graph_sha256": "3" * 64,
}


def _compile(question: str, query_type: str = "cross_workstream_synthesis") -> dict[str, object]:
    lanes = ["approved", "candidate"] if query_type == "candidate_promotion_review" else ["approved"]
    return compile_query_coverage_manifest(
        question=question,
        validated_request_digest="1" * 64,
        plan=_plan(query_type, lanes),
        active=ACTIVE,
        authorization_scope_digest="4" * 64,
    )


class QueryCoverageManifestTests(unittest.TestCase):
    def test_explicit_material_list_survives_in_order(self) -> None:
        manifest = _compile(
            "1. A2A protocol patterns\n2. Evidence packet requirements\n"
            "3. PostgreSQL write-back controls\nCreate a two-slide table."
        )
        labels = [row["requirement_label"] for row in manifest["requirements"]]
        self.assertEqual(
            labels[:3],
            [
                "A2A protocol patterns",
                "Evidence packet requirements",
                "PostgreSQL write-back controls",
            ],
        )

    def test_eleven_topics_cannot_collapse_into_generic_subquestions(self) -> None:
        question = "\n".join(f"{index}. A2A topic {index}" for index in range(1, 12))
        manifest = _compile(question)
        labels = [row["requirement_label"] for row in manifest["requirements"]]
        self.assertEqual(labels[:11], [f"A2A topic {index}" for index in range(1, 12)])
        self.assertGreaterEqual(len(manifest["requirements"]), 11)

    def test_output_format_bullets_do_not_become_evidence_requirements(self) -> None:
        manifest = _compile("- Create 3 slides\n- Use a table\n- A2A communication")
        labels = [row["requirement_label"] for row in manifest["requirements"]]
        self.assertNotIn("Create 3 slides", labels)
        self.assertNotIn("Use a table", labels)
        self.assertIn("A2A communication", labels)

    def test_complete_outcome_adds_policy_and_graph_facets(self) -> None:
        manifest = _compile(
            "Recommend the Project Beacon future-state A2A architecture and explain write-back."
        )
        labels = [row["requirement_label"] for row in manifest["requirements"]]
        self.assertIn(
            "Resolvable evidence and source locators for material claims", labels
        )
        self.assertIn(
            "A2A protocols, communication patterns, and interaction boundaries", labels
        )
        self.assertIn(
            "Architecture boundaries, component responsibilities, and shared services", labels
        )

    def test_every_query_type_produces_a_valid_manifest(self) -> None:
        policies = {
            "exact_lookup": ["approved"],
            "current_state": ["approved"],
            "current_state_corroboration": ["approved"],
            "relationship_explanation": ["approved"],
            "cross_workstream_synthesis": ["approved"],
            "gap_risk_inconsistency": ["approved"],
            "opportunity_nba": ["approved", "candidate", "external_public"],
            "candidate_promotion_review": ["approved", "candidate"],
        }
        for query_type, lanes in policies.items():
            with self.subTest(query_type=query_type):
                manifest = compile_query_coverage_manifest(
                    question="A2A evidence request",
                    validated_request_digest="1" * 64,
                    plan=_plan(query_type, lanes),
                    active=ACTIVE,
                    authorization_scope_digest="4" * 64,
                )
                validate_artifact(manifest)

    def test_candidate_facet_is_scoped_to_candidate_lane(self) -> None:
        manifest = _compile(
            "Review candidate Project Beacon assertions against approved evidence.",
            query_type="candidate_promotion_review",
        )
        facets = [
            row
            for row in manifest["requirements"]
            if row["requirement_label"]
            == "Candidate assertions and proposal-only posture"
        ]
        self.assertEqual(1, len(facets))
        self.assertEqual(["candidate"], facets[0]["allowed_lanes"])

    def test_empty_request_fails_before_retrieval(self) -> None:
        with self.assertRaisesRegex(ValueError, "QUERY_COVERAGE_REQUIREMENT_AMBIGUOUS"):
            _compile("   ")

    def test_manifest_is_identical_on_deterministic_replay(self) -> None:
        question = "1. A2A protocol\n2. Evidence packet"
        self.assertEqual(_compile(question), _compile(question))

    def test_report_is_sealed_visible_projection_of_selected_records(self) -> None:
        manifest = _compile("1. A2A protocol")
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved"],
            "approved": [{
                "record_id": "retrieval-record-a2a",
                "title": "A2A protocol",
                "retrieval_text": "Agent to agent communication protocol evidence",
                "evidence_ids": ["evidence-a2a"],
            }],
            "candidate": [],
            "external_public": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=packet,
            authorization_scope_digest="4" * 64,
            graph_sha256=ACTIVE["graph_sha256"],
            view_authorized=True,
        )
        validate_artifact(report)
        self.assertIn("A2A protocol", render_retrieval_evidence_markdown(manifest, report))
        self.assertLessEqual(
            len(
                __import__("json").dumps(
                    report,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            MAX_RETRIEVAL_EVIDENCE_REPORT_BYTES,
        )

    def test_report_binds_route_specific_material_records_not_packet_prefix(self) -> None:
        manifest = _compile(
            "Assess Project Beacon observed gaps and risks.",
            query_type="gap_risk_inconsistency",
        )
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved"],
            "approved": [
                {
                    "record": {
                        "record_id": "retrieval-record-unrelated",
                        "title": "Unrelated current staffing note",
                        "retrieval_text": "Current staffing note",
                        "evidence_ids": ["evidence-unrelated"],
                    }
                },
                {
                    "record": {
                        "record_id": "retrieval-record-risk-one",
                        "title": "Project Beacon observed gap",
                        "retrieval_text": (
                            "Project Beacon has an observed gap and unresolved risk; "
                            "counterevidence and evidence needed for resolution are recorded"
                        ),
                        "evidence_ids": ["evidence-risk-one"],
                    }
                },
                {
                    "record": {
                        "record_id": "retrieval-record-risk-two",
                        "title": "Project Beacon observed risks and conflicts",
                        "retrieval_text": (
                            "Project Beacon has observed risks and conflicts; "
                            "counterevidence and evidence needed for resolution are recorded"
                        ),
                        "evidence_ids": ["evidence-risk-two"],
                    }
                },
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=packet,
            authorization_scope_digest="4" * 64,
            graph_sha256=ACTIVE["graph_sha256"],
            view_authorized=True,
        )
        validate_artifact(report)
        self.assertEqual(
            ["retrieval-record-risk-one", "retrieval-record-risk-two"],
            report["required_material_record_ids_by_lane"]["approved"],
        )

    def test_l2_packet_bound_preserves_full_report_and_material_records(self) -> None:
        manifest = _compile("1. A2A protocol")
        covered_text = " ".join(
            requirement["requirement_label"]
            for requirement in manifest["requirements"]
        )
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved"],
            "approved": [
                {
                    "record_id": f"retrieval-record-{index:03d}",
                    "title": "A2A protocol",
                    "retrieval_text": covered_text,
                    "evidence_ids": [f"evidence-{index:03d}"],
                }
                for index in range(20)
            ],
            "candidate": [],
            "external_public": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=packet,
            authorization_scope_digest="4" * 64,
            graph_sha256=ACTIVE["graph_sha256"],
            view_authorized=True,
        )
        validate_artifact(report)
        filtered = filter_packet_for_l2(packet, report)
        self.assertEqual(
            20, len(report["l2_input_record_ids_by_lane"]["approved"])
        )
        self.assertEqual(
            MAX_L2_INPUT_RECORDS_PER_LANE, len(filtered["approved"])
        )
        visible_ids = {
            row["record_id"]
            for row in filtered["approved"]
        }
        self.assertTrue(
            set(report["required_material_record_ids_by_lane"]["approved"])
            <= visible_ids
        )
        self.assertEqual(
            report["required_material_record_ids_by_lane"],
            filtered["required_material_record_ids_by_lane"],
        )

    def test_oversized_report_fails_instead_of_hiding_coverage(self) -> None:
        manifest = _compile(
            "\n".join(f"{index}. A2A topic {index}" for index in range(1, 80))
        )
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved"],
            "approved": [
                {
                    "record_id": f"retrieval-record-{index:04d}-" + "x" * 90,
                    "title": "A2A topics",
                    "retrieval_text": " ".join(f"A2A topic {item}" for item in range(1, 80)),
                    "evidence_ids": [f"evidence-{index:04d}"],
                }
                for index in range(100)
            ],
            "candidate": [],
            "external_public": [],
        }
        with self.assertRaisesRegex(
            ValueError,
            "RETRIEVAL_EVIDENCE_REPORT_SIZE_LIMIT_EXCEEDED",
        ):
            build_retrieval_evidence_report(
                coverage_manifest=manifest,
                packet=packet,
                authorization_scope_digest="4" * 64,
                graph_sha256=ACTIVE["graph_sha256"],
                view_authorized=True,
            )

    def test_unrelated_record_cannot_cover_requirement(self) -> None:
        manifest = _compile("1. PostgreSQL durable write-back controls")
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved"],
            "approved": [{
                "record_id": "retrieval-record-weather",
                "title": "Weather forecast",
                "retrieval_text": "Daily temperature and rainfall forecast",
                "evidence_ids": ["evidence-weather"],
            }],
            "candidate": [], "external_public": [], "counterevidence": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest, packet=packet,
            authorization_scope_digest="4" * 64,
            graph_sha256=ACTIVE["graph_sha256"], view_authorized=True,
        )
        first = report["requirement_outcomes"][0]
        self.assertEqual(first["outcome"], "explicit_gap")
        self.assertEqual(first["record_ids"], [])
        self.assertEqual(report["l2_input_record_ids_by_lane"]["approved"], [])

    def test_candidate_review_requires_source_bound_candidate_lane_evidence(self) -> None:
        manifest = _compile(
            "Review the Project Beacon candidate proposal.",
            query_type="candidate_promotion_review",
        )
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved", "candidate"],
            "approved": [{
                "record_id": "retrieval-record-approved",
                "title": "Approved corroboration",
                "retrieval_text": "Approved source evidence",
                "evidence_ids": ["evidence-approved"],
            }],
            "candidate": [{
                "record_id": "retrieval-record-candidate",
                "title": "Project Beacon review item",
                "retrieval_text": "Source-bound proposal without a factual promotion.",
                "evidence_ids": ["evidence-candidate"],
            }],
            "external_public": [],
            "counterevidence": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=packet,
            authorization_scope_digest="4" * 64,
            graph_sha256=ACTIVE["graph_sha256"],
            view_authorized=True,
        )
        outcomes = {
            row["requirement_id"]: row for row in report["requirement_outcomes"]
        }
        candidate_requirement = next(
            row
            for row in manifest["requirements"]
            if row["requirement_label"] == "Candidate assertions under review"
        )
        self.assertEqual(
            "covered", outcomes[candidate_requirement["requirement_id"]]["outcome"]
        )
        self.assertEqual(
            ["retrieval-record-candidate"],
            outcomes[candidate_requirement["requirement_id"]]["record_ids"],
        )

    def test_reverse_only_path_cannot_cover_directional_requirement(self) -> None:
        manifest = _compile("1. Service writes durable state to PostgreSQL")
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "5" * 64,
            "allowed_lanes": ["approved"],
            "approved": [{
                "record": {
                    "record_id": "retrieval-record-postgres",
                    "title": "PostgreSQL durable state",
                    "retrieval_text": "Service writes durable state to PostgreSQL",
                    "evidence_ids": ["evidence-postgres"],
                },
                "graph_path": {"steps": [{
                    "traversal_direction": "reverse", "edge_type": "DEPENDS_ON"
                }]},
                "evidence": [{"evidence_id": "evidence-postgres"}],
            }],
            "candidate": [], "external_public": [], "counterevidence": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest, packet=packet,
            authorization_scope_digest="4" * 64,
            graph_sha256=ACTIVE["graph_sha256"], view_authorized=True,
        )
        first = report["requirement_outcomes"][0]
        self.assertEqual(first["outcome"], "explicit_gap")
        self.assertEqual(first["reason_code"], "REVERSE_ONLY_DIRECTIONAL_SUPPORT")


if __name__ == "__main__":
    unittest.main()
