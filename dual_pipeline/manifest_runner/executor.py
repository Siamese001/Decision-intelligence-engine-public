"""Stateless manifest pipeline executor coordinating parsing, QA, evaluation, and intake."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Tuple

from dual_pipeline.core import seal_artifact
from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.model_registry import ModelRegistry, ModelTier
from dual_pipeline.transcript_qa import evaluate_all_qa_gates, evaluate_judge_and_release
from tools.prepare_evidence_envelope_intake import prepare_package

from .reporting import (
    print_mutation_breakdown,
    query_graph_counts,
    query_new_assertions,
    query_sidecar_counts,
)

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("ManifestRunner.Executor")

from .transcript_formatter import format_output_01, parse_raw_transcript


def _write_outputs(manifest: IngestionMeetingManifest) -> Tuple[str, str, str, Dict[str, Any]]:
    """Parse source and write Output 01, Output 02, and Output 03 artifacts."""
    raw_text = manifest.resolve_source_text()
    turns = parse_raw_transcript(raw_text, manifest.speaker_map, manifest.term_replacements)
    f1_text = format_output_01(manifest, turns)
    manifest.f1_path.write_text(f1_text, encoding="utf-8")

    f2_text = manifest.resolve_f2_text()
    if not f2_text:
        raise ValueError(f"Output 02 text not available in manifest for {manifest.meeting_title}")
    manifest.f2_path.write_text(f2_text, encoding="utf-8")

    f3_data = manifest.resolve_f3_data()
    if not f3_data:
        raise ValueError(f"Output 03 data not available in manifest for {manifest.meeting_title}")
    manifest.f3_path.write_text(json.dumps(f3_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return raw_text, f1_text, f2_text, f3_data


def _run_qa_gates(
    manifest: IngestionMeetingManifest, raw_text: str, f1_text: str, f2_text: str, f3_data: Dict[str, Any]
) -> Tuple[Dict[str, Any], str, str, Dict[str, Any]]:
    """Evaluate deterministic QA gates, apply auto-healing, and record report."""
    qa_meta = {
        "meeting_name": manifest.meeting_title, "date": manifest.human_date, "time": manifest.time_str,
        "classification": manifest.classification, "audiences": manifest.audiences, "roster": manifest.roster,
        "expected_domain_terms": manifest.expected_domain_terms,
        "meeting_type": manifest.meeting_archetype,
        "entity_clarifications": getattr(manifest, "entity_clarifications", []),
    }
    qa_report = evaluate_all_qa_gates(f1_text, f2_text, f3_data, source_text=raw_text, metadata=qa_meta, auto_heal=True)
    if qa_report.get("healed_artifacts"):
        h_art = qa_report["healed_artifacts"]
        if h_art.get("output_1"):
            f1_text = h_art["output_1"]
            manifest.f1_path.write_text(f1_text, encoding="utf-8")
        if h_art.get("output_2"):
            f2_text = h_art["output_2"]
            manifest.f2_path.write_text(f2_text, encoding="utf-8")
        if h_art.get("output_3"):
            manifest.f3_path.write_text(h_art["output_3"], encoding="utf-8")
            try:
                f3_data = json.loads(h_art["output_3"])
            except Exception:
                pass
    manifest.qa_report_path.write_text(json.dumps(qa_report, indent=2), encoding="utf-8")

    if not qa_report.get("passed"):
        raise RuntimeError(f"Deterministic QA Gates failed for {manifest.meeting_title}")
    return qa_meta, f1_text, f2_text, f3_data


from .guard_utils import (
    init_monotonic_guards,
    load_pinned_evaluations,
    reconcile_guarded_scores,
    update_final_release_verdicts,
)


def _resolve_judge_params(
    model: str | None,
    reasoning_effort: str | None,
    mock: bool,
) -> tuple[str, str, bool]:
    """Resolve model, reasoning effort, and check live credentials under Zero Mocks Invariant."""
    if not model or not reasoning_effort:
        default_m, default_e = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        model = model or default_m
        reasoning_effort = reasoning_effort or default_e

    from config.credentials import resolve_openai_credentials
    try:
        has_openai = resolve_openai_credentials(allow_mock=False).has_live_key
    except ValueError:
        has_openai = False
    if not mock and not has_openai:
        raise RuntimeError("Zero mocks invariant violated: Live OpenAI credentials required for judge evaluation.")
    return model, reasoning_effort, has_openai


def _run_judge(
    manifest: IngestionMeetingManifest,
    raw_text: str,
    f1_text: str,
    f2_text: str,
    f3_data: Dict[str, Any],
    qa_meta: Dict[str, Any],
    model: str | None = None,
    reasoning_effort: str | None = None,
    fresh_eval: bool = False,
    mock: bool = False,
) -> None:
    """Execute LLM multi-judge evaluation and persist decisions with monotonic score protection."""
    model, reasoning_effort, has_openai = _resolve_judge_params(model, reasoning_effort, mock)
    pinned = {} if fresh_eval else load_pinned_evaluations(manifest.judge_report_path)
    f1_text = manifest.f1_path.read_text(encoding="utf-8")
    f2_text = manifest.f2_path.read_text(encoding="utf-8")
    current_f3_str = manifest.f3_path.read_text(encoding="utf-8")
    guard_out1, guard_out2, guard_out3 = init_monotonic_guards(manifest, f1_text, f2_text, current_f3_str)

    qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=current_f3_str,
        source_text=raw_text,
        metadata=qa_meta,
        mock=not has_openai,
        model=model,
        reasoning_effort=reasoning_effort,
        pinned_evaluations=pinned,
    )
    reconcile_guarded_scores(judge_rep, guard_out1, guard_out2, guard_out3, f1_text, f2_text, current_f3_str)
    update_final_release_verdicts(
        judge_rep,
        final_dec,
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=current_f3_str,
        model=model,
        reasoning_effort=reasoning_effort,
    )

    manifest.judge_report_path.write_text(json.dumps(judge_rep, indent=2), encoding="utf-8")
    manifest.release_decision_path.write_text(json.dumps(final_dec, indent=2), encoding="utf-8")
    if not (final_dec.get("authoritative_write_authorized") or final_dec.get("release_authorized")):
        raise RuntimeError(f"Authoritative write NOT authorized for {manifest.meeting_title}")


def _seal_intake_package(manifest: IngestionMeetingManifest) -> None:
    """Prepare and seal downstream intake package."""
    if manifest.pkg_path.exists():
        manifest.pkg_path.unlink()

    try:
        source_loc = str(manifest.f1_path.relative_to(ROOT))
    except ValueError:
        source_loc = str(manifest.f1_path)

    intake_package = prepare_package(
        draft_path=manifest.f3_path,
        source_path=manifest.f1_path,
        output_path=manifest.pkg_path,
        source_location=source_loc,
        classification=manifest.classification,
        audiences=manifest.audiences,
        started_at=f"{manifest.iso_date}T15:30:00-04:00",
        organizer=f"{manifest.project_name.upper()} Technology Leadership",
        platform_reference="Microsoft Teams",
    )
    if manifest.attendees:
        manifest_attendee_names = [
            att.get("name") if isinstance(att, dict) else str(att)
            for att in manifest.attendees
            if (isinstance(att, dict) and att.get("name")) or isinstance(att, str)
        ]
        if manifest_attendee_names:
            intake_package["meeting"]["attendees"] = manifest_attendee_names
    intake_package = seal_artifact(intake_package)
    manifest.pkg_path.write_text(json.dumps(intake_package, indent=2, ensure_ascii=False), encoding="utf-8")

    copy_pkg = manifest.resolved_output_dir / "intake_package.json"
    copy_pkg.write_text(json.dumps(intake_package, indent=2, ensure_ascii=False), encoding="utf-8")


def _execute_graph_publish(manifest: IngestionMeetingManifest) -> Dict[str, Any]:
    """Publish intake package to GraphDB via in-process governed API and report mutation."""
    active_pair_path = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
    curr_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    bp = ROOT / ".local" / "dual_pipeline"
    baseline_graph_path = Path(curr_pair["graph_path"]) if Path(curr_pair["graph_path"]).is_absolute() else bp / curr_pair["graph_path"]
    baseline_sidecar_path = Path(curr_pair["sidecar_path"]) if Path(curr_pair["sidecar_path"]).is_absolute() else bp / curr_pair["sidecar_path"]

    b_graph_counts = query_graph_counts(baseline_graph_path)
    b_sidecar_counts = query_sidecar_counts(baseline_sidecar_path)
    b_hash = curr_pair.get("graph_sha256", "")

    directive_id = manifest.directive_id or f"directive-{manifest.project_name}-{manifest.path_date}-auto"
    d_dir = bp / "ingestion_directives"
    if (d_dir / f"{directive_id}.json").exists():
        m_v = re.search(r"-v(\d+)$", directive_id)
        ver = int(m_v.group(1)) if m_v else 1
        prefix = directive_id[:m_v.start()] if m_v else directive_id
        while (d_dir / f"{prefix}-v{ver}.json").exists():
            ver += 1
        directive_id = f"{prefix}-v{ver}"

    from dual_pipeline.cli.handlers import _intake_publish
    from dual_pipeline.cli.parser import build_parser

    pkg_to_publish = manifest.resolved_output_dir / "intake_package.json"
    if not pkg_to_publish.exists():
        pkg_to_publish = manifest.pkg_path

    from dual_pipeline.transcript_pipeline_core.release_runner import _resolve_model_asset

    publish_args = [
        "intake-publish", "--package", str(pkg_to_publish), "--directive-id", directive_id,
        "--scope", f"{manifest.meeting_title} canonical projection for {manifest.human_date}",
        "--embedding-backend", "bge-m3-dev", "--allow-dev-bge-m3",
        "--model-path", _resolve_model_asset("models/bge-m3/5617a9f61b028005a4858fdac845db406aefb181"),
        "--model-manifest", _resolve_model_asset("models/bge-m3/bge-m3-manifest-5617a9f61b028005a4858fdac845db406aefb181.json"),
        "--model-revision", "5617a9f61b028005a4858fdac845db406aefb181",
        "--runtime-root", str(ROOT / ".local" / "dual_pipeline"),
    ]
    try:
        parsed_cli_args = build_parser().parse_args(publish_args)
        _intake_publish(parsed_cli_args)
    except Exception as exc:
        logger.error(f"In-process intake-publish execution failed: {exc}")
        raise RuntimeError(f"Governed in-process intake-publish failed: {exc}") from exc

    new_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    act_graph_path = Path(new_pair["graph_path"]) if Path(new_pair["graph_path"]).is_absolute() else bp / new_pair["graph_path"]
    act_sidecar_path = Path(new_pair["sidecar_path"]) if Path(new_pair["sidecar_path"]).is_absolute() else bp / new_pair["sidecar_path"]

    a_graph_counts = query_graph_counts(act_graph_path)
    a_sidecar_counts = query_sidecar_counts(act_sidecar_path)
    a_hash = new_pair.get("graph_sha256", "")
    assertions = query_new_assertions(act_graph_path, limit=10)

    print_mutation_breakdown(
        title=f"{manifest.meeting_title} ({manifest.human_date})",
        baseline_graph=b_graph_counts,
        active_graph=a_graph_counts,
        baseline_sidecar=b_sidecar_counts,
        active_sidecar=a_sidecar_counts,
        baseline_hash=b_hash,
        active_hash=a_hash,
        assertions=assertions,
    )

    return {
        "meeting": manifest.meeting_title,
        "status": "COMPLETE",
        "active_generation": new_pair.get("generation_id"),
        "graph_hash": a_hash,
        "baseline_hash": b_hash,
    }


class ManifestPipelineRunner:
    """Modular pipeline runner executing ingestion for IngestionMeetingManifest specifications."""

    @classmethod
    def run_manifest(
        cls,
        manifest: IngestionMeetingManifest,
        model: str | None = None,
        reasoning_effort: str | None = None,
        dry_run_qa: bool = False,
        publish_to_graph: bool = True,
        fresh_eval: bool = False,
    ) -> Dict[str, Any]:
        manifest.validate()
        manifest.resolved_output_dir.mkdir(parents=True, exist_ok=True)
        manifest.resolved_intake_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Executing manifest pipeline: {manifest.meeting_title} ({manifest.human_date})")

        raw_text, f1_text, f2_text, f3_data = _write_outputs(manifest)
        qa_meta, f1_text, f2_text, f3_data = _run_qa_gates(manifest, raw_text, f1_text, f2_text, f3_data)

        if dry_run_qa:
            logger.info(f"[Dry Run QA] QA Gates PASSED for {manifest.meeting_title}. Skipping Judge and Ingestion.")
            return {"meeting": manifest.meeting_title, "qa_passed": True, "status": "QA_PASSED"}

        _run_judge(manifest, raw_text, f1_text, f2_text, f3_data, qa_meta, model, reasoning_effort, fresh_eval=fresh_eval)
        _seal_intake_package(manifest)

        if not publish_to_graph:
            return {"meeting": manifest.meeting_title, "status": "PACKAGE_SEALED", "package": str(manifest.pkg_path)}

        return _execute_graph_publish(manifest)
