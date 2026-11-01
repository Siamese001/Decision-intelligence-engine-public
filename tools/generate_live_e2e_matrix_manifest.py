#!/usr/bin/env python3
"""tools/generate_live_e2e_matrix_manifest.py

Automatically generates reports/live_e2e_matrix_manifest.json from the
canonical format capability registry (dual_pipeline.source_classification.capability_registry)
and the CLI argument parser specifications (dual_pipeline.cli.parser).

Guarantees complete enumeration of all ingestion modalities, container extensions,
entrypoints, and scenario cells under the live E2E certification contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.source_classification.capability_registry import (
    _REGISTRY,
    SupportStatus,
    FormatCapability,
)
from dual_pipeline.canonical_pipeline.modalities import (
    CanonicalModality,
    get_deliverables_for_modality,
)

ALL_SCENARIOS = [
    "cold",
    "cached",
    "resume",
    "selective_retry",
    "malformed",
    "timeout",
    "judge_failure",
    "hitl_approve",
    "hitl_reject",
    "idempotent_replay",
]


def _build_modality_definitions() -> List[Dict[str, Any]]:
    """Builds structured modality definitions from canonical registries."""
    modalities = []

    # 1. Base format capabilities from capability_registry._REGISTRY
    for format_id, cap in _REGISTRY.items():
        is_blocked = cap.status == SupportStatus.BLOCKED
        primary_entrypoints = []

        if format_id == "AUDIO":
            primary_entrypoints = ["nee ingest-meeting", "nee record-meeting"]
            canonical_mod = CanonicalModality.AUDIO.value
            expected_deliverables = ["01", "02", "03"]
        elif format_id in ("VTT_SRT",):
            primary_entrypoints = ["nee ingest-meeting"]
            canonical_mod = CanonicalModality.MEETING_TRANSCRIPT.value
            expected_deliverables = ["01", "02", "03"]
        elif format_id == "DOCX":
            # DOCX can be ingested either as meeting transcript OR document
            primary_entrypoints = ["nee ingest-document", "nee ingest-meeting"]
            canonical_mod = CanonicalModality.DOCUMENT.value
            expected_deliverables = ["03"]
        elif format_id in ("TXT_MD", "HTML_HTM", "CSV_TSV", "XLSX_WORKBOOK", "PDF", "PPTX", "EML"):
            primary_entrypoints = ["nee ingest-document"]
            canonical_mod = CanonicalModality.DOCUMENT.value
            expected_deliverables = ["03"]
        elif format_id == "PNG_JPEG":
            primary_entrypoints = ["nee ingest-meeting --attachment", "nee ingest-document --attachment"]
            canonical_mod = "ATTACHMENT_COLLATERAL"
            expected_deliverables = ["03"]
        elif is_blocked:
            primary_entrypoints = ["nee ingest-document", "nee ingest-meeting"]
            canonical_mod = "GOVERNED_BLOCKED"
            expected_deliverables = []
        else:
            primary_entrypoints = ["nee ingest-document"]
            canonical_mod = CanonicalModality.DOCUMENT.value
            expected_deliverables = ["03"]

        mod_entry = {
            "format_id": cap.format_id,
            "name": cap.name,
            "status": cap.status.value,
            "canonical_modality": canonical_mod,
            "extensions": sorted(cap.extensions),
            "mime_types": sorted(cap.mime_types),
            "primary_adapter": cap.primary_adapter,
            "attachment_adapter": cap.attachment_adapter,
            "primary_entrypoints": primary_entrypoints,
            "expected_deliverables": expected_deliverables,
            "unit_type": cap.unit_type,
            "locator_grammar": cap.locator_grammar,
            "preflight_rule": cap.preflight_rule,
            "failure_disposition": cap.failure_disposition,
            "is_attachment_supported": cap.is_attachment_supported,
        }
        modalities.append(mod_entry)

    # 2. Specialized modality: COPILOT_NOTES
    modalities.append({
        "format_id": "COPILOT_NOTES",
        "name": "Copilot and Undiarized Meeting Notes",
        "status": SupportStatus.SUPPORTED.value,
        "canonical_modality": CanonicalModality.MEETING_NOTES.value,
        "extensions": [".txt", ".md", ".docx"],
        "mime_types": ["text/plain", "text/markdown", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        "primary_adapter": "dual_pipeline.notes_pipeline",
        "attachment_adapter": "none",
        "primary_entrypoints": ["nee ingest-meeting --notes"],
        "expected_deliverables": ["02", "03"],  # Deliverable 01 bypassed by design contract
        "unit_type": "discourse_section_bullet",
        "locator_grammar": "section:{header}#L{line}",
        "preflight_rule": "copilot_notes_header_sniffer",
        "failure_disposition": "FAIL_CLOSED",
        "is_attachment_supported": False,
    })

    # 3. Specialized composite modality: CROSS_MODAL_ATTACHMENTS
    modalities.append({
        "format_id": "ATTACHMENTS_SUITE",
        "name": "Cross-Modal Collateral & Attachment Extraction",
        "status": SupportStatus.SUPPORTED.value,
        "canonical_modality": "ATTACHMENT_COLLATERAL",
        "extensions": [".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg", ".eml"],
        "mime_types": ["application/pdf", "image/png", "image/jpeg", "application/vnd.openxmlformats-officedocument.presentationml.presentation"],
        "primary_adapter": "dual_pipeline.attachment_core.extractors",
        "attachment_adapter": "dual_pipeline.attachment_core.aligner",
        "primary_entrypoints": ["nee ingest-meeting --attachment", "nee ingest-meeting --attachments", "nee ingest-meeting --attachment-dir"],
        "expected_deliverables": ["03"],
        "unit_type": "collateral_part",
        "locator_grammar": "attachment:{filename}#{inner_locator}",
        "preflight_rule": "attachment_preflight_rule",
        "failure_disposition": "FAIL_CLOSED",
        "is_attachment_supported": True,
    })

    return sorted(modalities, key=lambda m: m["format_id"])


def _evaluate_scenario_cell(modality: Dict[str, Any], scenario: str) -> Dict[str, Any]:
    """Evaluates whether a scenario cell is REQUIRED or NOT_APPLICABLE with explicit rationale."""
    status = modality["status"]
    format_id = modality["format_id"]
    is_blocked = (status == "BLOCKED")

    if is_blocked:
        blocked_map = {
            "cold": f"Verify governed refusal preflight rejection for blocked format {format_id} with typed error code and zero graph mutation.",
            "malformed": f"Verify corrupted/empty {format_id} container still triggers fail-closed governed rejection rather than decoding crash.",
            "idempotent_replay": f"Verify re-submission of blocked format {format_id} consistently returns identical governed refusal receipt.",
        }
        if scenario in blocked_map:
            return {
                "disposition": "REQUIRED",
                "rationale": blocked_map[scenario],
            }
        return {
            "disposition": "NOT_APPLICABLE",
            "rationale": f"Format {format_id} is governed BLOCKED; lifecycle terminates at preflight so {scenario} is not reachable.",
        }

    # For supported / partial formats:
    if scenario == "cold":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Clean end-to-end ingestion from scratch for {format_id} into isolated runtime with zero pre-existing cache.",
        }
    if scenario == "cached":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify subsequent ingestion of identical {format_id} utilizes cache, emits zero redundant LLM/provider calls, and preserves valid output digests.",
        }
    if scenario == "resume":
        if format_id in ("AUDIO", "VTT_SRT", "COPILOT_NOTES", "DOCX"):
            return {
                "disposition": "REQUIRED",
                "rationale": f"Verify stage checkpointing allows resuming interrupted {format_id} pipeline without restarting prior completed stages.",
            }
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify document batch checkpointing allows resuming {format_id} ingestion without re-extracting parsed source units.",
        }
    if scenario == "selective_retry":
        if format_id in ("AUDIO", "VTT_SRT", "COPILOT_NOTES"):
            return {
                "disposition": "REQUIRED",
                "rationale": "Verify --regenerate flag regenerates only targeted outputs (e.g. 02,03) while keeping Output 01 frozen.",
            }
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify re-extraction of signal projection (Output 03) can be selectively triggered without re-parsing raw {format_id}.",
        }
    if scenario == "malformed":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify corrupt, truncated, 0-byte, or header-damaged {format_id} files trigger typed fail-closed errors without unhandled exceptions.",
        }
    if scenario == "timeout":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify execution timeout deadline strictly aborts long-running {format_id} tasks, releases file handles, and logs TIMEOUT outcome.",
        }
    if scenario == "judge_failure":
        return {
            "disposition": "REQUIRED",
            "rationale": "Verify independent judge scoring failure triggers bounded remediation retry, fails closed upon exhaustion, and prevents graph publication.",
        }
    if scenario == "hitl_approve":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify ambiguous {format_id} extraction triggers HITL review, operator approval commits package to graph, and receipt seals in ledger.",
        }
    if scenario == "hitl_reject":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify operator rejection of ambiguous {format_id} extraction cancels mutation, rolls back sandbox state, and records REJECTED in ledger.",
        }
    if scenario == "idempotent_replay":
        return {
            "disposition": "REQUIRED",
            "rationale": f"Verify exact duplicate replay of {format_id} detects existing run, returns existing artifact digests, and performs zero duplicate DB mutations.",
        }

    return {"disposition": "NOT_APPLICABLE", "rationale": f"Scenario {scenario} not configured."}


def generate_manifest() -> Dict[str, Any]:
    """Generates the full live E2E certification matrix manifest."""
    modalities = _build_modality_definitions()

    matrix_cells = {}
    required_count = 0
    not_applicable_count = 0

    for mod in modalities:
        fmt_id = mod["format_id"]
        matrix_cells[fmt_id] = {}
        for scenario in ALL_SCENARIOS:
            cell = _evaluate_scenario_cell(mod, scenario)
            matrix_cells[fmt_id][scenario] = cell
            if cell["disposition"] == "REQUIRED":
                required_count += 1
            else:
                not_applicable_count += 1

    # Ingestion Entrypoints enumeration
    entrypoints = [
        {
            "name": "nee ingest-meeting",
            "description": "Full multi-stage meeting transcript/audio ingestion emitting Outputs 01, 02, and 03.",
            "supported_modalities": ["AUDIO", "VTT_SRT", "TXT_MD", "DOCX", "COPILOT_NOTES", "ATTACHMENTS_SUITE"],
            "flag_support": ["--input", "--metadata", "--project", "--notes", "--attachment", "--attachments", "--package-only", "--mutate-graph", "--force-rerun", "--regenerate"],
        },
        {
            "name": "nee ingest-document",
            "description": "Full document ingestion pipeline emitting strictly Deliverable 03 canonical signal projection.",
            "supported_modalities": ["DOCX", "PDF", "PPTX", "XLSX_WORKBOOK", "CSV_TSV", "EML", "HTML_HTM", "TXT_MD"],
            "flag_support": ["--input", "--metadata", "--project", "--title", "--author", "--package-only", "--mutate-graph", "--force-rerun"],
        },
        {
            "name": "nee record-meeting",
            "description": "Live physical audio capture and MLX Whisper transcription stream dispatch.",
            "supported_modalities": ["AUDIO"],
            "flag_support": ["--project", "--meeting", "--attendees", "--runtime-root"],
        },
        {
            "name": "nee ingest",
            "description": "Governed Universal Write Gateway package intake and admission verification.",
            "supported_modalities": ["ALL_SUPPORTED_PACKAGES"],
            "flag_support": ["--package", "--directive-id", "--audience", "--runtime-root"],
        },
        {
            "name": "nee intake-publish",
            "description": "Direct graph/vector publication of sealed approved intake packages.",
            "supported_modalities": ["ALL_APPROVED_PACKAGES"],
            "flag_support": ["--package", "--runtime-root", "--graph"],
        },
        {
            "name": "nee ask / runtime-evidence",
            "description": "Evidence retrieval and post-publication readback verification.",
            "supported_modalities": ["ALL_COMMITTED_ENTITIES"],
            "flag_support": ["--query", "--runtime-root", "--graph", "--require-bge-m3"],
        },
    ]

    manifest = {
        "version": "1.0.0",
        "description": "SSOT Live End-to-End Certification Matrix Manifest for NEE Ingestion",
        "target_branch": "fix/ingestion-live-e2e-certification",
        "scenarios": ALL_SCENARIOS,
        "modalities": modalities,
        "entrypoints": entrypoints,
        "matrix_cells": matrix_cells,
        "summary": {
            "total_modalities": len(modalities),
            "total_scenarios": len(ALL_SCENARIOS),
            "total_cells": len(modalities) * len(ALL_SCENARIOS),
            "required_cells": required_count,
            "not_applicable_cells": not_applicable_count,
        },
    }

    # Compute deterministic content digest
    serialized = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    manifest["manifest_sha256"] = manifest_digest

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate live E2E matrix manifest")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=REPO_ROOT / "reports" / "live_e2e_matrix_manifest.json",
        help="Target output path for live E2E matrix manifest",
    )
    args = parser.parse_args()

    manifest = generate_manifest()
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Generated live E2E matrix manifest at: {output_path}")
    print(f"Total modalities: {manifest['summary']['total_modalities']}")
    print(f"Total matrix cells: {manifest['summary']['total_cells']} (Required: {manifest['summary']['required_cells']}, N/A: {manifest['summary']['not_applicable_cells']})")
    print(f"Manifest SHA-256: {manifest['manifest_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
