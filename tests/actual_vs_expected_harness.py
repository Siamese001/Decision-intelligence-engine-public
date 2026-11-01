"""tests/actual_vs_expected_harness.py

Reusable test harness and evaluation matrix for comparing Actual vs. Expected
pipeline outputs across all ingestion modalities:
1. Audio (.wav)
2. Raw Transcript (.txt, WebVTT, Diarized/Undiarized)
3. Written Documents (.md, .txt)
4. PDF Documents (.pdf)
5. Word / Presentation Documents (.docx, .pptx)
6. Notes Mode / CoPilot notes
"""
from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class ExpectedModalityContract:
    """Explicit ground-truth targets expected from the pipeline for a given modality."""
    modality: str
    input_filename: str
    expected_deliverables: Set[str]
    prohibited_deliverables: Set[str] = field(default_factory=set)
    expected_speakers: Optional[List[str]] = None
    expected_min_turns: Optional[int] = None
    expected_min_words: Optional[int] = None
    expected_schema_version: str = "5.7"
    expected_recap_sections: Tuple[str, ...] = ("Meeting Objective", "Key Decisions", "Action Items")
    expected_qa_status: str = "PASS"
    expected_entity_prefixes: Tuple[str, ...] = (
        "person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:"
    )
    expected_valid_kinds: Tuple[str, ...] = ("summary", "decision", "risk", "dependency", "technical", "strategic")
    expected_governance_receipts: Set[str] = field(
        default_factory=lambda: {
            "qa_gates_report.json", "llm_judge_evaluation.json", "final_release_decision.json", "intake_package.json"
        }
    )
    expected_page_count: Optional[int] = None


@dataclass
class ActualPipelineResult:
    """Inspects and parses actual outputs produced by the pipeline in its output directory."""
    output_dir: Path
    status: str
    deliverables_found: Set[str]
    receipts_found: Set[str]
    f01_path: Optional[Path] = None
    f02_path: Optional[Path] = None
    f03_path: Optional[Path] = None
    f01_text: str = ""
    f02_text: str = ""
    f03_data: Dict[str, Any] = field(default_factory=dict)
    qa_report: Dict[str, Any] = field(default_factory=dict)
    judge_report: Dict[str, Any] = field(default_factory=dict)
    extracted_speakers: List[str] = field(default_factory=list)
    extracted_entities: List[str] = field(default_factory=list)
    extracted_kinds: List[str] = field(default_factory=list)
    recap_date: Optional[str] = None
    turn_count: int = 0
    word_count: int = 0
    pages_extracted: Optional[int] = None

    @classmethod
    def from_pipeline_run(cls, run_result: Dict[str, Any]) -> ActualPipelineResult:
        out_dir = Path(run_result.get("output_dir", run_result.get("dest_dir", ".")))
        status = run_result.get("status")
        if not status:
            if "execution_receipt" in run_result or (out_dir / "pipeline_execution_receipt.json").exists():
                status = "COMPLETE"
            else:
                status = "UNKNOWN"
        status = str(status)

        all_files = list(out_dir.iterdir()) if out_dir.exists() else []
        file_names = {f.name for f in all_files}

        deliverables: Set[str] = set()
        f01_p: Optional[Path] = None
        f02_p: Optional[Path] = None
        f03_p: Optional[Path] = None

        for f in all_files:
            if f.name.startswith("01_") and f.name.endswith(".md"):
                deliverables.add("01")
                f01_p = f
            elif f.name.startswith("02_") and f.name.endswith(".md"):
                deliverables.add("02")
                f02_p = f
            elif f.name.startswith("03_") and f.name.endswith(".json"):
                deliverables.add("03")
                f03_p = f

        receipt_names = {
            "qa_gates_report.json",
            "llm_judge_evaluation.json",
            "final_release_decision.json",
            "intake_package.json",
        }
        receipts_found = receipt_names.intersection(file_names)

        f01_text = f01_p.read_text(encoding="utf-8") if f01_p and f01_p.exists() else ""
        f02_text = f02_p.read_text(encoding="utf-8") if f02_p and f02_p.exists() else ""

        f03_data: Dict[str, Any] = {}
        if f03_p and f03_p.exists():
            with contextlib.suppress(Exception):
                f03_data = json.loads(f03_p.read_text(encoding="utf-8"))

        qa_report: Dict[str, Any] = {}
        qa_p = out_dir / "qa_gates_report.json"
        if qa_p.exists():
            with contextlib.suppress(Exception):
                qa_report = json.loads(qa_p.read_text(encoding="utf-8"))

        judge_report: Dict[str, Any] = {}
        judge_p = out_dir / "llm_judge_evaluation.json"
        if judge_p.exists():
            with contextlib.suppress(Exception):
                judge_report = json.loads(judge_p.read_text(encoding="utf-8"))

        # Extract speakers from Deliverable 01
        extracted_speakers: List[str] = []
        if f01_text:
            transcript_body = f01_text
            if "## Transcript" in f01_text:
                transcript_body = f01_text.split("## Transcript", 1)[1]
                if "## Ambiguity Lock Register" in transcript_body:
                    transcript_body = transcript_body.split("## Ambiguity Lock Register", 1)[0]
            elif "---" in f01_text:
                transcript_body = f01_text.split("---", 1)[1]
                if "## Ambiguity Lock Register" in transcript_body:
                    transcript_body = transcript_body.split("## Ambiguity Lock Register", 1)[0]

            matches = (
                re.findall(r"\*\*([^*:\n]+):\*\*", transcript_body)
                + re.findall(r"\*\*([^*:\n]+)\*\*\s*\[\d+:\d+(?::\d+)?\]", transcript_body)
                + re.findall(r"\[([A-Za-z0-9\s._-]+?)(?:\s*\([^)]+\))?\]:\s*", transcript_body)
            )
            ignored = {"date", "time", "attendees", "participants", "platform reference", "transcript", "speaker"}
            extracted_speakers = sorted(list({
                m.strip() for m in matches
                if m.strip() and m.strip().lower() not in ignored and len(m.strip()) > 1
            }))

        # Extract recap date
        recap_date: Optional[str] = None
        if f02_text:
            m_date = re.search(r"(?:\*\*Date:\*\*|Date:)\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", f02_text)
            if m_date:
                recap_date = m_date.group(1)

        # Extract entities from Deliverable 03
        extracted_entities: List[str] = []
        extracted_kinds: List[str] = []
        if f03_data:
            mutations = f03_data.get("graph_mutations", {})
            for node in mutations.get("nodes", []) + mutations.get("entity_upserts", []):
                nid = node.get("node_id") or node.get("id") or node.get("name")
                if nid:
                    extracted_entities.append(str(nid))
            for st in f03_data.get("statements", []):
                k = st.get("kind")
                if k:
                    extracted_kinds.append(str(k))

        word_count = len(f01_text.split()) if f01_text else len(f02_text.split())
        turn_count = len(extracted_speakers)

        return cls(
            output_dir=out_dir,
            status=status,
            deliverables_found=deliverables,
            receipts_found=receipts_found,
            f01_path=f01_p,
            f02_path=f02_p,
            f03_path=f03_p,
            f01_text=f01_text,
            f02_text=f02_text,
            f03_data=f03_data,
            qa_report=qa_report,
            judge_report=judge_report,
            extracted_speakers=extracted_speakers,
            extracted_entities=extracted_entities,
            extracted_kinds=extracted_kinds,
            recap_date=recap_date,
            turn_count=turn_count,
            word_count=word_count,
        )


@dataclass
class ActualVsExpectedRow:
    """Individual comparison between expected and actual value."""
    modality: str
    dimension: str
    expected: Any
    actual: Any
    matched: bool
    detail: str = ""


class ActualVsExpectedMatrix:
    """Matrix comparing actual results from pipeline against expected contracts."""

    def __init__(self) -> None:
        self.rows: List[ActualVsExpectedRow] = []

    def evaluate(
        self,
        contract: ExpectedModalityContract,
        actual: ActualPipelineResult,
    ) -> List[ActualVsExpectedRow]:
        mod = contract.modality
        mod_rows: List[ActualVsExpectedRow] = []

        # 1. Deliverables match
        deliv_matched = (contract.expected_deliverables == actual.deliverables_found)
        mod_rows.append(
            ActualVsExpectedRow(
                modality=mod,
                dimension="Deliverables Emitted",
                expected=sorted(list(contract.expected_deliverables)),
                actual=sorted(list(actual.deliverables_found)),
                matched=deliv_matched,
                detail="Matches contract deliverable triad or document rules",
            )
        )

        # 2. Prohibited Deliverables strictly absent
        if contract.prohibited_deliverables:
            prohibited_present = contract.prohibited_deliverables.intersection(actual.deliverables_found)
            mod_rows.append(
                ActualVsExpectedRow(
                    modality=mod,
                    dimension="Prohibited Deliverables Absent",
                    expected=f"Absent: {sorted(list(contract.prohibited_deliverables))}",
                    actual=f"Found: {sorted(list(prohibited_present))}" if prohibited_present else "None (Correct)",
                    matched=len(prohibited_present) == 0,
                    detail="Ensures modality boundaries (e.g. docs omit 01 & 02)",
                )
            )

        # 3. Governance Receipts
        receipts_matched = contract.expected_governance_receipts.issubset(actual.receipts_found)
        mod_rows.append(
            ActualVsExpectedRow(
                modality=mod,
                dimension="Governance Receipts (4 SSOT)",
                expected=sorted(list(contract.expected_governance_receipts)),
                actual=sorted(list(actual.receipts_found)),
                matched=receipts_matched,
                detail="QA gates, judge evaluation, release decision, intake package",
            )
        )

        # 4. Schema Version (if 03 present)
        if "03" in actual.deliverables_found:
            actual_ver = actual.f03_data.get("schema_version")
            mod_rows.append(
                ActualVsExpectedRow(
                    modality=mod,
                    dimension="Deliverable 03 Schema Version",
                    expected=contract.expected_schema_version,
                    actual=actual_ver,
                    matched=actual_ver == contract.expected_schema_version,
                    detail=f"Schema 03 version {contract.expected_schema_version}",
                )
            )

        # 5. Speakers (for transcript/audio modalities)
        if contract.expected_speakers is not None:
            spk_matched = all(s in actual.extracted_speakers for s in contract.expected_speakers)
            mod_rows.append(
                ActualVsExpectedRow(
                    modality=mod,
                    dimension="Attributed Speakers",
                    expected=contract.expected_speakers,
                    actual=actual.extracted_speakers,
                    matched=spk_matched,
                    detail="Forensic transcript speaker attribution",
                )
            )

        # 6. Date formatting (MM/DD/YYYY) in Deliverable 02
        if "02" in actual.deliverables_found:
            has_valid_date = bool(actual.recap_date and re.match(r"^\d{2}/\d{2}/\d{4}$", actual.recap_date))
            mod_rows.append(
                ActualVsExpectedRow(
                    modality=mod,
                    dimension="Executive Recap Date Format",
                    expected="Strict MM/DD/YYYY",
                    actual=actual.recap_date or "Missing",
                    matched=has_valid_date,
                    detail="Header date compliance in Deliverable 02",
                )
            )

        # 7. QA Gate Status
        qa_status = (
            actual.qa_report.get("status")
            or actual.qa_report.get("overall_status")
            or ("PASS" if actual.status in ("COMPLETE", "PASS") else actual.status)
        )
        mod_rows.append(
            ActualVsExpectedRow(
                modality=mod,
                dimension="QA Gate Release Status",
                expected=contract.expected_qa_status,
                actual=qa_status,
                matched=qa_status == contract.expected_qa_status,
                detail="Passing non-compensating QA verification",
            )
        )

        # 8. Entity ID Prefixes (for Deliverable 03)
        if "03" in actual.deliverables_found and actual.extracted_entities:
            valid_prefixes = all(
                any(e.startswith(p) for p in contract.expected_entity_prefixes)
                for e in actual.extracted_entities
            )
            mod_rows.append(
                ActualVsExpectedRow(
                    modality=mod,
                    dimension="Entity ID Prefixes",
                    expected="All match W2 allowed prefixes",
                    actual=f"{len(actual.extracted_entities)} entities verified",
                    matched=valid_prefixes,
                    detail="person:, org:, system:, workstream:, project:, etc.",
                )
            )

        self.rows.extend(mod_rows)
        return mod_rows

    def to_markdown_table(self) -> str:
        lines = [
            "| Modality | Dimension / Metric | Expected | Actual | Match Status | Detail |",
            "| :--- | :--- | :--- | :--- | :---: | :--- |",
        ]
        for r in self.rows:
            st = "✅ MATCH" if r.matched else "❌ MISMATCH"
            exp_str = str(r.expected).replace("|", "\\|")
            act_str = str(r.actual).replace("|", "\\|")
            lines.append(f"| **{r.modality}** | {r.dimension} | `{exp_str}` | `{act_str}` | {st} | {r.detail} |")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_evaluations": len(self.rows),
            "matched_count": sum(1 for r in self.rows if r.matched),
            "mismatched_count": sum(1 for r in self.rows if not r.matched),
            "all_matched": all(r.matched for r in self.rows),
            "evaluations": [
                {
                    "modality": r.modality,
                    "dimension": r.dimension,
                    "expected": r.expected,
                    "actual": r.actual,
                    "matched": r.matched,
                    "detail": r.detail,
                }
                for r in self.rows
            ],
        }

    def assert_all_matched(self) -> None:
        mismatches = [r for r in self.rows if not r.matched]
        if mismatches:
            table = self.to_markdown_table()
            raise AssertionError(
                f"Actual vs Expected mismatches found ({len(mismatches)} failed):\n{table}"
            )
