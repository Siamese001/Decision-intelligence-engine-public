import tempfile
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionManifest
from dual_pipeline.query_core.firewall import ProhibitedCommandGuard


def test_firewall_blocks_reader_binaries_on_live_transcript():
    # Should block cat on live transcript
    cmd = "cat .worktrees/live_123/output/my_live_transcript.md"
    assert ProhibitedCommandGuard.is_prohibited(cmd)

    cmd2 = "grep -n 'foo' .worktrees/live_123/output/my_live_transcript.md"
    assert ProhibitedCommandGuard.is_prohibited(cmd2)

    cmd3 = "tail -f something_live_transcript.md"
    assert ProhibitedCommandGuard.is_prohibited(cmd3)

    # Should not block cat on random file
    cmd4 = "cat some_other_file.txt"
    assert not ProhibitedCommandGuard.is_prohibited(cmd4)

def test_live_buffer_reader():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "test_live_transcript.md"
        f.write_text("line 1\nline 2\n")

        reader = LiveBufferReader(Path(tmp))
        # Just verifying it instantiates properly
        assert reader.repo_root == Path(tmp).resolve()

def test_session_manifest_fields():
    manifest = SessionManifest(
        session_id="id",
        engine_signature="sig",
        parent_pid=123,
        parent_start_time=123.4,
        repo_root="root",
        graph_sha256="abc",
        generation=1
    )
    assert manifest.graph_sha256 == "abc"
    assert manifest.generation == 1
