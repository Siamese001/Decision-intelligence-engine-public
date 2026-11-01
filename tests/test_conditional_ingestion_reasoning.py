#!/usr/bin/env python3
"""Acceptance tests for bounded conditional ingestion reasoning."""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest import mock

from dual_pipeline.core import ROOT, canonical_json, read_json, write_json
from dual_pipeline.ingestion_openai import OpenAIIngestionReasoningProvider
from dual_pipeline.ingestion_reasoning import (
    IngestionProviderRequest,
    IngestionProviderResponse,
    IngestionReasoningService,
    build_ingestion_provider_body,
    build_ingestion_reasoning_decision,
    build_ingestion_reasoning_packet,
    ingestion_proposal_output_schema,
    load_ingestion_reasoning_policy,
)
from dual_pipeline.intake import IntakeEngine
from dual_pipeline.provider_request import measure_responses_request

POLICY_PATH = ROOT / "requirements" / "ingestion-reasoning-policy.json"


class CapturingRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


class FakeProvider:
    provider_id = "openai"

    def __init__(self, factory: "FakeProviderFactory") -> None:
        self.factory = factory

    def generate(self, request: Any) -> IngestionProviderResponse:
        self.factory.requests.append(request)
        if self.factory.invalid_calls > 0:
            self.factory.invalid_calls -= 1
            output: Any = {"recommendations": []}
        else:
            packet = request.packet or self.factory.primary_packet
            output = {
                "recommendations": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "recommendation": (
                            "HITL_REQUIRED"
                            if candidate["entity_resolution_outcomes"]
                            else "CANDIDATE"
                        ),
                        "evidence_ids": list(candidate["evidence_ids"]),
                        "reason_codes": ["SOURCE_BOUND_REVIEW"],
                        "reviewer_question": (
                            "Confirm the governed identity."
                            if candidate["entity_resolution_outcomes"]
                            else None
                        ),
                    }
                    for candidate in packet["candidates"]
                ],
                "overall_review_priority": "high",
                "summary_reason_codes": ["ADVISORY_REVIEW_READY"],
            }
        if request.packet is not None:
            self.factory.primary_packet = request.packet
        return IngestionProviderResponse(
            output=output,
            response_digest=f"{len(self.factory.requests):064x}",
            requested_model=self.factory.model,
            delivered_model=self.factory.model,
            input_tokens=100,
            cached_input_tokens=10,
            output_tokens=40,
            total_tokens=140,
            latency_ms=12.5,
        )


class FakeProviderFactory:
    def __init__(self, *, invalid_calls: int = 0) -> None:
        self.initialization_count = 0
        self.requests: list[Any] = []
        self.invalid_calls = invalid_calls
        self.model = ""
        self.primary_packet: Any = None

    def __call__(self, model_policy: Any) -> FakeProvider:
        self.initialization_count += 1
        self.model = model_policy.model
        return FakeProvider(self)


class NeverInitializeFactory:
    def __init__(self) -> None:
        self.initialization_count = 0

    def __call__(self, model_policy: Any) -> Any:
        self.initialization_count += 1
        raise AssertionError(f"provider initialized for {model_policy.model}")


def _inputs(
    *,
    candidate_count: int,
    candidate_type: str = "source_chunk",
    materiality: str = "low",
    classification: str = "internal",
    ambiguous_identity_count: int = 0,
    semantic_match_count: int = 0,
) -> dict[str, Any]:
    candidates = []
    comparisons = {}
    routes = []
    resolutions = []
    for index in range(candidate_count):
        candidate_id = f"candidate-reasoning-{index:03d}"
        evidence_id = f"evidence-reasoning-{index:03d}"
        mention_id = f"mention-reasoning-{index:03d}"
        mentions = (
            [
                {
                    "mention_id": mention_id,
                    "reported_text": "Reported Name",
                    "stable_identifier": None,
                    "context": "Bound source context",
                }
            ]
            if index < ambiguous_identity_count
            else []
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "candidate_type": candidate_type,
                "assertion_type": "candidate_assertion",
                "normalized_text": f"Source-bound candidate text {index}.",
                "materiality": materiality,
                "entity_mentions": mentions,
                "evidence_ids": [evidence_id],
                "source_locator": {"kind": "record", "value": f"record-{index}"},
                "classification": classification,
                "audiences": ["nee-engineering"],
                "extraction_confidence": 0.9,
            }
        )
        matches = [
            {
                "record_id": f"matched-record-{index}-{ordinal}",
                "lane": "candidate",
                "score": 0.5,
            }
            for ordinal in range(semantic_match_count)
        ]
        comparisons[candidate_id] = {
            "candidate_id": candidate_id,
            "exact_matches": [],
            "semantic_matches": matches,
        }
        routes.append(
            {
                "subject_id": candidate_id,
                "intake_route": "NEW_ASSERTION",
            }
        )
        if mentions:
            resolutions.append(
                {
                    "mention_id": mention_id,
                    "outcome": "HITL_CONFIRM_ENTITY",
                }
            )
    return {
        "bundle": {
            "canonical_digest": {"value": "a" * 64},
            "run_id": "run-conditional-ingestion-reasoning",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "source_id": "source-conditional-ingestion-reasoning",
            "source_version_id": "source-version-conditional-ingestion-reasoning",
            "candidates": candidates,
        },
        "plan": {"canonical_digest": {"value": "b" * 64}},
        "resolutions": resolutions,
        "comparison": {"comparisons": comparisons},
        "governed_context": {"record_count": 0, "records": []},
        "routes": routes,
    }


class ConditionalIngestionReasoningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_ingestion_reasoning_policy(POLICY_PATH)

    def _decision(self, **kwargs: Any) -> dict[str, Any]:
        values = _inputs(**kwargs)
        return build_ingestion_reasoning_decision(
            policy=self.policy,
            mode="shadow",
            offline=False,
            **values,
        )

    def test_model_tiers_are_exact_and_reasoning_effort_is_preserved(self) -> None:
        normal = self._decision(candidate_count=5)
        high = self._decision(
            candidate_count=1,
            candidate_type="risk",
            materiality="high",
        )
        most_complex = self._decision(candidate_count=50)
        self.assertEqual(
            ("normal", "gpt-5.6-luna", "low"),
            (
                normal["selected_model_tier"],
                normal["selected_model"],
                normal["reasoning_effort"],
            ),
        )
        self.assertEqual(
            ("high", "gpt-5.6-luna", "xhigh"),
            (
                high["selected_model_tier"],
                high["selected_model"],
                high["reasoning_effort"],
            ),
        )
        self.assertEqual(
            ("most_complex", "gpt-5.6-luna", "max"),
            (
                most_complex["selected_model_tier"],
                most_complex["selected_model"],
                most_complex["reasoning_effort"],
            ),
        )

    def test_openai_request_is_strict_responses_api_without_tools_or_storage(
        self,
    ) -> None:
        model_policy = self.policy.model_for_tier("high")
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            provider = OpenAIIngestionReasoningProvider(
                self.policy,
                model_policy,
            )
        request = IngestionProviderRequest(
            purpose="PRIMARY",
            packet={"packet_id": "sealed-packet"},
            output_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        )
        body = provider._body(request)
        self.assertEqual("gpt-5.6-luna", body["model"])
        self.assertEqual({"effort": "xhigh"}, body["reasoning"])
        self.assertEqual([], body["tools"])
        self.assertFalse(body["store"])
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertEqual("json_schema", body["text"]["format"]["type"])

    def test_low_complexity_and_restricted_content_never_initialize_provider(
        self,
    ) -> None:
        for values in (
            _inputs(candidate_count=1),
            _inputs(candidate_count=5, classification="restricted"),
        ):
            factory = NeverInitializeFactory()
            service = IngestionReasoningService(
                policy=self.policy,
                mode="shadow",
                provider_factory=factory,
            )
            recorder = CapturingRecorder()
            with tempfile.TemporaryDirectory() as temporary:
                result = service.execute(
                    run_dir=Path(temporary),
                    recorder=recorder,
                    **values,
                )
            self.assertEqual(0, factory.initialization_count)
            self.assertEqual(0, result["provider_call_count"])
            self.assertEqual("SKIPPED", result["status"])

    def test_primary_output_is_advisory_only(self) -> None:
        factory = FakeProviderFactory()
        service = IngestionReasoningService(
            policy=self.policy,
            mode="advisory",
            provider_factory=factory,
        )
        recorder = CapturingRecorder()
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            result = service.execute(
                run_dir=run_dir,
                recorder=recorder,
                **_inputs(
                    candidate_count=1,
                    candidate_type="risk",
                    materiality="high",
                ),
            )
            proposal = read_json(run_dir / "ingestion_reasoning_proposal.json")
        self.assertEqual("ADVISORY_READY", result["status"])
        self.assertEqual(1, result["provider_call_count"])
        self.assertEqual(1, factory.initialization_count)
        self.assertFalse(proposal["authority_changes_proposed"])
        self.assertFalse(proposal["graph_operations_proposed"])
        self.assertFalse(proposal["canonical_approval_proposed"])
        self.assertNotIn(
            "APPROVED",
            {value["recommendation"] for value in proposal["recommendations"]},
        )
        actual_metrics = measure_responses_request(
            build_ingestion_provider_body(
                policy=self.policy,
                model_policy=self.policy.model_for_tier(
                    result["selected_model_tier"]
                ),
                request=factory.requests[0],
            )
        )
        self.assertEqual(actual_metrics, result["request_metrics"])

    def test_one_schema_repair_is_allowed_without_replaying_packet(self) -> None:
        factory = FakeProviderFactory(invalid_calls=1)
        service = IngestionReasoningService(
            policy=self.policy,
            mode="shadow",
            provider_factory=factory,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = service.execute(
                run_dir=Path(temporary),
                recorder=CapturingRecorder(),
                **_inputs(
                    candidate_count=1,
                    candidate_type="risk",
                    materiality="high",
                ),
            )
        self.assertEqual("ADVISORY_READY", result["status"])
        self.assertEqual(2, result["provider_call_count"])
        self.assertEqual(1, result["primary_call_count"])
        self.assertEqual(1, result["repair_call_count"])
        self.assertEqual("PRIMARY", factory.requests[0].purpose)
        self.assertIsNotNone(factory.requests[0].packet)
        self.assertEqual("SCHEMA_REPAIR", factory.requests[1].purpose)
        self.assertIsNone(factory.requests[1].packet)
        self.assertEqual(200, result["input_tokens"])
        self.assertEqual(20, result["cached_input_tokens"])
        self.assertEqual(180, result["uncached_input_tokens"])
        self.assertEqual(80, result["output_tokens"])
        self.assertEqual(2.5, result["input_output_ratio"])
        self.assertEqual(0.1, result["cached_input_share"])

    def test_compact_schema_does_not_repeat_item_contract_per_candidate(self) -> None:
        values = _inputs(candidate_count=50)
        decision = build_ingestion_reasoning_decision(
            policy=self.policy,
            mode="shadow",
            offline=False,
            **values,
        )
        packet = build_ingestion_reasoning_packet(
            decision=decision,
            bundle=values["bundle"],
            resolutions=values["resolutions"],
            comparison=values["comparison"],
            governed_context=values["governed_context"],
            routes=values["routes"],
        )
        schema = ingestion_proposal_output_schema(packet)
        items = schema["properties"]["recommendations"]["items"]
        self.assertNotIn("oneOf", items)
        self.assertEqual(
            50,
            len(items["properties"]["candidate_id"]["enum"]),
        )
        self.assertLess(len(canonical_json(schema)), 12000)

    def test_oversized_request_blocks_before_provider_initialization(self) -> None:
        raw = deepcopy(self.policy.raw)
        raw["complexity_routing"]["maximum_provider_request_chars"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = root / "policy.json"
            write_json(policy_path, raw)
            policy = load_ingestion_reasoning_policy(policy_path)
            factory = NeverInitializeFactory()
            service = IngestionReasoningService(
                policy=policy,
                mode="shadow",
                provider_factory=factory,
            )
            result = service.execute(
                run_dir=root,
                recorder=CapturingRecorder(),
                **_inputs(
                    candidate_count=1,
                    candidate_type="risk",
                    materiality="high",
                ),
            )
        self.assertEqual(0, factory.initialization_count)
        self.assertEqual("CONSERVATIVE_FALLBACK", result["status"])
        self.assertEqual("BUDGET_EXHAUSTED", result["failure_code"])
        self.assertEqual("BLOCK", result["budget_verdict"]["status"])

    def test_invalid_primary_and_repair_fall_back_conservatively(self) -> None:
        factory = FakeProviderFactory(invalid_calls=2)
        service = IngestionReasoningService(
            policy=self.policy,
            mode="shadow",
            provider_factory=factory,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = service.execute(
                run_dir=Path(temporary),
                recorder=CapturingRecorder(),
                **_inputs(
                    candidate_count=1,
                    candidate_type="risk",
                    materiality="high",
                ),
            )
        self.assertEqual("CONSERVATIVE_FALLBACK", result["status"])
        self.assertEqual("INVALID_STRUCTURED_RESPONSE", result["failure_code"])
        self.assertEqual(0, result["graph_mutation_count"])
        self.assertEqual(0, result["canonical_approval_count"])
        self.assertEqual(0, result["escalation_call_count"])

    def test_intake_integration_preserves_deterministic_dispositions_and_graph(
        self,
    ) -> None:
        package = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        graph = ROOT / "graph" / "nee_graph.sqlite"
        factory = FakeProviderFactory()
        service = IngestionReasoningService(
            policy=self.policy,
            mode="shadow",
            provider_factory=factory,
        )
        with (
            tempfile.TemporaryDirectory() as disabled_root,
            tempfile.TemporaryDirectory() as enabled_root,
        ):
            disabled = IntakeEngine(graph, Path(disabled_root)).ingest(package)
            enabled = IntakeEngine(
                graph,
                Path(enabled_root),
                ingestion_reasoning=service,
            ).ingest(package)
            proposal = read_json(
                Path(enabled_root)
                / "runs"
                / enabled["run_id"]
                / "ingestion_reasoning_proposal.json"
            )
            enabled_seal = read_json(
                Path(enabled_root) / "runs" / enabled["run_id"] / "run_seal.json"
            )
            disabled_seal = read_json(
                Path(disabled_root) / "runs" / disabled["run_id"] / "run_seal.json"
            )
        self.assertEqual(disabled["disposition_counts"], enabled["disposition_counts"])
        self.assertEqual(
            disabled["graph_operation_count"], enabled["graph_operation_count"]
        )
        self.assertEqual(enabled["graph_sha256_before"], enabled["graph_sha256_after"])
        self.assertEqual(1, enabled["provider_call_count"])
        self.assertEqual(0, enabled["graph_mutation_count"])
        self.assertFalse(proposal["canonical_approval_proposed"])
        self.assertTrue({"L2D", "PA", "L2"} <= set(enabled_seal["required_stages"]))
        self.assertFalse({"L2D", "PA", "L2"} & set(disabled_seal["required_stages"]))


if __name__ == "__main__":
    unittest.main()
