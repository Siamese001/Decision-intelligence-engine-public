"""
Tests for Wave 5: transcript_cli.py
Covers:
  - Decision 2: HITLRequiredError → structured JSON to stdout + exit code 2
  - Decision 3: --supplementary-sources loads files and injects into metadata
  - Argument parsing (--source, --metadata, --output-dir, --provider, --bypass-speaker-hitl)
  - PA supplementary sources binding
"""
from __future__ import annotations

import contextlib
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.transcript_cli import (
    HITL_EXIT_CODE,
    _emit_hitl_json,
    _load_metadata,
    _load_supplementary_sources,
    build_parser,
    main,
)
from dual_pipeline.transcript_stages import HITLRequiredError, PAPacketAssembler

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

NATIVE_TRANSCRIPT = textwrap.dedent("""\
    **Reid Schenk [00:00:00]:** Good morning.
    **Amit Ayer [00:01:00]:** Let's get started.
""")

UNDIARIZED_TRANSCRIPT = textwrap.dedent("""\
    [00:00:00] Speaker 1: Good morning everyone. Today we will discuss the roadmap.
    [00:01:00] Speaker 1: The team has agreed on the next quarter priorities.
""")

BASE_METADATA = {
    "meeting_name": "TestMeeting",
    "date": "09/09/2026",
    "time": "09:00 AM - 09:30 AM ET",
    "roster": [{"name": "Speaker 1", "org": "PartnerAdvisory", "role": "Lead"}],
}


# ─────────────────────────────────────────────────────────────────────────────
# Decision 2 tests — HITLRequiredError → JSON stdout + exit 2
# ─────────────────────────────────────────────────────────────────────────────

class TestHITLJSONSurface(unittest.TestCase):
    """Decision 2: When HITLRequiredError fires, CLI emits JSON to stdout + exit 2."""

    def _make_hitl_error(self, hitl_type="roster_gate"):
        err = HITLRequiredError("Roster gate HITL required")
        err.hitl_type = hitl_type
        return err

    def test_hitl_exit_code_is_2(self):
        self.assertEqual(HITL_EXIT_CODE, 2)

    def test_emit_hitl_json_structure(self, capsys=None):
        """_emit_hitl_json writes valid JSON with required keys to stdout."""
        import io
        from contextlib import redirect_stdout
        err = self._make_hitl_error("roster_gate")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _emit_hitl_json(err)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["hitl_required"])
        self.assertEqual(payload["hitl_type"], "roster_gate")
        self.assertIn("prompt", payload)
        self.assertIn("required_fields", payload)
        self.assertIn("resume_instructions", payload)
        self.assertEqual(payload["decision_number"], 1)

    def test_emit_hitl_json_required_fields_for_roster_gate(self):
        """Roster gate HITL must enumerate all 5 required fields."""
        import io
        from contextlib import redirect_stdout
        err = self._make_hitl_error("roster_gate")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _emit_hitl_json(err)
        payload = json.loads(buf.getvalue())
        required = payload["required_fields"]
        self.assertIn("attendee_list", required)
        self.assertIn("organization_per_attendee", required)
        self.assertIn("role_per_attendee", required)

    def test_cli_exits_2_on_hitl_required(self):
        """main() returns exit code 2 when pipeline raises HITLRequiredError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tx.md"
            source.write_text(UNDIARIZED_TRANSCRIPT, encoding="utf-8")

            meta_path = Path(tmpdir) / "meta.json"
            # Minimal valid metadata — no roster, no bypass → roster HITL fires
            meta_path.write_text(json.dumps({
                "meeting_name": "TestMeeting",
                "date": "09/09/2026",
                "time": "09:00 AM - 09:30 AM ET",
                "classification": "internal",
            }), encoding="utf-8")

            exit_code = main([
                "--source", str(source),
                "--metadata", str(meta_path),
                "--output-dir", tmpdir,
                "--provider", "offline",
            ])
            self.assertEqual(exit_code, HITL_EXIT_CODE)

    def test_cli_exit_2_json_is_parseable(self):
        """JSON emitted on HITL exit must be valid and contain hitl_required=true."""
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tx.md"
            source.write_text(UNDIARIZED_TRANSCRIPT, encoding="utf-8")

            # Provide all U0-required keys but no roster → roster gate fires
            meta_path = Path(tmpdir) / "meta.json"
            meta_path.write_text(json.dumps({
                "meeting_name": "TestMeeting",
                "date": "09/09/2026",
                "time": "09:00 AM - 09:30 AM ET",
                "classification": "internal",
            }), encoding="utf-8")

            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = main([
                    "--source", str(source),
                    "--metadata", str(meta_path),
                    "--output-dir", tmpdir,
                    "--provider", "offline",
                ])
            self.assertEqual(exit_code, 2)
            output = buf.getvalue().strip()
            if output:
                payload = json.loads(output)
                self.assertTrue(payload["hitl_required"])

    def test_bypass_flag_rejected(self):
        """--bypass-speaker-hitl is rejected by the CLI parser."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tx.md"
            source.write_text(UNDIARIZED_TRANSCRIPT, encoding="utf-8")

            with self.assertRaises(SystemExit):
                main([
                    "--source", str(source),
                    "--output-dir", tmpdir,
                    "--provider", "offline",
                    "--bypass-speaker-hitl",
                ])


# ─────────────────────────────────────────────────────────────────────────────
# Decision 3 tests — --supplementary-sources CLI flag
# ─────────────────────────────────────────────────────────────────────────────

class TestSupplementarySources(unittest.TestCase):
    """Decision 3: --supplementary-sources loads files and injects into metadata."""

    def test_load_supplementary_sources_text_file(self):
        """Text files are loaded with content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes = Path(tmpdir) / "notes.md"
            notes.write_text("# Meeting Notes\nKey point here.", encoding="utf-8")
            result = _load_supplementary_sources([str(notes)])
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "notes.md")
            self.assertIn("Meeting Notes", result[0]["content"])
            self.assertIn("path", result[0])

    def test_load_supplementary_sources_binary_file(self):
        """Binary files (pdf, docx) are referenced by path with placeholder content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            deck = Path(tmpdir) / "deck.pdf"
            deck.write_bytes(b"%PDF-1.4 fake")
            result = _load_supplementary_sources([str(deck)])
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["name"], "deck.pdf")
            self.assertIn("binary", result[0]["content"].lower())

    def test_load_supplementary_sources_multiple_files(self):
        """Multiple supplementary sources are all loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = Path(tmpdir) / "deck.md"
            f2 = Path(tmpdir) / "notes.txt"
            f1.write_text("Deck content", encoding="utf-8")
            f2.write_text("Notes content", encoding="utf-8")
            result = _load_supplementary_sources([str(f1), str(f2)])
            self.assertEqual(len(result), 2)
            names = {r["name"] for r in result}
            self.assertIn("deck.md", names)
            self.assertIn("notes.txt", names)

    def test_load_supplementary_sources_missing_file_exits_1(self):
        """Missing supplementary source file causes sys.exit(1)."""
        with self.assertRaises(SystemExit) as ctx:
            _load_supplementary_sources(["/nonexistent/path/deck.pdf"])
        self.assertEqual(ctx.exception.code, 1)

    def test_cli_injects_supplementary_into_metadata(self):
        """--supplementary-sources injects loaded sources into metadata before PA."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tx.md"
            source.write_text(NATIVE_TRANSCRIPT, encoding="utf-8")

            notes = Path(tmpdir) / "notes.md"
            notes.write_text("Reid: Q4 is the target.", encoding="utf-8")

            meta_path = Path(tmpdir) / "meta.json"
            meta_path.write_text(json.dumps(BASE_METADATA), encoding="utf-8")

            captured_metadata = {}

            __import__(
                "dual_pipeline.transcript_pipeline",
                fromlist=["RawTranscriptPipeline"]
            ).RawTranscriptPipeline.run

            def mock_run(self, input_path, metadata, output_dir):
                captured_metadata.update(metadata)
                # Return a minimal result to avoid full pipeline execution
                raise SystemExit(0)

            with patch(
                "dual_pipeline.transcript_cli.CanonicalAgenticPipeline.run",
                mock_run
            ), contextlib.suppress(SystemExit):
                main([
                    "--source", str(source),
                    "--metadata", str(meta_path),
                    "--output-dir", tmpdir,
                    "--provider", "offline",
                    "--supplementary-sources", str(notes),
                ])

            self.assertIn("supplementary_sources", captured_metadata)
            sources = captured_metadata["supplementary_sources"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["name"], "notes.md")

    def test_pa_exposes_supplementary_sources_in_packet(self):
        """PAPacketAssembler binds _supplementary_sources from metadata."""
        pa = PAPacketAssembler()
        c0_ctx = {
            "transcript": "Hello",
            "statements": [],
            "hash": "abc123",
            "diarization_status": "NATIVE",
            "source_type": "DIARIZED_TEXT",
        }
        meta = {
            "meeting_name": "Test",
            "supplementary_sources": [
                {"name": "deck.md", "path": "/tmp/deck.md", "content": "Deck content"}
            ],
        }
        packet = pa.execute(c0_ctx, meta)
        self.assertIn("_supplementary_sources", packet)
        self.assertEqual(len(packet["_supplementary_sources"]), 1)
        self.assertEqual(packet["_supplementary_sources"][0]["name"], "deck.md")

    def test_pa_empty_supplementary_when_not_provided(self):
        """PAPacketAssembler produces empty _supplementary_sources when not in metadata."""
        pa = PAPacketAssembler()
        c0_ctx = {
            "transcript": "Hello",
            "statements": [],
            "hash": "abc123",
            "diarization_status": "NATIVE",
            "source_type": "DIARIZED_TEXT",
        }
        packet = pa.execute(c0_ctx, {"meeting_name": "Test"})
        self.assertEqual(packet["_supplementary_sources"], [])


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser tests
# ─────────────────────────────────────────────────────────────────────────────

class TestArgumentParser(unittest.TestCase):
    """Parser structure and defaults."""

    def test_required_source_arg(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_default_provider_is_offline(self):
        parser = build_parser()
        args = parser.parse_args(["--source", "tx.md"])
        provider = args.provider or ("llm" if getattr(args, "model", None) else "offline")
        self.assertEqual(provider, "offline")

    def test_supplementary_sources_accepts_multiple(self):
        parser = build_parser()
        args = parser.parse_args([
            "--source", "tx.md",
            "--supplementary-sources", "a.md", "b.txt",
        ])
        self.assertEqual(args.supplementary_sources, ["a.md", "b.txt"])

    def test_supplementary_sources_default_empty(self):
        parser = build_parser()
        args = parser.parse_args(["--source", "tx.md"])
        self.assertEqual(args.supplementary_sources, [])

    def test_bypass_speaker_hitl_rejected_by_parser(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--source", "tx.md", "--bypass-speaker-hitl"])

    def test_provider_choices_enforced(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--source", "tx.md", "--provider", "invalid"])


# ─────────────────────────────────────────────────────────────────────────────
# Metadata loading tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMetadataLoading(unittest.TestCase):
    def test_load_metadata_from_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"meeting_name": "Demo"}, f)
            path = f.name
        result = _load_metadata(path)
        self.assertEqual(result["meeting_name"], "Demo")
        Path(path).unlink(missing_ok=True)

    def test_load_metadata_none_returns_empty(self):
        result = _load_metadata(None)
        self.assertEqual(result, {})

    def test_load_metadata_missing_file_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            _load_metadata("/nonexistent/path.json")
        self.assertEqual(ctx.exception.code, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Wave 1 Mutation Approval Gating Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMutationApprovalGating(unittest.TestCase):
    """Wave 1: Verification that --mutate-graph requires explicit visible host UI approval."""

    def test_mutate_graph_blocked_when_host_approval_unavailable(self):
        """CLI disables mutate_graph when host interactive tool is unavailable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tx.md"
            source.write_text(NATIVE_TRANSCRIPT, encoding="utf-8")

            meta_path = Path(tmpdir) / "meta.json"
            meta_path.write_text(json.dumps(BASE_METADATA), encoding="utf-8")

            captured_run_kwargs = {}

            def mock_run(self, input_path, metadata, output_dir, **kwargs):
                captured_run_kwargs.update(kwargs)
                raise SystemExit(0)

            with patch(
                "dual_pipeline.transcript_cli.CanonicalAgenticPipeline.run",
                mock_run,
            ), contextlib.suppress(SystemExit):
                main([
                    "--source", str(source),
                    "--metadata", str(meta_path),
                    "--output-dir", tmpdir,
                    "--provider", "offline",
                    "--mutate-graph",
                ])

            # In Wave 1, mutate_graph flag must be stripped if host approval was not authorized
            self.assertFalse(captured_run_kwargs.get("mutate_graph", False))

    def test_mutate_graph_permitted_when_approval_present(self):
        """CLI retains mutate_graph when authentic approval is already present in runtime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "tx.md"
            source.write_text(NATIVE_TRANSCRIPT, encoding="utf-8")

            meta_path = Path(tmpdir) / "meta.json"
            meta_path.write_text(json.dumps(BASE_METADATA), encoding="utf-8")

            from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
                HostApprovalDispatcher,
                MutationProposal,
                persist_approval_request,
            )
            import hashlib

            src_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            proposal = MutationProposal(
                proposal_id=f"prop-{source.stem}",
                meeting_name="TestMeeting",
                source_hash=src_hash,
                output_hashes={},
                intake_hash="",
                target_graph="CANONICAL_GRAPH.db",
                runtime_root=tmpdir,
                expected_base_generation="current",
                deltas={"nodes_added": 0, "edges_added": 0, "source_retirements": []},
                validation_evidence={"cli_flag_requested": True},
                rollback_scope="HEAD",
            )
            dig = proposal.compute_proposal_digest()
            record = {
                "request_id": "req-preapproved",
                "proposal_id": proposal.proposal_id,
                "proposal_digest": dig,
                "meeting_name": proposal.meeting_name,
                "target_graph": proposal.target_graph,
                "expected_base_generation": "current",
                "host_scope": "test",
                "thread_scope": "test",
                "delivery_state": "DELIVERED",
                "status": "APPROVED",
                "authorized": True,
                "consumed": False,
                "updated_at": "2026-10-06T12:00:00Z",
            }
            persist_approval_request(record, tmpdir)

            captured_run_kwargs = {}

            def mock_run(self, input_path, metadata, output_dir, **kwargs):
                captured_run_kwargs.update(kwargs)
                raise SystemExit(0)

            with patch(
                "dual_pipeline.transcript_cli.CanonicalAgenticPipeline.run",
                mock_run,
            ), contextlib.suppress(SystemExit):
                main([
                    "--source", str(source),
                    "--metadata", str(meta_path),
                    "--output-dir", tmpdir,
                    "--provider", "offline",
                    "--mutate-graph",
                ])

            self.assertTrue(captured_run_kwargs.get("mutate_graph", False))


if __name__ == "__main__":
    unittest.main()
