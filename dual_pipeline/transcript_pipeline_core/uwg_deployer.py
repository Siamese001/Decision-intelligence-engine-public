"""
dual_pipeline/transcript_pipeline_core/uwg_deployer.py

Stage UWG: Universal Write Gateway.
Atomically deploys pipeline deliverables (Outputs 01, 02, 03) and governance receipts
with safe archival and checksum validation.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union

from dual_pipeline.archival_manager import ArchivalManager
from dual_pipeline.core import assert_disk_headroom
from dual_pipeline.meeting_paths import (
    normalize_meeting_date,
    normalize_time_str,
    sanitize_filename_slug,
)

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically writes text to path using .tmp -> fsync -> os.replace."""
    tmp_path = path.with_suffix(f"{path.suffix}.tmp_{os.getpid()}_{int(time.time() * 1000)}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise


def _resolve_base_filename(out_path: Path, metadata: Dict[str, Any]) -> Tuple[str, str, str, str]:
    name = metadata.get("standardized_slug") or sanitize_filename_slug(metadata.get("meeting_name", "Meeting"))
    date = metadata.get("date_filename")
    if not date or not re.match(r"^\d{2}-\d{2}-\d{4}$", str(date)):
        date = normalize_meeting_date(metadata.get("date"))[0]
    time_str = metadata.get("time_filename") or normalize_time_str(metadata.get("time"))
    base = f"{name}_{date}_{time_str}"
    existing_01 = list(out_path.glob("01_*_hitl_resolved_transcript.md"))
    if existing_01 and (metadata.get("no_subfolder") or metadata.get("force_rerun")):
        stem_m = re.match(r"^01_(.+)_hitl_resolved_transcript\.md$", existing_01[0].name)
        if stem_m:
            base = stem_m.group(1)
    return name, date, time_str, base


def _resolve_destination_paths(
    out_path: Path, base: str, name: str, date: str, metadata: Dict[str, Any]
) -> Tuple[Path, Path, Path, Path]:
    if metadata.get("short_deliverables"):
        clean_short = name
        p_prefix = f"{metadata.get('project', '').lower()}_"
        if p_prefix != "_" and clean_short.lower().startswith(p_prefix):
            clean_short = clean_short[len(p_prefix):]
        f1 = out_path / f"01_{clean_short}_{date}_Transcript.md"
        f2 = out_path / f"02_{clean_short}_{date}_Recap.md"
        f3 = out_path / f"03_{clean_short}_{date}_Signals.json"
    else:
        f1 = out_path / f"01_{base}_hitl_resolved_transcript.md"
        f2 = out_path / f"02_{base}_executive_recap.md"
        f3 = out_path / f"03_{base}_canonical_signal_projection.json"
    f_source = out_path / f"{base}_source.txt"
    return f1, f2, f3, f_source


def _inject_ingestion_context_to_graph(artifacts: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    try:
        graph_data = json.loads(artifacts["03_canonical_signal_projection.json"])
        if "ingestion_context" in metadata:
            graph_data["ingestion_context"] = metadata["ingestion_context"]
        if "ingestion_context_hash" in metadata:
            graph_data["ingestion_context_hash"] = metadata["ingestion_context_hash"]
        artifacts["03_canonical_signal_projection.json"] = json.dumps(graph_data, indent=2)
    except Exception as e:
        logger.warning(f"Could not inject ingestion_context into graph JSON: {e}")


def _write_notes_source_and_meta(
    out_path: Path,
    f_source: Path,
    artifacts: Dict[str, Any],
    metadata: Dict[str, Any],
    name: str,
    date: str,
) -> None:
    source_content = (
        artifacts.get("source_notes")
        or metadata.get("raw_text")
        or metadata.get("source_text")
        or ""
    )
    atomic_write_text(f_source, source_content)

    meta_file = out_path / "metadata.json"
    meta_payload = {
        "notes_mode": True,
        "is_notes": True,
        "source_type": metadata.get("source_type", "copilot_notes"),
        "outputs": ["02", "03"],
        "meeting_name": metadata.get("meeting_name", "Meeting"),
        "date": metadata.get("date"),
        "date_filename": date,
        "time": metadata.get("time"),
        "standardized_slug": name,
    }
    atomic_write_text(meta_file, json.dumps(meta_payload, indent=2))


def _write_qa_and_judge_reports(
    out_path: Path,
    qa_report: Optional[Dict[str, Any]],
    judge_report: Optional[Dict[str, Any]],
    final_decision: Optional[Dict[str, Any]],
) -> Tuple[Path, Path, Path]:
    f_qa = out_path / "qa_gates_report.json"
    f_judge = out_path / "llm_judge_evaluation.json"
    f_decision = out_path / "final_release_decision.json"

    if judge_report is not None and final_decision is not None:
        j_digest = judge_report.get("evaluation_receipt_digest")
        d_manifest = final_decision.get("artifact_manifest", {}) if isinstance(final_decision, dict) else {}
        d_digest = d_manifest.get("judge_evaluation_digest") or final_decision.get("judge_evaluation_digest")
        if j_digest and d_digest and j_digest != d_digest:
            raise RuntimeError(
                f"UWG deployer rejected artifact write: judge evaluation digest '{j_digest}' != "
                f"decision manifest digest '{d_digest}'"
            )

    if qa_report is not None:
        atomic_write_text(f_qa, json.dumps(qa_report, indent=2))
    if judge_report is not None:
        atomic_write_text(f_judge, json.dumps(judge_report, indent=2))
    if final_decision is not None:
        atomic_write_text(f_decision, json.dumps(final_decision, indent=2))
    return f_qa, f_judge, f_decision


def _write_presentation_intake_if_present(
    out_path: Path,
    presentation_path: Optional[Union[str, Path]],
    metadata: Dict[str, Any],
) -> str:
    if not presentation_path:
        return ""
    pres_p = Path(presentation_path)
    if not (pres_p.exists() and pres_p.suffix.lower() == ".pptx"):
        return ""
    from dual_pipeline.core import normalize_iso_date
    from tools.prepare_presentation_intake import build_pptx_intake_package
    pres_date = normalize_iso_date(metadata.get("date")) or "2026-09-11"
    pptx_pkg = build_pptx_intake_package(
        pres_p,
        title=metadata.get("meeting_name", ""),
        date_str=pres_date,
        classification=metadata.get("classification", "confidential"),
    )
    pkg_file = out_path / f"{pres_p.stem}.intake.json"
    pkg_file.write_text(json.dumps(pptx_pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pkg_md = out_path / f"{pres_p.stem}.intake.md"
    pkg_md.write_text(pptx_pkg["source"]["extracted_markdown"], encoding="utf-8")
    logger.info(f"UWG: wrote presentation intake package and markdown to {pkg_file}")
    return str(pkg_file)


def _inject_arc_into_output3(artifacts: Dict[str, Any], tone_analyzer: Any, arc: Any) -> None:
    if "03_canonical_signal_projection.json" not in artifacts:
        return
    try:
        curr_o3 = json.loads(artifacts["03_canonical_signal_projection.json"])
        if not curr_o3.get("tone_signals"):
            curr_o3["tone_signals"] = tone_analyzer.format_output_3_tone_signals(arc)
            artifacts["03_canonical_signal_projection.json"] = json.dumps(curr_o3, indent=2)
    except Exception:
        pass


def _inject_tone_arc(artifacts: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    try:
        from dual_pipeline.tone_analysis_core import ParalinguisticToneAnalyzer
        tone_analyzer = ParalinguisticToneAnalyzer()
        out1_text = artifacts.get("01_hitl_resolved_transcript.md", "")
        turns = metadata.get("turns") or (tone_analyzer.parse_turns_from_text(out1_text) if out1_text else [])
        arch_val = str(metadata.get("archetype") or metadata.get("meeting_type") or "").upper()
        is_tech = any(k in arch_val for k in ("TECH", "ARCH", "ENG", "DEEP_DIVE"))
        if not (turns and len(turns) >= 6):
            return
        arc = tone_analyzer.analyze_turns(turns)
        if "02_executive_recap.md" in artifacts and not is_tech:
            artifacts["02_executive_recap.md"] = tone_analyzer.inject_tone_arc_into_recap(
                artifacts["02_executive_recap.md"], arc
            )
        _inject_arc_into_output3(artifacts, tone_analyzer, arc)
    except Exception as e:
        logger.warning(f"Tone analysis injection bypassed: {e}")


def _refresh_decision_manifest_seal(
    artifacts: Dict[str, Any], final_decision: Optional[Dict[str, Any]]
) -> None:
    if not isinstance(final_decision, dict):
        return
    manifest = final_decision.get("artifact_manifest")
    if not isinstance(manifest, dict):
        return
    import hashlib
    from dual_pipeline.release_policy import compute_decision_seal

    for idx, key in (
        (1, "01_hitl_resolved_transcript.md"),
        (2, "02_executive_recap.md"),
        (3, "03_canonical_signal_projection.json"),
    ):
        txt = artifacts.get(key)
        if isinstance(txt, str):
            manifest[f"output_{idx}_sha256"] = hashlib.sha256(txt.encode("utf-8")).hexdigest()
    manifest["decision_seal_sha256"] = compute_decision_seal(manifest)


def _validate_stakeholders(
    artifacts: Dict[str, Any], final_decision: Optional[Dict[str, Any]] = None
) -> None:
    _refresh_decision_manifest_seal(artifacts, final_decision)
    from dual_pipeline.transcript_pipeline_core.stakeholder_validator import StakeholderResolutionValidator
    val = StakeholderResolutionValidator()
    try:
        o3_dict = json.loads(artifacts.get("03_canonical_signal_projection.json", "{}"))
    except Exception:
        o3_dict = {}
    val.validate_deliverables(
        output_1_text=artifacts.get("01_hitl_resolved_transcript.md", ""),
        output_2_text=artifacts.get("02_executive_recap.md", ""),
        output_3_payload=o3_dict,
        fail_closed=True,
    )


def deploy_uwg_artifacts(
    artifacts: Dict[str, Any],
    metadata: Dict[str, Any],
    output_dir: str,
    qa_report: Optional[Dict[str, Any]] = None,
    judge_report: Optional[Dict[str, Any]] = None,
    final_decision: Optional[Dict[str, Any]] = None,
    presentation_path: Optional[Union[str, Path]] = None,
) -> Dict[str, str]:
    """
    Writes the deliverable triad (01, 02, 03) and mandatory QA/Judge reports to disk.
    Called only after all gates pass or for failure diagnostic records.
    """
    out_path = Path(output_dir)
    assert_disk_headroom(out_path)
    name, date, time_str, base = _resolve_base_filename(out_path, metadata)

    if out_path.exists():
        root_dir = out_path.parent.parent
        project = out_path.parent.name
        date_folder = out_path.name
        archival_mgr = ArchivalManager(str(root_dir))
        archival_mgr.archive_if_exists(project, date_folder, meeting_stem=base)

    out_path.mkdir(parents=True, exist_ok=True)

    is_notes_mode = bool(
        metadata.get("notes_mode")
        or metadata.get("is_notes")
        or metadata.get("source_type") in ("copilot_notes", "meeting_notes", "notes")
        or metadata.get("outputs") == ["02", "03"]
    )

    f1, f2, f3, f_source = _resolve_destination_paths(out_path, base, name, date, metadata)
    _inject_ingestion_context_to_graph(artifacts, metadata)
    _inject_tone_arc(artifacts, metadata)
    _validate_stakeholders(artifacts, final_decision)

    if not is_notes_mode and "01_hitl_resolved_transcript.md" in artifacts:
        atomic_write_text(f1, artifacts["01_hitl_resolved_transcript.md"])
    atomic_write_text(f2, artifacts["02_executive_recap.md"])
    atomic_write_text(f3, artifacts["03_canonical_signal_projection.json"])

    if is_notes_mode:
        _write_notes_source_and_meta(out_path, f_source, artifacts, metadata, name, date)

    f_qa, f_judge, f_decision = _write_qa_and_judge_reports(
        out_path, qa_report, judge_report, final_decision
    )
    pres_intake_file = _write_presentation_intake_if_present(out_path, presentation_path, metadata)

    logger.info(f"UWG: wrote canonical artifacts and mandatory QA/Judge reports to {out_path}")
    result = {
        "01_transcript": "" if is_notes_mode else str(f1),
        "02_recap": str(f2),
        "03_graph": str(f3),
        "output_dir": str(out_path),
        "source_notes": str(f_source) if is_notes_mode else "",
        "qa_gates_report": str(f_qa) if qa_report is not None else "",
        "llm_judge_evaluation": str(f_judge) if judge_report is not None else "",
        "final_release_decision": str(f_decision) if final_decision is not None else "",
    }
    if pres_intake_file:
        result["presentation_intake"] = pres_intake_file
    return result


def authorize_mutation_for_publication(
    written: Dict[str, Any],
    package_path: Path,
    metadata: Dict[str, Any],
    canon_runtime_root: Path,
) -> Dict[str, Any]:
    """Validates pre-mutation operator authorization before graph deployment."""
    from dual_pipeline.canonical_pipeline.document_resume import is_document_request
    if is_document_request(metadata):
        return {"authorized": True, "status": "APPROVED", "is_document": True}
    from dual_pipeline.hitl_governance_core.mutation_authorization import validate_mutation_authorization
    out_target = Path(written.get("03_graph") or package_path.parent)
    if out_target.is_file():
        out_target = out_target.parent
    return validate_mutation_authorization(
        output_dir=out_target,
        metadata=metadata,
        written=written,
        runtime_root=metadata.get("runtime_root") or canon_runtime_root,
    )


def _record_ledger_activation(
    req_id: str, gen_id: str, pub_res: Any, metadata: Dict[str, Any], canon_runtime_root: Path
) -> None:
    try:
        from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
        rt_root = Path(metadata.get("runtime_root") or canon_runtime_root).resolve()
        l_paths = [rt_root / "graph_mutation_ledger.json"]
        if (rt_root.parent / "ledger").is_dir():
            l_paths.append(rt_root.parent / "ledger" / "graph_mutation_ledger.json")
        mut_sum = pub_res.get("mutation_summary") or pub_res.get("ingestion_completion", {}).get("mutation_summary") if isinstance(pub_res, dict) else None
        nodes = mut_sum.get("nodes_upserted", 0) if isinstance(mut_sum, dict) else 0
        assertions = mut_sum.get("assertions_upserted", 0) if isinstance(mut_sum, dict) else 0
        graph_p = metadata.get("graph_path")
        for lp in l_paths:
            ledger = GraphMutationLedger(lp)
            ledger.record_mutation(run_id=req_id, node_count=nodes, assertion_count=assertions, target_graph_path=graph_p, runtime_root=rt_root, generation_id=str(gen_id))
            ledger.record_release_activation(run_id=req_id, generation_id=str(gen_id), target_graph_path=graph_p, runtime_root=rt_root)
    except Exception as led_err:
        logger.debug("Failed updating GraphMutationLedger on activation: %s", led_err)


def record_mutation_activation(
    auth_info: Dict[str, Any],
    pub_res: Any,
    metadata: Dict[str, Any],
    canon_runtime_root: Path,
) -> None:
    """Marks mutation request as activated following successful ingestion."""
    if auth_info.get("is_document"):
        return
    from dual_pipeline.hitl_governance_core.mutation_authorization import mark_mutation_activated
    req_id = auth_info.get("request_id")
    if req_id and pub_res:
        gen_id = (
            pub_res.get("generation_id")
            or pub_res.get("active_pair", {}).get("generation_id")
            or pub_res.get("ingestion_completion", {}).get("generation_id")
            or pub_res.get("ingestion_completion", {}).get("generation_after")
            or auth_info.get("active_generation")
            or ""
        )
        if gen_id:
            eff_rt = (
                canon_runtime_root
                if canon_runtime_root and (not metadata.get("runtime_root") or ".worktrees" in Path(metadata["runtime_root"]).parts)
                else (metadata.get("runtime_root") or canon_runtime_root)
            )
            mark_mutation_activated(
                request_id=req_id,
                runtime_root=eff_rt,
                activated_generation_id=str(gen_id),
                activation_receipt=pub_res if isinstance(pub_res, dict) else {},
            )
            _record_ledger_activation(req_id, str(gen_id), pub_res, metadata, canon_runtime_root)


def build_publish_args(
    package_path: Union[Path, str, Dict[str, Any]],
    dir_id: str = "dir_default",
    meeting_disp: str = "meeting",
    reviewer: str = "operator",
    rationale: Optional[str] = None,
    date_disp: str = "2026-10-02",
    project_disp: str = "knowledge",
    embedding_backend: str = "mock",
    metadata: Optional[Dict[str, Any]] = None,
    canon_runtime_root: Optional[Path] = None,
    root_path: Optional[Path] = None,
    resolve_model_asset_fn: Optional[Any] = None,
) -> argparse.Namespace:
    """Builds the argparse Namespace required for intake publish."""
    rev = "5617a9f61b028005a4858fdac845db406aefb181"
    meta = metadata or {}
    rt_root = canon_runtime_root or Path.cwd()
    r_path = root_path or Path.cwd()
    res_fn = resolve_model_asset_fn or (lambda p, e: Path(p))
    pkg = package_path if isinstance(package_path, (Path, str)) else Path("deliverables.zip")
    return argparse.Namespace(
        package=[pkg],
        directive_id=dir_id,
        scope=f"Transcript meeting ingestion: {meeting_disp}",
        reviewer=reviewer,
        rationale=rationale or f"Ingest {meeting_disp} ({date_disp}) into {project_disp} knowledge graph",
        embedding_backend=embedding_backend,
        allow_dev_bge_m3=(embedding_backend == "bge-m3-dev"),
        allow_internal_operational=False,
        internal_operational_policy=None,
        model_path=res_fn(f"models/bge-m3/{rev}", "NEE_BGE_M3_MODEL_PATH"),
        model_manifest=res_fn(f"models/bge-m3/bge-m3-manifest-{rev}.json", "NEE_BGE_M3_MODEL_MANIFEST"),
        model_revision=rev,
        runtime_root=(
            canon_runtime_root
            if canon_runtime_root and (not meta.get("runtime_root") or ".worktrees" in Path(meta["runtime_root"]).parts)
            else (Path(meta["runtime_root"]).resolve() if meta.get("runtime_root") else rt_root)
        ),
        graph=Path(meta["graph_path"]).resolve() if meta.get("graph_path") else None,
        auth_token=(
            meta.get("auth_token")
            or meta.get("operator_token")
            or (meta.get("approval_receipt", {}).get("auth_token") if isinstance(meta.get("approval_receipt"), dict) else None)
        ),
        auth_public_key=(
            meta.get("auth_public_key")
            or (meta.get("approval_receipt", {}).get("auth_public_key") if isinstance(meta.get("approval_receipt"), dict) else None)
        ),
        auth_issuer=(
            meta.get("auth_issuer")
            or (meta.get("approval_receipt", {}).get("auth_issuer") if isinstance(meta.get("approval_receipt"), dict) else None)
        ),
        auth_audience=(
            meta.get("auth_audience")
            or (meta.get("approval_receipt", {}).get("auth_audience") if isinstance(meta.get("approval_receipt"), dict) else None)
        ),
        embedding_device="auto", embedding_fp16=None, audience="internal_consulting,nee-engineering",
        semantic_cache_mode=None, ingestion_reasoning="off",
        ingestion_reasoning_policy=r_path / "requirements" / "ingestion-reasoning-policy.json",
        offline_ingestion_reasoning=True,
        a2a_finalization_receipt=None,
        batch_manifest=None,
        require_bge_m3=True,
    )


