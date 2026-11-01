"""Wave 1 E2E Test: Re-Ingestion Idempotency, Content-Addressed Synthesis Cache, and Resume Journal Integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.publish_helper import check_idempotent_or_continue
from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy
from dual_pipeline.cli.ingest import _apply_semantic_flags
from dual_pipeline.transcript_pipeline_core.caching import (
    check_idempotent_meeting_run,
    compute_prompt_template_hash,
)
from dual_pipeline.transcript_stages_core.completion_call import (
    _SYNTHESIS_CONTENT_CACHE,
    lookup_content_addressed_synthesis,
    store_content_addressed_synthesis,
)

FROZEN_OUTPUT_01 = """# Forensic Normalizer — HITL Resolved Transcript

**Meeting:** Infrastructure Automation Orals
**Project:** infrastructure_automation
**Date:** 10/02/2026
**Time:** 00:00:01.29
**Platform:** Microsoft Teams
**Classification:** Confidential

## Attribution Summary

| Acoustic Cluster | Resolved Speaker | Role & Title | Turn Count | Evidence Basis |
| :--- | :--- | :--- | :--- | :--- |
| SPEAKER_00 | Amit Ayer | Managing Director, PartnerAdvisory | 2 | Voiceprint |
| SPEAKER_01 | Lan Tran | Senior Delivery Principal, PartnerAdvisory | 2 | Voiceprint |

## Ambiguity Lock Register

| Lock ID | Cluster / Span | Resolved Speaker | Evidence Basis | Resolution Status |
| :--- | :--- | :--- | :--- | :--- |
| ALR-001 | SPEAKER_00 (00:00:05) | Amit Ayer | Acoustic Match 0.96 | [OPERATOR VERIFIED] |

## Resolved Verbatim Dialogue

### [00:00:05]

**Amit Ayer:** We are aligning the infrastructure automation pattern catalog for Apex Global Enterprises today.

### [00:01:10]

**Lan Tran:** Confirmed. Our Terraform modules will standardize CIDR allocation and VPC provisioning.
"""


def test_reingest_frozen_output01_short_circuits_idempotently_when_valid_outputs_exist() -> None:
    """Verify re-ingesting an already-published 01_*_hitl_resolved_transcript.md does not force regeneration."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_w1_e2e_") as tmp:
        out_p = Path(tmp)
        base = "apex_global_Energy_PartnerAdvisory_10-02-2026_00000129"
        f1 = out_p / f"01_{base}_hitl_resolved_transcript.md"
        f2 = out_p / f"02_{base}_executive_recap.md"
        f3 = out_p / f"03_{base}_canonical_signal_projection.json"
        f1.write_text(FROZEN_OUTPUT_01, encoding="utf-8")
        f2.write_text("# Executive Recap\n\nValid recap content exceeding minimum threshold." * 5, encoding="utf-8")
        f3.write_text(json.dumps({"schema_version": "5.8", "graph_mutations": {"entity_upserts": []}}), encoding="utf-8")

        f1_sha = hashlib.sha256(FROZEN_OUTPUT_01.encode("utf-8")).hexdigest()
        prompt_hash = compute_prompt_template_hash()
        (out_p / "final_release_decision.json").write_text(
            json.dumps({
                "status": "PASS",
                "input_hash": "original-audio-wav-hash",
                "output_01_sha256": f1_sha,
                "prompt_template_hash": prompt_hash,
            }),
            encoding="utf-8",
        )
        (out_p / "pipeline_execution_receipt.json").write_text(
            json.dumps({
                "terminal_outcome": "PUBLISHED_UNVERIFIED",
                "active_generation": "generation-551408c0f635ead59056038ee19d",
                "source_evidence": {"raw_source_sha256": f1_sha},
            }),
            encoding="utf-8",
        )
        (out_p / "publication_coverage_receipt.json").write_text(
            json.dumps({"status": "VERIFIED", "coverage_summary": {"committed_count": 10, "missing_count": 0}}),
            encoding="utf-8",
        )

        # Seed isolated SQLite database to verify +0 row delta after idempotent check
        db_path = out_p / "graph.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE source_version (source_version_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE extraction_run (extraction_run_id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO source_version VALUES ('sv-1')")
        conn.execute("INSERT INTO extraction_run VALUES ('er-1')")
        conn.execute("INSERT INTO assertion VALUES ('asrt-1')")
        conn.commit()
        conn.close()

        args = argparse.Namespace(
            input=str(f1),
            document=False,
            notes=False,
            copilot_notes=False,
            output3_only=False,
            command="ingest-meeting",
            regenerate=None,
        )
        meta = {
            "meeting_name": "apex_global_Energy_PartnerAdvisory",
            "date": "10-02-2026",
            "time": "00:00:01.29",
            "source_file_hash": f1_sha,
        }
        _apply_semantic_flags(meta, args)

        assert meta["freeze_output_1"] is True
        assert meta["resume"] is True
        assert meta.get("regenerate") is None
        assert meta.get("auto_regenerate_track1") is False

        tracker = MagicMock()
        idem = check_idempotent_or_continue(
            tracker=tracker,
            output_dir=str(out_p),
            meta=meta,
            input_hash=f1_sha,
            mutate_graph=True,
        )
        assert idem is not None
        assert Path(idem["01_transcript"]).name == f1.name
        assert Path(idem["02_recap"]).name == f2.name
        assert Path(idem["03_graph"]).name == f3.name

        ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        assert ro_conn.execute("SELECT COUNT(*) FROM source_version").fetchone()[0] == 1
        assert ro_conn.execute("SELECT COUNT(*) FROM extraction_run").fetchone()[0] == 1
        assert ro_conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0] == 1
        ro_conn.close()


def test_content_addressed_synthesis_cache_returns_byte_identical_outputs_on_regenerate() -> None:
    """Verify explicit --regenerate 02,03 on identical Output 01 hits the content-addressed synthesis cache."""
    _SYNTHESIS_CONTENT_CACHE.clear()
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_w1_cache_") as tmp:
        cache_dir = Path(tmp) / "syn_cache"
        meta = {
            "meeting_name": "apex_global_Energy_PartnerAdvisory",
            "date": "10-02-2026",
            "output_dir": str(tmp),
            "synthesis_cache_dir": str(cache_dir),
            "regenerate": "02,03",
        }
        expected_o2 = "# 1. Meeting Objective\n\nDeterministic recap output."
        expected_o3 = json.dumps({"schema_version": "5.8", "graph_mutations": {"entity_upserts": []}})

        store_content_addressed_synthesis("output_2", FROZEN_OUTPUT_01, meta, expected_o2)
        store_content_addressed_synthesis("output_3", FROZEN_OUTPUT_01, meta, expected_o3)

        # Clear in-memory cache to verify disk persistence as well
        _SYNTHESIS_CONTENT_CACHE.clear()

        hit_2 = lookup_content_addressed_synthesis("output_2", FROZEN_OUTPUT_01, meta, attempt=1, needs_rem=False)
        hit_3 = lookup_content_addressed_synthesis("output_3", FROZEN_OUTPUT_01, meta, attempt=1, needs_rem=False)
        assert hit_2 == expected_o2
        assert hit_3 == expected_o3

        # Verify force_rerun bypasses cache
        meta_force = dict(meta, force_rerun=True)
        assert lookup_content_addressed_synthesis("output_2", FROZEN_OUTPUT_01, meta_force, attempt=1, needs_rem=False) is None
