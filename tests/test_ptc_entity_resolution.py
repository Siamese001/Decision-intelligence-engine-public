"""
tests/test_ptc_entity_resolution.py

Unit tests for Programmatic Entity Resolution & Grounding Harness (PERH) in Pipeline A.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.entity_resolution import (
    ALLOWED_ENTITY_PREFIXES,
    PTCEntityResolutionPlan,
    PTCEntityResolver,
    format_temporal_date,
    normalize_predicate,
    normalize_prefix,
)
from dual_pipeline.ptc.sandbox import PTCSandbox


class TestPTCEntityResolution(unittest.TestCase):

    def setUp(self) -> None:
        self.resolver = PTCEntityResolver()

    def test_prefix_normalization_preserves_valid_prefixes(self) -> None:
        for prefix in ALLOWED_ENTITY_PREFIXES:
            entity = f"{prefix}sample_entity"
            self.assertEqual(normalize_prefix(entity), entity)

    def test_prefix_normalization_prepends_default_prefix(self) -> None:
        raw_entity = "Project Nexus Engine"
        normalized = normalize_prefix(raw_entity, default_prefix="project:")
        self.assertEqual(normalized, "project:project_nexus_engine")

    def test_predicate_normalization_maps_to_w2_spine(self) -> None:
        self.assertEqual(normalize_predicate("relies_on"), "depends_on")
        self.assertEqual(normalize_predicate("dependency"), "depends_on")
        self.assertEqual(normalize_predicate("mitigates"), "mitigated_by")
        self.assertEqual(normalize_predicate("owned_by"), "owns")
        self.assertEqual(normalize_predicate("depends_on"), "depends_on")

    def test_temporal_date_stamping_enforces_mm_dd_yyyy(self) -> None:
        formatted = format_temporal_date("09/13/2026")
        self.assertEqual(formatted, "09/13/2026")

        fallback = format_temporal_date("invalid-date-format")
        self.assertRegex(fallback, r"^\d{2}/\d{2}/\d{4}$")

    def test_resolver_enforces_closed_kinds_and_ambiguity_locks(self) -> None:
        triples = [
            # High confidence valid triple
            {
                "subject": "system:nexus_engine",
                "predicate": "depends_on",
                "object": "system:maximo",
                "kind": "technical",
                "confidence": 0.95,
            },
            # Ambiguous confidence triple (0.75) -> should register in ambiguity_locks
            {
                "subject": "person:tanner",
                "predicate": "owns",
                "object": "workstream:governance",
                "kind": "strategic",
                "confidence": 0.75,
            },
            # Invalid kind -> should be rejected
            {
                "subject": "system:a",
                "predicate": "affects",
                "object": "system:b",
                "kind": "unsupported_kind",
                "confidence": 0.90,
            },
        ]

        plan = PTCEntityResolutionPlan(
            triples=triples,
            meeting_date="09/13/2026",
            validity_months=6,
            catalog_alias_map={"person:tanner": "person:tanner_stevens"},
        )

        resolved = self.resolver.resolve(plan)

        self.assertEqual(resolved.total_triples, 3)
        self.assertEqual(resolved.promoted_count, 2)
        self.assertEqual(len(resolved.rejected_triples), 1)
        self.assertEqual(resolved.ambiguity_count, 1)

        # Verify alias resolution
        self.assertEqual(resolved.promoted_triples[1]["subject"], "person:tanner_stevens")
        # Verify temporal stamps
        self.assertEqual(resolved.promoted_triples[0]["observed_at"], "09/13/2026")
        self.assertEqual(resolved.promoted_triples[0]["stale_after"], "03/13/2027")

    def test_sandbox_executes_entity_resolution(self) -> None:
        class EntityProxy:
            def __init__(self, resolver):
                self.resolver = resolver
            def resolve_triples(self, triples_list):
                plan = PTCEntityResolutionPlan(
                    triples=triples_list,
                    meeting_date="09/13/2026",
                )
                res = self.resolver.resolve(plan)
                return res.to_dict()

        script = """
def run(entity_proxy):
    raw_triples = [
        {"subject": "Nexus", "predicate": "relies_on", "object": "Maximo", "kind": "technical", "confidence": 0.92}
    ]
    resolved = entity_proxy.resolve_triples(raw_triples)
    first_promoted = resolved["promoted_triples"][0]
    return {
        "subject": first_promoted["subject"],
        "predicate": first_promoted["predicate"],
        "object": first_promoted["object"],
        "observed_at": first_promoted["observed_at"]
    }
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ctx = PTCExecutionContext(
                task_id="task_entity_sandbox",
                runtime_root=Path(tmp_dir),
                timeout_seconds=5.0,
                token_budget=TokenBudget(max_payload_bytes=2048),
                proxies={"entity_proxy": EntityProxy(self.resolver)},
            )
            sandbox = PTCSandbox()
            summary = sandbox.execute(script, ctx)

        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data["subject"], "system:nexus")
        self.assertEqual(summary.data["predicate"], "depends_on")
        self.assertEqual(summary.data["object"], "system:maximo")
        self.assertEqual(summary.data["observed_at"], "09/13/2026")


if __name__ == "__main__":
    unittest.main()
