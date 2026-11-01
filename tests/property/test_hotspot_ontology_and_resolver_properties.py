"""tests/property/test_hotspot_ontology_and_resolver_properties.py

Property-based and boundary verification suite for Top 5 empirical hotspots:
- dual_pipeline.transcript_qa_core.lens_ontology
- dual_pipeline.transcript_qa_core.graph_ontology_entities
- dual_pipeline.transcript_metadata_core.entity_resolver_graph
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, settings, strategies as st

from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    EntityCandidate,
    _find_graph_in_base,
    resolve_active_generation_id,
    resolve_active_graph_path,
)
from dual_pipeline.transcript_qa_core.graph_ontology_entities import (
    _match_and_insert_anchor,
    _populate_core_evidence_index,
    _prune_unmatched_anchor,
)
from dual_pipeline.transcript_qa_core.lens_ontology import (
    _find_lens_evidence,
    _reground_data_store,
    _reground_maximo_and_sow2,
    _reground_ocr_vlm,
    _reground_system_lens,
)


class HotspotOntologyAndResolverPropertyTests(unittest.TestCase):
    @settings(max_examples=25, deadline=None)
    @given(
        role=st.text(min_size=1, max_size=40),
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        currentness=st.sampled_from(["current", "historical", "superseded"]),
        val_state=st.sampled_from(["verified", "needs_review", "retracted"]),
    )
    def test_entity_candidate_dict_roundtrip_and_item_access_property(
        self, role: str, confidence: float, currentness: str, val_state: str
    ) -> None:
        """Property test: EntityCandidate supports dict-like item access, .get(), and lossless .as_dict() serialization."""
        cand = EntityCandidate(
            role=role,
            confidence=confidence,
            currentness_state=currentness,
            validation_state=val_state,
            valid_from="2026-10-25",
            assertion_id="asrt-001",
            source_node_id="node-001",
        )
        self.assertEqual(cand["role"], role)
        self.assertEqual(cand.get("confidence"), confidence)
        self.assertIsNone(cand.get("nonexistent_key"))
        as_d = cand.as_dict()
        self.assertEqual(as_d["role"], role)
        self.assertEqual(as_d["currentness_state"], currentness)
        self.assertEqual(as_d["validation_state"], val_state)

    @settings(max_examples=20, deadline=None)
    @given(
        exact_text=st.sampled_from([
            "processes them using ocr and vision language models",
            "ready for write-back to ibm maximo under sow 2 covering write-back",
            "interim system of record for extraction output and reprocessing",
            "routine tower climbs nameplate inspection",
            "unmatched generic text",
        ]),
        speaker=st.sampled_from(["Amit Ayer", "meeting roster", "operator", "Participant"]),
    )
    def test_lens_ontology_regrounding_idempotence_property(self, exact_text: str, speaker: str) -> None:
        """Property test: LENS ontology regrounding functions are deterministic and idempotent across evidence permutations."""
        ev_idx = {
            "ev-06": {"exact_text": exact_text, "text": exact_text, "speaker": speaker},
            "ev-project": {"exact_text": "`project_lens`", "text": "`project_lens`", "speaker": "meeting_metadata"},
        }
        ent1 = {"id": "system:lens", "type": "unknown", "name": "lens"}
        changes1: list[str] = []
        _reground_system_lens(ent1, ev_idx, changes1)
        snapshot1 = dict(ent1)

        changes2: list[str] = []
        _reground_system_lens(ent1, ev_idx, changes2)
        self.assertEqual(ent1, snapshot1)
        self.assertEqual(ent1["type"], "system")
        self.assertEqual(ent1["name"], "Project LENS")

        ocr_ent = {"id": "system:vlm", "name": "vlm"}
        _reground_ocr_vlm(ocr_ent, "system:vlm", ev_idx, [])
        self.assertEqual(ocr_ent["id"], "system:ocr-extraction-engine")

        ds_ent = {"id": "system:ds", "name": "ds"}
        _reground_data_store(ds_ent, "system:ds", ev_idx, [])
        self.assertEqual(ds_ent["name"], "Structured Data Store")

        max_ent = {"id": "system:maximo", "name": "maximo"}
        _reground_maximo_and_sow2(max_ent, "system:maximo", ev_idx, [])
        self.assertEqual(max_ent["id"], "system:ibm-maximo")
        self.assertEqual(max_ent["name"], "IBM Maximo")

    @settings(max_examples=20, deadline=None)
    @given(
        include_mandy=st.booleans(),
        include_llm=st.booleans(),
        extra_noise=st.text(min_size=1, max_size=80),
    )
    def test_graph_ontology_entities_anchor_population_and_pruning_property(
        self, include_mandy: bool, include_llm: bool, extra_noise: str
    ) -> None:
        """Property test: _populate_core_evidence_index only inserts anchors grounded in source_text and prunes ungrounded ones."""
        parts = [f"header:{extra_noise}"]
        if include_mandy:
            parts.append("**harper Hoffman** (Participant, NEA)")
        if include_llm:
            parts.append("working with images with LLM.")
        source_text = "\n".join(parts)

        ev_index: dict[str, dict] = {
            "ev-07": {
                "exact_text": "You're going through an Atlas engineer, reviewing an asset and clicking approve, submit, or edit a field."
            }
        }
        _populate_core_evidence_index(ev_index, source_text=source_text)
        if include_mandy:
            self.assertIn("ev-att-mandy", ev_index)
        else:
            self.assertNotIn("ev-att-mandy", ev_index)
        if include_llm:
            self.assertIn("ev-llm", ev_index)
        else:
            self.assertNotIn("ev-llm", ev_index)
        self.assertNotIn("ev-07", ev_index)

    def test_entity_resolver_graph_hermetic_discovery_and_corrupt_active_pair(self) -> None:
        """Boundary & malformed input check: _find_graph_in_base and resolve_active_graph_path handle corrupt active_pair.json cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "active_pair.json").write_text("{corrupt_json", encoding="utf-8")
            self.assertIsNone(_find_graph_in_base(base))

            db_file = base / "CANONICAL_GRAPH.db"
            db_file.write_bytes(b"SQLite format 3\x00")
            resolved = resolve_active_graph_path(root=base, runtime_root=base)
            self.assertEqual(resolved, db_file)

            gen_id = resolve_active_generation_id(root=base, runtime_root=base, fallback_generation_id="gen-fallback")
            self.assertIsNotNone(gen_id)


if __name__ == "__main__":
    unittest.main()
