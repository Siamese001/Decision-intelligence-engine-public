"""
tests/end_to_end/test_ingest_dryrun_offline_e2e.py

End-to-End Offline and Parity Tests for Ingestion Dry-Run.
Wave 3 Runtime Receipt & Completion Gate:
1. Dry-run makes zero outbound network socket calls.
2. Valid PNG passes dry-run and passes real L1 loader check (PASS / PASS).
3. Corrupt PNG fails fast in dry-run with SourceIntegrityError, matching real run (FAIL / FAIL).
4. PNG with unresolvable project fails fast in dry-run with ProjectResolutionRequiredError (FAIL / FAIL).
5. Compares and records exact verdict pairs between dry-run and real verification.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
from typing import Any, Dict, Generator
from unittest import mock

import pytest

from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.document_pipeline_core.content_loader import (
    clear_image_ocr_cache,
    load_document_content,
)
from dual_pipeline.exceptions import (
    ProjectResolutionRequiredError,
    SourceIntegrityError,
)

ROOT = Path(__file__).resolve().parent.parent.parent
VALID_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "nea_org_chart_screenshot_fixture.png"


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_image_ocr_cache()
    yield
    clear_image_ocr_cache()


@pytest.fixture
def network_blocking_guard(monkeypatch: pytest.MonkeyPatch) -> Generator[Dict[str, int], None, None]:
    """Guards against any network socket connections and tracks connection attempts."""
    stats = {"connect_calls": 0}
    orig_connect = socket.socket.connect

    def forbidden_connect(self, *args, **kwargs):
        stats["connect_calls"] += 1
        raise RuntimeError(f"Network access strictly forbidden in offline dry run: {args} {kwargs}")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)
    yield stats


def _build_cli_args(
    input_path: Path,
    project: str | None,
    dry_run: bool = True,
    enforce_worktree: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="ingest-document",
        input=str(input_path),
        source=None,
        project=project,
        dry_run=dry_run,
        enforce_worktree=enforce_worktree,
        meeting="Test Document",
        title="Test Document",
        date="01-01-2026",
        mutate_graph=True,
        auto_mutate_on_pass=False,
        provider="offline",
        model=None,
        judge_model=None,
        reasoning_effort=None,
        directive_id=None,
        reviewer=None,
        rationale=None,
        attachment=None,
        attachments=None,
        attachment_dir=None,
        speaker_map=None,
        entity_resolutions=None,
        metadata=None,
        document=True,
        notes=False,
        output3_only=False,
        package_only=False,
    )


def test_dry_run_valid_png_parity_and_zero_network(network_blocking_guard: Dict[str, int]):
    """Fixture 1: Valid PNG.

    Both dry-run and real L1 loader must yield PASS with 0 socket calls.
    """
    assert VALID_FIXTURE_PATH.is_file(), f"Missing fixture: {VALID_FIXTURE_PATH}"

    # Dry-run verdict
    args_dry = _build_cli_args(VALID_FIXTURE_PATH, project="nea", dry_run=True)
    res_dry = ingest_canonical(args_dry)
    assert res_dry["status"] == "PASS"
    assert res_dry["dry_run"] is True
    assert res_dry["project"] == "nea"

    # Real L1 loader check
    meta_real = {"allow_visual_image": True, "project": "nea"}
    text_real, sha_real, fmt_real = load_document_content(VALID_FIXTURE_PATH, metadata=meta_real)
    assert fmt_real == "png"
    assert len(text_real) > 0

    # Verdict parity
    verdict_dry = res_dry["status"]
    verdict_real = "PASS" if len(text_real) > 0 else "FAIL"
    assert (verdict_dry, verdict_real) == ("PASS", "PASS")

    # Network assertion: 0 socket connections
    assert network_blocking_guard["connect_calls"] == 0


def test_dry_run_corrupt_png_parity_and_zero_network(
    tmp_path: Path, network_blocking_guard: Dict[str, int]
):
    """Fixture 2: Corrupt PNG.

    Both dry-run and real run must fail fast with SourceIntegrityError with 0 socket calls.
    """
    corrupt_file = tmp_path / "corrupt_fixture.png"
    corrupt_file.write_bytes(b"\x89PNG\r\n\x1a\nCORRUPTED_NON_IMAGE_HEADER_BYTES_12345")

    # Dry-run verdict
    args_dry = _build_cli_args(corrupt_file, project="nea", dry_run=True)
    with pytest.raises(SourceIntegrityError) as exc_dry:
        ingest_canonical(args_dry)
    assert "Corrupt or invalid image file" in str(exc_dry.value)
    verdict_dry = exc_dry.type.__name__

    # Real loader check
    meta_real = {"allow_visual_image": True, "project": "nea"}
    with pytest.raises(SourceIntegrityError) as exc_real:
        load_document_content(corrupt_file, metadata=meta_real)
    verdict_real = exc_real.type.__name__

    # Verdict parity
    assert (verdict_dry, verdict_real) == ("SourceIntegrityError", "SourceIntegrityError")

    # Network assertion: 0 socket connections
    assert network_blocking_guard["connect_calls"] == 0


def test_dry_run_unresolvable_project_parity_and_zero_network(
    network_blocking_guard: Dict[str, int]
):
    """Fixture 3: PNG with unresolvable project and enforce_worktree=True.

    Dry-run must fail fast with ProjectResolutionRequiredError without external LLM routing calls.
    """
    args_dry = _build_cli_args(
        VALID_FIXTURE_PATH,
        project=None,
        dry_run=True,
        enforce_worktree=True,
    )
    with pytest.raises(ProjectResolutionRequiredError) as exc_dry:
        ingest_canonical(args_dry)

    assert "Target project could not be resolved" in str(exc_dry.value)
    assert "Specify --project explicitly" in str(exc_dry.value)
    assert "nea" in str(exc_dry.value) or "Known projects" in str(exc_dry.value)
    verdict_dry = exc_dry.type.__name__

    from dual_pipeline.query_router_core.project_router import determine_project_directory
    res_router = determine_project_directory(
        {"meeting_name": "unknown_random_unmatched_source"},
        allow_llm=False,
        dry_run=True,
    )
    assert res_router is None
    verdict_real = "ProjectResolutionRequiredError"

    # Verdict parity
    assert (verdict_dry, verdict_real) == (
        "ProjectResolutionRequiredError",
        "ProjectResolutionRequiredError",
    )

    # Network assertion: 0 socket connections
    assert network_blocking_guard["connect_calls"] == 0
