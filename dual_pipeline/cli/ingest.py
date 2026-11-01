"""
dual_pipeline/cli/ingest.py

Canonical Ingestion CLI Handler for 'nee ingest'.
Unifies meeting audio, diarized transcripts, copilot notes, and written documents
into a single governed execution path driven by CanonicalAgenticPipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.canonical_pipeline.modalities import (
    AUDIO_EXTENSIONS, DOCUMENT_EXTENSIONS, DUAL_USE_TEXT_EXTENSIONS, IMAGE_EXTENSIONS,
    _read_content_sample, is_unambiguous_document_extension,
)
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass

logger = logging.getLogger("nee.cli.ingest")

def is_document_source(input_path: Any, metadata: Any = None) -> bool:
    """Helper to detect if an input file is a document rather than a speaker transcript."""
    if metadata and metadata.get("is_document"):
        return True
    p = Path(input_path).expanduser().resolve() if input_path else None
    if not p or not p.is_file() or p.suffix.lower() in AUDIO_EXTENSIONS:
        return False
    if p.suffix.lower() in IMAGE_EXTENSIONS:
        return True
    from dual_pipeline.transcript_metadata_helper import is_document_content
    return is_document_content(_read_content_sample(p, None, p.suffix.lower()), p.name)

def _discover_colocated_speaker_map(input_candidate: Any) -> Dict[str, Any] | None:
    """Auto-detect co-located speaker_map.json sidecar next to input file via SSOT loader."""
    from dual_pipeline.transcript_pipeline_core.speaker_map_loader import load_colocated_speaker_map
    return load_colocated_speaker_map(input_candidate)

def _validate_semantic_flag_conflicts(is_doc: bool, is_notes: bool, is_out3: bool) -> None:
    if is_doc and is_notes:
        raise ValueError("Contradictory options: Cannot specify both --document and --notes.")


def _extract_input_candidate(args: argparse.Namespace, meta: Optional[Dict[str, Any]] = None) -> Any:
    cand = (meta.get("input_path") if meta else None) or getattr(args, "input", None) or getattr(args, "source", None)
    if not cand:
        batch_inputs = getattr(args, "inputs", None)
        if batch_inputs:
            cand = batch_inputs[0]
    return cand


def _should_infer_document_modality(
    args: argparse.Namespace, is_notes: bool, meta: Optional[Dict[str, Any]] = None
) -> bool:
    if is_notes or getattr(args, "command", "") == "record-meeting":
        return False
    if meta and meta.get("is_document"):
        return True
    input_val = _extract_input_candidate(args, meta)
    ext = Path(str(input_val)).suffix.lower() if input_val else ""
    if is_unambiguous_document_extension(ext):
        return True
    if input_val and ext in DUAL_USE_TEXT_EXTENSIONS and Path(str(input_val)).expanduser().is_file():
        return is_document_source(input_val, meta)
    return False


def _apply_semantic_flags(meta: Dict[str, Any], args: argparse.Namespace) -> None:
    is_doc = bool(getattr(args, "document", False) or getattr(args, "command", "") == "ingest-document" or meta.get("is_document"))
    is_notes = bool(getattr(args, "notes", False) or getattr(args, "copilot_notes", False) or meta.get("notes_mode") or meta.get("is_notes"))
    is_out3 = bool(getattr(args, "output3_only", False) or meta.get("output3_only", False))
    input_val = _extract_input_candidate(args, meta)
    ext = Path(str(input_val)).suffix.lower() if input_val else ""
    if ext in IMAGE_EXTENSIONS:
        meta.update({"allow_visual_image": True, "enable_visual_ocr": True, "is_visual_document": True})
        is_doc = True
    _validate_semantic_flag_conflicts(is_doc, is_notes, is_out3)
    from dual_pipeline.transcript_stages_core.output01_parser import apply_track1_track2_semantic_defaults
    if is_notes:
        meta.update({"notes_mode": True, "is_notes": True, "skip_output_1": True, "canonical_modality": CanonicalModality.MEETING_NOTES.value})
        meta.setdefault("outputs", ["02", "03"])
        meta.setdefault("source_type", "copilot_notes")
    elif is_doc or _should_infer_document_modality(args, is_notes, meta):
        is_doc, is_out3 = True, bool(is_out3 or not (getattr(args, "regenerate", None) or meta.get("regenerate")))
        meta.update({"is_document": True, "canonical_modality": CanonicalModality.DOCUMENT.value, "output3_only": is_out3, "skip_output_1": True, "skip_output_2": True, "outputs": ["03"]})
        meta.setdefault("source_type", "document")
    else:
        meta.setdefault("outputs", ["01", "02", "03"])
    apply_track1_track2_semantic_defaults(meta, input_val, is_doc, is_notes, is_out3)


def _load_metadata_file_arg(args: argparse.Namespace) -> Dict[str, Any]:
    meta_path = getattr(args, "metadata", None)
    if not meta_path:
        return {}
    p = Path(meta_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"--metadata file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        ephemeral = ("state_machine", "circuit_breaker", "remediation_directive", "remediation_directive_forensic", "remediation_directive_recap", "remediation_directive_graph")
        return {k: v for k, v in data.items() if not (k in ephemeral and isinstance(v, str) and v.startswith("<dual_pipeline"))}
    except json.JSONDecodeError as e:
        raise ValueError(f"--metadata file contains invalid JSON: {p}: {e}") from e


_FLAG_ATTR_MAP = (
    ("project", "project"), ("meeting", "meeting_name"), ("title", "title"), ("date", "date"), ("time", "time"),
    ("classification", "classification"), ("platform", "platform"), ("author", "author"), ("attendees", "attendees"),
    ("archetype", "archetype"), ("force_rerun", "force_rerun"), ("allow_duplicate", "allow_duplicate"),
    ("collision_override_rationale", "collision_override_rationale"), ("no_subfolder", "no_subfolder"),
    ("progress_mode", "progress_mode"), ("enforce_worktree", "enforce_worktree"), ("worktree_dir", "worktree_dir"),
    ("worktree_branch", "worktree_branch"), ("cleanup_worktree", "cleanup_worktree"), ("filename_slug", "filename_slug"),
    ("resume", "resume"), ("preserve_partial", "preserve_partial"), ("auth_token", "auth_token"), ("backtest", "backtest"),
    ("regenerate", "regenerate"), ("run_mode", "run_mode"), ("runtime_root", "runtime_root"), ("graph", "graph_path"),
    ("audiences", "audiences"), ("audience", "audiences"), ("no_cache", "no_cache"),
    ("conversion_cache_dir", "conversion_cache_dir"), ("conversion_cache", "conversion_cache"),
    ("backend", "backend"), ("whisper_model", "whisper_model"), ("batch_queue", "batch_queue"),
    ("fast_promote", "fast_promote"),
)


def _extract_flag_attributes(args: argparse.Namespace, meta: Dict[str, Any]) -> None:
    for flag, key in _FLAG_ATTR_MAP:
        val = getattr(args, flag, None)
        if val is not None:
            meta[key] = str(val) if isinstance(val, Path) else val
    if getattr(args, "worktree_dir", None) is not None or meta.get("worktree_dir"):
        meta["preserve_metadata_worktree"] = True
    if meta.get("no_cache"):
        meta["disable_conversion_cache"] = True


def _parse_json_or_file_flag(val: Any) -> Any:
    if not val:
        return val
    clean = val.strip().strip("'\"") if isinstance(val, str) else val
    try:
        if isinstance(clean, str) and clean.startswith(("{", "[")):
            return json.loads(clean)
        if isinstance(val, str) and Path(val).is_file():
            return json.loads(Path(val).read_text(encoding="utf-8"))
    except Exception:
        return val
    return val


def _resolve_speaker_map(args: argparse.Namespace, meta: Dict[str, Any]) -> None:
    spk = _parse_json_or_file_flag(getattr(args, "speaker_map", None)) or _discover_colocated_speaker_map(
        getattr(args, "input", None) or getattr(args, "source", None)
    )
    if spk:
        meta["speaker_map"] = spk


def _apply_backtest_flags(args: argparse.Namespace, meta: Dict[str, Any]) -> None:
    if meta.get("backtest"):
        meta.update({"run_mode": "backtest", "force_rerun": True, "judge_tier": getattr(args, "judge_tier", None) or "fast"})
        meta.setdefault("regenerate", "02,03")


def _is_fuzzy_resolvable_input(input_val: Any, p_val: Path, args: argparse.Namespace) -> bool:
    s_val = str(input_val)
    return not getattr(args, "resume", False) and not p_val.exists() and not s_val.startswith(("http://", "https://")) and "\n" not in s_val and len(s_val) < 4096


def _update_args_input_path(args: argparse.Namespace, new_path: str) -> None:
    if hasattr(args, "input") and getattr(args, "input", None):
        args.input = new_path
    elif hasattr(args, "source") and getattr(args, "source", None):
        args.source = new_path


def _merge_discovered_metadata_file(meta_file: Path, meta: Dict[str, Any]) -> None:
    try:
        for k, v in json.loads(meta_file.read_text(encoding="utf-8")).items():
            if k not in ("state_machine", "circuit_breaker") and not str(v).startswith("<dual_pipeline"):
                meta.setdefault(k, v)
    except Exception as err:
        logger.warning(f"Failed to auto-load metadata {meta_file}: {err}")


def _rebind_worktree_input_path(args: argparse.Namespace, meta: Dict[str, Any], input_val: Any, p_val: Path) -> Tuple[str, Path]:
    wt_raw = getattr(args, "worktree_dir", None) or meta.get("worktree_dir")
    if wt_raw and not p_val.is_absolute() and not p_val.exists():
        wt_cand = (Path(str(wt_raw)).expanduser().resolve() / p_val).resolve()
        if wt_cand.exists():
            _update_args_input_path(args, str(wt_cand))
            return str(wt_cand), wt_cand
    return str(input_val), p_val


def _hydrate_input_and_colocated_metadata(args: argparse.Namespace, meta: Dict[str, Any]) -> None:
    input_val = getattr(args, "input", None) or getattr(args, "source", None)
    if not input_val:
        meta["input_ext"] = ""
        return
    input_val, p_val = _rebind_worktree_input_path(args, meta, input_val, Path(str(input_val)))
    if _is_fuzzy_resolvable_input(input_val, p_val, args):
        matched_p, meta_p = _resolve_input_resource_fuzzy(str(input_val), args=args, meta=meta)
        if matched_p != p_val:
            input_val, p_val = str(matched_p), matched_p
            _update_args_input_path(args, input_val)
            if not getattr(args, "metadata", None) and meta_p and not meta.get("metadata_path"):
                meta["metadata_path"], args.metadata = str(meta_p), str(meta_p)
                _merge_discovered_metadata_file(meta_p, meta)
    if not getattr(args, "metadata", None) and not meta.get("metadata_path"):
        cand = (p_val if p_val.is_dir() else p_val.parent) / "metadata.json"
        if cand.is_file():
            meta["metadata_path"] = str(cand)
            _merge_discovered_metadata_file(cand, meta)
    meta.update({"input_path": str(input_val), "input_ext": Path(str(input_val)).suffix.lower()})
    from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
    hydrate_live_container_artifacts(meta, input_path=input_val)


def build_canonical_metadata(args: argparse.Namespace) -> Dict[str, Any]:
    """Extracts and normalizes metadata from CLI arguments."""
    meta = _load_metadata_file_arg(args)
    _extract_flag_attributes(args, meta)
    _apply_backtest_flags(args, meta)
    _resolve_speaker_map(args, meta)
    res_val = _parse_json_or_file_flag(getattr(args, "entity_resolutions", None))
    if res_val:
        meta["entity_resolutions"] = res_val
        from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import _merge_explicit_resolutions
        _merge_explicit_resolutions(meta, meta.setdefault("entity_clarifications", []))
    attendees_val = meta.get("attendees")
    if isinstance(attendees_val, str) and attendees_val.strip().startswith(("[", "{")):
        try:
            meta["attendees"] = json.loads(attendees_val)
        except Exception as e:
            logger.warning(f"Could not parse attendees JSON string: {e}")
    if getattr(args, "augment", False) or getattr(args, "augment_existing", False):
        meta["augmentation_mode"] = True
    if getattr(args, "transcript_cache", None):
        meta["transcript_cache_path"] = str(args.transcript_cache)
    if meta.get("allow_duplicate") and not meta.get("collision_override_rationale"):
        raise ValueError("--collision-override-rationale is required when --allow-duplicate is used.")
    if getattr(args, "bypass_speaker_hitl", False):
        raise ValueError("HITL cannot be removed, disabled, or bypassed in Stage U0 ingestion (--bypass-speaker-hitl).")
    _hydrate_input_and_colocated_metadata(args, meta)
    _apply_semantic_flags(meta, args)
    assert_no_u0_hitl_bypass(meta)
    return meta


def _disambiguate_input_slug(in_p: Any) -> str:
    if not in_p:
        return "meeting"
    p_obj = Path(str(in_p)).expanduser()
    stem_slug = re.sub(r"[^a-z0-9_]+", "", p_obj.stem.lower().replace(" ", "_").replace("-", "_")) or "doc"
    raw_bytes = p_obj.read_bytes() if p_obj.is_file() else str(in_p).encode("utf-8")
    return f"{stem_slug[:32]}_{hashlib.sha256(raw_bytes).hexdigest()[:8]}"


def _generate_default_directive_id(metadata: Dict[str, Any]) -> str:
    if metadata.get("directive_id"):
        return str(metadata["directive_id"])
    raw_project = str(metadata.get("project") or "knowledge").lower()
    project = re.sub(r"[^a-z0-9_\-]+", "_", raw_project).strip("_") or "knowledge"
    meeting_raw = metadata.get("meeting_name") or metadata.get("title")
    raw_date = metadata.get("date_filename") or metadata.get("date")
    if not meeting_raw and not raw_date:
        slug = _disambiguate_input_slug(metadata.get("input_path") or metadata.get("input") or metadata.get("source") or metadata.get("source_name"))
        return f"directive-{project}-{slug}-00-00-0000-v1"
    slug = re.sub(r"[^a-z0-9_]+", "", str(meeting_raw or "meeting").lower().replace(" ", "_")) or "meeting"
    date_slug = str(raw_date or "00-00-0000").replace("/", "-")
    return f"directive-{project}-{slug}-{date_slug}-v1"


def _record_cleared_approval_receipt(metadata: Dict[str, Any], clear_rec: Optional[Dict[str, Any]]) -> None:
    if clear_rec:
        if (metadata.get("mutation_approval_receipt") or {}).get("status") == "ACTIVATED":
            return
        effective_rev = clear_rec.get("operator_id") or clear_rec.get("reviewer") or "human_operator"
        meeting = metadata.get("meeting_name") or metadata.get("title") or "Meeting"
        receipt = {
            "request_id": clear_rec.get("decision_id"),
            "status": "APPROVED",
            "authorized": True,
            "operator_origin": effective_rev,
            "approved_at": clear_rec.get("recorded_at"),
            "expected_base_generation": "current",
            "raw_clearance_record": clear_rec,
            "meeting_name": meeting,
        }
        metadata["approval_receipt"] = receipt
        metadata["mutation_approval_receipt"] = receipt
        metadata.setdefault("reviewer", effective_rev)


def _resolve_canonical_ingest_clearance(metadata: Dict[str, Any], directive_id: str, args: Any) -> Optional[Dict[str, Any]]:
    from dual_pipeline.hitl_governance_core.active_ingestion_approval import check_active_ingestion_clearance
    rt_root = metadata.get("runtime_root") or getattr(args, "runtime_root", None) or os.environ.get("NEE_RUNTIME_ROOT")
    ledger_p = None
    if rt_root:
        rt_path = Path(rt_root).resolve()
        for fn in ("hitl_decision_ledger.sqlite", "hitl_decision_ledger.json"):
            if (rt_path / fn).is_file():
                ledger_p = rt_path / fn
                break
    is_cleared, clear_reason, clear_rec = check_active_ingestion_clearance(
        run_id=metadata.get("run_id") or directive_id, directive_id=directive_id,
        delta_digest=metadata.get("proposed_delta_digest") or "", ledger_path=ledger_p,
    )
    if is_cleared:
        _record_cleared_approval_receipt(metadata, clear_rec)
        return None
    if "DECLINED" in clear_reason:
        return {"status": "DECLINED", "directive_id": directive_id, "reason": clear_reason, "graph_mutation_committed": False}
    return None


def _resolve_model_and_effort(args: argparse.Namespace) -> Tuple[str, str, str]:
    synthesis_model = getattr(args, "model", None)
    judge_model = getattr(args, "judge_model", None) or synthesis_model or resolve_tier_model_name(ModelTier.DIALECTICAL_JUDGE)
    eff_model = synthesis_model or judge_model
    reasoning_effort = getattr(args, "reasoning_effort", None) or "high"
    for suffix, eff in (("-xhigh", "xhigh"), ("-max", "max"), ("-high", "high")):
        if judge_model.endswith(suffix):
            judge_model = judge_model[:-len(suffix)]
            reasoning_effort = eff
            break
    return eff_model, judge_model, reasoning_effort


def _validate_offline_provider(provider: str) -> None:
    if provider == "offline" and not any(os.environ.get(k) for k in ("PYTEST_CURRENT_TEST", "NEE_ALLOW_TEST_FIXTURES", "RUNNING_TESTS_WITH_PROGRESS", "NEE_TEST_MODE")):
        raise ValueError("FAIL_CLOSED: '--provider offline' is prohibited on production CLI ingestion.")


_AUDIO_BINARY_EXTS = frozenset({".wav", ".wave", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".webm", ".mp4"})


def _assert_no_speaker_collapse(input_val: Any, metadata: Dict[str, Any]) -> None:
    """Pre-clearance check to reject single-speaker collapsed transcripts fail-fast."""
    if metadata.get("is_document") or (metadata.get("freeze_output_1") and metadata.get("resume")) or not input_val:
        return
    in_p = Path(str(input_val))
    if not in_p.is_file() or in_p.suffix.lower() in _AUDIO_BINARY_EXTS:
        return
    from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import extract_roster_with_roles
    from dual_pipeline.speaker_attribution_core.collapse_detector import check_speaker_skew_and_collapse
    roster = metadata.get("roster") or extract_roster_with_roles(metadata)
    if roster and len(roster) >= 2:
        check_speaker_skew_and_collapse(in_p.read_text(encoding="utf-8", errors="replace"), roster, metadata)


def _extract_ingest_attachments(args: argparse.Namespace) -> Tuple[List[str], Optional[str]]:
    attachment_list: List[str] = []
    for att_attr in ("attachment", "attachments"):
        att_val = getattr(args, att_attr, None)
        if att_val:
            attachment_list.extend(str(a) for a in (att_val if isinstance(att_val, list) else [att_val]) if a)
    att_dir = str(args.attachment_dir) if getattr(args, "attachment_dir", None) else None
    return attachment_list, att_dir


def _resolve_auto_auth_token(args: argparse.Namespace, reviewer: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    auth_tok = getattr(args, "auth_token", None)
    auto_auth = getattr(args, "auto_auth", False)
    is_auto_tok = bool(auth_tok and str(auth_tok) == "auto")
    if not ((auto_auth and not auth_tok) or is_auto_tok):
        return auth_tok, reviewer
    from dual_pipeline.credentials import CredentialManager
    configured_rev = CredentialManager.get_credential("NEE_REVIEWER") or CredentialManager.get_credential("USER")
    base_allowed = {"human_operator", "operator"}
    if configured_rev:
        base_allowed.add(str(configured_rev).strip())
    if reviewer and str(reviewer).strip():
        base_allowed.add(str(reviewer).strip())
    eff_rev = reviewer if (reviewer and str(reviewer).strip() in base_allowed) else ("operator" if is_auto_tok else configured_rev)
    if not eff_rev:
        return auth_tok, reviewer
    tok = json.dumps({"sub": eff_rev, "capabilities": ["knowledge:release", "knowledge:governance", "knowledge:intake"]})
    return tok, eff_rev


def _resolve_ingest_rbac_authorization(
    args: argparse.Namespace, reviewer: Optional[str], rationale: Optional[str], metadata: Dict[str, Any]
) -> Optional[str]:
    if metadata.get("approval_receipt", {}).get("authorized"):
        return reviewer or metadata["approval_receipt"].get("operator_origin") or metadata["approval_receipt"].get("reviewer")
    from dual_pipeline.rbac import validate_mutation_authorization
    auth_tok = getattr(args, "auth_token", None)
    if (reviewer in (None, "operator")) and getattr(args, "auto_auth", False) and not auth_tok:
        eff_rev = reviewer or "operator"
        metadata["rbac_auth"] = {
            "authorized": False, "subject": eff_rev, "capabilities": ["knowledge:intake"],
            "auth_type": "DEFERRED_HITL_APPROVAL", "awaiting_interactive_approval": True,
        }
        return reviewer
    auth_tok, reviewer = _resolve_auto_auth_token(args, reviewer)
    if not (auth_tok or reviewer):
        return reviewer
    rbac_res = validate_mutation_authorization(auth_tok, reviewer)
    metadata["rbac_auth"] = rbac_res
    if rbac_res and rbac_res.get("authorized"):
        resolved_rev = rbac_res.get("subject") or reviewer or "human_operator"
        import datetime as dt
        req_id = f"rbac-{metadata.get('run_id') or metadata.get('directive_id') or 'auth'}"
        _record_cleared_approval_receipt(metadata, {
            "decision_id": req_id, "operator_id": resolved_rev, "reviewer": resolved_rev,
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "rationale": rationale, "capabilities": rbac_res.get("capabilities", []),
        })
        return resolved_rev
    return reviewer


def _resolve_default_rationale(metadata: Dict[str, Any], rationale: Optional[str]) -> str:
    if rationale:
        return rationale
    m = metadata.get("meeting_name") or metadata.get("title") or "Meeting"
    d = metadata.get("date") or metadata.get("date_filename") or "Date"
    p = metadata.get("project") or "knowledge"
    return f"Ingest {m} ({d}) into {p} knowledge graph"


def _resolve_input_resource_fuzzy(
    input_val: str, args: Optional[argparse.Namespace] = None, meta: Optional[Dict[str, Any]] = None,
) -> Tuple[Path, Optional[Path]]:
    """Resolves missing/unmatched input path using non-hanging fuzzy discovery."""
    from dual_pipeline.canonical_pipeline.resource_discovery import discover_resource_fuzzy
    from dual_pipeline.exceptions import HITLRequiredError
    proj = getattr(args, "project", None) if args else (meta.get("project") if meta else None)
    date = getattr(args, "date", None) if args else (meta.get("date") if meta else None)
    wt_val = (getattr(args, "worktree_dir", None) if args else None) or (meta.get("worktree_dir") if meta else None)
    res = discover_resource_fuzzy(input_val, project=proj, date=date, worktree_dir=Path(str(wt_val)) if wt_val else None)
    if res.requires_hitl:
        opts = [{"label": str(c.path), "score": c.score, "description": f"Match score: {c.score} (modality: {c.modality})"} for c in res.candidates]
        raise HITLRequiredError(
            message=f"Ambiguous resource match for '{input_val}'. Multiple candidates found within 30% margin.",
            hitl_type="resource_resolution", options=opts,
            evidence={"query": input_val, "candidates": [str(c.path) for c in res.candidates], "margin": res.confidence_margin},
        )
    if res.best_match:
        logger.info(f"[FUZZY_RESOURCE_DISCOVERY] Resolved '{input_val}' -> '{res.best_match.path}' (score={res.best_match.score}, elapsed={res.elapsed_seconds}s)")
        return res.best_match.path, res.best_match.metadata_path
    return Path(input_val), None


_BLOCKED_LEGACY_EXTENSIONS = frozenset({".msg", ".ppt", ".xls"})


def _assert_not_legacy_extension(cand: Any) -> None:
    in_ext = Path(str(cand)).suffix.lower()
    if in_ext in _BLOCKED_LEGACY_EXTENSIONS:
        raise ValueError(
            f"UNSUPPORTED_MODALITY_GOVERNED_REFUSAL: Legacy format '{in_ext}' is explicitly blocked from ingestion. "
            "Governed conversion required."
        )


def _validate_canonical_input(args: argparse.Namespace) -> Path:
    input_val = _extract_input_candidate(args)
    if not input_val:
        raise ValueError("Ingestion requires --input or --source path.")
    batch_inputs = getattr(args, "inputs", None)
    for cand in (batch_inputs if batch_inputs else [input_val]):
        _assert_not_legacy_extension(cand)
    p = Path(str(input_val))
    wt_raw = getattr(args, "worktree_dir", None)
    if wt_raw and not p.is_absolute() and not p.exists():
        wt_cand = (Path(str(wt_raw)).expanduser().resolve() / p).resolve()
        if wt_cand.exists():
            p = wt_cand
            _update_args_input_path(args, str(p))
    if _is_fuzzy_resolvable_input(input_val, p, args):
        matched_p, meta_p = _resolve_input_resource_fuzzy(str(input_val), args=args)
        _assert_not_legacy_extension(matched_p)
        if matched_p != p:
            if not getattr(args, "metadata", None) and meta_p:
                args.metadata = str(meta_p)
            _update_args_input_path(args, str(matched_p))
            return matched_p
    return p


def _report_ingest_worktree(result: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    wt_dir = metadata.get("worktree_dir") or result.get("worktree_dir")
    wt_branch = metadata.get("worktree_branch") or result.get("worktree_branch")
    if wt_dir:
        msg = f"[WORKTREE_ISOLATION] Ingestion outputs isolated in worktree: {wt_dir} (branch: {wt_branch})"
        logger.info(msg)
        print(msg, file=sys.stderr, flush=True)


def _batch_all_document_inputs(batch_inputs: Any, metadata: Dict[str, Any]) -> bool:
    if not batch_inputs:
        return False
    for item in batch_inputs:
        cand_path = item.get("input_path") if isinstance(item, dict) else item
        if not cand_path:
            return False
        ext = Path(str(cand_path)).suffix.lower()
        if not (is_unambiguous_document_extension(ext) or is_document_source(cand_path, metadata)):
            return False
    return True


def _resolve_mutation_flags(args: argparse.Namespace, metadata: Dict[str, Any]) -> Tuple[bool, bool]:
    mutate_arg = getattr(args, "mutate_graph", None)
    mutate_graph = bool(mutate_arg if mutate_arg is not None else metadata.get("mutate_graph", True))
    auto_mutate_arg = getattr(args, "auto_mutate_on_pass", None)
    auto_mutate = bool(auto_mutate_arg if auto_mutate_arg is not None else metadata.get("auto_mutate_on_pass", True))
    return mutate_graph, auto_mutate


def _prepare_parallel_batch_metadata(args: argparse.Namespace, batch_inputs: Any) -> Dict[str, Any]:
    for cand in batch_inputs:
        cand_path = cand.get("input_path") if isinstance(cand, dict) else cand
        if cand_path:
            _assert_not_legacy_extension(cand_path)
    metadata = build_canonical_metadata(args)
    metadata["enforce_worktree"] = True
    all_docs = _batch_all_document_inputs(batch_inputs, metadata)
    if not getattr(args, "document", False) and len(batch_inputs) > 1 and not all_docs:
        for k in ("is_document", "output3_only", "skip_output_1", "skip_output_2", "canonical_modality", "outputs"):
            metadata.pop(k, None)
    elif not metadata.get("is_notes") and (getattr(args, "document", False) or all_docs):
        metadata.update({"is_document": True, "canonical_modality": CanonicalModality.DOCUMENT.value})
        if not (getattr(args, "regenerate", None) or metadata.get("regenerate")):
            metadata.update({"output3_only": True, "outputs": ["03"], "skip_output_1": True, "skip_output_2": True})
    return metadata


def _maybe_dispatch_parallel_batch(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    batch_inputs = getattr(args, "inputs", None)
    if not batch_inputs:
        return None
    raw_workers = getattr(args, "parallel_workers", None)
    from dual_pipeline.canonical_pipeline.parallel_orchestrator import resolve_optimal_parallel_workers
    from dual_pipeline.document_pipeline_core.parallel_ingestor import ingest_documents_parallel
    parallel_workers = resolve_optimal_parallel_workers(
        int(raw_workers) if raw_workers is not None and int(raw_workers) > 0 else None,
        task_count=len(batch_inputs),
    )
    metadata = _prepare_parallel_batch_metadata(args, batch_inputs)
    directive_id = getattr(args, "directive_id", None) or _generate_default_directive_id(metadata)
    clearance_result = _resolve_canonical_ingest_clearance(metadata, directive_id, args)
    if clearance_result is not None:
        return clearance_result
    rationale = _resolve_default_rationale(metadata, metadata.get("rationale"))
    reviewer = _resolve_ingest_rbac_authorization(args, metadata.get("reviewer"), rationale, metadata)
    mutate_graph, auto_mutate = _resolve_mutation_flags(args, metadata)
    eff_model, _, reasoning_effort = _resolve_model_and_effort(args)
    return ingest_documents_parallel(
        documents=batch_inputs, max_workers=parallel_workers,
        provider_type=getattr(args, "provider", None) or "llm", model_name=eff_model,
        reasoning_effort=reasoning_effort or "high", embedding_backend=getattr(args, "embedding_backend", "bge-m3"),
        mutate_graph=mutate_graph, auto_mutate_on_pass=auto_mutate,
        group_commit=getattr(args, "group_commit", False), reviewer=reviewer,
        rationale=rationale, output_dir=getattr(args, "output_dir", "auto"), default_metadata=metadata,
    )


from .ingest_worktree_guard import assert_ingest_worktree_code_freeze


def _maybe_cleanup_completed_worktree(res: Dict[str, Any], result: Dict[str, Any], metadata: Dict[str, Any], args: argparse.Namespace) -> None:
    should_cleanup = bool(getattr(args, "cleanup_worktree", False) or metadata.get("cleanup_worktree", False))
    if res.get("status") != "COMPLETE" or not should_cleanup:
        return
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import teardown_ingest_worktree
    wt_dir = metadata.get("worktree_dir") or result.get("worktree_dir")
    wt_branch = metadata.get("worktree_branch") or result.get("worktree_branch")
    if wt_dir and wt_branch and teardown_ingest_worktree(Path(wt_dir), wt_branch):
        res["worktree_cleaned"] = True


def _preflight_canonical_ingest(args: argparse.Namespace) -> Tuple[str, str]:
    if getattr(args, "enforce_worktree", True) is False:
        raise ValueError("FAIL_CLOSED: Worktree isolation cannot be disabled for ingestion pipelines.")
    assert_ingest_worktree_code_freeze(getattr(args, "worktree_dir", None))
    input_val = str(_validate_canonical_input(args))
    from .model_resolution import auto_resolve_backend_config
    auto_resolve_backend_config(args)
    provider = getattr(args, "provider", None) or "llm"
    if not getattr(args, "dry_run", False):
        _validate_offline_provider(provider)
    return input_val, provider


def handle_canonical_ingest(args: argparse.Namespace) -> Dict[str, Any]:
    """Universal handler executing CanonicalAgenticPipeline across all modalities."""
    input_val, provider = _preflight_canonical_ingest(args)
    batch_res = _maybe_dispatch_parallel_batch(args)
    if batch_res is not None:
        return batch_res

    metadata = build_canonical_metadata(args)
    if metadata.get("worktree_dir") and not getattr(args, "worktree_dir", None):
        assert_ingest_worktree_code_freeze(metadata["worktree_dir"])
    metadata["enforce_worktree"] = True
    eff_model, judge_model, reasoning_effort = _resolve_model_and_effort(args)
    attachment_list, att_dir = _extract_ingest_attachments(args)

    directive_id = getattr(args, "directive_id", None) or _generate_default_directive_id(metadata)
    clearance_result = _resolve_canonical_ingest_clearance(metadata, directive_id, args)
    if clearance_result is not None:
        return clearance_result

    rationale = _resolve_default_rationale(metadata, metadata.get("rationale"))
    reviewer = _resolve_ingest_rbac_authorization(args, metadata.get("reviewer"), rationale, metadata)
    _assert_no_speaker_collapse(input_val, metadata)
    from dual_pipeline.cli.intake_approval_helper import attach_cli_judge_resolver
    attach_cli_judge_resolver(metadata)
    mutate_graph, auto_mutate = _resolve_mutation_flags(args, metadata)

    pipeline = CanonicalAgenticPipeline(provider_type=provider, model_name=eff_model, reasoning_effort=reasoning_effort)
    pres_val = getattr(args, "presentation", None)
    result = pipeline.run(
        input_path=input_val, metadata=metadata, output_dir=getattr(args, "output_dir", "auto"),
        mutate_graph=mutate_graph, auto_mutate_on_pass=auto_mutate, directive_id=directive_id,
        reviewer=reviewer, rationale=rationale, embedding_backend=getattr(args, "embedding_backend", "bge-m3"),
        judge_model=judge_model, reasoning_effort=reasoning_effort,
        max_judge_retries=getattr(args, "max_judge_retries", 2),
        progress_callback=getattr(args, "progress_callback", None),
        presentation_path=str(pres_val) if pres_val else None,
        attachments=attachment_list or None, attachment_dir=att_dir,
        timeout_seconds=getattr(args, "timeout_seconds", None),
    )

    _report_ingest_worktree(result, metadata)
    from .stage_latency_reporter import report_stage_latencies
    report_stage_latencies(result)
    from dual_pipeline.cli.ingest_resolve_cmd import dispatch_and_resolve_ingest_approval
    res = dispatch_and_resolve_ingest_approval(result, metadata, directive_id, args, reviewer, rationale)
    if res.get("status") in ("PACKAGE_SEALED", "AWAITING_APPROVAL") and "stage_latencies" in result:
        res.setdefault("stage_latencies", result["stage_latencies"])
    _maybe_cleanup_completed_worktree(res, result, metadata, args)
    return res


ingest_canonical = handle_canonical_ingest
