"""
tests/property/test_card_archival_adversarial.py

Adversarial and Property-Based Verification for Wave 3 Card Archival & Sidecar Atomicity:
1. Boundary Payloads: 0-byte cards, non-existent targets, empty active_pair, empty directories.
2. Malformed Inputs: Unclosed YAML frontmatter, truncated JSON, missing required metadata in sidecars.
3. Invariant Bypass Attempts: Stale card detection, tampered SHA-256 digests, unverified sidecar binding.
4. Concurrency & Race: Multi-threaded archive operations under concurrent execution.
5. Hypothesis Property Invariants: Arbitrary generation IDs, graph digests, and card content roundtrip.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import hypothesis.strategies as st
from hypothesis import given, settings

from dual_pipeline.card_archival import (
    archive_superseded_card,
    parse_card_frontmatter,
    verify_card_freshness,
)
from dual_pipeline.core import sha256_bytes
from dual_pipeline.index import score_prefiltered_candidates, verify_sidecar_binding


class TestCardArchivalAdversarial(unittest.TestCase):
    """Adversarial and property-based test suite for card archival and sidecar lockstep."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee_w3_adv_")
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- Category 1: Boundary Payloads ---

    def test_boundary_nonexistent_and_empty_card(self) -> None:
        """Verifies archival handling of non-existent files and zero-byte cards."""
        non_existent = self.root / "non_existent.md"
        result = archive_superseded_card(
            non_existent,
            new_generation_id="gen_01",
            new_graph_sha256="sha_01",
        )
        self.assertIsNone(result, "Archiving non-existent file must safely return None")

        # Zero-byte card
        empty_card = self.root / "empty.md"
        empty_card.write_text("", encoding="utf-8")
        meta = parse_card_frontmatter("")
        self.assertEqual(meta, {}, "Empty string frontmatter must return empty dict")

        archived = archive_superseded_card(
            empty_card,
            new_generation_id="gen_01",
            new_graph_sha256="sha_01",
        )
        self.assertIsNotNone(archived)
        self.assertTrue(archived.exists())
        self.assertIn("[SUPERSEDED]", archived.read_text(encoding="utf-8"))

    def test_boundary_empty_cards_directory_freshness(self) -> None:
        """Verifies freshness check when cards directory is empty or contains non-card files."""
        empty_dir = self.root / "empty_cards"
        empty_dir.mkdir()
        (empty_dir / "README.md").write_text("# Readme only", encoding="utf-8")

        res = verify_card_freshness(empty_dir, {"generation_id": "gen_01", "graph_sha256": "sha_01"})
        self.assertEqual(res["total_cards_scanned"], 0)
        self.assertFalse(res["is_fresh"], "Empty directory cannot be marked fresh")
        self.assertEqual(res["status"], "PASS")

    # --- Category 2: Malformed Inputs ---

    def test_malformed_unclosed_frontmatter(self) -> None:
        """Verifies that unclosed or malformed YAML frontmatter parses safely without raising."""
        malformed = "---\ngeneration_id: gen_broken\ngraph_sha256: sha_broken\nno_closing_delimiter"
        meta = parse_card_frontmatter(malformed)
        self.assertEqual(meta.get("generation_id"), "gen_broken")
        self.assertEqual(meta.get("graph_sha256"), "sha_broken")

        # Text with no frontmatter at all
        meta_plain = parse_card_frontmatter("# Just a title\nNo frontmatter")
        self.assertEqual(meta_plain, {})

    def test_malformed_sidecar_missing_metadata(self) -> None:
        """Verifies fail-closed behavior when sidecar database lacks binding metadata."""
        sidecar_path = self.root / "broken_sidecar.sqlite"
        conn = sqlite3.connect(sidecar_path)
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        # Empty metadata table
        conn.commit()
        conn.close()

        dummy_graph = self.root / "dummy_graph.sqlite"
        dummy_graph.write_text("test graph", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            verify_sidecar_binding(sidecar_path, dummy_graph)
        self.assertIn("SIDECAR_GRAPH_HASH_MISMATCH", str(ctx.exception))

    # --- Category 3: Invariant Bypass Attempts ---

    def test_invariant_stale_card_detection(self) -> None:
        """Verifies that stale cards with mismatched generation_id or graph_sha256 fail closed."""
        cards_dir = self.root / "stale_test_cards"
        cards_dir.mkdir()
        card_content = (
            "---\n"
            "card_id: NEE_TEST\n"
            "generation_id: gen_old\n"
            "graph_sha256: sha_old\n"
            "---\n"
            "# Card Body\n"
        )
        (cards_dir / "NEE_TEST.md").write_text(card_content, encoding="utf-8")

        active_pair = {
            "generation_id": "gen_new",
            "graph_sha256": "sha_new",
        }
        res = verify_card_freshness(cards_dir, active_pair)
        self.assertEqual(res["status"], "DRIFT_DETECTED")
        self.assertFalse(res["is_fresh"])
        self.assertEqual(res["drift_count"], 1)
        self.assertEqual(res["stale_cards"][0]["card_id"], "NEE_TEST")

    def test_invariant_sidecar_rejects_missing_or_tampered_graph(self) -> None:
        """Verifies that missing files or mismatched SHA-256 bindings fail closed immediately."""
        sidecar_path = self.root / "valid_sidecar.sqlite"
        dummy_graph = self.root / "valid_graph.sqlite"
        dummy_graph.write_text("canonical graph content", encoding="utf-8")
        graph_sha = sha256_bytes(b"canonical graph content")

        conn = sqlite3.connect(sidecar_path)
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO metadata (key, value) VALUES ('bound_graph_sha256', ?), ('generation_id', 'gen_01')",
            (graph_sha,),
        )
        conn.execute("CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INT, vector BLOB)")
        conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, artifact_json TEXT)")
        conn.commit()
        conn.close()

        # Valid verification passes
        valid_res = verify_sidecar_binding(sidecar_path, dummy_graph)
        self.assertEqual(valid_res["status"], "PASS")

        # Missing graph file
        with self.assertRaises(FileNotFoundError):
            verify_sidecar_binding(sidecar_path, self.root / "missing_graph.sqlite")

        # Missing sidecar file
        with self.assertRaises(FileNotFoundError):
            verify_sidecar_binding(self.root / "missing_sidecar.sqlite", dummy_graph)

        # Tampered graph file
        dummy_graph.write_text("tampered content", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            verify_sidecar_binding(sidecar_path, dummy_graph)
        self.assertIn("SIDECAR_GRAPH_HASH_MISMATCH", str(ctx.exception))

    # --- Category 4: Concurrency & Race ---

    def test_concurrent_card_archival_race_safety(self) -> None:
        """Verifies race safety when multiple worker threads attempt card archival simultaneously."""
        card_file = self.root / "concurrent_card.md"
        card_content = (
            "---\n"
            "card_id: NEE_CONCURRENT\n"
            "generation_id: gen_initial\n"
            "graph_sha256: sha_initial\n"
            "---\n"
            "# Content\n"
        )
        card_file.write_text(card_content, encoding="utf-8")

        def _archive_task(task_id: int) -> Path | None:
            return archive_superseded_card(
                card_file,
                new_generation_id=f"gen_concurrent_{task_id}",
                new_graph_sha256=f"sha_concurrent_{task_id}",
                new_content=f"# New Content {task_id}\n",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_archive_task, i) for i in range(16)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # At least one succeeded in archiving
        archived_results = [r for r in results if r is not None]
        self.assertGreaterEqual(len(archived_results), 1)

        # Archive directory exists and has archived file
        archive_dir = self.root / "archive" / "gen_initial"
        self.assertTrue(archive_dir.exists())
        self.assertTrue((archive_dir / "concurrent_card.md").exists())
        self.assertTrue((archive_dir / "concurrent_card_diff_receipt.json").exists())

    # --- Category 5: Hypothesis Property-Based Invariant Verification ---

    @settings(max_examples=50, deadline=None)
    @given(
        card_id=st.text(min_size=1, max_size=32, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Pc"))),
        gen_id=st.text(min_size=1, max_size=32, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Pc"))),
        sha_val=st.text(min_size=16, max_size=64, alphabet="0123456789abcdef"),
        extra_content=st.text(min_size=0, max_size=200),
    )
    def test_property_frontmatter_parsing_invariance(
        self,
        card_id: str,
        gen_id: str,
        sha_val: str,
        extra_content: str,
    ) -> None:
        """Hypothesis fuzzing proving frontmatter parsing preserves arbitrary IDs and digests."""
        frontmatter = (
            f"---\n"
            f"card_id: {card_id}\n"
            f"generation_id: {gen_id}\n"
            f"graph_sha256: {sha_val}\n"
            f"---\n"
            f"{extra_content}\n"
        )
        meta = parse_card_frontmatter(frontmatter)
        self.assertEqual(meta.get("card_id"), card_id)
        self.assertEqual(meta.get("generation_id"), gen_id)
        self.assertEqual(meta.get("graph_sha256"), sha_val)


if __name__ == "__main__":
    unittest.main()
