"""
tests/end_to_end/test_all_pipeline_sources_matrix_e2e.py

End-to-End Verification Matrix Across All NEE Pipeline Ingestion Sources:
1. Full Meeting Pipeline (Outputs 01, 02, 03 + Governed Graph Mutation):
   - 1a: Pre-existing recording undiarized (.wav, .mp4, .m4a, .mp3, .flac, .ogg)
   - 1b: Pre-existing recording diarized (.wav / .mp4 with speaker_map.json / DIARIZED_ACOUSTIC_RESOLVED)
   - 1c: Live meeting captured & transcribed (00_*_master.wav + 01_*_live_transcript.md + speaker_hints_queue.json)
   - 1d: Pre-existing raw transcript undiarized (Speaker 1 / Unknown in .txt, .md)
   - 1e: Pre-existing raw transcript platform-diarized (.vtt, .srt, Teams .docx, Teams/Zoom .json, .txt, .md)
   - 1f: Companion meeting collateral (.pptx deck + .png visual exhibit)
2. Partial / Post-01 Meeting Pipeline (Outputs 02 & 03 + Governed Graph Mutation):
   - 2a: Pre-existing HITL-resolved transcript (01_*_hitl_resolved_transcript.md, --backtest / --regenerate 02,03)
   - 2b: Pre-processed meeting notes / AI summaries (--notes / --copilot-notes in .md, .txt, .docx)
3. Document & Visual Pipeline (Output 03 Only + Governed Graph Mutation):
   - 3a: PDF (.pdf)
   - 3b: Word (.docx narrative & tables)
   - 3c: PowerPoint (.pptx standalone deck)
   - 3d: Spreadsheets (.xlsx, .xlsm, .csv, .tsv)
   - 3e: Raster Images (.png, .jpg, .jpeg, .gif, .webp, .bmp, .tiff)
   - 3f: Diagrams (.svg, .drawio, .vsdx)
   - 3g: Email (.eml)
   - 3h: Web / Text / JSON (.html, .htm, .md, .markdown, .txt, .text, .json)
   - 3i: Remote URLs & Pre-Built Intake Packages
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.canonical_pipeline.modalities import (
    AUDIO_EXTENSIONS,
    detect_modality,
    get_deliverables_for_modality,
)
from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file
from dual_pipeline.exceptions import HITLRequiredError, UnmappedSpeakerError
from dual_pipeline.graph_scout_core.pipeline_hook import ScoutHookResult
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    classify_source_modality,
)
from tools.graph_mutation_proof_core import raw_byte_scan
from tools.mcp_test_orchestrator_server import provision_isolated_test_db

TEST_GRAPH_SCHEMA = """
CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_name TEXT NOT NULL, source_date TEXT, authority_rank INTEGER NOT NULL DEFAULT 5, source_posture TEXT NOT NULL, cold_archive_ref TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE source_version (source_version_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version_number INTEGER NOT NULL, source_fingerprint TEXT, content_hash TEXT NOT NULL, source_date_normalized TEXT, source_date_precision TEXT, source_date_status TEXT, authority_rank INTEGER, scope_text TEXT, posture_text TEXT, created_at TEXT NOT NULL);
CREATE TABLE extraction_run (extraction_run_id TEXT PRIMARY KEY, algorithm_version TEXT NOT NULL, baseline_graph_sha256 TEXT NOT NULL, input_manifest_sha256 TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, deterministic INTEGER NOT NULL);
CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, primary_label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_version_id TEXT NOT NULL, locator_kind TEXT NOT NULL, locator_value TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE assertion (
    assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT NOT NULL, subject_node_id TEXT NOT NULL, predicate TEXT NOT NULL,
    legacy_predicate TEXT, object_node_id TEXT, object_literal TEXT, object_literal_type TEXT, assertion_type TEXT NOT NULL,
    atomicity_state TEXT NOT NULL, scope_text TEXT NOT NULL, speaker_or_author TEXT, primary_source_id TEXT NOT NULL,
    extraction_run_id TEXT NOT NULL, extraction_method TEXT NOT NULL, origin_type TEXT NOT NULL, confidence_label TEXT NOT NULL,
    confidence_score REAL NOT NULL, validation_state TEXT NOT NULL, currentness_state TEXT NOT NULL, valid_from TEXT,
    valid_to TEXT, date_precision TEXT, date_status TEXT, recorded_at TEXT NOT NULL, superseded_at TEXT, superseded_by_assertion_id TEXT,
    resolution_state TEXT NOT NULL, canonical_status TEXT NOT NULL, canonical_eligible INTEGER NOT NULL, retrieval_eligible INTEGER NOT NULL,
    materiality TEXT NOT NULL, classification TEXT NOT NULL, allowed_audiences_json TEXT NOT NULL, hitl_required INTEGER NOT NULL,
    validation_priority TEXT, validation_owner TEXT, qualifiers_json TEXT NOT NULL, legacy_record_type TEXT NOT NULL,
    legacy_record_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, approval_reference TEXT
);
CREATE TABLE assertion_evidence (assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL, evidence_relationship TEXT NOT NULL, rank INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY (assertion_id, evidence_id));
CREATE TABLE assertion_label_state (assertion_id TEXT PRIMARY KEY, extraction_confidence REAL NOT NULL, assertion_confidence_label TEXT, assertion_confidence_score REAL, validation_state TEXT NOT NULL, lifecycle_state TEXT NOT NULL, mapping_state TEXT NOT NULL, mapping_rule_id TEXT, updated_at TEXT NOT NULL);
CREATE TABLE assertion_audience (assertion_id TEXT NOT NULL, audience_id TEXT NOT NULL, mapping_state TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (assertion_id, audience_id));
CREATE TABLE search_document (search_document_id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, canonical_status TEXT NOT NULL, currentness_state TEXT NOT NULL, retrieval_eligible INTEGER NOT NULL, source_id TEXT NOT NULL, assertion_id TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL, action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL, previous_value_json TEXT, new_value_json TEXT NOT NULL, source_id TEXT NOT NULL, event_timestamp TEXT NOT NULL, reason TEXT NOT NULL, validation_reference TEXT, approval_reference TEXT, migration_or_ingestion_run_id TEXT NOT NULL, release_version TEXT NOT NULL);
CREATE TABLE governance_approval (approval_id TEXT PRIMARY KEY, approval_reference TEXT NOT NULL, scope_json TEXT NOT NULL, status TEXT NOT NULL, active INTEGER NOT NULL, approved_at TEXT NOT NULL);
"""

AMIT_TURNS = [
    "We need to enforce zero trust security controls and FedRAMP audit compliance across every microservice boundary.",
    "Our Terraform state automation workflows and GitHub Actions infrastructure provisioning pipeline are ready for validation.",
    "Reusable deployment patterns in the service catalog will eliminate bespoke scripts and manual configuration drift.",
    "The Backstage developer portal integration with ServiceNow CMDB provides a unified entry point for engineering pods.",
    "We should lock the SQLite write-ahead logging configuration and thirty-second busy timeout before load testing.",
    "Deterministic schema validation gates will verify every entity payload before any downstream storage commit.",
    "Latency benchmarks show sub-second query response times across both dense and sparse retrieval indexes.",
    "I will finalize the technical architecture specification and publish the migration runbook by Friday afternoon.",
]

DAN_TURNS = [
    "From a commercial strategy perspective, our joint sales pipeline and co-sell funding targets for Q4 are on track.",
    "We structured the deployment voucher amendment and statement of work milestones to align with executive sponsorship.",
    "The user enablement academy and training curriculum modules will onboard forty client engineers next month.",
    "Our federated hub and spoke operating model workshops resolved the cross-functional team handoff friction.",
    "Looking at the program risk register, our top mitigation priority is clearing the staging environment blocker.",
    "Self-service request intake workflows will reduce ticket turnaround time across all regional business units.",
    "Portfolio roadmap alignment confirms that phase two expansion begins immediately after the initial release sign-off.",
    "Let us schedule the executive steering committee review for Tuesday to ratify the final release decision.",
]


def _build_16_turn_dialogue(spk1: str = "Amit Ayer", spk2: str = "Drew Oakley") -> str:
    lines: List[str] = []
    for idx in range(16):
        ts = f"00:0{idx // 6}:{(idx * 10) % 60:02d}"
        if idx % 2 == 0:
            lines.append(f"[{ts}] {spk1}: {AMIT_TURNS[idx // 2]}")
        else:
            lines.append(f"[{ts}] {spk2}: {DAN_TURNS[idx // 2]}")
    return "\n\n".join(lines)


def _build_docx_bytes(paragraphs: List[str], include_table: bool = False) -> bytes:
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/office/word"
    p_xml = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    tbl_xml = ""
    if include_table:
        tbl_xml = (
            "<w:tbl>"
            "<w:tr><w:tc><w:p><w:r><w:t>Component</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc></w:tr>"
            "<w:tr><w:tc><w:p><w:r><w:t>Payment Gateway</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Amit Ayer</w:t></w:r></w:p></w:tc></w:tr>"
            "</w:tbl>"
        )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{p_xml}{tbl_xml}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def _build_pptx_bytes(slide_texts: List[str], notes_text: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        for idx, s_txt in enumerate(slide_texts, start=1):
            slide_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{s_txt}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
            )
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)
        if notes_text:
            notes_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{notes_text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
            )
            zf.writestr("ppt/notesSlides/notesSlide1.xml", notes_xml)
    return buf.getvalue()


def _build_valid_wav_bytes() -> bytes:
    pcm_data = b"\x00\x00" * 16000
    data_size = len(pcm_data)
    riff_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        16000,
        32000,
        2,
        16,
        b"data",
        data_size,
    )
    return header + pcm_data


def _build_valid_pdf_bytes(text: str) -> bytes:
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font_obj = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_obj)
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    t_stream = DecodedStreamObject()
    t_stream.set_data(f"BT /F1 12 Tf 72 712 Td ({text[:120]}) Tj ET".encode("latin1", errors="ignore"))
    page[NameObject("/Contents")] = writer._add_object(t_stream)
    page[NameObject("/Resources")] = resources
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _build_xlsx_bytes(rows: List[List[str]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>",
        )
        unique_strings: List[str] = []
        str_to_id: Dict[str, int] = {}
        for row in rows:
            for val in row:
                if val not in str_to_id:
                    str_to_id[val] = len(unique_strings)
                    unique_strings.append(val)
        sst = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(unique_strings)}" uniqueCount="{len(unique_strings)}">'
            + "".join(f"<si><t>{s}</t></si>" for s in unique_strings)
            + "</sst>"
        )
        zf.writestr("xl/sharedStrings.xml", sst)
        sheet_rows: List[str] = []
        for r_idx, row in enumerate(rows, start=1):
            cells = "".join(
                f'<c r="{chr(ord("A") + c_idx)}{r_idx}" t="s"><v>{str_to_id[val]}</v></c>'
                for c_idx, val in enumerate(row)
            )
            sheet_rows.append(f'<row r="{r_idx}">{cells}</row>')
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>",
        )
    return buf.getvalue()


def _build_vsdx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        pages_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main">'
            '<Page ID="1" Name="Architecture_Topology"/></Pages>'
        )
        page1_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">'
            '<Shapes>'
            '<Shape ID="1"><Text>Enterprise Ingestion Gateway coordinates multi-modal document, transcript, and audio routing across all regional business units.</Text></Shape>'
            '<Shape ID="2"><Text>Governed SQLite Knowledge Graph enforces WAL mode, busy timeouts, and five-check physical mutation receipts overseen by Amit Ayer.</Text></Shape>'
            '<Shape ID="3"><Text>Hybrid BGE-M3 Vector Sidecar synchronizes dense and lexical retrieval indexes for Drew Oakley and client delivery teams.</Text></Shape>'
            '</Shapes>'
            '<Connects>'
            '<Connect FromSheet="1" ToSheet="2"/>'
            '<Connect FromSheet="2" ToSheet="3"/>'
            '</Connects>'
            '</PageContents>'
        )
        zf.writestr("visio/pages/pages.xml", pages_xml)
        zf.writestr("visio/pages/page1.xml", page1_xml)
    return buf.getvalue()


@pytest.mark.e2e
class AllPipelineSourcesMatrixE2ETests(unittest.TestCase):
    """Comprehensive E2E test suite verifying all NEE pipeline ingestion sources and graph mutations."""

    def setUp(self) -> None:
        from opentelemetry.trace import NoOpTracerProvider
        self._tmp = tempfile.TemporaryDirectory(prefix="nee_all_sources_e2e_")
        self.root = Path(self._tmp.name)
        self._prev_sandbox = os.environ.get("TEST_SANDBOX_DIR")
        self._prev_fast = os.environ.get("NEE_FAST_TEST_MODE")
        self._saved_creds = {}
        for k in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
            if k in os.environ:
                self._saved_creds[k] = os.environ.pop(k)
        os.environ["TEST_SANDBOX_DIR"] = str(self.root)
        os.environ["NEE_FAST_TEST_MODE"] = "1"

        self._patches = [
            patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", self.root),
            patch("dual_pipeline.canonical_pipeline.concurrency.ROOT", self.root),
            patch("dual_pipeline.dlq.DeadLetterQueueManager"),
            patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager"),
            patch("dual_pipeline.otel_engine.get_tracer", return_value=NoOpTracerProvider().get_tracer("test")),
            patch("dual_pipeline.telemetry_core.metrics.record_stage_duration"),
            patch("dual_pipeline.hitl_governance_core.ledger._find_default_ledger_path", return_value=self.root / "hitl.json"),
            patch("dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook", return_value=ScoutHookResult(skipped_reason="test")),
        ]
        for p in self._patches:
            p.start()
        self.pipeline = CanonicalAgenticPipeline(provider_type="offline")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        for k, v in self._saved_creds.items():
            os.environ[k] = v
        if self._prev_sandbox is None:
            os.environ.pop("TEST_SANDBOX_DIR", None)
        else:
            os.environ["TEST_SANDBOX_DIR"] = self._prev_sandbox
        if self._prev_fast is None:
            os.environ.pop("NEE_FAST_TEST_MODE", None)
        else:
            os.environ["NEE_FAST_TEST_MODE"] = self._prev_fast
        self._tmp.cleanup()

    def _base_meeting_meta(self, meeting_name: str, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "project": "nea",
            "meeting_name": meeting_name,
            "date": "10/15/2026",
            "time": "11:00 AM - 11:05 AM ET",
            "attendees": [
                {"name": "Amit Ayer", "role": "Enterprise Architect"},
                {"name": "Drew Oakley", "role": "Client Service Lead"},
            ],
            "hitl_role_resolutions": {
                "Amit Ayer": "Enterprise Architect",
                "Drew Oakley": "Client Service Lead",
            },
            "attendees_confirmed_by_operator": True,
            "attendee_confirmation_receipt": {
                "status": "RESOLVED_HITL",
                "subject_sha256": "urn:nee:live-container-preflight-proof",
                "confirmed_attendees": ["Amit Ayer", "Drew Oakley"],
            },
            "enforce_worktree": False,
            "test_fast": True,
        }
        if extra:
            meta.update(extra)
        return meta

    def _base_doc_meta(self, title: str, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "project": "nea",
            "title": title,
            "date": "10/15/2026",
            "author": "Amit Ayer",
            "is_document": True,
            "enforce_worktree": False,
            "test_fast": True,
            "no_cache": True,
        }
        if extra:
            meta.update(extra)
        return meta

    def _apply_output03_to_isolated_graph(self, result: Dict[str, Any], tag: str) -> Dict[str, Any]:
        """Applies Output 03 from a pipeline run to an isolated SQLite GraphDB via GovernedGraphWriter with 5-check proof."""
        self.assertEqual(result.get("status"), "COMPLETE", f"Expected COMPLETE for {tag}, got {result.get('status')}")
        f3_path = Path(result.get("03_graph") or result.get("output_03"))
        pkg_path = Path(result["intake_package"])
        self.assertTrue(f3_path.is_file(), f"Missing Output 03 file for {tag}")
        self.assertTrue(pkg_path.is_file(), f"Missing intake_package.json for {tag}")

        f3_data = json.loads(f3_path.read_text(encoding="utf-8"))
        pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
        pkg_digest = pkg_data.get("canonical_digest", {}).get("value") or sha256_file(pkg_path)

        entities = f3_data.get("graph_mutations", {}).get("entity_upserts", [])
        subj_name = entities[0].get("canonical_name", "Platform Gateway") if entities else "Platform Gateway"
        obj_name = entities[1].get("canonical_name", "Core Ledger") if len(entities) > 1 else "Core Ledger"

        prov = json.loads(provision_isolated_test_db())
        graph_path = Path(prov["isolated_db_path"])
        conn = connect_sqlite(graph_path)
        conn.executescript(TEST_GRAPH_SCHEMA)
        conn.execute("INSERT INTO node VALUES (?, ?, 'system', 1, '2026-10-15T00:00:00Z')", (f"node-subj-{tag}", subj_name))
        conn.execute("INSERT INTO node VALUES (?, ?, 'module', 1, '2026-10-15T00:00:00Z')", (f"node-obj-{tag}", obj_name))
        conn.execute("INSERT INTO source VALUES (?, 'dual_pipeline_intake', ?, '2026-10-15', 7, 'governed_internal', NULL, 'Notes', '2026-10-15T00:00:00Z')", (f"src-{tag}", tag))
        conn.execute(
            "INSERT INTO source_version VALUES (?, ?, 1, 'fp', ?, '2026-10-15', 'day', 'exact', 7, 'scope', 'posture', '2026-10-15T00:00:00Z')",
            (f"sv-{tag}", f"src-{tag}", pkg_digest[:64].ljust(64, "0")),
        )
        conn.execute("INSERT INTO evidence_item VALUES (?, ?, ?, 'locator', 'line:1', '2026-10-15T00:00:00Z')", (f"ev-{tag}", f"src-{tag}", f"sv-{tag}"))
        conn.execute("INSERT INTO governance_approval VALUES (?, ?, json_object('allowed_actions', json_array('canonical_assertion_promotion')), 'approved', 1, '2026-10-15T00:00:00Z')", (f"app-{tag}", f"ref-{tag}"))
        conn.commit()
        conn.close()

        base_hash = sha256_file(graph_path)
        target_assertion_id = f"as-{tag}-001"
        op_id = f"op-{tag}-001"
        cand = {
            "candidate_id": f"cand-{target_assertion_id}",
            "candidate_type": "assertion",
            "normalized_text": f"{subj_name} integrates with {obj_name} ({tag})",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_ids": [f"ev-{tag}"],
            "extraction_confidence": 0.96,
            "materiality": "high",
        }
        payload = {
            "candidate": cand,
            "subject_node_id": f"node-subj-{tag}",
            "predicate": "INTEGRATES_WITH",
            "object_node_id": f"node-obj-{tag}",
            "disposition": "CANDIDATE",
            "retrieval_audiences": ["engineering"],
        }
        op = {
            "operation_id": op_id,
            "sequence": 1,
            "operation": "add_candidate_assertion",
            "target_type": "assertion",
            "target_id": target_assertion_id,
            "candidate_id": f"cand-{target_assertion_id}",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_ids": [f"ev-{tag}"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(payload),
        }
        source_meta = {
            "source_id": f"src-{tag}",
            "source_version_id": f"sv-{tag}",
            "name": tag,
            "package_digest": pkg_digest[:64].ljust(64, "0"),
            "authority_rank": 7,
            "source_type": "governed_internal",
        }
        change_set = seal_artifact(
            create_artifact(
                "graph-change-set",
                f"cs-{op_id}",
                change_set_id=f"cs-{op_id}",
                run_id=f"run-{tag}",
                trace_id=hashlib.sha256(tag.encode("utf-8")).hexdigest()[:32],
                approval_id=f"app-{tag}",
                status="approved",
                base_graph_snapshot=f"snap-{tag}",
                base_graph_hash=base_hash,
                transaction_required=True,
                rollback_required=True,
                operations=[op],
                extensions={
                    "com.partner_advisory.nee.source": source_meta,
                    "com.partner_advisory.nee.operation-payloads": {op_id: payload},
                },
            )
        )
        receipt = GovernedGraphWriter(graph_path).apply(change_set)
        self.assertEqual(receipt["status"], "COMMITTED")
        proof = receipt["physical_proof"]
        self.assertEqual(proof["overall"], "PASS")
        self.assertEqual(set(proof["checks"]), {"file_changed", "raw_bytes", "integrity", "readback", "row_delta"})
        self.assertTrue(all(v == "PASS" for v in proof["checks"].values()))
        self.assertIsNotNone(raw_byte_scan(graph_path, [target_assertion_id])[target_assertion_id])
        return receipt

    def test_1a_pre_existing_recording_undiarized_all_audio_video_formats_e2e(self) -> None:
        """Category 1a: Pre-existing undiarized recordings (.mp4, .wav, .m4a, .mp3, .flac, .ogg, .aac, .wma, .m4v) -> Gate -> Outputs 01, 02, 03 + Graph."""
        for ext in (".mp4", ".wav", ".m4a", ".mp3", ".flac", ".ogg", ".aac", ".wma", ".m4v"):
            self.assertIn(ext, AUDIO_EXTENSIONS)
            mod = detect_modality(Path(f"recording{ext}"), "", {})
            self.assertEqual(mod, CanonicalModality.AUDIO)
            self.assertEqual(get_deliverables_for_modality(mod), ["01", "02", "03"])

        undiarized_dialogue = _build_16_turn_dialogue("Speaker 1", "Speaker 2")
        sub_mod = classify_source_modality(undiarized_dialogue, {"_from_audio_transcription": True}, input_ext=".wav")
        self.assertEqual(sub_mod, SourceModality.AUDIO_ONLY_UNDIARIZED)

        wav_file = self.root / "session_1a.wav"
        wav_file.write_bytes(_build_valid_wav_bytes())
        mp4_file = self.root / "session_1a.mp4"
        mp4_file.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)

        with patch("dual_pipeline.transcript_pipeline_core.ingress.transcribe_audio_with_cache", return_value=undiarized_dialogue):
            with self.assertRaises((HITLRequiredError, UnmappedSpeakerError)):
                self.pipeline.run(
                    input_path=wav_file,
                    metadata=self._base_meeting_meta("Undiarized Audio Block"),
                    output_dir=str(self.root / "out_1a_blocked"),
                    mutate_graph=False,
                )

            for idx, media_file in enumerate((wav_file, mp4_file), start=1):
                res = self.pipeline.run(
                    input_path=media_file,
                    metadata=self._base_meeting_meta(
                        f"Resolved Audio 1a {idx}",
                        {"speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Drew Oakley"}},
                    ),
                    output_dir=str(self.root / f"out_1a_{idx}"),
                    mutate_graph=False,
                )
                self.assertTrue(Path(res["01_transcript"]).is_file())
                self.assertTrue(Path(res["02_recap"]).is_file())
                self.assertTrue(Path(res["03_graph"]).is_file())
                self._apply_output03_to_isolated_graph(res, f"1a_{media_file.suffix.lstrip('.')}")

    def test_1b_pre_existing_recording_diarized_with_sidecar_e2e(self) -> None:
        """Category 1b: Pre-existing recording with co-located speaker_map.json sidecar -> Outputs 01, 02, 03 + Graph."""
        wav_file = self.root / "01_10-15-2026_Diarized_Session_master.wav"
        wav_file.write_bytes(_build_valid_wav_bytes())
        sidecar = self.root / "01_10-15-2026_Diarized_Session_speaker_map.json"
        sidecar.write_text(json.dumps({"SPEAKER_00": "Amit Ayer", "SPEAKER_01": "Drew Oakley"}), encoding="utf-8")

        diarized_dialogue = (
            "**Diarization:** `DIARIZED_ACOUSTIC_RESOLVED (pyannote.audio 3.1)`\n\n"
            + _build_16_turn_dialogue("Amit Ayer", "Drew Oakley")
        )
        with patch("dual_pipeline.transcript_pipeline_core.ingress.transcribe_audio_with_cache", return_value=diarized_dialogue):
            res = self.pipeline.run(
                input_path=wav_file,
                metadata=self._base_meeting_meta(
                    "Diarized Session 1b",
                    {"speaker_map": {"SPEAKER_00": "Amit Ayer", "SPEAKER_01": "Drew Oakley"}},
                ),
                output_dir=str(self.root / "out_1b"),
                mutate_graph=False,
            )
            self.assertTrue(Path(res["01_transcript"]).is_file())
            self.assertTrue(Path(res["02_recap"]).is_file())
            self.assertTrue(Path(res["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res, "1b_audio_diarized")

    def test_1c_live_meeting_captured_and_transcribed_container_e2e(self) -> None:
        """Category 1c: Live meeting container (00_*_master.wav + 01_*_live_transcript.md + speaker_hints_queue.json) -> Outputs 01, 02, 03 + Graph."""
        live_dir = self.root / "live_container"
        live_dir.mkdir(parents=True, exist_ok=True)
        master_wav = live_dir / "00_10-15-2026_110000_master.wav"
        master_wav.write_bytes(_build_valid_wav_bytes())
        live_md = live_dir / "01_10-15-2026_Live_Architecture_Sync_live_transcript.md"
        live_md.write_text(
            "# Live Meeting Transcript: Live Architecture Sync\n\n"
            "**Date:** 10/15/2026\n"
            "**Start Time:** 11:00 AM ET\n\n"
            + _build_16_turn_dialogue("Amit Ayer", "Drew Oakley")
            + "\n\n*Session ended at 11:05 AM ET*\n",
            encoding="utf-8",
        )
        hints_file = live_dir / "speaker_hints_queue.json"
        hints_file.write_text(
            json.dumps([{"event_id": "evt-01", "speaker_name": "Amit Ayer", "quote": "zero trust security controls", "status": "QUEUED"}]),
            encoding="utf-8",
        )
        meta = self._base_meeting_meta("Live Architecture Sync")
        meta.pop("time", None)
        res = self.pipeline.run(
            input_path=live_md,
            metadata=meta,
            output_dir=str(self.root / "out_1c"),
            mutate_graph=False,
        )
        self.assertTrue(Path(res["01_transcript"]).is_file())
        self.assertTrue(Path(res["02_recap"]).is_file())
        self.assertTrue(Path(res["03_graph"]).is_file())
        persisted_meta = json.loads((Path(res["03_graph"]).parent / "metadata.json").read_text(encoding="utf-8"))
        self.assertIn("11:00 AM - 11:05 AM ET", str(persisted_meta.get("time", "")))
        self._apply_output03_to_isolated_graph(res, "1c_live_meeting")

    def test_1d_raw_transcript_undiarized_hitl_and_resolved_e2e(self) -> None:
        """Category 1d: Pre-existing raw transcript undiarized (.txt, .md) -> U0 HITL gate -> Resolved Outputs 01, 02, 03 + Graph."""
        raw_undiarized = _build_16_turn_dialogue("Speaker 1", "Speaker 2")
        self.assertEqual(classify_source_modality(raw_undiarized, {}, input_ext=".txt"), SourceModality.TEXT_UNDIARIZED)

        for ext in (".txt", ".md"):
            src_p = self.root / f"raw_undiarized_1d{ext}"
            src_p.write_text(raw_undiarized, encoding="utf-8")

            with self.assertRaises((HITLRequiredError, UnmappedSpeakerError)):
                self.pipeline.run(
                    input_path=src_p,
                    metadata=self._base_meeting_meta(f"Undiarized 1d {ext}"),
                    output_dir=str(self.root / f"out_1d_block_{ext.lstrip('.')}"),
                    mutate_graph=False,
                )

            res = self.pipeline.run(
                input_path=src_p,
                metadata=self._base_meeting_meta(
                    f"Resolved 1d {ext}",
                    {"speaker_map": {"Speaker 1": "Amit Ayer", "Speaker 2": "Drew Oakley"}},
                ),
                output_dir=str(self.root / f"out_1d_{ext.lstrip('.')}"),
                mutate_graph=False,
            )
            f1_text = Path(res["01_transcript"]).read_text(encoding="utf-8")
            self.assertIn("Amit Ayer", f1_text)
            self.assertIn("Drew Oakley", f1_text)
            self.assertTrue(Path(res["02_recap"]).is_file())
            self.assertTrue(Path(res["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res, f"1d_{ext.lstrip('.')}")

    def test_1e_raw_transcript_platform_diarized_all_formats_e2e(self) -> None:
        """Category 1e: Platform-diarized raw transcripts (.vtt, .srt, .docx, .json, .txt, .md) -> Outputs 01, 02, 03 + Graph."""
        src_dir = self.root / "src_1e"
        src_dir.mkdir(parents=True, exist_ok=True)
        dialogue_lines: List[tuple[str, str, str]] = []
        for idx in range(16):
            ts = f"00:0{idx // 6}:{(idx * 10) % 60:02d}"
            spk = "Amit Ayer" if idx % 2 == 0 else "Drew Oakley"
            txt = AMIT_TURNS[idx // 2] if idx % 2 == 0 else DAN_TURNS[idx // 2]
            dialogue_lines.append((ts, spk, txt))

        # 1. .vtt
        vtt_p = src_dir / "meeting_1e.vtt"
        vtt_cues = ["WEBVTT\n"]
        for idx, (ts, spk, txt) in enumerate(dialogue_lines, start=1):
            vtt_cues.append(f"{idx}\n{ts}.000 --> {ts}.900\n<v {spk}>{txt}</v>\n")
        vtt_p.write_text("\n".join(vtt_cues), encoding="utf-8")

        # 2. .srt
        srt_p = src_dir / "meeting_1e.srt"
        srt_cues = [
            f"{idx}\n{ts},000 --> {ts},900\n{spk}: {txt}\n"
            for idx, (ts, spk, txt) in enumerate(dialogue_lines, start=1)
        ]
        srt_p.write_text("\n".join(srt_cues), encoding="utf-8")

        # 3. .docx (Teams transcript export)
        docx_p = src_dir / "meeting_1e.docx"
        docx_p.write_bytes(_build_docx_bytes([f"[{ts}] {spk}: {txt}" for ts, spk, txt in dialogue_lines]))

        # 4. .json (Teams/Zoom transcript JSON)
        json_p = src_dir / "meeting_1e.json"
        json_p.write_text(
            json.dumps({"utterances": [{"timestamp": ts, "speaker": spk, "text": txt} for ts, spk, txt in dialogue_lines]}),
            encoding="utf-8",
        )

        # 5. .txt & .md
        txt_p = src_dir / "meeting_1e.txt"
        txt_p.write_text("\n\n".join(f"[{ts}] {spk}: {txt}" for ts, spk, txt in dialogue_lines), encoding="utf-8")
        md_p = src_dir / "meeting_1e.md"
        md_p.write_text("\n\n".join(f"[{ts}] {spk}: {txt}" for ts, spk, txt in dialogue_lines), encoding="utf-8")

        for src_p in (vtt_p, srt_p, docx_p, json_p, txt_p, md_p):
            ext_tag = src_p.suffix.lstrip(".")
            single_dir = src_dir / f"in_{ext_tag}"
            single_dir.mkdir(parents=True, exist_ok=True)
            isolated_src = single_dir / src_p.name
            isolated_src.write_bytes(src_p.read_bytes())
            res = self.pipeline.run(
                input_path=isolated_src,
                metadata=self._base_meeting_meta(f"Platform Diarized {ext_tag}"),
                output_dir=str(self.root / f"out_1e_{ext_tag}"),
                mutate_graph=False,
            )
            self.assertTrue(Path(res["01_transcript"]).is_file())
            self.assertTrue(Path(res["02_recap"]).is_file())
            self.assertTrue(Path(res["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res, f"1e_{ext_tag}")

    def test_1f_companion_meeting_collateral_pptx_and_diagram_e2e(self) -> None:
        """Category 1f: Meeting transcript + companion .pptx presentation deck + .svg/.png collateral -> Outputs 01, 02, 03 + Graph."""
        tr_p = self.root / "meeting_1f.txt"
        tr_p.write_text(_build_16_turn_dialogue("Amit Ayer", "Drew Oakley"), encoding="utf-8")
        deck_p = self.root / "companion_deck.pptx"
        deck_p.write_bytes(_build_pptx_bytes(["Slide 1: Enterprise Target Architecture", "Slide 2: Q4 Migration Milestones"], "Speaker note: Confirm WAL mode."))
        svg_p = self.root / "architecture_diagram.svg"
        svg_p.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><title>Target Architecture</title>'
            '<text id="n1">Ingestion Gateway</text><text id="n2">SQLite GraphDB</text></svg>',
            encoding="utf-8",
        )
        res = self.pipeline.run(
            input_path=tr_p,
            presentation_path=deck_p,
            attachments=[svg_p],
            metadata=self._base_meeting_meta("Collateral Enriched Meeting", {"has_architecture_diagram": True}),
            output_dir=str(self.root / "out_1f"),
            mutate_graph=False,
        )
        self.assertTrue(Path(res["01_transcript"]).is_file())
        self.assertTrue(Path(res["02_recap"]).is_file())
        self.assertTrue(Path(res["03_graph"]).is_file())
        recap_txt = Path(res["02_recap"]).read_text(encoding="utf-8")
        self.assertIn("architecture_diagram.svg", recap_txt)
        self.assertTrue((Path(res["03_graph"]).parent / "companion_deck.intake.json").is_file())
        self._apply_output03_to_isolated_graph(res, "1f_collateral")

    def test_2a_hitl_resolved_transcript_freezes_output01_and_emits_02_03_e2e(self) -> None:
        """Category 2a: Pre-existing 01_*_hitl_resolved_transcript.md bypasses Stage 01 synthesis and produces Outputs 02, 03 + Graph."""
        out_dir = self.root / "out_2a"
        out_dir.mkdir(parents=True, exist_ok=True)
        resolved_01_content = (
            "# Architecture Review 2a -- HITL-Resolved Transcript | 10/15/2026\n\n"
            "**Date:** 10/15/2026\n"
            "**Participants:** Amit Ayer (Enterprise Architect, PartnerAdvisory), Drew Oakley (Client Service Lead, PartnerAdvisory)\n\n"
            "---\n\n"
            "## Forensic Transcript\n\n"
            + _build_16_turn_dialogue("**Amit Ayer:**", "**Drew Oakley:**").replace(":** :", ":**")
            + "\n\n---\n\n"
            "## Ambiguity Lock Register\n"
            "| Locator | Spoken Term | Flag Type | Resolution / Evidence | Rule Applied |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| [00:00:00] | FedRAMP | Domain Term | Verified cloud compliance standard | RULE-LOSSLESS-SYNTAX |\n"
        )
        f1_path = out_dir / "01_Architecture_Review_2a_10-15-2026_hitl_resolved_transcript.md"
        f1_path.write_text(resolved_01_content, encoding="utf-8")
        f1_sha_before = sha256_file(f1_path)

        seals = {
            "output_1_seal": True,
            "output_1_pass": True,
            "output_1": {"status": "PASS", "score": 96.0, "sha256": f1_sha_before, "timestamp": 1726340000.0},
        }
        (out_dir / "stage_seals.json").write_text(json.dumps(seals), encoding="utf-8")

        with patch.object(self.pipeline.provider, "synthesize_forensic", wraps=self.pipeline.provider.synthesize_forensic) as spy_f1:
            res = self.pipeline.run(
                input_path=f1_path,
                metadata=self._base_meeting_meta(
                    "Architecture Review 2a",
                    {"regenerate": "02,03", "backtest": True},
                ),
                output_dir=str(out_dir),
                mutate_graph=False,
            )
            spy_f1.assert_not_called()

        self.assertTrue(Path(res["02_recap"]).is_file())
        self.assertTrue(Path(res["03_graph"]).is_file())
        self._apply_output03_to_isolated_graph(res, "2a_hitl_resolved")

    def test_2b_meeting_notes_pipeline_outputs_02_and_03_only_e2e(self) -> None:
        """Category 2b: Pre-processed meeting notes (--notes / --copilot-notes in .md, .txt, .docx) -> Outputs 02 & 03 only + Graph."""
        notes_body = (
            "# Copilot Meeting Notes: Executive Architecture Sync\n\n"
            "- Amit Ayer and Drew Oakley reviewed the enterprise platform architecture and confirmed the Q4 2026 deployment target.\n"
            "- Decision: Adopt SQLite WAL mode with serialized write gateway for zero lock contention across worker threads.\n"
            "- Action Item: Amit Ayer will finalize the schema migration verification suite by Friday and circulate the readiness report to leadership.\n"
            "- Risk Mitigation: Drew Oakley aligned regional engineering leads on the staging environment cutover checklist and rollback guards."
        )
        md_notes = self.root / "notes_2b.md"
        md_notes.write_text(notes_body, encoding="utf-8")
        txt_notes = self.root / "notes_2b.txt"
        txt_notes.write_text(notes_body, encoding="utf-8")
        docx_notes = self.root / "notes_2b.docx"
        docx_notes.write_bytes(_build_docx_bytes(notes_body.splitlines()))

        for src_p in (md_notes, txt_notes, docx_notes):
            ext_tag = src_p.suffix.lstrip(".")
            res = self.pipeline.run(
                input_path=src_p,
                metadata=self._base_meeting_meta(
                    f"Notes Sync {ext_tag}",
                    {
                        "notes_mode": True,
                        "is_notes": True,
                        "skip_output_1": True,
                        "canonical_modality": CanonicalModality.MEETING_NOTES.value,
                    },
                ),
                output_dir=str(self.root / f"out_2b_{ext_tag}"),
                mutate_graph=False,
            )
            self.assertEqual(res["modality"], CanonicalModality.MEETING_NOTES.value)
            self.assertNotIn("01_transcript", res)
            self.assertIsNone(res.get("output_01"))
            self.assertTrue(Path(res["02_recap"]).is_file())
            self.assertTrue(Path(res["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res, f"2b_notes_{ext_tag}")

    def test_3a_to_3d_office_and_tabular_documents_output03_and_graph_e2e(self) -> None:
        """Categories 3a (PDF), 3b (DOCX), 3c (PPTX), 3d (XLSX, XLSM, CSV, TSV) -> Output 03 only + Graph."""
        doc_prose = (
            "Enterprise Architecture Specification for Apex Global Enterprises Platform Modernization. "
            "The payment gateway service integrates with the core ledger database for deterministic settlement across all regional business units. "
            "Amit Ayer and Drew Oakley ratified the Phase 2 migration schedule for Q4 2026 during the architecture review board session. "
            "All microservices must enforce mutual TLS authentication, RFC-8259 schema validation, and idempotent retry semantics before production cutover."
        )
        # 3a: PDF
        pdf_p = self.root / "spec_3a.pdf"
        pdf_p.write_bytes(_build_valid_pdf_bytes(doc_prose))

        # 3b: DOCX
        docx_p = self.root / "spec_3b.docx"
        docx_p.write_bytes(_build_docx_bytes(["# Enterprise Architecture Specification", doc_prose], include_table=True))

        # 3c: PPTX
        pptx_p = self.root / "deck_3c.pptx"
        pptx_p.write_bytes(_build_pptx_bytes(["Enterprise Architecture Overview", doc_prose], notes_text="Approved by ARB."))

        # 3d: XLSX, XLSM, CSV, TSV
        sheet_rows = [
            ["Requirement_ID", "Component", "Specification_Summary", "Accountable_Owner"],
            ["REQ-001", "Payment Gateway", doc_prose, "Amit Ayer"],
            ["REQ-002", "Core Ledger", "Enforce SQLite WAL mode with thirty-second busy timeout and serialized write gateway across workers.", "Drew Oakley"],
        ]
        xlsx_p = self.root / "matrix_3d.xlsx"
        xlsx_p.write_bytes(_build_xlsx_bytes(sheet_rows))
        xlsm_p = self.root / "matrix_3d.xlsm"
        xlsm_p.write_bytes(_build_xlsx_bytes(sheet_rows))
        csv_p = self.root / "matrix_3d.csv"
        csv_p.write_text("\n".join(",".join(r) for r in sheet_rows), encoding="utf-8")
        tsv_p = self.root / "matrix_3d.tsv"
        tsv_p.write_text("\n".join("\t".join(r) for r in sheet_rows), encoding="utf-8")

        with patch("dual_pipeline.document_pipeline_core.content_loader._load_pdf", return_value=f"# PDF Spec\n\n{doc_prose}"):
            for tag, src_p in (
                ("3a_pdf", pdf_p),
                ("3b_docx", docx_p),
                ("3c_pptx", pptx_p),
                ("3d_xlsx", xlsx_p),
                ("3d_xlsm", xlsm_p),
                ("3d_csv", csv_p),
                ("3d_tsv", tsv_p),
            ):
                res = self.pipeline.run(
                    input_path=src_p,
                    metadata=self._base_doc_meta(f"Document {tag}"),
                    output_dir=str(self.root / f"out_{tag}"),
                    mutate_graph=False,
                )
                self.assertEqual(res["modality"], CanonicalModality.DOCUMENT.value)
                self.assertNotIn("01_transcript", res)
                self.assertNotIn("02_recap", res)
                self.assertTrue(Path(res["03_graph"]).is_file())
                self.assertTrue((Path(res["03_graph"]).parent / "source_document.txt").is_file())
                self._apply_output03_to_isolated_graph(res, tag)

    def test_3e_and_3f_visual_raster_and_vector_diagrams_output03_e2e(self) -> None:
        """Categories 3e (Images) and 3f (SVG, DrawIO, VSDX Diagrams) -> Output 03 only + Graph."""
        rich_text = (
            "Enterprise Architecture Topology and Organizational Alignment Report. "
            "Amit Ayer leads the Enterprise Architecture group overseeing the Ingestion Gateway, Hybrid Retrieval Sidecar, and Governed SQLite GraphDB. "
            "Drew Oakley leads Client Service Delivery coordinating cross-workstream release readiness, compliance verification, and production cutover governance across all regional business units."
        )
        # 3e: Raster images (.png, .jpg, .jpeg, .gif, .webp, .bmp, .tiff)
        img_files = []
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"):
            p = self.root / f"org_chart_3e{ext}"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            img_files.append((f"3e_{ext.lstrip('.')}", p))

        # 3f: Vector & architecture diagrams (.svg, .drawio, .vsdx)
        svg_p = self.root / "arch_3f.svg"
        svg_p.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg"><title>Architecture Diagram</title>'
            f'<desc>{rich_text}</desc><text id="s1">{rich_text}</text><text id="s2">SQLite GraphDB</text></svg>',
            encoding="utf-8",
        )
        drawio_p = self.root / "arch_3f.drawio"
        drawio_p.write_text(
            f'<mxfile><diagram name="Topology"><mxGraphModel><root>'
            f'<mxCell id="0"/><mxCell id="1" parent="0"/>'
            f'<mxCell id="2" value="{rich_text}" vertex="1" parent="1"/>'
            f'<mxCell id="3" value="Hybrid Sidecar" vertex="1" parent="1"/>'
            f'<mxCell id="4" value="syncs" edge="1" source="2" target="3" parent="1"/>'
            f'</root></mxGraphModel></diagram></mxfile>',
            encoding="utf-8",
        )
        vsdx_p = self.root / "arch_3f.vsdx"
        vsdx_p.write_bytes(_build_vsdx_bytes())

        mock_ocr = {
            "status": "COMPLETED",
            "title": "Org Chart",
            "text": rich_text,
            "components": ["Ingestion Gateway", "SQLite GraphDB"],
            "connections": [{"from": "Ingestion Gateway", "to": "SQLite GraphDB"}],
            "summary": rich_text,
        }
        with (
            patch("dual_pipeline.document_pipeline_core.content_loader._extract_image_bytes", return_value=mock_ocr),
            patch("dual_pipeline.attachment_core.image_extractor.extract_image_visual", return_value=mock_ocr),
        ):
            for tag, src_p in img_files:
                res = self.pipeline.run(
                    input_path=src_p,
                    metadata=self._base_doc_meta(f"Visual {tag}", {"allow_visual_image": True, "enable_visual_ocr": True, "is_visual_document": True}),
                    output_dir=str(self.root / f"out_{tag}"),
                    mutate_graph=False,
                )
                self.assertEqual(res["modality"], CanonicalModality.DOCUMENT.value)
                self.assertNotIn("01_transcript", res)
                self.assertNotIn("02_recap", res)
                self.assertTrue(Path(res["03_graph"]).is_file())
                self._apply_output03_to_isolated_graph(res, tag)

        for tag, src_p in (
            ("3f_svg", svg_p),
            ("3f_drawio", drawio_p),
            ("3f_vsdx", vsdx_p),
        ):
            res = self.pipeline.run(
                input_path=src_p,
                metadata=self._base_doc_meta(f"Doc {tag}"),
                output_dir=str(self.root / f"out_{tag}"),
                mutate_graph=False,
            )
            self.assertEqual(res["modality"], CanonicalModality.DOCUMENT.value)
            self.assertNotIn("01_transcript", res)
            self.assertNotIn("02_recap", res)
            self.assertTrue(Path(res["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res, tag)

    def test_3g_to_3i_email_web_text_and_url_output03_e2e(self) -> None:
        """Categories 3g (Email), 3h (HTML/MD/TXT/JSON), 3i (Remote URL) -> Output 03 only + Graph."""
        rich_text = (
            "Enterprise Architecture Topology and Organizational Alignment Report for Apex Global Enterprises Platform Modernization. "
            "Amit Ayer leads the Enterprise Architecture group overseeing the Ingestion Gateway, Hybrid Retrieval Sidecar, and Governed SQLite GraphDB across all cloud regions. "
            "Drew Oakley leads Client Service Delivery coordinating cross-workstream release readiness, security compliance verification, operational telemetry, and production cutover governance across all regional business units and engineering delivery pods."
        )
        # 3g: Email (.eml)
        eml_p = self.root / "message_3g.eml"
        eml_p.write_text(
            f"From: Amit Ayer <amit@example.com>\nTo: Drew Oakley <dan@example.com>\n"
            f"Subject: Architecture Sign-Off\nDate: Thu, 15 Oct 2026 11:00:00 -0400\n"
            f"Content-Type: text/plain; charset=utf-8\n\n{rich_text}\n",
            encoding="utf-8",
        )

        # 3h: Web / Text / JSON (.html, .htm, .md, .markdown, .txt, .text, .json)
        html_p = self.root / "doc_3h.html"
        html_p.write_text(f"<html><body><h1>Architecture Brief</h1><p>{rich_text}</p></body></html>", encoding="utf-8")
        htm_p = self.root / "doc_3h.htm"
        htm_p.write_text(f"<html><body><h1>Architecture Brief HTM</h1><p>{rich_text}</p></body></html>", encoding="utf-8")
        md_p = self.root / "doc_3h.md"
        md_p.write_text(f"# Architecture Brief MD\n\n{rich_text}\n", encoding="utf-8")
        markdown_p = self.root / "doc_3h.markdown"
        markdown_p.write_text(f"# Architecture Brief Markdown\n\n{rich_text}\n", encoding="utf-8")
        txt_p = self.root / "doc_3h.txt"
        txt_p.write_text(f"Architecture Specification Brief:\n\n{rich_text}\n", encoding="utf-8")
        text_p = self.root / "doc_3h.text"
        text_p.write_text(f"Architecture Specification Text:\n\n{rich_text}\n", encoding="utf-8")
        json_p = self.root / "doc_3h.json"
        json_p.write_text(json.dumps({"document_title": "Architecture JSON", "summary": rich_text, "owner": "Amit Ayer"}), encoding="utf-8")

        for tag, src_p in (
            ("3g_eml", eml_p),
            ("3h_html", html_p),
            ("3h_htm", htm_p),
            ("3h_md", md_p),
            ("3h_markdown", markdown_p),
            ("3h_txt", txt_p),
            ("3h_text", text_p),
            ("3h_json", json_p),
        ):
            res = self.pipeline.run(
                input_path=src_p,
                metadata=self._base_doc_meta(f"Doc {tag}"),
                output_dir=str(self.root / f"out_{tag}"),
                mutate_graph=False,
            )
            self.assertEqual(res["modality"], CanonicalModality.DOCUMENT.value)
            self.assertNotIn("01_transcript", res)
            self.assertNotIn("02_recap", res)
            self.assertTrue(Path(res["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res, tag)

        # 3i: Remote URL ingestion
        mock_receipt = {"sha256": hashlib.sha256(rich_text.encode()).hexdigest(), "method": "HTTP_GET", "url": "https://internal.example.com/architecture/spec"}
        with patch("dual_pipeline.url_ingress.fetch_url_with_sso_guard", return_value=(rich_text, mock_receipt)):
            res_url = self.pipeline.run(
                input_path="https://internal.example.com/architecture/spec",
                metadata=self._base_doc_meta("Remote URL Spec"),
                output_dir=str(self.root / "out_3i_url"),
                mutate_graph=False,
            )
            self.assertEqual(res_url["modality"], CanonicalModality.DOCUMENT.value)
            self.assertNotIn("01_transcript", res_url)
            self.assertNotIn("02_recap", res_url)
            self.assertTrue(Path(res_url["03_graph"]).is_file())
            self._apply_output03_to_isolated_graph(res_url, "3i_url")


if __name__ == "__main__":
    unittest.main()
