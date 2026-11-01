"""Unit tests for FinalEvidenceContract and CompiledPromptArtifact (Phase 3)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.evidence_contract import (
    create_final_evidence_contract,
    determine_stratum,
)
from dual_pipeline.prompt_assembly import (
    assemble_prompt,
    compile_prompt_artifact,
)

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "contracts" / "dual_pipeline" / "schemas"


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


class TestFinalEvidenceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_packet = {
            "retrieval_mode": "hybrid_colbert",
            "allowed_lanes": ["approved", "candidate"],
            "approved": [
                {
                    "record": {
                        "record_id": "rec-app-001",
                        "title": "System Architecture Overview",
                        "retrieval_text": "The dual pipeline consists of ingestion and query engines.",
                        "evidence_ids": ["ev-001"],
                        "entity_ids": ["ent-sys-001"],
                        "retrieval_text_hash": "a" * 64,
                    },
                    "retrieval_metadata": {
                        "answer_use": "factual_answer",
                        "dense_score": 0.88,
                        "sparse_score": 0.75,
                        "colbert_score": 0.90,
                        "graph_hop_depth": 0,
                    },
                }
            ],
            "candidate": [
                {
                    "record": {
                        "record_id": "rec-cand-001",
                        "title": "Proposed Scaling Policy",
                        "retrieval_text": "Auto-scaling is proposed for Q4.",
                        "evidence_ids": ["ev-002"],
                        "retrieval_text_hash": "b" * 64,
                    },
                    "retrieval_metadata": {
                        "answer_use": "candidate_hypothesis",
                        "dense_score": 0.65,
                    },
                }
            ],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [
                {"record_id": "rec-cand-001", "target_record_id": "rec-app-001"}
            ],
            "exact_anchor_ids": ["rec-app-001"],
            "bm25_anchor_ids": [],
            "gaps": [],
            "exclusions": [],
            "graph_snapshot": "snap-001",
            "packet_digest": "c" * 64,
        }

    def test_create_and_seal_contract(self) -> None:
        contract = create_final_evidence_contract(
            query_id="query-test-001",
            run_id="run-test-001",
            graph_generation_id="gen-test-001",
            graph_sha256="d" * 64,
            sidecar_sha256="e" * 64,
            packet=self.sample_packet,
            retrieval_evidence_report={
                "requirement_outcomes": [
                    {"requirement_id": "req-1", "outcome": "covered", "reason_code": None},
                    {"requirement_id": "req-2", "outcome": "excluded", "reason_code": "SCOPE_LIMIT"},
                ]
            },
            classification="internal",
            audiences=["nee-engineering"],
        )

        self.assertEqual(contract["contract_id"], "urn:nee:dual-pipeline:final-evidence-contract:1.0.0")
        self.assertEqual(contract["schema_version"], "1.0.0")
        self.assertIn("canonical_digest", contract)
        self.assertEqual(contract["canonical_digest"]["algorithm"], "sha256")
        self.assertEqual(contract["canonical_digest"]["scope"], "artifact_without_canonical_digest")
        self.assertEqual(len(contract["canonical_digest"]["value"]), 64)

        # Evidence records checks
        records = contract["evidence_records"]
        self.assertEqual(len(records), 2)

        # rec-app-001 is anchor and counterevidence target -> check stratum
        app_rec = next(r for r in records if r["record_id"] == "rec-app-001")
        self.assertEqual(app_rec["lane"], "approved")
        self.assertTrue(app_rec["contradiction_flag"])
        self.assertEqual(app_rec["stratum"], "CONTRADICTS")

        # Coverage summary
        cov = contract["coverage_summary"]
        self.assertEqual(cov["total_requirements"], 2)
        self.assertEqual(cov["covered_requirements"], 2)
        self.assertTrue(cov["coverage_sufficient"])

    def test_stratum_determination(self) -> None:
        self.assertEqual(
            determine_stratum("r1", {}, "approved", required_ids={"r1"}, contradiction_ids=set(), anchor_ids=set()),
            "MUST_USE",
        )
        self.assertEqual(
            determine_stratum("r2", {}, "approved", required_ids=set(), contradiction_ids={"r2"}, anchor_ids=set()),
            "CONTRADICTS",
        )
        self.assertEqual(
            determine_stratum("r3", {}, "approved", required_ids=set(), contradiction_ids=set(), anchor_ids={"r3"}),
            "MUST_USE",
        )
        self.assertEqual(
            determine_stratum("r4", {}, "approved", required_ids=set(), contradiction_ids=set(), anchor_ids=set()),
            "SUPPORTING",
        )
        self.assertEqual(
            determine_stratum("r5", {}, "external_public", required_ids=set(), contradiction_ids=set(), anchor_ids=set()),
            "BACKGROUND",
        )

    def test_schema_conformance(self) -> None:
        schema = load_schema("final-evidence-contract.schema.json")
        contract = create_final_evidence_contract(
            query_id="query-test-001",
            run_id="run-test-001",
            graph_generation_id="gen-test-001",
            graph_sha256="d" * 64,
            sidecar_sha256="e" * 64,
            packet=self.sample_packet,
            classification="internal",
            audiences=["nee-engineering"],
        )
        # Validate that required keys and properties adhere to the contract schema
        self.assertEqual(contract["contract_id"], schema["properties"]["contract_id"]["const"])
        self.assertEqual(contract["schema_version"], schema["properties"]["schema_version"]["const"])


class TestPromptAssembly(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "query_type": "factual",
            "prompt_blueprint": "bp-v1",
            "materiality": "medium",
            "retrieval_mode": "hybrid_colbert",
            "subquestions": ["What is the system status?"],
            "allowed_lanes": ["approved"],
            "required_output_schema": {"type": "object"},
            "output_schema": "standard_factual",
            "plan_digest": "f" * 64,
        }
        self.route = {"query_route": "CURRENT_STATE_CORROBORATION"}
        self.packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-app-001",
                        "title": "Status Doc",
                        "retrieval_text": "System operational.",
                        "evidence_ids": ["ev-001"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                }
            ],
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
            "exclusions": [],
            "exact_anchor_ids": ["rec-app-001"],
            "graph_snapshot": "snap-001",
            "packet_digest": "g" * 64,
        }

    def test_assemble_prompt(self) -> None:
        prompt = assemble_prompt(
            question="Is the system operational?",
            plan=self.plan,
            route=self.route,
            packet=self.packet,
        )
        self.assertIn("slots", prompt)
        self.assertIn("S0", prompt["slots"])
        self.assertIn("C0A", prompt["slots"])
        self.assertIn("prompt_hash", prompt)
        self.assertEqual(prompt["slots"]["U0"], "Is the system operational?")

    def test_compile_prompt_artifact(self) -> None:
        contract = create_final_evidence_contract(
            query_id="query-test-001",
            run_id="run-test-001",
            graph_generation_id="gen-test-001",
            graph_sha256="d" * 64,
            sidecar_sha256="e" * 64,
            packet=self.packet,
        )
        prompt = assemble_prompt(
            question="Is the system operational?",
            plan=self.plan,
            route=self.route,
            packet=self.packet,
        )
        compiled_artifact = compile_prompt_artifact(
            query_id="query-test-001",
            run_id="run-test-001",
            evidence_contract=contract,
            l2_execution_decision_id="l2-exec-001",
            compiled_prompt=prompt,
            question="Is the system operational?",
            plan=self.plan,
            route=self.route,
            target_model="gpt-5.6-luna",
            reasoning_effort="high",
            materiality="medium",
            assurance_profile="standard",
            classification="internal",
            audiences=["nee-engineering"],
        )

        self.assertEqual(compiled_artifact["contract_id"], "urn:nee:dual-pipeline:compiled-prompt-artifact:1.0.0")
        self.assertEqual(compiled_artifact["target_model"], "gpt-5.6-luna")
        self.assertEqual(compiled_artifact["evidence_contract_id"], contract["artifact_id"])
        self.assertEqual(compiled_artifact["evidence_contract_digest"], contract["canonical_digest"]["value"])
        self.assertIn("canonical_digest", compiled_artifact)
        self.assertEqual(len(compiled_artifact["canonical_digest"]["value"]), 64)
        self.assertEqual(compiled_artifact["prompt_components"]["must_use_evidence_count"], 1)


if __name__ == "__main__":
    unittest.main()
