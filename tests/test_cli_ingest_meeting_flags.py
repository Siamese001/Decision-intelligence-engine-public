"""Unit tests for ingest-meeting CLI parser and flag-based metadata assembly."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"
import pytest

from dual_pipeline.cli.parser import build_parser
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


def test_cli_parser_ingest_meeting_flags_without_metadata() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "ingest-meeting",
            "--input",
            "samples/sample_meeting.vtt",
            "--project",
            "nea",
            "--meeting",
            "NEA_CI_Prep_Kannan_Debrief",
            "--date",
            "09/14/2026",
            "--time",
            "03:00 PM - 03:30 PM ET",
            "--classification",
            "confidential",
            "--platform",
            "Microsoft Teams CoPilot",
            "--attendees",
            "Drew Oakley, Amit Ayer",
            "--speaker-map",
            '{"Speaker 1": "Drew Oakley"}',
        ]
    )

    assert str(args.input) == "samples/sample_meeting.vtt"
    assert args.metadata is None
    assert args.project == "nea"
    assert args.meeting == "NEA_CI_Prep_Kannan_Debrief"
    assert args.date == "09/14/2026"
    assert args.time == "03:00 PM - 03:30 PM ET"
    assert args.classification == "confidential"
    assert args.platform == "Microsoft Teams CoPilot"
    assert args.attendees == "Drew Oakley, Amit Ayer"
    assert args.speaker_map == '{"Speaker 1": "Drew Oakley"}'


def test_scaffold_meeting_metadata_from_flags() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        dummy_input = Path(tmp_dir) / "01_kannan_interview_10-02-2026.txt"
        dummy_input.write_text("dummy content", encoding="utf-8")

        meta = scaffold_meeting_metadata(
            input_path=dummy_input,
            metadata_path=None,
            project="nea",
            meeting_name="Kannan_Screening",
            date_str="10-02-2026",
            attendees="Drew Oakley, Amit Ayer",
            time_str="03:00 PM - 03:30 PM ET",
            classification="confidential",
        )

        assert meta["meeting_name"] == "Kannan_Screening"
        assert meta["date"] == "10/02/2026"
        assert meta["date_filename"] == "10-02-2026"
        assert meta["time"].startswith("03:00 PM - 03:30 PM ET")
        assert meta["time_filename"] == "0300PM_0330PM_ET"
        assert meta["project"] == "nea"
        assert meta["classification"] == "confidential"
        att = meta["attendees"]
        if isinstance(att, dict):
            att_names = [name for grp in att.values() for name in grp]
        else:
            att_names = list(att)
        assert any("Drew Oakley" in a for a in att_names)
        assert any("Amit Ayer" in a for a in att_names)


def test_cli_ingest_meeting_minimal_input_only() -> None:
    """Verify CLI ingest-meeting executes with only --input and zero other flags."""
    from dual_pipeline.cli.handlers import _ingest_meeting

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        raw_file = tmp_path / "LENS_Turbine_Image_Review_10-02-2026.txt"
        raw_file.write_text(
            "# LENS Turbine Image Review 09/14/2026\n\n"
            "**harper Hoffman:** [02:00:00] We have 193 turbine photos ready for testing.\n"
            "**Alex Sanchez:** [02:01:00] We will apply the extraction rules and benchmark accuracy.\n"
            "**harper Hoffman:** [02:02:00] I have verified the image quality and metadata bindings.\n"
            "**Alex Sanchez:** [02:03:00] Excellent, the extraction pipeline will run against these today.\n"
            "**harper Hoffman:** [02:04:00] We should also prepare the validation reports for review.\n"
            "**Alex Sanchez:** [02:05:00] Confirmed, I will align the team on the completion criteria.\n",
            encoding="utf-8",
        )

        out_dir = tmp_path / "project_lens" / "10-02-2026"
        out_dir.mkdir(parents=True, exist_ok=True)

        parser = build_parser()
        args = parser.parse_args(
            [
                "ingest-meeting",
                "--input",
                str(raw_file),
                "--time",
                "02:00 PM - 02:05 PM ET",
                "--output-dir",
                str(out_dir),
                "--provider",
                "offline",
            ]
        )

        def fake_clearance(metadata, directive_id, args):
            metadata["operator_approval"] = {"operator_id": "Alex Sanchez", "status": "APPROVED"}
            metadata["attendee_roles_confirmed_by_operator"] = True
            return None

        with patch("dual_pipeline.cli.ingest._resolve_canonical_ingest_clearance", side_effect=fake_clearance), \
             patch("dual_pipeline.canonical_pipeline.pipeline.check_mutation_reviewer_and_preflight"), \
             patch("dual_pipeline.canonical_pipeline.pipeline.publish_graph_if_requested", return_value={"status": "COMPLETE", "table_deltas": {}}):
            result = _ingest_meeting(args)
            assert result is not None
            assert Path(result["01_transcript"]).exists()
            assert Path(result["02_recap"]).exists()
            assert Path(result["03_graph"]).exists()
            intake_pkg = (
                Path(result.get("intake_package", ""))
                if result.get("intake_package")
                else (out_dir / "intake_package.json")
            )
            assert (
                intake_pkg.exists()
                or (out_dir / "intake_package.json").exists()
                or any(out_dir.glob("*/intake_package.json"))
            )


def test_cli_ingest_meeting_model_and_effort_resolution() -> None:
    """Verify parser resolution of --model gpt-6-luna and --reasoning-effort high."""
    parser = build_parser()
    args = parser.parse_args(
        [
            "ingest-meeting",
            "--input",
            "samples/sample_meeting.vtt",
            "--model",
            "gpt-6-luna",
            "--reasoning-effort",
            "high",
        ]
    )

    assert args.model == "gpt-6-luna"
    assert args.reasoning_effort == "high"
    assert args.judge_model is None  # Defaults to None, falling back to --model in handler


def test_scaffold_meeting_metadata_complex_attendees_and_project_aliases() -> None:
    """Verify attendee parsing handles group conjunctions and project aliases."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        dummy_input = Path(tmp_dir) / "dummy.txt"
        dummy_input.write_text("sample dialogue", encoding="utf-8")

        meta = scaffold_meeting_metadata(
            input_path=dummy_input,
            project="lens",
            meeting_name="Project_Lens_Sync",
            date_str="09-15-2026",
            time_str="10:00 AM - 10:30 AM ET",
            attendees="harper Hoffman (NEA) and PartnerAdvisory Lens Team",
        )

        assert meta["project"] == "project_lens"
        assert meta["date"] == "09/15/2026"
        assert meta["date_filename"] == "09-15-2026"
        roster = meta["roster"]
        assert len(roster) == 2
        assert any(
            r["name"] == "harper Hoffman" and r["org"] in ("NEA", "Apex Global Enterprises (NEA)", "Apex Global Analytics", "Apex Global Analytics (NEA)")
            for r in roster
        )
        assert any(r["name"] == "PartnerAdvisory Lens Team" and r["org"] == "PartnerAdvisory" for r in roster)


def test_cli_parser_attachment_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "ingest-meeting",
            "--input",
            "samples/sample_meeting.vtt",
            "--attachment",
            "diagram.png",
            "--attachment",
            "schema.svg",
            "--attachment-dir",
            "meeting_collateral/",
        ]
    )
    assert [str(p) for p in args.attachment] == ["diagram.png", "schema.svg"]
    assert str(args.attachment_dir) == "meeting_collateral"

    args_multi = parser.parse_args(
        [
            "ingest-meeting",
            "--input",
            "samples/sample_meeting.vtt",
            "--attachments",
            "diagram.png",
            "schema.svg",
        ]
    )
    assert [str(p) for p in args_multi.attachments] == ["diagram.png", "schema.svg"]


def test_audio_file_never_classified_as_document_even_with_sow_name() -> None:
    from dual_pipeline.cli.handlers import is_document_source

    with tempfile.TemporaryDirectory() as tmp_dir:
        dummy_wav = (
            Path(tmp_dir) / "00_09-15-2026_discuss_LENS_SOW_spliced_master_163304_165125.wav"
        )
        dummy_wav.write_bytes(b"RIFFdummywavdata")

        meta = scaffold_meeting_metadata(
            input_path=dummy_wav,
            metadata_path=None,
            project="project_lens",
            meeting_name="SOW_Boundaries_Mandy_PartnerAdvisory",
            date_str="09-15-2026",
            time_str="04:33 PM - 04:51 PM ET",
        )
        assert meta["is_document"] is False
        assert meta["output3_only"] is False
        assert is_document_source(dummy_wav, meta) is False


class CliIngestMeetingFlagsTests(unittest.TestCase):
    def test_cli_parser_flags_no_metadata(self) -> None:
        test_cli_parser_ingest_meeting_flags_without_metadata()

    def test_scaffold_metadata_from_flags(self) -> None:
        test_scaffold_meeting_metadata_from_flags()

    def test_minimal_input_only(self) -> None:
        test_cli_ingest_meeting_minimal_input_only()

    def test_model_and_effort_resolution(self) -> None:
        test_cli_ingest_meeting_model_and_effort_resolution()

    def test_complex_attendees_and_project_aliases(self) -> None:
        test_scaffold_meeting_metadata_complex_attendees_and_project_aliases()

    def test_cli_parser_attachment_flags(self) -> None:
        test_cli_parser_attachment_flags()

    def test_audio_file_never_document(self) -> None:
        test_audio_file_never_classified_as_document_even_with_sow_name()

    def test_hitl_exit_code_single_speaker_collapse(self) -> None:
        test_hitl_exit_code_single_speaker_collapse()

    def test_hitl_exit_code_dry_run(self) -> None:
        test_hitl_exit_code_dry_run()

    def test_hitl_exit_code_argparse_usage_errors_exit_1(self) -> None:
        test_hitl_exit_code_argparse_usage_errors_exit_1()

    def test_hitl_exit_code_engine_main_intercepts_collapse(self) -> None:
        test_hitl_exit_code_engine_main_intercepts_collapse()

    def test_auto_directive_id_and_rationale_generation(self) -> None:
        from dual_pipeline.cli.ingest import ingest_canonical
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()
        with tempfile.NamedTemporaryFile(suffix=".vtt") as tmp_in:
            tmp_in.write(b"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello\n")
            tmp_in.flush()
            args = parser.parse_args(
                [
                    "ingest-meeting",
                    "--input",
                    tmp_in.name,
                    "--project",
                    "project_lens",
                    "--meeting",
                    "LENS CatchUp Mandy Jen",
                    "--date",
                    "09/24/2026",
                    "--time",
                    "09:00 AM - 09:30 AM ET",
                ]
            )

            with patch("dual_pipeline.cli.ingest._resolve_canonical_ingest_clearance", return_value=None), \
                 patch("dual_pipeline.cli.ingest.CanonicalAgenticPipeline") as mock_pipeline_cls:
                mock_instance = MagicMock()
                mock_pipeline_cls.return_value = mock_instance
                mock_instance.run.return_value = {"status": "SUCCESS"}

                result = ingest_canonical(args)
                assert result == {"status": "SUCCESS"}
                mock_instance.run.assert_called_once()
            _, kwargs = mock_instance.run.call_args
            assert (
                kwargs["directive_id"]
                == "directive-project_lens-lens_catchup_mandy_jen-09-24-2026-v1"
            )
            assert (
                kwargs["rationale"]
                == "Ingest LENS CatchUp Mandy Jen (09/24/2026) into project_lens knowledge graph"
            )
            assert kwargs["mutate_graph"] is True


def test_hitl_exit_code_single_speaker_collapse() -> None:
    import io
    import re
    from contextlib import redirect_stdout

    import jsonschema

    from dual_pipeline.transcript_cli import HITL_EXIT_CODE
    from dual_pipeline.transcript_cli import main as transcript_cli_main

    schema_path = Path("schemas/cli/hitl-single-speaker-collapse-cli.schema.json")
    if not schema_path.exists():
        schema_path = (
            Path(__file__).resolve().parent.parent
            / "schemas"
            / "cli"
            / "hitl-single-speaker-collapse-cli.schema.json"
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    import uuid

    transcript = "\n".join(f"**Hayden:** [00:{i:02d}:00] Discussion point {i}." for i in range(74))
    metadata = {
        "meeting_name": f"Alan_Q4_Scope_Hayden_Amit_{uuid.uuid4().hex[:8]}",
        "date": "09/25/2026",
        "time": "10:00 AM - 10:30 AM ET",
        "attendee_roles_confirmed_by_operator": True,
        "hitl_role_resolutions": {
            "Hayden": "Lead Consultant",
            "Alex Sanchez": "Tech Lead / Senior Engineer",
        },
        "roster": [
            {"name": "Hayden", "org": "PartnerAdvisory", "role": "Lead Consultant"},
            {"name": "Alex Sanchez", "org": "PartnerAdvisory", "role": "Tech Lead / Senior Engineer"},
        ],
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "transcript.md"
        source_file.write_text(transcript, encoding="utf-8")
        meta_file = tmp_path / "metadata.json"
        meta_file.write_text(json.dumps(metadata), encoding="utf-8")

        buf = io.StringIO()
        with (
            patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", tmp_path),
            patch("dual_pipeline.canonical_pipeline.concurrency.ROOT", tmp_path),
            redirect_stdout(buf),
        ):
            exit_code = transcript_cli_main(
                [
                    "--source",
                    str(source_file),
                    "--metadata",
                    str(meta_file),
                    "--output-dir",
                    str(tmp_path),
                    "--provider",
                    "offline",
                ]
            )

        assert exit_code == HITL_EXIT_CODE
        stdout_text = buf.getvalue().strip()
        assert "\n" not in stdout_text
        payload = json.loads(stdout_text)

        jsonschema.validate(instance=payload, schema=schema)
        assert payload["protocol"] == "dual_pipeline.cli.v1"
        assert payload["status"] == "hitl_required"
        assert payload["reason"] == "single_speaker_collapse"
        assert re.match(r"^[0-9a-f]{32}$", payload["request_id"])
        assert re.match(r"^[0-9a-f]{64}$", payload["context_sha256"])
        assert payload["gate"] == "1"
        assert payload["dry_run"] is False
        assert payload["action"] == "ask_user_question"
        assert payload["evidence"]["attendee_count"] == 2
        assert payload["evidence"]["total_turns"] == 74
        assert payload["evidence"]["dominant_label"] == "Hayden"
        assert payload["evidence"]["dominant_label_turns"] == 74
        assert payload["evidence"]["max_share_bps"] == 8500


def test_hitl_exit_code_dry_run() -> None:
    from dual_pipeline.transcript_cli import main as transcript_cli_main

    with pytest.raises(SystemExit) as exc_info:
        transcript_cli_main(["--source", "sample.md", "--dry-run"])
    assert exc_info.value.code == 1


def test_hitl_exit_code_argparse_usage_errors_exit_1() -> None:
    from dual_pipeline.transcript_cli import build_parser as build_transcript_parser

    parser_t = build_transcript_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser_t.parse_args(["--invalid-argument-xyz"])
    assert exc_info.value.code == 1

    from dual_pipeline.cli.parser import build_parser as build_nee_parser

    parser_n = build_nee_parser()
    with pytest.raises(SystemExit) as exc_info2:
        parser_n.parse_args(["--invalid-argument-abc"])
    assert exc_info2.value.code == 1

    with pytest.raises(SystemExit) as exc_info3:
        parser_n.parse_args(["ingest-meeting", "--invalid-argument-def"])
    assert exc_info3.value.code == 1


def test_hitl_exit_code_engine_main_intercepts_collapse() -> None:
    import io
    from contextlib import redirect_stdout

    from dual_pipeline.cli.engine import main as nee_cli_main
    from dual_pipeline.transcript_cli import HITL_EXIT_CODE

    transcript = "\n".join(f"**Hayden:** [00:{i:02d}:00] Discussion turn {i}." for i in range(15))
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        source_file = tmp_path / "transcript.md"
        source_file.write_text(transcript, encoding="utf-8")

        buf = io.StringIO()
        with (
            patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", tmp_path),
            patch("dual_pipeline.canonical_pipeline.concurrency.ROOT", tmp_path),
            redirect_stdout(buf),
        ):
            exit_code = nee_cli_main(
                [
                    "ingest-meeting",
                    "--input",
                    str(source_file),
                    "--time",
                    "10:00 AM - 10:30 AM ET",
                    "--attendees",
                    "Hayden (Lead), Amit Ayer (Architect)",
                    "--output-dir",
                    str(tmp_path),
                    "--provider",
                    "offline",
                ]
            )

        assert exit_code == HITL_EXIT_CODE
        stdout_text = buf.getvalue().strip()
        payload = json.loads(stdout_text)
        assert payload["status"] == "hitl_required"
        assert payload["reason"] == "single_speaker_collapse"
