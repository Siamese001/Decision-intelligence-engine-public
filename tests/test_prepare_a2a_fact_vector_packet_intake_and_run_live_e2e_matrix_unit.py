"""Unit tests for tools.prepare_a2a_fact_vector_packet_intake and tools.run_live_e2e_matrix."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from tools.prepare_a2a_fact_vector_packet_intake import (
    _archive_members,
    _evidence_reference,
    _jsonl,
    _locator,
    _parse_checksums,
    _single_root,
    _source_date,
)
from tools.run_live_e2e_matrix import (
    IsolatedRuntime,
    LiveE2EMatrixRunner,
    sha256_file,
)


def test_jsonl_valid_and_invalid_inputs() -> None:
    raw = b'{"id": "1", "val": 10}\n{"id": "2", "val": 20}\n'
    rows = _jsonl(raw, "sources")
    assert len(rows) == 2
    assert rows[0]["id"] == "1"

    with pytest.raises(ValueError, match="invalid UTF-8 JSONL member"):
        _jsonl(b"{bad json", "sources")

    with pytest.raises(ValueError, match="non-object row"):
        _jsonl(b"[1, 2, 3]\n", "sources")


def test_single_root_and_archive_members() -> None:
    assert _single_root(["bundle_v3/a.json", "bundle_v3/sub/b.json"]) == "bundle_v3"
    with pytest.raises(ValueError, match="exactly one directory root"):
        _single_root(["root1/a.json", "root2/b.json"])

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bundle_v3/", "")
        zf.writestr("bundle_v3/file.txt", b"hello")
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        members = _archive_members(zf)
    assert members == {"bundle_v3/file.txt": b"hello"}


def test_parse_checksums_validation() -> None:
    d1 = "a" * 64
    d2 = "b" * 64
    valid = f"{d1}  BUNDLE_MANIFEST.json\n{d2}  data/sources.jsonl\n".encode("utf-8")
    parsed = _parse_checksums(valid)
    assert parsed["BUNDLE_MANIFEST.json"] == d1
    assert parsed["data/sources.jsonl"] == d2

    with pytest.raises(ValueError, match="empty"):
        _parse_checksums(b"")

    with pytest.raises(ValueError, match="invalid or duplicate"):
        _parse_checksums(f"{d1}  same.json\n{d1}  same.json\n".encode("utf-8"))

    with pytest.raises(ValueError, match="invalid or duplicate"):
        _parse_checksums(b"short_digest  file.json\n")


def test_source_date_locator_and_evidence_reference() -> None:
    assert _source_date("2026-10-31T12:00:00Z") == "2026-10-31"
    with pytest.raises(ValueError, match="no ISO source date"):
        _source_date("invalid")

    ev = {
        "evidence_id": "ev-1",
        "line_start": 10,
        "line_end": 25,
        "anchor_hint": "sec-intro",
        "has_exact_excerpt_preview": True,
        "excerpt_preview": "Exact quote text",
        "exact_excerpt_sha256": "c" * 64,
    }
    loc = _locator(ev, "https://example.com/doc")
    assert loc["kind"] == "uri_fragment"
    assert loc["value"] == "https://example.com/doc#sec-intro:L10-L25"
    assert loc["quote_digest"] == "c" * 64

    ref = _evidence_reference(
        ev,
        source_id="src-1",
        source_version_id="ver-1",
        url="https://example.com/doc",
        authority_rank=90,
        fresh_capture_evidence={"preview_match": "MATCH"},
    )
    assert ref["verification_state"] == "fresh_capture_verified"
    assert ref["directness"] == "direct"

    with pytest.raises(ValueError, match="no usable locator"):
        _locator({"evidence_id": "ev-bad"}, "https://example.com")


def test_sha256_file_and_isolated_runtime(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    assert sha256_file(missing) == ""

    sample = tmp_path / "sample.txt"
    sample.write_bytes(b"unit test payload")
    assert sha256_file(sample) == hashlib.sha256(b"unit test payload").hexdigest()

    tpl = tmp_path / "template"
    gen_dir = tpl / "generations" / "generation-80155e1e593fa30bb2d22e501491"
    gen_dir.mkdir(parents=True)
    for db_name in ("nee_graph.sqlite", "sidecar.sqlite"):
        conn = sqlite3.connect(str(gen_dir / db_name))
        conn.execute("CREATE TABLE t (id INT)")
        conn.commit()
        conn.close()
    (tpl / "active_pair.json").write_text(
        json.dumps(
            {
                "generation_id": "generation-80155e1e593fa30bb2d22e501491",
                "graph_path": "generations/generation-80155e1e593fa30bb2d22e501491/nee_graph.sqlite",
                "sidecar_path": "generations/generation-80155e1e593fa30bb2d22e501491/sidecar.sqlite",
            }
        ),
        encoding="utf-8",
    )

    rt_root = tmp_path / "isolated_rt"
    rt = IsolatedRuntime(custom_root=rt_root, cleanup=True)
    rt.initialize(template_dir=tpl)
    assert rt.active_pair_path.is_file()
    rt.cleanup()
    assert not rt_root.exists()


def test_live_e2e_matrix_runner_cell_commands_and_json_parser(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid --cell format"):
        LiveE2EMatrixRunner(phase="baseline", target_cells=["BAD_CELL_FORMAT"], runtime_root=str(tmp_path))

    runner = LiveE2EMatrixRunner(
        phase="baseline",
        target_cells=["DOC_PDF:cold"],
        dry_run=True,
        runtime_root=str(tmp_path),
    )
    assert runner.target_cells == [("DOC_PDF", "cold")]

    parsed = runner._parse_json_payload('log line\n{"status": "PASS", "count": 3}\n')
    assert parsed == {"status": "PASS", "count": 3}
    assert runner._parse_json_payload("not json at all") == {}

    rt = IsolatedRuntime(custom_root=tmp_path / "cell_rt", cleanup=False)
    cmd, ctx = runner._build_command_for_cell(
        modality="DOC_PDF",
        scenario="cold",
        corpus_entry={
            "primary_file": "inputs/sample.pdf",
            "metadata": {"document": True, "project": "proj_x", "title": "Spec Doc", "author": "Jane"},
        },
        matrix_cell={"disposition": "ALLOWED"},
        runtime=rt,
        cell_output_dir=tmp_path / "out",
    )
    assert "ingest-document" in cmd
    assert "--dry-run" in cmd
    assert "--author" in cmd
    assert ctx["modality"] == "DOC_PDF"
