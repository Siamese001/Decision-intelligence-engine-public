"""End-to-end and adversarial regression tests for E2E critical functionality repairs (10-28-2026)."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import (
    _pre_persist_role_resolutions_if_needed,
)
from dual_pipeline.core import digest
from storage.connection import create_sqlite_conn
from tools.prepare_combined_remediation_control_package import (
    COMBINED_REMEDIATION_CONTROL_KEY,
    main as combined_remediation_main,
)
from tools.prepare_markdown_source_intake import (
    MARKDOWN_SOURCE_CONTRACT_ID,
    prepare_package,
)
from tools.prepare_source_retirement_control import main as source_retirement_main


def _init_sandbox_graph(db_path: Path) -> None:
    from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph

    db_path.parent.mkdir(parents=True, exist_ok=True)
    _bootstrap_test_graph(db_path)
    with create_sqlite_conn(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, properties_json)
            VALUES ('person_alice_smith', 'Person', 'Alice Smith', '{}')
            """
        )
        conn.commit()


def test_e2e_role_pre_persistence_resolves_fallback_graph_path(tmp_path: Path) -> None:
    """E2E-FINDING-01: _pre_persist_role_resolutions_if_needed resolves candidate graph via publication_verifier."""
    out_dir = tmp_path / "deliverable_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "graph.sqlite"
    _init_sandbox_graph(db_path)

    meta = {
        "runtime_root": str(out_dir),
        "target_output_dir": str(out_dir),
        "hitl_role_resolutions": {
            "Alice Smith": {"resolved_role": "VP of Grid Engineering, Apex Global Enterprises"},
        },
    }
    # Pass empty written dict so cand_graph falls back to _resolve_graph_path(meta, output_dir=out_dir)
    _pre_persist_role_resolutions_if_needed(meta, {}, "operator_qa")

    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as ro_conn:
        assert ro_conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        row = ro_conn.execute(
            "SELECT properties_json FROM node WHERE node_id = 'person_alice_smith'"
        ).fetchone()
        assert row is not None
        props = json.loads(row[0])
        assert props.get("role") == "VP of Grid Engineering, Apex Global Enterprises"
        asrt_row = ro_conn.execute(
            "SELECT scope_text FROM assertion WHERE subject_node_id = 'person_alice_smith'"
        ).fetchone()
        assert asrt_row is not None
        assert "VP of Grid Engineering, Apex Global Enterprises" in asrt_row[0]

    # Also verify warning path when _resolve_graph_path raises an unexpected error
    from unittest.mock import patch

    with patch(
        "dual_pipeline.canonical_pipeline.publication_verifier._resolve_graph_path",
        side_effect=RuntimeError("simulated resolution error"),
    ):
        _pre_persist_role_resolutions_if_needed(meta, {}, "operator_qa")

    # Verify _resolve_graph_path falls back to default .local/dual_pipeline when sandbox has no DB
    from dual_pipeline.canonical_pipeline.publication_verifier import _resolve_graph_path

    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir(parents=True, exist_ok=True)
    fallback_g = _resolve_graph_path({"runtime_root": str(empty_dir)}, output_dir=empty_dir)
    assert fallback_g is None or fallback_g.is_file()


def test_e2e_prepare_source_retirement_and_combined_control_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-FINDING-02: prepare_source_retirement_control and prepare_combined_remediation_control_package run end-to-end."""
    md_source = tmp_path / "retirement_control.md"
    md_source.write_text(
        "# Source Retirement Control\n\n"
        "**Source ID:** `src-retire-001`\n"
        "**Source date:** `2026-10-28`\n\n"
        "## Requested correction\n"
        "Retire obsolete test assertion from source src-obsolete-01.\n",
        encoding="utf-8",
    )

    retire_control = {
        "schema_version": "1.0.0",
        "target_source_id": "src-obsolete-01",
        "remove": {
            "assertion_ids": ["assertion-obsolete-01"],
            "claim_ids": [],
            "edge_ids": [],
            "alias_ids": [],
            "canonical_alias_ids": [],
            "evidence_ids": [],
        },
        "retain": {
            "assertion_ids": [],
            "claim_ids": [],
            "edge_ids": [],
            "alias_ids": [],
            "canonical_alias_ids": [],
            "evidence_ids": [],
        },
        "related_removals": [],
        "supersession_detachments": [],
        "source_updates": [
            {
                "source_id": "src-obsolete-01",
                "expected_source_name": "Old Source",
                "expected_source_posture": "working_notes",
                "expected_notes": None,
                "source_name": "Old Source (Retired)",
                "source_posture": "working_notes",
                "notes": "Retired in test.",
            }
        ],
        "source_version_updates": [
            {
                "source_version_id": "sv-obsolete-01",
                "expected_scope_text": "Old Scope",
                "expected_posture_text": "working_notes",
                "scope_text": "Retired Scope",
                "posture_text": "retired",
            }
        ],
        "reason": "Retire superseded record in hermetic sandbox.",
    }
    targets_json = tmp_path / "targets.json"
    targets_json.write_text(json.dumps(retire_control, indent=2), encoding="utf-8")
    out_pkg = tmp_path / "retirement_pkg.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_source_retirement_control.py",
            "--source",
            str(md_source),
            "--targets",
            str(targets_json),
            "--output",
            str(out_pkg),
            "--source-id",
            "src-retire-001",
            "--source-date",
            "2026-10-28",
            "--audience",
            "internal_consulting",
        ],
    )
    rc = source_retirement_main()
    assert rc == 0
    assert out_pkg.is_file()
    sealed_pkg = json.loads(out_pkg.read_text(encoding="utf-8"))
    assert sealed_pkg["contract_id"] == MARKDOWN_SOURCE_CONTRACT_ID
    assert "canonical_digest" in sealed_pkg

    # Also test prepare_combined_control_package end-to-end
    combined_unsigned = {
        "schema_version": "1.0.0",
        "control_type": "combined_sherpa_retirement_and_project_status_demotion",
        "base_active_pair": {
            "runtime_root": str(tmp_path),
            "generation_id": "generation-test",
            "graph_sha256": "a" * 64,
            "sidecar_sha256": "b" * 64,
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "rev1",
            "model_manifest_digest": "c" * 64,
            "production_qualified": False,
        },
        "source_retirement_execution_order": ["src-obsolete-01"],
        "source_retirement_controls": [retire_control],
        "project_status_reclassification_control": {
            "schema_version": "1.0.0",
            "reason": "Demote stale status in sandbox.",
            "targets": [
                {
                    "source_id": "src-status-01",
                    "source_version_id": "sv-status-01",
                    "expected_content_sha256": "d" * 64,
                    "expected_source_type": "meeting_transcript",
                    "expected_source_name": "EDO Weekly Status",
                    "expected_source_posture": "governed_internal",
                    "expected_notes": None,
                    "expected_scope_text": "EDO status",
                    "expected_version_posture_text": "governed_internal",
                    "scope_id": "EDO",
                    "as_of_date": "2026-10-20",
                    "assertion_ids": ["assertion-status-01"],
                }
            ],
        },
        "reason": "Combined remediation sandbox verification.",
    }
    combined_control = dict(combined_unsigned, canonical_digest=digest(combined_unsigned))
    combined_control_path = tmp_path / "combined_control.json"
    combined_control_path.write_text(json.dumps(combined_control, indent=2), encoding="utf-8")
    combined_md_out = tmp_path / "combined_source.md"
    combined_pkg_out = tmp_path / "combined_pkg.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_combined_remediation_control_package.py",
            "--control",
            str(combined_control_path),
            "--source",
            str(combined_md_out),
            "--output",
            str(combined_pkg_out),
            "--source-id",
            "src-combined-001",
            "--source-date",
            "2026-10-28",
        ],
    )
    rc_combined = combined_remediation_main()
    assert rc_combined == 0
    assert combined_pkg_out.is_file()
    combined_pkg = json.loads(combined_pkg_out.read_text(encoding="utf-8"))
    assert combined_pkg["contract_id"] == MARKDOWN_SOURCE_CONTRACT_ID
    assert COMBINED_REMEDIATION_CONTROL_KEY in combined_pkg["extensions"]


def test_adversarial_markdown_intake_boundary_malformed_and_bypass(tmp_path: Path) -> None:
    """Adversarial checks covering boundary payload, malformed input, and invariant bypass attempts."""
    # Category 1: Boundary payload (0-byte empty file & blank audiences)
    empty_md = tmp_path / "empty.md"
    empty_md.write_bytes(b"")
    with pytest.raises(ValueError, match="non-empty Markdown file"):
        prepare_package(
            empty_md,
            tmp_path / "out1.json",
            source_id="s1",
            source_date="2026-10-28",
            classification="internal",
            audiences=["internal_consulting"],
            source_posture="governed_internal",
            authority_rank=5,
            anchor_entity="Entity",
            anchor_stable_identifier="entity-1",
        )

    # Category 2: Malformed input (invalid UTF-8 bytes & UTF-8 BOM rejection & missing level-1 title)
    bad_utf8 = tmp_path / "bad_utf8.md"
    bad_utf8.write_bytes(b"\xff\xfe# Invalid UTF-8")
    with pytest.raises(ValueError, match="canonical UTF-8 Markdown"):
        prepare_package(
            bad_utf8,
            tmp_path / "out2.json",
            source_id="s2",
            source_date="2026-10-28",
            classification="internal",
            audiences=["internal_consulting"],
            source_posture="governed_internal",
            authority_rank=5,
            anchor_entity="Entity",
            anchor_stable_identifier="entity-1",
        )

    no_title = tmp_path / "no_title.md"
    no_title.write_text("## Requested correction\nBody text only.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="level-one title"):
        prepare_package(
            no_title,
            tmp_path / "out3.json",
            source_id="s3",
            source_date="2026-10-28",
            classification="internal",
            audiences=["internal_consulting"],
            source_posture="governed_internal",
            authority_rank=5,
            anchor_entity="Entity",
            anchor_stable_identifier="entity-1",
            chunk_sections=["Requested correction"],
        )

    # Category 3: Invariant bypass attempt (attempting to overwrite built-in chunk-selection extension)
    valid_md = tmp_path / "valid.md"
    valid_md.write_text("# Valid Control\n\n## Requested correction\nKeep bounded.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot replace built-in controls"):
        prepare_package(
            valid_md,
            tmp_path / "out4.json",
            source_id="s4",
            source_date="2026-10-28",
            classification="internal",
            audiences=["internal_consulting"],
            source_posture="governed_internal",
            authority_rank=5,
            anchor_entity="Entity",
            anchor_stable_identifier="entity-1",
            chunk_sections=["Requested correction"],
            additional_extensions={"com.partner_advisory.nee.chunk-selection": {"profile": "bypass"}},
        )


def test_e2e_cli_subprocess_entrypoint_execution(tmp_path: Path) -> None:
    """Real subprocess execution of tools/prepare_markdown_source_intake.py without internal mocks."""
    md_file = tmp_path / "cli_source.md"
    md_file.write_text(
        "# CLI Subprocess Control Source\n\n"
        "**Source ID:** `cli-src-01`\n"
        "**Source date:** `2026-10-28`\n\n"
        "## Requested correction\n"
        "Subprocess verified section content.\n",
        encoding="utf-8",
    )
    out_file = tmp_path / "cli_pkg.json"
    proc = subprocess.run(
        [
            sys.executable,
            "tools/prepare_markdown_source_intake.py",
            "--source",
            str(md_file),
            "--output",
            str(out_file),
            "--classification",
            "internal",
            "--audience",
            "internal_consulting",
            "--anchor-entity",
            "EDO graph correction",
            "--anchor-stable-identifier",
            "EDO-graph-correction",
            "--chunk-section",
            "Requested correction",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "PASS"
    assert out_file.is_file()
