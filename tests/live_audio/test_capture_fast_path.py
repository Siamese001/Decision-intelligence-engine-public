"""Tests for Wave 2: Zero-Cloud Capture Fast-Path and Local Actuation Contracts."""

import time

from dual_pipeline.cli.engine import execute
from dual_pipeline.cli.parser import build_parser


def test_dry_run_fast_path_latency():
    """Verify record-meeting --dry-run completes quickly without network calls."""
    parser = build_parser()
    args = parser.parse_args([
        "record-meeting",
        "--meeting", "Liz Alvarez Check-in",
        "--project", "auto",

        "--dry-run",
    ])
    # Warm up import
    execute(args)

    # Steady state latency measurement
    start = time.perf_counter()
    res = execute(args)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert res["status"] == "PASS"
    assert res["dry_run"] is True
    assert elapsed_ms < 100, f"Steady-state dry run latency exceeded 100ms limit: {elapsed_ms:.1f}ms"


def test_local_heuristic_project_mapping():
    """Verify local zero-cloud heuristic routes project without external LLMs."""
    parser = build_parser()

    cases = [
        ("meeting with Liz Alvarez (PartnerAdvisory)", "", "internal_consulting"),
        ("Project LENS Review", "", "project_lens"),
        ("Sherpa Architecture Session", "", "project_beacon"),
        ("Conduit pipeline intake", "", "conduit"),
        ("Project Atlas status", "", "project_atlas"),
        ("NEA Reid S meeting", "", "project_nexus"),
        ("Random Unrelated Meeting", "", "project_nexus"),
    ]

    for meeting, attendees, expected_proj in cases:
        cmd = ["record-meeting", "--meeting", meeting, "--project", "auto", "--dry-run", ]
        if attendees:
            cmd.extend(["--attendees", attendees])
        args = parser.parse_args(cmd)
        res = execute(args)
        assert res["project"] == expected_proj, f"Expected {expected_proj} for '{meeting}', got {res['project']}"


def test_copilot_disabled_by_default():
    """Verify live copilot advisory is False by default to maintain zero cloud dispatch."""
    parser = build_parser()
    args = parser.parse_args([
        "record-meeting",
        "--meeting", "Team Sync",
        "--project", "project_nexus",
        "--dry-run",

    ])
    res = execute(args)
    assert res["enable_copilot"] is False, "Copilot must be disabled by default for zero cloud egress"

    # Explicitly enabled with --copilot
    args_enabled = parser.parse_args([
        "record-meeting",
        "--meeting", "Team Sync",
        "--project", "project_nexus",
        "--copilot",
        "--dry-run",

    ])
    res_enabled = execute(args_enabled)
    assert res_enabled["enable_copilot"] is True, "Copilot should be True when --copilot flag is provided"
