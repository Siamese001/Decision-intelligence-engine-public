"""Unit tests for Phase 7 — Signal Extraction Separation."""
from __future__ import annotations

import unittest

from dual_pipeline.signal_extraction import (
    SignalExtractor,
    score_candidate_signal,
    validate_entity_prefix,
)


class TestSignalExtraction(unittest.TestCase):
    def test_entity_prefix_validation(self) -> None:
        self.assertTrue(validate_entity_prefix("person:amit-ayer"))
        self.assertTrue(validate_entity_prefix("org:partner_advisory"))
        self.assertTrue(validate_entity_prefix("system:bge-m3"))
        self.assertTrue(validate_entity_prefix("workstream:di-engine"))
        self.assertTrue(validate_entity_prefix("project:decision-intelligence"))
        self.assertTrue(validate_entity_prefix("metric:recall-at-20"))
        self.assertTrue(validate_entity_prefix("role:lead-architect"))
        self.assertTrue(validate_entity_prefix("policy:lane-separation"))
        self.assertFalse(validate_entity_prefix("unknown:entity"))
        self.assertFalse(validate_entity_prefix("plain_text"))

    def test_score_valid_entity(self) -> None:
        entity = {
            "id": "person:jane-doe",
            "type": "PERSON",
            "name": "Jane Doe",
        }
        res = score_candidate_signal(entity, "entity_upsert")
        self.assertEqual(res["promotion_status"], "PROMOTED")
        self.assertEqual(res["epistemic_confidence"], 1.0)
        self.assertIn("CONFORMANT", res["reason_codes"])

    def test_score_invalid_entity(self) -> None:
        entity = {
            "id": "badprefix:jane-doe",
            "type": "",
            "name": "",
        }
        res = score_candidate_signal(entity, "entity_upsert")
        self.assertEqual(res["promotion_status"], "QUARANTINED")
        self.assertIn("INVALID_ENTITY_PREFIX", res["reason_codes"])
        self.assertIn("MISSING_ENTITY_TYPE", res["reason_codes"])

    def test_extract_and_gate(self) -> None:
        extractor = SignalExtractor()
        raw_projection = {
            "schema_version": "5.7",
            "meeting_id": "meet-001",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "PERSON", "name": "Alice"},
                    {"id": "bad:bob", "type": "PERSON", "name": "Bob"},
                ],
                "relationship_signals": [
                    {
                        "from_id": "person:alice",
                        "to_id": "org:partner_advisory",
                        "relationship": "MEMBER_OF",
                    }
                ],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": ["ev-1", "ev-2", "ev-3", "ev-4", "ev-5", "ev-6", "ev-7", "ev-8"],
        }

        promoted, receipt = extractor.extract_and_gate(raw_projection)

        # Alice is promoted, Bob is quarantined
        upserts = promoted["graph_mutations"]["entity_upserts"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0]["id"], "person:alice")

        # Relationships
        rels = promoted["graph_mutations"]["relationship_signals"]
        self.assertEqual(len(rels), 1)

        # Receipt checks
        self.assertEqual(receipt["promoted_signal_count"], 2)  # 1 entity + 1 rel
        self.assertEqual(receipt["quarantined_signal_count"], 1)

        # C-17 Hard Evidence Ceiling: max(3, 2 * 2) = 4
        self.assertEqual(receipt["evidence_ceiling_enforced"], 4)
        self.assertEqual(len(promoted["evidence_only_statement_ids"]), 4)

    def test_multiline_pdf_evidence_span_preserved_without_line_truncation(self) -> None:
        extractor = SignalExtractor()
        pdf_source = (
            "Quarterly Bonus Eligibility and Plan Period\n"
            "PartnerAdvisory\u2019s fiscal quarters are 1 January through 31 March, 1 April\n"
            "through 30 June, 1 July through 30 September, and 1 October through 31 December.\n"
        )
        single_line_quote = (
            "PartnerAdvisory\u2019s fiscal quarters are 1 January through 31 March, 1 April "
            "through 30 June, 1 July through 30 September, and 1 October through 31 December."
        )
        raw_projection = {
            "schema_version": "5.7",
            "meeting_id": "document-20260101-bonus-plan",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_index": {
                "ev-08": {
                    "speaker": "PartnerAdvisory People Team",
                    "source_classification": "FACT",
                    "kind": "summary",
                    "text": single_line_quote,
                    "exact_text": single_line_quote,
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-08"]}
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
        }
        promoted, _ = extractor.extract_and_gate(
            raw_projection,
            metadata={"is_document": True, "output3_only": True},
            forensic_transcript=pdf_source,
        )
        ev08 = promoted["evidence_index"]["ev-08"]
        self.assertIn("1 October through 31 December", ev08["exact_text"])
        self.assertEqual(ev08["grounding_status"], "GROUNDED")

    def test_cross_page_pdf_sentence_and_mid_sentence_pointer_extension(self) -> None:
        from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
            locate_quote_pointer,
            resolve_evidence_pointers,
        )

        source_text = (
            "## Page 2\n\n"
            "Payout\n"
            "The discretionary bonus payout amount is determined by PartnerAdvisory management based on\n"
            "achievement against bonus goals and other factors as determined by management after\n\n"
            "## Page 3\n\n"
            "US Bonus Plan Program (1.2026)\n"
            "the end of the Plan period and once results are determined. The payout is subject to\n"
            "applicable tax withholdings.\n"
        )
        full_sentence = (
            "The discretionary bonus payout amount is determined by PartnerAdvisory management based on "
            "achievement against bonus goals and other factors as determined by management after "
            "the end of the Plan period and once results are determined."
        )
        ptr = locate_quote_pointer(source_text, full_sentence)
        self.assertIsNotNone(ptr)
        assert ptr is not None
        self.assertIn("once results are determined.", source_text[ptr[0] : ptr[1]])

        start_idx = source_text.index("The discretionary bonus")
        truncated_end = start_idx + len("The discretionary bonus payout amount is determined by PartnerAdvisory management based on")
        proj = {
            "evidence_index": {
                "ev-15": {
                    "exact_text": "The discretionary bonus payout amount is determined by PartnerAdvisory management based on",
                    "text": "The discretionary bonus payout amount is determined by PartnerAdvisory management based on",
                    "quote_pointer": [start_idx, truncated_end],
                    "start_offset": start_idx,
                    "end_offset": truncated_end,
                }
            }
        }
        resolve_evidence_pointers(proj, source_text)
        self.assertIn("once results are determined.", proj["evidence_index"]["ev-15"]["exact_text"])


if __name__ == "__main__":
    unittest.main()
