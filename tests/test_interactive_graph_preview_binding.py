"""Dry-run preview must read canonical nested mutations and the active baseline."""

from __future__ import annotations

import hashlib
import json

import pytest

from dual_pipeline.graph_preview_core.preview import (
    generate_graph_delta_preview,
    resolve_preview_graph_path,
)


def test_nested_document_projection_produces_nonzero_preview() -> None:
    projection = {
        "meeting_id": "document-preview",
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:source", "type": "system", "name": "Source"},
                {"id": "system:target", "type": "system", "name": "Target"},
            ],
            "relationship_signals": [
                {"signal_id": "rel-1", "from_id": "system:source", "to_id": "system:target", "relationship": "DELIVERS"},
            ],
            "strategic_intent_signals": [
                {"signal_id": "strategic-1", "text": "Compare delivery timing.", "evidence_ids": ["ev-1"]},
            ],
        },
    }
    preview, _ = generate_graph_delta_preview(projection)
    assert preview["counts"]["nodes_added"] == 2
    assert preview["counts"]["relationships_new"] == 1
    assert preview["counts"]["assertions_new"] == 1
    assert preview["assertion_diff"]["new_assertions"][0]["id"] == "strategic-1"


def test_implicit_preview_uses_digest_checked_active_graph(tmp_path) -> None:
    runtime = tmp_path / ".local" / "dual_pipeline"
    graph = runtime / "generations" / "generation-1" / "graph.sqlite"
    graph.parent.mkdir(parents=True)
    graph.write_bytes(b"read-only graph fixture")
    pair = {
        "graph_path": "generations/generation-1/graph.sqlite",
        "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
    }
    (runtime / "active_pair.json").write_text(json.dumps(pair))

    assert resolve_preview_graph_path(tmp_path, {}) == graph
    graph.write_bytes(b"changed graph fixture")
    with pytest.raises(ValueError, match="digest mismatch"):
        resolve_preview_graph_path(tmp_path, {})
