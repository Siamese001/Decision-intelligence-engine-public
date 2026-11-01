#!/usr/bin/env python3
"""
tools/lens_progress_renderer.py

Markdown dashboard renderer for Antigravity IDE live UI progress tracking.
Renders live ASCII progress bars and granular sub-step tables for each output.
"""
from __future__ import annotations

import datetime
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

GATE_DEFINITIONS: Dict[str, List[Tuple[str, str, str]]] = {
    "output_1": [
        ("1.1", "Immutable Source Binding & Turn Ordering", "Rule C-01 (Verifiable source locators)"),
        ("1.2", "Structured Attribution Hygiene & Roster Verification", "Attendee roster alignment (Amit, Jake, Miles)"),
        ("1.3", "Temporal Strictness & Header Date (MM/DD/YYYY)", "Header date format & zero ISO-8601 leaks"),
        ("1.4", "Source-Derived Vocabulary & Anti-Hallucination", "Lexical grounding in verbatim audio tokens"),
        ("1.5", "Timestamp Continuity & Gap Annotations", "Monotonicity & [Natural pause] for gaps >32s"),
        ("1.6", "Duration Turn Density & Cadence Audit", "Spoken turn density across 56-minute meeting"),
        ("1.7", "Ambiguity Lock Register & Speaker HITL Alignment", "Cross-linking ambiguous turns to ALR"),
        ("1.8", "Attribution Summary Presence & Metrics Audit", "Attribution percentages and word counts"),
        ("1.9", "ALR Row Count Minimum (≥3 verified entries)", "10 calibrated ambiguity entries locked"),
    ],
    "output_2": [
        ("2.1", "Parsed Structural Completeness & Core Sections", "Subject, Date, Time, Attendees, Objective, Next Steps"),
        ("2.2", "Typed Action Register with Owners & Deadlines", "Action items table with explicit owners and dates"),
        ("2.3", "Evidence Lineage & Epistemic Status Grounding", "Epistemic tagging (fact/decision/proposal)"),
        ("2.4", "Archetype Governance (Architecture Deep Dive)", "LENS relaxed model & data accuracy architecture"),
        ("2.5", "Word Count Bounds & Executive Altitude", "Concise executive synthesis without fluff"),
        ("2.6", "Strategic Synthesis Matrix & Trade-off Lenses", "Explicit comparison of architectural pathways"),
        ("2.7", "Deep Section Density & Architectural Specifics", "Model confidence thresholding & pipeline flows"),
        ("2.8", "Appendix Completeness & Raw Context Lineage", "Detailed technical appendices and discussion points"),
        ("2.9", "Collateral Architecture Fidelity & System Landscape", "Project LENS integration points and data pipelines"),
        ("2.10", "Plain English Compliance & Jargon Sanitization", "Anti-word-salad linter & readable syntax"),
        ("2.11", "Numeric Tracing & Quantitative Calibration", "56m duration, confidence metrics, date bounds"),
    ],
    "output_3": [
        ("3.1", "Strict Schema Validation (V5.7 Pydantic / RFC-8259)", "RFC-8259 UTF-8 canonical JSON conformance"),
        ("3.2", "Semantic Spine & Ontology Conformance", "W2 Semantic Spine types & allowed prefixes"),
        ("3.3", "Temporal Hygiene & Lifecycle Consistency", "Strict MM/DD/YYYY observed_at & stale_after"),
        ("3.4", "Evidence Index Fidelity & Character Grounding", "Verbatim transcript excerpt locators"),
        ("3.5", "Signal Amplification & Anti-Sprawl Budget", "Promoted mutations within governed ratio"),
        ("3.6", "Conversational Fragment Suppression", "Zero conversational fragments or filler claims"),
        ("3.7", "Compound Property Linter & Atomic Predicates", "Atomic signal decomposition without overloading"),
        ("3.8", "Citation Coverage Linter & Exact Source Matching", "100% locator match in source transcript"),
        ("3.9", "Predicate Domain Linter & W2 Relationships", "Canonical W2 ontology relationship predicates"),
        ("3.10", "Mutation Budget Governor & Signal Identity", "Idempotent signal IDs & collision-free hashing"),
    ],
    "cross_artifact": [
        ("Cross.1", "Entity Alignment Across Triad", "Amit Ayer, Jake Stark, Miles Erickson consistency"),
        ("Cross.2", "Temporal Consistency (09/18/2026)", "Header date, filesystem date, and metadata sync"),
        ("Cross.3", "Action Item Coverage & Next Steps Alignment", "Executive recap next steps match graph actions"),
        ("Cross.4", "Career Firewall Integrity", "Zero career/positioning notes in team deliverables"),
        ("Cross.5", "Collateral Evidence Propagation", "Direct citation cross-reference across 01/02/03"),
    ],
    "judge_and_sealing": [
        ("Judge.1", "Output 01 Forensic Fidelity & Attribution", "Dialectical evaluation of transcript accuracy"),
        ("Judge.2", "Output 02 Executive Synthesis & Clarity", "Executive value, plain English, and technical depth"),
        ("Judge.3", "Output 03 Graph Signal Extraction & Ontology", "Semantic graph representation quality"),
        ("Judge.4", "Triad Epistemic Alignment & Dialectical Verdict", "Global consistency score & release qualification"),
        ("Seal.1", "Deterministic QA Gates Report Sealing", "qa_gates_report.json generated and verified"),
        ("Seal.2", "LLM Judge Evaluation Digest Sealing", "llm_judge_evaluation.json digest-verified"),
        ("Seal.3", "Authoritative Release Decision Sealing", "final_release_decision.json authoritative seal"),
        ("Seal.4", "Evidence Envelope Intake Package Creation", "intake_package.json validated against schema"),
    ],
}



def make_progress_bar(pct: int, length: int = 20) -> str:
    filled = max(0, min(length, int(length * (pct / 100.0))))
    return "█" * filled + "░" * (length - filled)


def render_terminal_dashboard(
    step_idx: int,
    total_steps: int,
    current_stage_name: str,
    stages: List[Dict[str, Any]],
    output_status: Dict[str, Dict[str, Any]],
    elapsed_sec: float,
    model_name: str,
    reasoning_effort: str,
    meeting_dir: Optional[Path] = None,
    fenced: bool = False,
) -> str:
    """Renders an in-band monospaced ASCII progress dashboard for IDE terminal/stream execution."""
    overall_pct = int((step_idx / max(1, total_steps)) * 100)
    overall_bar = make_progress_bar(overall_pct, length=24)
    mins, secs = divmod(int(elapsed_sec), 60)
    time_str = f"{mins:02d}:{secs:02d}"
    target_str = meeting_dir.name if meeting_dir else "target_directory"

    lines = [
        "=" * 88,
        "🚀 PROJECT LENS THREE-OUTPUT SYNTHESIS & QUALIFICATION ENGINE",
        "=" * 88,
        f"Target: {target_str} | Model: {model_name} ({reasoning_effort})",
        f"Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Elapsed: {time_str}",
        "",
        f"Overall Pipeline Progress: [{overall_bar}] {overall_pct}% (Stage {step_idx}/{total_steps}: {current_stage_name})",
        "",
        "-" * 88,
        "DELIVERABLE VERIFICATION & GATE QUALIFICATION",
        "-" * 88,
    ]

    for key, info in output_status.items():
        o_pct = info.get("pct", 0)
        o_bar = make_progress_bar(o_pct, length=18)
        passed_count = info.get("gates_passed", 0)
        total_count = info.get("gates_total", 0)
        status_icon = "🟢" if info.get("passed") else ("🔄" if info.get("running") else "⏳")
        status_label = "PASS" if info.get("passed") else ("RUNNING" if info.get("running") else "QUEUED")
        title = info.get("title", key)
        padded_title = f"{title:<40}"
        lines.append(
            f"{status_icon} {padded_title} : [{o_bar}] {o_pct:>3}% | {passed_count}/{total_count} Steps ({status_label})"
        )

    lines.extend([
        "",
        "-" * 88,
        "PIPELINE STAGES EXECUTION MATRIX",
        "-" * 88,
    ])
    for s in stages:
        s_icon = "🟢 PASS" if s.get("status") == "PASS" else ("🔄 RUNNING" if s.get("status") == "RUNNING" else "⏳ PENDING")
        s_name = s.get("name", "")
        s_target = s.get("target", "")
        s_elapsed = s.get("elapsed", "--")
        lines.append(f"  {s['idx']}. {s_name:<38} [{s_target:<18}] : {s_icon:<10} ({s_elapsed})")

    lines.append("=" * 88)
    dashboard_text = "\n".join(lines)
    if fenced:
        return f"```text\n{dashboard_text}\n```"
    return dashboard_text


def render_live_markdown(
    step_idx: int,
    total_steps: int,
    current_stage_name: str,
    stages: List[Dict[str, Any]],
    output_status: Dict[str, Dict[str, Any]],
    elapsed_sec: float,
    model_name: str,
    reasoning_effort: str,
    meeting_dir: Path,
) -> str:
    overall_pct = int((step_idx / max(1, total_steps)) * 100)
    overall_bar = make_progress_bar(overall_pct, length=24)
    mins, secs = divmod(int(elapsed_sec), 60)
    time_str = f"{mins:02d}:{secs:02d}"

    lines = [
        "# Project LENS Synthesis Pipeline — Live Progress Dashboard",
        "",
        f"**Local Time:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | **Elapsed:** `{time_str}` | **Model:** `{model_name}` (`{reasoning_effort}`)",
        "",
        f"### Overall Pipeline Progress: `{overall_pct}%`",
        "```text",
        f"[{overall_bar}] {overall_pct}% | Stage {step_idx}/{total_steps}: {current_stage_name}",
        "```",
        "",
        "> [!IMPORTANT]",
        f"> **Current Stage [{step_idx}/{total_steps}]:** {current_stage_name}  ",
        f"> **Target Subfolder:** [`{meeting_dir.name}`](file://{meeting_dir})",
        "",
        "## Deliverable-by-Deliverable Progress & Granular Verification Steps",
        "",
    ]

    for key, info in output_status.items():
        o_pct = info.get("pct", 0)
        o_bar = make_progress_bar(o_pct, length=18)
        passed_count = info.get("gates_passed", 0)
        total_count = info.get("gates_total", 0)
        status_icon = "🟢" if info.get("passed") else ("🔄" if info.get("running") else "⏳")
        lines.append(f"### {status_icon} {info.get('title', key)}: `{o_pct}%` ({passed_count}/{total_count} Steps Complete)")
        lines.append(f"```text\n[{o_bar}] {o_pct}% | {info.get('summary', 'Pending')}\n```")
        if info.get("filename"):
            f_path = meeting_dir / info["filename"]
            lines.append(f"- **Deliverable Artifact:** [`{info['filename']}`](file://{f_path})")

        substeps = GATE_DEFINITIONS.get(key, [])
        if substeps:
            lines.extend([
                "",
                "| Step | Verification Sub-Task | Status | Target Invariant / Details |",
                "| :---: | :--- | :---: | :--- |",
            ])
            for s_idx, (s_code, s_name, s_inv) in enumerate(substeps):
                if s_idx < passed_count:
                    s_status = "✅ PASS"
                elif s_idx == passed_count and info.get("running"):
                    s_status = "🔄 RUNNING"
                else:
                    s_status = "⏳ QUEUED"
                lines.append(f"| `{s_code}` | {s_name} | {s_status} | {s_inv} |")
        lines.append("")

    lines.extend([
        "## Pipeline Stages Execution Matrix",
        "",
        "| Step | Stage Description | Target Output | Status | Elapsed |",
        "| :---: | :--- | :--- | :---: | :---: |",
    ])
    for s in stages:
        s_icon = "🟢 PASS" if s.get("status") == "PASS" else ("🔄 RUNNING" if s.get("status") == "RUNNING" else "⏳ PENDING")
        lines.append(f"| {s['idx']} | {s['name']} | {s['target']} | {s_icon} | `{s.get('elapsed', '--')}` |")

    lines.extend([
        "",
        "## Deterministic QA Gates Verification Summary",
        "",
        "| Gate Scope | Total Steps | Passing | Status | Verified Invariants |",
        "| :--- | :---: | :---: | :---: | :--- |",
    ])
    for _scope_key, gate_info in output_status.items():
        if "gates_total" in gate_info:
            g_status = "✅ PASS" if gate_info.get("passed") else ("🔄 RUNNING" if gate_info.get("running") else "⏳ QUEUED")
            lines.append(
                f"| **{gate_info['title']}** | {gate_info['gates_total']} | {gate_info.get('gates_passed', 0)} | {g_status} | {gate_info.get('invariants', 'All standard rules')} |"
            )

    lines.append("")
    return "\n".join(lines)


def get_initial_stages() -> List[Dict[str, Any]]:
    return [
        {"idx": 1, "name": "Forensic Transcript Verification", "target": "Output 01", "status": "PENDING"},
        {"idx": 2, "name": "Executive Recap Verification", "target": "Output 02", "status": "PENDING"},
        {"idx": 3, "name": "Canonical Signal Projection Verification", "target": "Output 03", "status": "PENDING"},
        {"idx": 4, "name": "Cross-Artifact Consistency Evaluation", "target": "Triad (01/02/03)", "status": "PENDING"},
        {"idx": 5, "name": "Dialectical Micro-Judge Evaluation", "target": "Quality Qualification", "status": "PENDING"},
        {"idx": 6, "name": "Governance Sealing & UWG Release", "target": "Release Receipts", "status": "PENDING"},
    ]


def get_initial_output_status() -> Dict[str, Dict[str, Any]]:
    return {
        "output_1": {
            "title": "Output 01: Forensic Transcript",
            "filename": "01_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_hitl_resolved_transcript.md",
            "pct": 0, "passed": False, "running": False, "gates_total": 9, "gates_passed": 0,
            "summary": "Forensic source, 10 ALR entries, 0 gaps >32s",
            "invariants": "Rule C-01, C-11, C-12, C-19",
        },
        "output_2": {
            "title": "Output 02: Executive Recap",
            "filename": "02_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_executive_recap.md",
            "pct": 0, "passed": False, "running": False, "gates_total": 11, "gates_passed": 0,
            "summary": "Email header, **Attendees:**, trade-off matrix, plain English",
            "invariants": "Architecture deep dive archetype, plain English",
        },
        "output_3": {
            "title": "Output 03: Canonical Signal Projection",
            "filename": "03_Jake_Amit_Miles_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_canonical_signal_projection.json",
            "pct": 0, "passed": False, "running": False, "gates_total": 10, "gates_passed": 0,
            "summary": "V5.7 Pydantic schema, W2 predicates, entity prefixes",
            "invariants": "W2 semantic spine, character offset grounding",
        },
        "cross_artifact": {
            "title": "Cross-Artifact Consistency",
            "filename": "",
            "pct": 0, "passed": False, "running": False, "gates_total": 5, "gates_passed": 0,
            "summary": "Entity alignment, temporal SSOT, career firewall",
            "invariants": "Gate Cross 1-5 reconciliation",
        },
        "judge_and_sealing": {
            "title": "Decomposed Micro-Judge & Governance Sealing",
            "filename": "final_release_decision.json",
            "pct": 0, "passed": False, "running": False, "gates_total": 8, "gates_passed": 0,
            "summary": "Authoritative release seal & 4 receipts (qa, judge, release, intake) deployed",
            "invariants": "RFC-8259 digest, zero mocks invariant",
        },
    }


DEFAULT_LENS_METADATA: Dict[str, Any] = {
    "project": "project_lens",
    "meeting": "Jake_Amit_Miles_PartnerAdvisory",
    "meeting_name": "Jake_Amit_Miles_PartnerAdvisory",
    "date": "09/18/2026",
    "date_filename": "09-18-2026",
    "time": "12:43 PM–01:39 PM ET",
    "time_filename": "1243PM_0139PM_ET",
    "standardized_slug": "Jake_Amit_Miles_PartnerAdvisory",
    "attendees": ["Amit Ayer", "Jake Stark", "Miles Erickson"],
    "classification": "confidential",
    "audiences": ["internal_consulting", "nee-engineering"],
    "archetype": "ARCHITECTURE_DEEP_DIVE",
}


class LiveHeartbeat:
    """Emits periodic in-band progress heartbeat updates during long-running operations."""

    def __init__(self, desc: str, interval: float = 10.0, file: Optional[TextIO] = None) -> None:
        self.desc = desc
        self.interval = max(0.01, float(interval))
        self.file = file or sys.stdout
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_time = time.monotonic()

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval):
            elapsed = time.monotonic() - self._start_time
            mins, secs = divmod(int(elapsed), 60)
            self.file.write(f"⏱️  [HEARTBEAT {mins:02d}:{secs:02d}] {self.desc} (actively waiting on model response...)\n")
            if hasattr(self.file, "flush"):
                self.file.flush()

    def __enter__(self) -> LiveHeartbeat:
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


