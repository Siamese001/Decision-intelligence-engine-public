"""Adversarial and Edge-Case Tests for Wave 4: Source Grounding, Provenance & Fixture Containment.

Exercises:
1. Ungrounded quote hallucination detection & stripping.
2. Trivial section header locator rejection in evidence envelope intake.
3. Cross-project fixture containment (no LENS/NEA leaks into project_nexus or unrelated projects).
4. Substring false positive rejection (e.g., 'near' != 'org:nea').
5. Speaker override elimination (no hardcoded timestamp renames).
6. Adversarial malformed payload resilience.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.transcript_stages_core.graph_post_entity_healer import (
    align_canonical_signals,
    heal_and_align_entities,
)
from dual_pipeline.transcript_stages_core.graph_post_helpers import (
    _normalize_evidence_index,
)
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    resolve_evidence_pointers,
    resolve_quote_from_pointer,
)
from tools.prepare_evidence_envelope_intake import _validate_locators

SAMPLE_FORENSIC_TRANSCRIPT = (
    "## 01: Forensic Meeting Transcript\n\n"
    "[00:01:10] Alice Smith: We need to ensure the data pipelines scale properly across environments.\n"
    "[00:01:25] Bob Jones: Agreed. The architectural risk is primarily in network latency between cloud regions.\n"
    "[00:01:45] Alice Smith: Let's track that as a high priority risk item for the Q4 roadmap.\n"
)


class QuoteGroundingAdversarialTests(unittest.TestCase):
    """Verifies that hallucinated or altered quotes fail closed and are flagged."""

    def test_verbatim_grounded_pointer_succeeds(self):
        start = SAMPLE_FORENSIC_TRANSCRIPT.find("The architectural risk is primarily in network latency")
        end = start + len("The architectural risk is primarily in network latency")
        projection = {
            "evidence_index": {
                "ev-01": {
                    "quote_pointer": [start, end],
                }
            }
        }
        res = resolve_evidence_pointers(projection, SAMPLE_FORENSIC_TRANSCRIPT)
        ev = res["evidence_index"]["ev-01"]
        self.assertEqual(ev["grounding_status"], "GROUNDED")
        self.assertEqual(ev["exact_text"], "The architectural risk is primarily in network latency")

    def test_ungrounded_hallucinated_quote_flagged_and_stripped(self):
        projection = {
            "evidence_index": {
                "ev-fake": {
                    "text": "This text was completely made up by an LLM and does not exist in the source.",
                    "exact_text": "This text was completely made up by an LLM and does not exist in the source.",
                }
            }
        }
        res = resolve_evidence_pointers(projection, SAMPLE_FORENSIC_TRANSCRIPT)
        ev = res["evidence_index"]["ev-fake"]
        self.assertEqual(ev["grounding_status"], "UNGROUNDED_HALLUCINATION")
        self.assertIsNone(ev["quote_pointer"])
        self.assertNotIn("exact_text", ev)

    def test_out_of_bounds_pointer_fails_closed(self):
        projection = {
            "evidence_index": {
                "ev-bad": {
                    "quote_pointer": [10, 999999],
                }
            }
        }
        with self.assertRaises(ValueError):
            resolve_evidence_pointers(projection, SAMPLE_FORENSIC_TRANSCRIPT)

    def test_negative_or_inverted_pointer_fails_closed(self):
        with self.assertRaises(ValueError):
            resolve_quote_from_pointer(SAMPLE_FORENSIC_TRANSCRIPT, [-5, 10])

        with self.assertRaises(ValueError):
            resolve_quote_from_pointer(SAMPLE_FORENSIC_TRANSCRIPT, [50, 40])


class TrivialLocatorIntakeAdversarialTests(unittest.TestCase):
    """Verifies that non-anchored, trivial section locators are strictly rejected."""

    def test_trivial_section_locators_rejected(self):
        bad_sections = [
            "## Transcript",
            "Transcript",
            "## Transcript Context",
            "Session Context",
            "Transcript Context",
            "   ## FORENSIC TRANSCRIPT   ",
        ]
        with tempfile.TemporaryDirectory() as td:
            source_file = Path(td) / "source.txt"
            source_file.write_text("Valid source text exceeding length", encoding="utf-8")
            for bad_section in bad_sections:
                package = {
                    "speakers": [
                        {
                            "speaker": "Alice",
                            "statements": [
                                {
                                    "text": "Valid statement text",
                                    "locator": {"kind": "section", "value": bad_section},
                                }
                            ],
                        }
                    ],
                    "actions": [],
                }
                with self.assertRaises(ValueError):
                    _validate_locators(package, source_file)


class CrossProjectFixtureContainmentTests(unittest.TestCase):
    """Verifies that project-specific entities and relationships never leak into other projects."""

    def test_nexus_project_does_not_receive_lens_or_nea_injections(self):
        mutations = {
            "entity_upserts": [
                {"id": "system:nexus-platform", "type": "system", "name": "Nexus Platform"},
            ],
            "relationship_signals": [],
            "decision_updates": [],
        }
        ev_index = {"ev-01": {"text": "General context."}}
        fallback_ev = ["ev-01"]
        source_text = "We are near the completion deadline and building a rule builder and registry."

        heal_and_align_entities(
            mutations,
            ev_index=ev_index,
            fallback_ev=fallback_ev,
            source_text=source_text,
            project="project_nexus",
        )
        align_canonical_signals(
            mutations,
            ev_index=ev_index,
            fallback_ev=fallback_ev,
            project="project_nexus",
        )

        injected_ids = {e["id"] for e in mutations.get("entity_upserts", [])}
        self.assertNotIn("org:nea", injected_ids)
        self.assertNotIn("system:lens", injected_ids)
        self.assertNotIn("system:lens-rule-builder", injected_ids)
        self.assertNotIn("system:lens-rule-registry", injected_ids)
        self.assertNotIn("metric:lens-acceptance-rate", injected_ids)
        self.assertNotIn("metric:lens-baseline-ratio", injected_ids)

        signal_ids = {s.get("signal_id") for s in mutations.get("relationship_signals", [])}
        self.assertFalse(any("lens" in sid.lower() for sid in signal_ids if sid))

    def test_banned_pseudo_systems_are_stripped_regardless_of_project(self):
        mutations = {
            "entity_upserts": [
                {"id": "system:lens-backend", "type": "system"},
                {"id": "system:review-queue", "type": "system"},
                {"id": "system:amazon-textract", "type": "system"},
                {"id": "system:legitimate-service", "type": "system", "name": "Legitimate Service"},
            ]
        }
        heal_and_align_entities(
            mutations,
            ev_index={},
            fallback_ev=["ev-01"],
            source_text="Clean transcript",
            project="project_lens",
        )
        remaining = [e["id"] for e in mutations["entity_upserts"]]
        self.assertNotIn("system:lens-backend", remaining)
        self.assertNotIn("system:review-queue", remaining)
        self.assertNotIn("system:amazon-textract", remaining)
        self.assertIn("system:legitimate-service", remaining)


class SpeakerOverrideEliminationTests(unittest.TestCase):
    """Verifies that hardcoded meeting timestamp and phrase speaker overrides are eliminated."""

    def test_phrases_do_not_forcibly_override_speakers(self):
        data = {
            "evidence_index": {
                "ev-01": {
                    "speaker": "Unknown Participant",
                    "text": "Before we take all the training wheels off, we must ensure validations pass and go into maximo.",
                    "locator": "line 45",
                },
                "ev-02": {
                    "speaker": "Unknown Participant",
                    "text": "The review queue was mentioned by Jennifer during the status update.",
                    "locator": "line 80",
                },
            }
        }
        _normalize_evidence_index(data, source_text=None)
        self.assertNotEqual(data["evidence_index"]["ev-01"]["speaker"], "Parker Vance")
        self.assertNotEqual(data["evidence_index"]["ev-02"]["speaker"], "Jennifer Pan")
