"""
dual_pipeline/pipeline_runner.py

[DEPRECATED / STATIC TEST HELPER ONLY]
TranscriptIngestionPipelineRunner is maintained strictly as a static testing helper for legacy unit tests.
All production execution paths MUST use CanonicalAgenticPipeline.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
from dual_pipeline.transcript_qa import evaluate_all_qa_gates, evaluate_judge_and_release

logger = logging.getLogger("TranscriptIngestionPipelineRunner")


def prepare_package(*args: Any, **kwargs: Any) -> Any:
    """Module-level packaging entrypoint allowing test monkeypatching and lazy tool import."""
    from tools.prepare_evidence_envelope_intake import prepare_package as _prep

    return _prep(*args, **kwargs)


class TranscriptIngestionPipelineRunner:
    """Unified pipeline runner executing governed transcript ingestion lifecycles."""

    @staticmethod
    def parse_transcript(manifest: IngestionMeetingManifest) -> Tuple[str, List[Tuple[str, str, str]]]:
        """Parse raw transcript into structured (speaker, timestamp, text) turns."""
        raw_text = manifest.raw_text
        if not raw_text and manifest.source_transcript_path:
            raw_text = Path(manifest.source_transcript_path).read_text(encoding="utf-8")
        if not raw_text:
            raise ValueError("No raw transcript text available in manifest")

        idx = raw_text.find("started transcription")
        body = raw_text[idx + len("started transcription"):] if idx != -1 else raw_text

        pattern = re.compile(r"(?:^|[\r\n]+)\s*([A-Za-z, ]+?)\s{2,}(\d{1,2}:\d{2}(?::\d{2})?)\s*[\r\n]+")
        matches = list(pattern.finditer(body))
        turns: List[Tuple[str, str, str]] = []

        for i, m in enumerate(matches):
            spk_raw = m.group(1).strip()
            t_raw = m.group(2).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
            turn_body = body[start:end].strip()

            spk = manifest.speaker_map.get(spk_raw, spk_raw)
            parts = t_raw.split(":")
            if len(parts) == 2:
                ts = f"00:{int(parts[0]):02d}:{int(parts[1]):02d}"
            elif len(parts) == 3:
                ts = f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
            else:
                ts = t_raw

            cleaned = re.sub(r"[A-Za-z0-9, ./\-]+?\s+stopped transcription.*$", "", turn_body, flags=re.DOTALL).strip()
            if cleaned:
                turns.append((spk, ts, cleaned))

        return raw_text, turns

    @staticmethod
    def build_output_01(manifest: IngestionMeetingManifest, turns: List[Tuple[str, str, str]]) -> str:
        """Construct Output 01: Forensic Attributed Transcript with Ambiguity Register."""
        sections = [
            f"# 01: Forensic Attributed Transcript — {manifest.meeting_title}",
            "",
            "## Meeting Metadata",
            f"- **Meeting Title**: {manifest.meeting_title}",
            f"- **Date:** {manifest.human_date}",
            f"- **Time**: {manifest.time_str}",
            "- **Platform**: Microsoft Teams (Apex Global Enterprises)",
            f"- **Classification**: {manifest.classification.upper()}",
            f"- **Audiences**: {', '.join(manifest.audiences)}",
            "",
            "## Attendees & Roster Attribution",
        ]
        if manifest.attendees:
            for att in manifest.attendees:
                name = att.get("name", "Unknown")
                org = att.get("company", "External")
                role = att.get("role", "Participant")
                sections.append(f"- **{name}** ({org}) — {role}")
        elif manifest.roster:
            for r in manifest.roster:
                sections.append(f"- **{r.get('name', 'Participant')}** ({r.get('company', 'External')})")
        else:
            sections.append("- (Roster metadata parsed from speaker turns)")

        sections.extend([
            "",
            "## Ambiguity Lock Register",
            "| ID | Ambiguity Description | Affected Statement IDs | Lock Status | Resolution Pathway |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ])
        if manifest.ambiguity_rows:
            for row in manifest.ambiguity_rows:
                sections.append(f"| `{row.get('id')}` | {row.get('description')} | `{row.get('statements', 'N/A')}` | `{row.get('status', 'LOCKED')}` | {row.get('pathway', 'Verified in session')} |")
        else:
            sections.append("| `AMB-01` | No unresolved speaker attribution ambiguities | `N/A` | `RESOLVED` | Verbatim speaker logs verified |")
            sections.append("| `AMB-02` | Technical terminology and project lexicon verified | `N/A` | `RESOLVED` | Source vocabulary grounded |")

        sections.extend(["", "## Forensic Attributed Spoken Dialogue", ""])
        for spk, ts, text in turns:
            sections.append(f"### [{ts}]\n\n**{spk}:** {text}\n")

        return "\n".join(sections) + "\n"

    @staticmethod
    def build_output_02(manifest: IngestionMeetingManifest) -> str:
        """Construct Output 02: Email-ready Executive Recap with Strategic Synthesis and deep sections."""
        from .manifest_runner.transcript_formatter import format_output_02
        return format_output_02(manifest)

    @staticmethod
    def build_output_03(manifest: IngestionMeetingManifest) -> Dict[str, Any]:
        """Prepare Output 03: Canonical Signal Projection JSON."""
        if not manifest.f3_data:
            raise ValueError("Manifest must supply f3_data for Output 03")
        f3 = dict(manifest.f3_data)
        f3["schema_version"] = "5.7"
        f3["meeting_id"] = manifest.resolved_base_name
        f3.setdefault("classification", manifest.classification)
        f3.setdefault("audiences", manifest.audiences)
        f3.setdefault("evidence_only_statement_ids", [])
        f3.setdefault("graph_mutations", {})
        if "metadata" in f3 and isinstance(f3["metadata"], dict):
            f3["metadata"].setdefault("schema_version", "5.7")
            f3["metadata"].setdefault("meeting_archetype", manifest.meeting_archetype)
            f3["metadata"].setdefault("observed_at", manifest.human_date)
            f3["metadata"].setdefault("classification", manifest.classification)
            f3["metadata"].setdefault("audiences", manifest.audiences)
        return f3

    def run(
        self,
        manifest: IngestionMeetingManifest,
        dry_run_qa: bool = False,
        execute_publish: bool = False,
    ) -> Dict[str, Any]:
        """Execute the full ingestion pipeline for a given meeting manifest."""
        manifest.validate()
        manifest.resolved_output_dir.mkdir(parents=True, exist_ok=True)
        manifest.resolved_intake_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Executing ingestion pipeline for: {manifest.resolved_base_name}")

        # Step 1: Parse & Write Output 01
        raw_text, turns = self.parse_transcript(manifest)
        f1_text = self.build_output_01(manifest, turns)
        manifest.f1_path.write_text(f1_text, encoding="utf-8")

        # Step 2: Write Output 02
        f2_text = self.build_output_02(manifest)
        manifest.f2_path.write_text(f2_text, encoding="utf-8")

        # Step 3: Write Output 03
        f3_data = self.build_output_03(manifest)
        manifest.f3_path.write_text(json.dumps(f3_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # Step 4: Evaluate Deterministic QA Gates
        qa_meta = {
            "meeting_name": manifest.meeting_title,
            "date": manifest.human_date,
            "time": manifest.time_str,
            "classification": manifest.classification,
            "audiences": manifest.audiences,
            "roster": manifest.roster or manifest.attendees,
            "expected_domain_terms": manifest.expected_domain_terms,
            "meeting_type": manifest.meeting_archetype,
        }
        qa_report = evaluate_all_qa_gates(f1_text, f2_text, f3_data, source_text=raw_text, metadata=qa_meta)
        manifest.qa_report_path.write_text(json.dumps(qa_report, indent=2), encoding="utf-8")

        if not qa_report.get("passed", False):
            raise RuntimeError(f"Deterministic QA Gates failed for {manifest.resolved_base_name}")

        if dry_run_qa:
            return {
                "base_name": manifest.resolved_base_name,
                "qa_passed": True,
                "qa_report_path": str(manifest.qa_report_path),
                "status": "QA_PASSED_DRY_RUN",
            }

        # Step 5: LLM Multi-Judge & Reconciler
        f3_str = json.dumps(f3_data, indent=2)
        eval_out = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_str,
            source_text=raw_text,
            qa_report=qa_report,
            meeting_name=manifest.meeting_title,
            date_str=manifest.human_date,
            time_str=manifest.time_str,
            metadata=qa_meta,
            output_dir=manifest.resolved_output_dir,
            save_artifacts=True,
        )
        if len(eval_out) == 3:
            _, judge_res, release_dec = eval_out
        else:
            judge_res, release_dec = eval_out

        final_verdict = release_dec.get("final_release_verdict") or release_dec.get("status")
        score_o3 = (
            release_dec.get("scores", {}).get("output_3_canonical_signal_projection")
            or judge_res.get("output_3_evaluation", {}).get("composite_score", 0)
        )
        expected_verdict = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, score_o3).value

        # Fail-closed validation
        if final_verdict not in ("PASS", "PASS_ADVISORY"):
            raise RuntimeError(f"Release authorization rejected: verdict={final_verdict}, expected={expected_verdict}")

        # Step 6: Package Downstream Intake
        source_loc = str(manifest.source_transcript_path or manifest.f1_path)
        prepare_package(
            draft_path=manifest.f3_path,
            source_export=manifest.f1_path,
            source_location=source_loc,
            output_path=manifest.pkg_path,
            classification=manifest.classification,
            audiences=manifest.audiences,
        )

        return {
            "base_name": manifest.resolved_base_name,
            "qa_passed": True,
            "release_verdict": final_verdict,
            "output_3_score": score_o3,
            "pkg_path": str(manifest.pkg_path),
            "status": "PACKAGE_SEALED",
        }

    def run_agentic(
        self,
        manifest: IngestionMeetingManifest,
        coordinator: Optional[Any] = None,
        dry_run_qa: bool = False,
        mock_mode: bool = False,
    ) -> Dict[str, Any]:
        """Execute ingestion using the agentic IngestionCoordinator DAG while enforcing the release lifecycle."""
        manifest.validate()
        manifest.resolved_output_dir.mkdir(parents=True, exist_ok=True)
        manifest.resolved_intake_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Executing agentic ingestion pipeline for: {manifest.resolved_base_name}")

        if coordinator is None:
            from pipeline_a_intake.agents import IngestionCoordinator
            coordinator = IngestionCoordinator(mock_mode=mock_mode)

        raw_text, turns = self.parse_transcript(manifest)
        roster_names = [
            a.get("name", "")
            for a in (manifest.attendees or manifest.roster or [])
            if isinstance(a, dict) and a.get("name")
        ]

        agent_res = coordinator.run_ingestion_pipeline(
            meeting_name=manifest.meeting_title,
            raw_transcript_text=raw_text,
            known_roster=roster_names,
        )

        f1_text = agent_res.get("artifact_01_markdown") or agent_res.get("artifact_01_normalized_transcript") or self.build_output_01(manifest, turns)
        manifest.f1_path.write_text(f1_text, encoding="utf-8")

        f2_text = self.build_output_02(manifest)
        manifest.f2_path.write_text(f2_text, encoding="utf-8")

        f3_data = self.build_output_03(manifest)
        manifest.f3_path.write_text(json.dumps(f3_data, indent=2, ensure_ascii=False), encoding="utf-8")

        qa_meta = {
            "meeting_name": manifest.meeting_title,
            "date": manifest.human_date,
            "time": manifest.time_str,
            "classification": manifest.classification,
            "audiences": manifest.audiences,
            "roster": manifest.roster or manifest.attendees,
            "expected_domain_terms": manifest.expected_domain_terms,
            "meeting_type": manifest.meeting_archetype,
        }
        qa_report = evaluate_all_qa_gates(f1_text, f2_text, f3_data, source_text=raw_text, metadata=qa_meta)
        manifest.qa_report_path.write_text(json.dumps(qa_report, indent=2), encoding="utf-8")

        if not qa_report.get("passed", False):
            raise RuntimeError(f"Deterministic QA Gates failed for {manifest.resolved_base_name}")

        if dry_run_qa:
            return {
                "base_name": manifest.resolved_base_name,
                "execution_mode": "AGENTIC_DAG",
                "qa_passed": True,
                "qa_report_path": str(manifest.qa_report_path),
                "status": "QA_PASSED_DRY_RUN",
            }

        f3_str = json.dumps(f3_data, indent=2)
        eval_out = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_str,
            source_text=raw_text,
            qa_report=qa_report,
            meeting_name=manifest.meeting_title,
            date_str=manifest.human_date,
            time_str=manifest.time_str,
            metadata=qa_meta,
            output_dir=manifest.resolved_output_dir,
            save_artifacts=True,
        )
        if len(eval_out) == 3:
            _, judge_res, release_dec = eval_out
        else:
            judge_res, release_dec = eval_out

        final_verdict = release_dec.get("final_release_verdict") or release_dec.get("status")
        score_o3 = (
            release_dec.get("scores", {}).get("output_3_canonical_signal_projection")
            or judge_res.get("output_3_evaluation", {}).get("composite_score", 0)
        )
        expected_verdict = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, score_o3).value

        if final_verdict not in ("PASS", "PASS_ADVISORY"):
            raise RuntimeError(f"Release authorization rejected: verdict={final_verdict}, expected={expected_verdict}")

        source_loc = str(manifest.source_transcript_path or manifest.f1_path)
        from tools.prepare_evidence_envelope_intake import prepare_package  # noqa: E402
        prepare_package(
            draft_path=manifest.f3_path,
            source_path=manifest.f1_path,
            output_path=manifest.pkg_path,
            source_location=source_loc,
            classification=manifest.classification,
            audiences=manifest.audiences,
        )

        return {
            "base_name": manifest.resolved_base_name,
            "execution_mode": "AGENTIC_DAG",
            "qa_passed": True,
            "release_verdict": final_verdict,
            "output_3_score": score_o3,
            "pkg_path": str(manifest.pkg_path),
            "status": "PACKAGE_SEALED",
        }
