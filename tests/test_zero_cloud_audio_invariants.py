"""Tests verifying zero cloud dispatch for live audio recording and MLX Whisper transcription."""

import pytest

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    _resolve_effective_backend,
)


def test_cli_parser_bans_cloud_transcription_backend():
    """Verify CLI parser disallows openai / cloud backends for recording."""
    parser = build_parser()
    record_parser = None
    for action in parser._actions:
        if action.dest == "command" and hasattr(action, "choices"):
            record_parser = action.choices.get("record-meeting")
            break

    assert record_parser is not None, "record-meeting subcommand must exist in parser"

    backend_action = None
    for act in record_parser._actions:
        if act.dest == "backend":
            backend_action = act
            break

    assert backend_action is not None, "--backend flag must exist"
    assert "openai" not in backend_action.choices, "openai backend must not be an option for live recording"
    assert set(backend_action.choices) == {"auto", "mlx"}, "Only auto and mlx local backends are permitted"


def test_chunk_transcriber_blocks_cloud_transcription():
    """Verify ChunkTranscriber rejects cloud transcription with an explicit error."""
    with pytest.raises(ValueError, match="OpenAI cloud audio transcription for raw chunks is strictly prohibited"):
        _resolve_effective_backend("openai")

    with pytest.raises(ValueError, match="OpenAI cloud audio transcription for raw chunks is strictly prohibited"):
        _resolve_effective_backend("cloud")


def test_agent_routing_route0_declares_zero_cloud_dispatch():
    """Verify .agents/rules/agent-routing.md explicitly mandates zero cloud dispatch for Route 0."""
    from pathlib import Path
    rules_path = Path(__file__).resolve().parent.parent / ".agents" / "rules" / "agent-routing.md"
    assert rules_path.exists(), "agent-routing.md must exist"

    content = rules_path.read_text(encoding="utf-8")
    assert "ZERO CLOUD DISPATCH" in content
    assert "MLX Whisper" in content
