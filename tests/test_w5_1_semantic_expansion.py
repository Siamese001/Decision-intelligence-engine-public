"""Provider-free adversarial tests for the W5.1 semantic expansion."""

from __future__ import annotations

import copy
import hashlib
import unittest

from dual_pipeline.reference_monitor import ReferenceMonitor, SourceAssertionRegistry
from dual_pipeline.semantic_expansion import (
    SemanticExpansionError,
    project_agentic_readiness,
    validate_bundle,
)
from tests.decision_intelligence_support import authorization, source_assertion


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _bundle(*, classification: str = "internal", audiences: tuple[str, ...] = ("internal_consulting",)) -> dict[str, list[dict[str, object]]]:
    def envelope(record_id: str) -> dict[str, object]:
        return {
            "source_assertion_ids": [f"src-{record_id}"],
            "classification": classification,
            "audiences": list(audiences),
            "source_as_of": "2026-08-08T00:00:00Z",
            "recorded_at": "2026-08-08T00:05:00Z",
        }

    return {
        "agents": [
            {
                **envelope("agent-1"),
                "record_id": "agent-1",
                "record_type": "agent",
                "agent_id": "agent-1",
                "name": "Delivery Readiness Agent",
                "owner_scope_id": "nea-ai",
                "purpose": "Bounded readiness evidence review",
            }
        ],
        "agent_versions": [
            {
                **envelope("version-1"),
                "record_id": "version-1",
                "record_type": "agent_version",
                "agent_version_id": "version-1",
                "agent_id": "agent-1",
                "version": "1.0.0",
                "model_id": "model-readiness-1",
                "prompt_policy_digest": _digest("p"),
            }
        ],
        "tools": [
            {
                **envelope("tool-1"),
                "record_id": "tool-1",
                "record_type": "tool",
                "tool_id": "tool-1",
                "name": "Evidence Resolver",
                "purpose": "Resolve source-bound evidence",
                "access_scope": "nea-ai",
                "egress_policy_digest": _digest("e"),
            }
        ],
        "deployments": [
            {
                **envelope("deployment-1"),
                "record_id": "deployment-1",
                "record_type": "agent_deployment",
                "deployment_id": "deployment-1",
                "agent_version_id": "version-1",
                "environment": "pilot",
                "lifecycle_state": "ACTIVE",
                "tool_ids": ["tool-1"],
                "effective_at": "2026-08-08T00:00:00Z",
            }
        ],
        "evaluations": [
            {
                **envelope("evaluation-1"),
                "record_id": "evaluation-1",
                "record_type": "evaluation_run",
                "evaluation_run_id": "evaluation-1",
                "deployment_id": "deployment-1",
                "suite_digest": _digest("s"),
                "result_state": "PASS",
                "score": 0.94,
                "evaluated_at": "2026-08-08T12:00:00Z",
            }
        ],
        "readiness_observations": [
            {
                **envelope("observation-1"),
                "record_id": "observation-1",
                "record_type": "readiness_observation",
                "observation_id": "observation-1",
                "deployment_id": "deployment-1",
                "dimension": "quality",
                "state": "READY",
                "value": "evaluation threshold met",
                "observed_at": "2026-08-08T12:00:00Z",
                "stale_after": "2026-08-20T00:00:00Z",
                "evaluation_run_ids": ["evaluation-1"],
            }
        ],
    }


def _monitor(bundle: dict[str, list[dict[str, object]]], *, private: bool = False) -> ReferenceMonitor:
    assertions_by_id: dict[str, dict[str, object]] = {}
    for rows in bundle.values():
        for row in rows:
            for source_id in row["source_assertion_ids"]:
                if source_id == "unknown-source":
                    continue
                assertions_by_id.setdefault(
                    source_id,
                    source_assertion(
                        source_id,
                        classification=str(row["classification"]),
                        audiences=tuple(row["audiences"]),
                    ),
                )
    assertions = list(assertions_by_id.values())
    return ReferenceMonitor(SourceAssertionRegistry(assertions))


class W51SemanticExpansionTests(unittest.TestCase):
    def test_valid_agent_version_deployment_tool_evaluation_chain_projects_candidate(self) -> None:
        bundle = _bundle()
        result = project_agentic_readiness(
            bundle,
            deployment_id="deployment-1",
            audience="internal_consulting",
            as_of="2026-08-09T00:00:00Z",
            known_at="2026-08-09T00:00:00Z",
            authorization_context=authorization(),
            reference_monitor=_monitor(bundle),
        )
        self.assertEqual("READY_FOR_REVIEW", result["state"])
        self.assertEqual("DERIVED_CANDIDATE_ONLY", result["authority"])
        self.assertFalse(result["graph_mutation_authorized"])
        self.assertEqual(0, result["provider_calls"])

    def test_typed_link_integrity_rejects_unknown_version(self) -> None:
        bundle = _bundle()
        bundle["deployments"][0]["agent_version_id"] = "unknown-version"
        with self.assertRaisesRegex(SemanticExpansionError, "unknown version"):
            validate_bundle(bundle)

    def test_raw_telemetry_is_not_a_semantic_record(self) -> None:
        bundle = _bundle()
        bundle["agents"][0]["raw_prompt"] = "secret prompt"
        with self.assertRaises(SemanticExpansionError):
            validate_bundle(bundle)

    def test_unknown_source_lineage_fails_closed(self) -> None:
        bundle = _bundle()
        bundle["agents"][0]["source_assertion_ids"] = ["unknown-source"]
        with self.assertRaisesRegex(SemanticExpansionError, "unknown or unavailable"):
            project_agentic_readiness(
                bundle,
                deployment_id="deployment-1",
                audience="internal_consulting",
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
                authorization_context=authorization(),
                reference_monitor=_monitor(bundle),
            )

    def test_caller_label_tampering_fails_closed(self) -> None:
        bundle = _bundle()
        monitor = _monitor(bundle)
        bundle["agent_versions"][0]["audiences"] = ["client_approved_external"]
        with self.assertRaisesRegex(SemanticExpansionError, "exactly equal"):
            project_agentic_readiness(
                bundle,
                deployment_id="deployment-1",
                audience="internal_consulting",
                as_of="2026-08-09T00:00:00Z",
                known_at="2026-08-09T00:00:00Z",
                authorization_context=authorization(),
                reference_monitor=monitor,
            )

    def test_future_recorded_readiness_abstains(self) -> None:
        bundle = _bundle()
        bundle["readiness_observations"][0]["recorded_at"] = "2026-08-10T00:00:00Z"
        result = project_agentic_readiness(
            bundle,
            deployment_id="deployment-1",
            audience="internal_consulting",
            as_of="2026-08-09T00:00:00Z",
            known_at="2026-08-09T00:00:00Z",
            authorization_context=authorization(),
            reference_monitor=_monitor(bundle),
        )
        self.assertEqual("STALE_OR_UNKNOWN", result["state"])
        self.assertEqual([], result.get("readiness_observation_ids", []))

    def test_private_tool_is_excluded_without_leaking_its_id(self) -> None:
        bundle = _bundle(audiences=("client_approved_external",), classification="public")
        private_tool = {
            "record_id": "tool-private",
            "record_type": "tool",
            "tool_id": "tool-private",
            "name": "Private Tool",
            "purpose": "Restricted internal control",
            "access_scope": "private",
            "egress_policy_digest": _digest("r"),
            "source_assertion_ids": ["src-private-tool"],
            "classification": "restricted",
            "audiences": ["partner_advisory_account_leadership"],
            "source_as_of": "2026-08-08T00:00:00Z",
            "recorded_at": "2026-08-08T00:05:00Z",
        }
        bundle["tools"].append(private_tool)
        result = project_agentic_readiness(
            bundle,
            deployment_id="deployment-1",
            audience="client_approved_external",
            as_of="2026-08-09T00:00:00Z",
            known_at="2026-08-09T00:00:00Z",
            authorization_context=authorization(
                audiences=("client_approved_external",), classification_clearance="public"
            ),
            reference_monitor=_monitor(bundle, private=True),
        )
        self.assertNotIn("tool-private", result.get("tool_ids", []))
        self.assertNotIn("src-private-tool", result["source_assertion_ids"])

    def test_hidden_agent_version_downgrades_to_candidate_gap(self) -> None:
        bundle = _bundle(audiences=("client_approved_external",), classification="public")
        bundle["agent_versions"][0]["classification"] = "restricted"
        bundle["agent_versions"][0]["audiences"] = ["partner_advisory_account_leadership"]
        result = project_agentic_readiness(
            bundle,
            deployment_id="deployment-1",
            audience="client_approved_external",
            as_of="2026-08-09T00:00:00Z",
            known_at="2026-08-09T00:00:00Z",
            authorization_context=authorization(
                audiences=("client_approved_external",), classification_clearance="public"
            ),
            reference_monitor=_monitor(bundle),
        )
        self.assertEqual("CANDIDATE_WITH_GAPS", result["state"])
        self.assertIsNone(result["agent_id"])
        self.assertNotIn("src-version-1", result["source_assertion_ids"])

    def test_conflicting_current_readiness_requires_hitl(self) -> None:
        bundle = _bundle()
        conflict = copy.deepcopy(bundle["readiness_observations"][0])
        conflict.update(
            {
                "record_id": "observation-2",
                "observation_id": "observation-2",
                "source_assertion_ids": ["src-observation-2"],
                "state": "AT_RISK",
                "value": "quality threshold disputed",
            }
        )
        bundle["readiness_observations"].append(conflict)
        result = project_agentic_readiness(
            bundle,
            deployment_id="deployment-1",
            audience="internal_consulting",
            as_of="2026-08-09T00:00:00Z",
            known_at="2026-08-09T00:00:00Z",
            authorization_context=authorization(),
            reference_monitor=_monitor(bundle),
        )
        self.assertEqual("HITL_REQUIRED", result["state"])

    def test_output_never_contains_raw_telemetry_or_approval_authority(self) -> None:
        bundle = _bundle()
        result = project_agentic_readiness(
            bundle,
            deployment_id="deployment-1",
            audience="internal_consulting",
            as_of="2026-08-09T00:00:00Z",
            known_at="2026-08-09T00:00:00Z",
            authorization_context=authorization(),
            reference_monitor=_monitor(bundle),
        )
        serialized = repr(result)
        self.assertNotIn("prompt", serialized.lower())
        self.assertNotIn("trace", serialized.lower())
        self.assertFalse(result["graph_mutation_authorized"])
        self.assertFalse(result["release_activation"])


if __name__ == "__main__":
    unittest.main()
