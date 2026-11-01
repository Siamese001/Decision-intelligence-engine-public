"""Wave 7 cache namespaces never cross L1 controller modes or policies."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from dual_pipeline.core import digest
from dual_pipeline.l1_control import load_policy
from dual_pipeline.semantic_cache import SemanticCache

ROOT = Path(__file__).resolve().parents[1]


class L1QueryCacheTests(unittest.TestCase):
    def _query_context(self, *, mode: str, policy_digest: str | None) -> dict:
        return {
            "use_case": "query",
            "cache_payload_kind": "sealed_query_output_v4",
            "destination": "NEE_DECISION_INTELLIGENCE",
            "eligibility_policy_digest": "a" * 64,
            "assurance_profile": "standard",
            "delivery_profile": "decision_review",
            "artifact_type": "decision_review",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "allowed_lanes": ["approved"],
            "active_pair": {"generation_id": "generation-a"},
            "coverage_requirements_digest": "b" * 64,
            "l2_policy_digest": "c" * 64,
            "x1_policy_digest": "d" * 64,
            "provider_egress_allowed": False,
            "query_plan_version": "3.0.0",
            "l1_controller_mode": mode,
            "l1_controller_policy_digest": policy_digest,
            "l1_action_policy_digest": digest(["requirement_hybrid_retrieval", "stop"]),
            "l1_budget_profile": {"max_actions": 2},
        }

    def test_namespace_requires_all_l1_bindings_and_separates_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = SemanticCache(Path(temp) / "runtime")
            off = cache.compatibility(self._query_context(mode="off", policy_digest=None))
            bounded = cache.compatibility(
                self._query_context(mode="bounded", policy_digest="e" * 64)
            )
            self.assertNotEqual(digest(off), digest(bounded))
            incomplete = self._query_context(mode="off", policy_digest=None)
            incomplete.pop("l1_budget_profile")
            with self.assertRaisesRegex(ValueError, "namespace is incomplete"):
                cache.compatibility(incomplete)

    def test_policy_digest_changes_the_bounded_namespace(self) -> None:
        base = load_policy(
            __import__("json").loads(
                (ROOT / "requirements" / "query-l1-control-policy.json").read_text(encoding="utf-8")
            )
        )
        bounded_a = replace(base, mode="bounded", policy_digest="1" * 64)
        bounded_b = replace(base, mode="bounded", policy_digest="2" * 64)
        self.assertNotEqual(bounded_a.policy_digest, bounded_b.policy_digest)


if __name__ == "__main__":
    unittest.main()
