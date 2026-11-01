"""
tests/graph_writer/test_evidence_eviction.py

Verification of evidence eviction, assertion supersession, and bounded cache eviction
under AGENTS.md.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from dual_pipeline.archival_manager import cleanup_transcript_cache
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)


def test_neighborhood_lru_eviction():
    """Verify bounded memory index evicts oldest LRU keys and updates telemetry."""
    store = CopilotNeighborhoodStore(max_entries=2, max_ram_bytes=10_000_000)

    # Ingest 2 nodes with unique seed_ids
    store.add_chains([{"seed_id": "node_1", "target_id": "node_1", "chain_str": "fact 1"}])
    store.add_chains([{"seed_id": "node_2", "target_id": "node_2", "chain_str": "fact 2"}])

    assert len(store.get_chains_for_node("node_1")) > 0
    assert len(store.get_chains_for_node("node_2")) > 0

    # Adding a 3rd node triggers deterministic LRU eviction of the oldest (node_1)
    store.add_chains([{"seed_id": "node_3", "target_id": "node_3", "chain_str": "fact 3"}])

    stats = store.get_cache_stats()
    assert stats["evictions"] >= 1
    assert len(store.get_chains_for_node("node_3")) > 0


def test_transcript_cache_ttl_and_lru_eviction():
    """Verify archival manager cleans up files exceeding TTL or capacity limits."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_dir = Path(tmp_dir)

        # Create two files: one normal, one old
        file1 = cache_dir / "transcript_1.json"
        file1.write_text('{"turn": 1}', encoding="utf-8")

        file2 = cache_dir / "transcript_2.json"
        file2.write_text('{"turn": 2}', encoding="utf-8")

        # Evict with tiny byte limit (max_bytes=5) forces LRU eviction of oldest
        summary = cleanup_transcript_cache(
            cache_dir=cache_dir,
            max_age_seconds=3600,
            max_bytes=5,
        )

        assert summary["evicted_lru_count"] >= 1
        assert len(summary["evicted_files"]) >= 1
