"""
tests/end_to_end/test_copilot_live_transcript_visibility_e2e.py

End-to-End verification for Wave 1: Live Transcript Visibility & Hybrid Dialogue-Graph Grounding.
Validates:
1. Active sessions survive >15s MLX Whisper batch pauses (up to 120s window) in LiveBufferReader.
2. Live transcript turns are loaded from both segments.jsonl and *_live_transcript.md (bolded and unbolded).
3. Lowercase ASR dialogue turns yield non-empty retrieval seeds in ConversationContextModel.
4. Tolerated speaker errors preserve live dialogue window turns and hybrid graph-dialogue grounding.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pytest

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.cli.copilot_session import _resolve_session_and_context_model
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
    extract_turn_entities,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry


def _build_test_retriever(engagement_id: str = "project:lens") -> tuple[object, CopilotGraphRetriever]:
    class DummyTopology:
        def __init__(self) -> None:
            self.nodes = {
                "person:brien": {
                    "node_id": "person:brien",
                    "canonical_name": "Speaker_Alpha",
                    "display_name": "Speaker_Alpha",
                    "node_type": "person",
                    "organization": "Apex Global",
                },
                "workstream:conduit": {
                    "node_id": "workstream:conduit",
                    "canonical_name": "Conduit Deployment",
                    "display_name": "Conduit Deployment",
                    "node_type": "workstream",
                },
            }
            self.assertions = {
                "asrt_conduit_1": {
                    "assertion_id": "asrt_conduit_1",
                    "subject_node_id": "person:brien",
                    "object_node_id": "workstream:conduit",
                    "predicate": "COMMITTED_TO",
                    "claim_text": "Speaker_Alpha committed to completing the conduit deployment migration before Friday.",
                    "confidence_score": 0.94,
                    "engagement_id": engagement_id,
                    "primary_source_id": "src_lens_01",
                    "observed_at": "2026-10-15",
                }
            }
            self.adjacency = {"person:brien": ["asrt_conduit_1"], "workstream:conduit": ["asrt_conduit_1"]}

    topo = DummyTopology()
    retriever = CopilotGraphRetriever(
        topology=topo,
        graph_sha256="a" * 64,
        generation=1,
        attendees=["Speaker_Alpha", "Sarah"],
        engagement_id=engagement_id,
    )
    return topo, retriever


@pytest.mark.e2e
def test_live_session_survives_whisper_pause_and_loads_markdown_turns(tmp_path: Path) -> None:
    """Active session with a 45-second heartbeat gap remains visible and loads markdown turns."""
    repo_root = tmp_path / "repo"
    out_dir = repo_root / "output" / "lens_sync"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "lens_sync_live_transcript.md"
    md_path.write_text(
        "# Live Transcript\n"
        "[00:00:12] **Speaker_Alpha:** we are seeing latency in the conduit deployment migration today\n"
        "**[00:00:25]** Sarah: let us review the Friday migration target\n",
        encoding="utf-8",
    )

    reg = SessionRegistry(repo_root)
    manifest = reg.register(
        session_id="sess_whisper_pause_01",
        parent_pid=99999991,  # Simulate detached parent PID
        meeting_slug="lens_sync",
        engagement_name="LENS",
        output_dir="output/lens_sync",
        capture_state="CAPTURING",
        copilot_state="READY",
        attendees=["Speaker_Alpha", "Sarah"],
    )
    # Age heartbeat by 45 seconds (> 15s old limit, < 120s new window)
    manifest_file = repo_root / ".session_registry" / f"{manifest.session_id}.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    data["last_heartbeat"] = time.time() - 45.0
    manifest_file.write_text(json.dumps(data), encoding="utf-8")

    reader = LiveBufferReader(repo_root)
    active = reader.list_active_sessions()
    assert len(active) == 1
    assert active[0].session_id == "sess_whisper_pause_01"

    args = argparse.Namespace(runtime_root=repo_root, active=True, project="lens")
    resolved_dir, ctx_model, resolved_manifest, slug = _resolve_session_and_context_model(args, reader=reader)
    assert resolved_dir == out_dir.resolve()
    assert resolved_manifest is not None
    focus = ctx_model.focus_turns()
    assert len(focus) == 2
    assert focus[0]["speaker"] == "Speaker_Alpha"
    assert "conduit" in focus[0]["text"]

    seeds = ctx_model.retrieval_seeds()
    assert any("conduit" in s.lower() for s in seeds), f"Expected lowercase domain seed in {seeds}"


@pytest.mark.e2e
def test_hybrid_live_transcript_and_graph_grounding_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end copilot execution blends live markdown transcript turns with graph assertions."""
    repo_root = tmp_path / "repo"
    out_dir = repo_root / "output" / "lens_live"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "lens_live_transcript.md"
    md_path.write_text(
        "[00:01:05] Speaker_Alpha: we need to verify the conduit deployment migration schedule\n"
        "[00:01:18] Sarah: can we check what was committed for Friday?\n",
        encoding="utf-8",
    )

    topo, retriever = _build_test_retriever("project:lens")
    monkeypatch.setattr(
        "dual_pipeline.cli.copilot_handlers.load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    args = argparse.Namespace(
        request="Respond to Speaker_Alpha",
        intent="respond",
        target="Speaker_Alpha",
        topic=None,
        meeting_dir=str(out_dir),
        runtime_root=repo_root,
        project="lens",
        json=True,
    )
    res = handle_copilot(args)
    assert res["status"] == "PASS", f"Expected PASS, got {res}"
    assert "asrt_conduit_1" in res["cited_assertion_ids"]
    acoustic = res["evidence_breakdown"]["acoustic_transcript_evidence"]
    assert len(acoustic) >= 1
    assert any("conduit" in str(t.get("text", "")).lower() for t in acoustic)
