#!/usr/bin/env python3
"""Wave 4 shadow attempt, observation, terminal, and telemetry tests."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import artifact_digest, digest
from dual_pipeline.l1_control import load_policy
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import (
    build_query_plan,
    build_retrieval_evidence_report,
)
from dual_pipeline.query_l1_shadow import (
    ShadowReceiptError,
    build_attempt_zero,
    build_off_shadow_comparison,
    build_shadow_receipts,
    shadow_telemetry_payload,
    validate_shadow_telemetry,
)
from dual_pipeline.query_router import route_query
from tools.validate_dual_pipeline_contracts import SchemaRegistry, validate_artifact

ROOT = Path(__file__).resolve().parents[1]
GRAPH_SHA = "0" * 64
SIDECAR_SHA = "1" * 64
AUTH_SHA = "2" * 64


def _policy():
    return load_policy(json.loads((ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8")))


def _plan(case_id: str = "complete") -> dict[str, object]:
    question = "Which exact governed Project Beacon records are available?"
    route = route_query(question).to_artifact(
        request_id=f"wave4-request-{case_id}",
        run_id=f"wave4-run-{case_id}",
        trace_id=digest(["wave4-trace", case_id])[:32],
        decided_at="2026-08-06T00:00:00Z",
    )
    return build_query_plan(
        question=question,
        query_type="exact_lookup",
        request_id=f"wave4-request-{case_id}",
        run_id=f"wave4-run-{case_id}",
        current_trace_id=digest(["wave4-trace", case_id])[:32],
        l2_config=load_l2_config(),
        provider_egress_allowed=False,
        destination_decision=route,
        assurance_profile=None,
    )


def _manifest(plan: dict[str, object], case_id: str, *, kind: str = "evidence", allowed_lanes: list[str] | None = None, disposition: str = "explicit_gap") -> dict[str, object]:
    body = {
        "manifest_id": f"wave4-manifest-{case_id}",
        "run_id": plan["run_id"],
        "validated_request_digest": digest(["wave4-request", case_id]),
        "query_plan_digest": plan["canonical_digest"]["value"],
        "graph_snapshot": "wave4-fixture",
        "graph_sha256": GRAPH_SHA,
        "authorization_scope_digest": AUTH_SHA,
        "requirements": [
            {
                "requirement_id": f"wave4-requirement-{case_id}",
                "requirement_label": "A2A protocol evidence",
                "requirement_kind": kind,
                "allowed_lanes": allowed_lanes or ["approved"],
                "missing_evidence_disposition": disposition,
                "materiality": "high",
            }
        ],
    }
    return create_artifact("query-coverage-manifest", body["manifest_id"], **{key: value for key, value in body.items() if key != "manifest_id"}, manifest_id=body["manifest_id"])


def _report(plan: dict[str, object], case_id: str, *, outcome: str = "explicit_gap", kind: str = "evidence", allowed_lanes: list[str] | None = None, packet_allowed_lanes: list[str] | None = None, row: dict[str, object] | None = None, counterevidence: list[dict[str, object]] | None = None) -> tuple[dict[str, object], dict[str, object]]:
    manifest = _manifest(plan, case_id, kind=kind, allowed_lanes=allowed_lanes)
    packet_lanes = packet_allowed_lanes or allowed_lanes or ["approved"]
    packet_body = {
        "graph_snapshot": "wave4-fixture",
        "allowed_lanes": packet_lanes,
        "approved": [row] if row is not None and packet_lanes == ["approved"] else [],
        "candidate": [row] if row is not None and packet_lanes == ["candidate"] else [],
        "external_public": [],
        "counterevidence": counterevidence or [],
    }
    packet_body["packet_digest"] = digest(packet_body)
    report = build_retrieval_evidence_report(
        coverage_manifest=manifest,
        packet=packet_body,
        authorization_scope_digest=AUTH_SHA,
        graph_sha256=GRAPH_SHA,
        view_authorized=True,
    )
    return manifest, report


def _active() -> dict[str, str]:
    return {
        "generation_id": "wave4-generation",
        "graph_sha256": GRAPH_SHA,
        "sidecar_sha256": SIDECAR_SHA,
        "embedding_backend": "deterministic_fixture",
        "model_id": "fixture-model",
        "model_revision": "fixture-revision",
    }


class L1QueryShadowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = _policy()
        cls.registry = SchemaRegistry(ROOT / "contracts" / "dual_pipeline" / "schemas")
        cls.registry.load()

    def _build(self, case_id: str, **kwargs):
        plan = _plan(case_id)
        manifest, report = _report(plan, case_id, **kwargs)
        return plan, manifest, report, build_shadow_receipts(
            plan_v2=plan,
            coverage_manifest=manifest,
            evidence_report=report,
            active_pair=_active(),
            policy=self.policy,
            runtime_terminal_outcome="X3B_ESCALATE_HITL" if kwargs.get("outcome") != "covered" else "X3D_ALLOW_FINISH",
            runtime_next_stage="HITL" if kwargs.get("outcome") != "covered" else "NONE",
        )

    def test_complete_case_seals_attempt_observation_terminal_and_no_extra_work(self) -> None:
        row = {"record": {"record_id": "record-a2a", "title": "A2A protocol evidence", "retrieval_text": "A2A protocol evidence"}}
        plan, manifest, report, receipts = self._build("complete", outcome="covered", row=row)
        self.assertIsNone(receipts.shadow_action)
        self.assertEqual("L1_COMPLETE", receipts.controller_terminal_reason)
        for artifact in receipts.artifacts:
            if artifact.get("contract_id", "").endswith("query-plan:3.0.0") or artifact.get("contract_id", "").endswith("query-retrieval-attempt:1.0.0") or artifact.get("contract_id", "").endswith("query-plan-observation:1.0.0") or artifact.get("contract_id", "").endswith("query-plan-terminal:1.0.0"):
                validate_artifact(artifact, self.registry)
            self.assertEqual(artifact_digest(artifact), artifact["canonical_digest"]["value"])
        self.assertEqual(0, receipts.terminal["final_provider_call_count"])
        self.assertEqual(GRAPH_SHA, receipts.terminal["graph_sha256_before"])
        self.assertEqual(GRAPH_SHA, receipts.terminal["graph_sha256_after"])
        telemetry = shadow_telemetry_payload(receipts, runtime_terminal_outcome="X3D_ALLOW_FINISH", runtime_next_stage="NONE")
        self.assertFalse(telemetry["executed"])
        self.assertEqual("SHADOW_ONLY", telemetry["reason_code"])
        self.assertEqual(0, telemetry["provider_call_count"])

    def test_recoverable_gap_emits_skipped_shadow_action_and_explicit_reason(self) -> None:
        _, _, report, receipts = self._build("recoverable-gap", outcome="explicit_gap")
        self.assertIsNotNone(receipts.shadow_action)
        self.assertEqual("skipped", receipts.shadow_action["result"]["status"])
        self.assertEqual("SHADOW_ONLY", receipts.shadow_action["extensions"]["reason_code"])
        self.assertEqual("SHADOW", receipts.terminal["terminal_reason"])
        self.assertEqual(report["canonical_digest"]["value"], receipts.terminal["final_evidence_report_digest"])
        self.assertEqual(0, receipts.shadow_action["provider_call_count"])
        self.assertEqual(0, receipts.shadow_action["provider_initialization_count"])

    def test_true_gap_reverse_only_unauthorized_and_conflict_do_not_refine(self) -> None:
        reverse_row = {
            "record": {"record_id": "record-reverse", "title": "A2A protocol evidence", "retrieval_text": "A2A protocol evidence"},
            "graph_path": {"steps": [{"edge_type": "DEPENDS_ON", "traversal_direction": "reverse"}]},
        }
        cases = {
            "reverse-only": {"kind": "relationship", "row": reverse_row},
            "unauthorized": {"packet_allowed_lanes": ["candidate"]},
            "conflict": {
                "row": {"record": {"record_id": "record-conflict", "title": "A2A protocol evidence", "retrieval_text": "A2A protocol evidence"}},
                "counterevidence": [{"record_id": "record-conflict"}],
            },
        }
        for case_id, kwargs in cases.items():
            with self.subTest(case_id=case_id):
                _, _, report, receipts = self._build(case_id, **kwargs)
                self.assertIn(report["requirement_outcomes"][0]["outcome"], {"explicit_gap", "unauthorized", "conflict"})
                self.assertIsNone(receipts.shadow_action)
                self.assertEqual(0, receipts.planning.actions.__len__())

    def test_cache_hit_and_miss_comparison_is_content_safe_and_detects_drift(self) -> None:
        projection = {
            "route_digest": "a" * 64,
            "retrieval_digest": "b" * 64,
            "selected_records_digest": "c" * 64,
            "evidence_packet_digest": "d" * 64,
            "l2d_digest": "e" * 64,
            "answer_digest": "f" * 64,
            "x_gates_digest": "0" * 64,
            "cache_behavior_digest": "1" * 64,
            "provider_call_count": 0,
            "graph_sha256_before": GRAPH_SHA,
            "graph_sha256_after": GRAPH_SHA,
            "terminal_outcome": "X3D_ALLOW_FINISH",
            "caller_output_digest": "2" * 64,
        }
        hit = build_off_shadow_comparison(off_projection=projection, shadow_projection=projection)
        self.assertEqual("PASS", hit["status"])
        miss_projection = dict(projection, cache_behavior_digest="3" * 64)
        miss = build_off_shadow_comparison(off_projection=projection, shadow_projection=miss_projection)
        self.assertEqual("FAIL", miss["status"])
        self.assertFalse(miss["fields"]["cache_behavior_digest"]["equal"])
        self.assertNotIn("question", json.dumps(hit).casefold())
        self.assertNotIn("prompt", json.dumps(hit).casefold())

    def test_mutated_bindings_missing_attempt_and_telemetry_content_fail_closed(self) -> None:
        plan = _plan("mutation")
        manifest, report = _report(plan, "mutation")
        with self.assertRaisesRegex(ShadowReceiptError, "L1_INTERNAL_INVALID"):
            build_attempt_zero(
                plan=plan,
                coverage_manifest=manifest,
                evidence_report=dict(report, coverage_manifest_digest="f" * 64),
                active_pair=_active(),
                graph_sha256_before=GRAPH_SHA,
                graph_sha256_after=GRAPH_SHA,
            )
        _, _, _, receipts = self._build("missing-attempt")
        with self.assertRaisesRegex(ShadowReceiptError, "L1_INTERNAL_INVALID"):
            # Rebuilding from no attempt artifact is not a valid observation chain.
            from dual_pipeline.query_l1_shadow import build_observation_zero

            build_observation_zero(
                plan=plan,
                coverage_manifest=manifest,
                evidence_report=report,
                attempt_zero={"canonical_digest": {"value": "a" * 64}},
                graph_sha256_before=GRAPH_SHA,
                graph_sha256_after=GRAPH_SHA,
            )
        with self.assertRaisesRegex(ShadowReceiptError, "L1_POLICY_BLOCKED"):
            validate_shadow_telemetry({"question": "raw secret"})

    def test_off_mode_is_empty_and_does_not_touch_v2_artifacts(self) -> None:
        plan = _plan("off")
        manifest, report = _report(plan, "off")
        receipts = build_shadow_receipts(
            plan_v2=plan,
            coverage_manifest=manifest,
            evidence_report=report,
            active_pair=_active(),
            policy=self.policy,
            mode="off",
        )
        self.assertEqual("off", receipts.mode)
        self.assertEqual((), receipts.artifacts)
        self.assertIsNone(receipts.planning)


if __name__ == "__main__":
    unittest.main()
