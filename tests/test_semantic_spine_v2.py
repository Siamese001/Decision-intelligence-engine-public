from __future__ import annotations

import unittest

from dual_pipeline.semantic_spine_v2 import SemanticSpineV2Error, validate_relation


class SemanticSpineV2Tests(unittest.TestCase):
    def test_nea_owner_group_can_own_rewire(self) -> None:
        result = validate_relation(
            relation="owns",
            source_type="owner_group",
            target_type="initiative",
            qualifiers={"effective_from": "2026-08-10", "source_assertion_ids": ["hitl-rewire"]},
        )
        self.assertEqual("owns", result["relation"])

    def test_partner_advisory_support_requires_a_selected_scope(self) -> None:
        with self.assertRaisesRegex(SemanticSpineV2Error, "unspecified"):
            validate_relation(
                relation="supports",
                source_type="organization",
                target_type="initiative",
                qualifiers={
                    "support_scope_id": "UNSPECIFIED",
                    "source_assertion_ids": ["hitl-rewire"],
                },
            )

    def test_compound_legacy_predicate_is_not_a_v2_relation(self) -> None:
        with self.assertRaisesRegex(SemanticSpineV2Error, "unsupported"):
            validate_relation(
                relation="OWNS_OR_LEADS",
                source_type="organization",
                target_type="initiative",
                qualifiers={"source_assertion_ids": ["legacy"]},
            )

    def test_evidence_binding_is_required(self) -> None:
        with self.assertRaisesRegex(SemanticSpineV2Error, "source_assertion_ids"):
            validate_relation(
                relation="leads",
                source_type="organization",
                target_type="initiative",
                qualifiers={"effective_from": "2026-08-10", "source_assertion_ids": []},
            )
