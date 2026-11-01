"""Adversarial and edge-case test suite for Domain Intelligence Agent.

Covers boundary payloads, malformed inputs, invariant bypass attempts,
concurrency race conditions, and unicode/emoji resilience.
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.domain import classify_domain_envelope
from tools.pr_governance_core.domain_cache import (
    load_cached_envelope,
    store_cached_envelope,
)
from tools.pr_governance_core.domain_models import (
    DomainClassificationEnvelope,
    ModalityScope,
)


class TestDomainClassifierAdversarial(unittest.TestCase):
    def test_boundary_empty_and_none_payloads(self):
        """Boundary: empty string, None, and huge file list handling without throwing."""
        env1 = classify_domain_envelope("", [], use_cache=False)
        self.assertIsNotNone(env1.domain)

        env2 = classify_domain_envelope("feat/normal-branch", [], use_cache=False)
        self.assertIn(env2.domain, ("Enhancement", "Housekeeping", "—"))

        huge_files = [f"path/to/file_{i}.txt" for i in range(5000)]
        env3 = classify_domain_envelope("feat/huge-batch", huge_files, use_cache=False)
        self.assertEqual(env3.domain, "Enhancement")

    def test_malformed_metadata_json_resilience(self):
        """Malformed: corrupted metadata.json and binary files do not crash the introspector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "output" / "project_nexus" / "10-04-2026" / "Corrupted_Deliverable"
            out_dir.mkdir(parents=True)
            # Write invalid JSON
            (out_dir / "metadata.json").write_text("{ unclosed invalid json syntax: 123 ", encoding="utf-8")
            (out_dir / "03_canonical_signal_projection.json").write_bytes(b"\x00\xff\xfe\xed\xfa\xce")

            changed_files = [
                "output/project_nexus/10-04-2026/Corrupted_Deliverable/metadata.json",
                "output/project_nexus/10-04-2026/Corrupted_Deliverable/03_canonical_signal_projection.json",
            ]
            env = classify_domain_envelope(
                "feat/corrupted-output",
                changed_files,
                repo_root=root,
                use_cache=False,
            )
            self.assertIsNotNone(env.domain)
            # Should safely fall back without unhandled exception
            self.assertIn(env.domain, ("Client Document", "Live Meeting", "Enhancement"))

    def test_invariant_bypass_live_branch_with_written_deliverable(self):
        """Invariant: Deceptive 'live' branch name with DOCUMENT metadata MUST NOT be labeled Live Meeting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_dir = root / "output" / "project_nexus" / "10-04-2026" / "Live_Tricky_Doc"
            out_dir.mkdir(parents=True)
            meta = {
                "project": "project_nexus",
                "title": "Live Market Intelligence Briefing on Solar & Nuclear Trends",
                "source_type": "document",
                "canonical_modality": "DOCUMENT",
                "is_document": True,
            }
            (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

            changed_files = [
                "output/project_nexus/10-04-2026/Live_Tricky_Doc/metadata.json",
            ]
            deceptive_branch = "feat/live-audio-transcription-recorded-call"
            env = classify_domain_envelope(
                deceptive_branch,
                changed_files,
                repo_root=root,
                use_cache=False,
            )
            # The structural introspector detects DOCUMENT modality; Live Meeting is strictly disqualified
            self.assertEqual(env.modality, ModalityScope.DOCUMENT_WRITTEN)
            self.assertNotEqual(env.domain, "Live Meeting")
            self.assertEqual(env.domain, "External Research")

    def test_concurrent_cache_writes_race_safety(self):
        """Concurrency: 10 parallel threads writing and reading the cache simultaneously."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "concurrent_cache.json"

            def _worker(idx: int) -> bool:
                branch = f"feat/branch_{idx % 5}"
                files = [f"file_{idx}.py"]
                env = DomainClassificationEnvelope(
                    domain="External Research",
                    confidence=0.90,
                    rationale=f"Worker {idx}",
                )
                store_cached_envelope(branch, files, env, cache_path=cache_file)
                loaded = load_cached_envelope(branch, files, cache_path=cache_file)
                return loaded is not None and loaded.domain == "External Research"

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(_worker, i) for i in range(25)]
                results = [f.result() for f in futures]

            self.assertTrue(all(results))

    def test_unicode_and_emoji_resilience(self):
        """Unicode: Emoji and non-ASCII characters in branch names and titles."""
        branch = "feat/⚡-energy-trends-🚀-market-intel"
        files = ["documents/market_intelligence/trends.md"]
        env = classify_domain_envelope(branch, files, use_cache=False)
        self.assertIsNotNone(env.domain)
        self.assertEqual(env.domain, "External Research")


if __name__ == "__main__":
    unittest.main()
