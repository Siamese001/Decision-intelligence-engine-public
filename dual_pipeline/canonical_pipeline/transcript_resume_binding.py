"""Exact, source-verified identity for transcript synthesis/QA checkpoint reuse."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from dual_pipeline.canonical_pipeline.document_resume_binding import (
    _file_digest,
    _judge_binding,
    _OPERATIONAL,
    _provider_binding,
)

_ROOT = Path(__file__).resolve().parents[2]

_TRANSCRIPT_CODE_FILES = (
    "dual_pipeline/transcript_stages_core/synthesizers.py",
    "dual_pipeline/transcript_stages_core/external_synthesizer.py",
    "dual_pipeline/transcript_stages_core/offline_synthesizer.py",
    "dual_pipeline/transcript_pipeline_core/qa_runner.py",
    "dual_pipeline/transcript_qa_core/evaluator.py",
    "dual_pipeline/llm_judge_core/orchestrator.py",
    "dual_pipeline/llm_judge_core/judges/recap.py",
    "dual_pipeline/llm_judge_core/judges/graph_judge.py",
    "dual_pipeline/release_thresholds.py",
    "dual_pipeline/release_policy.py",
    "dual_pipeline/parameters.py",
)


_TRANSCRIPT_OPERATIONAL = frozenset(_OPERATIONAL | {
    "transcript_resume",
    "transcript_resume_binding",
    "transcript_resume_cache",
    "_transcript_checkpoint_path",
    "_frozen_metadata_sha256",
    "gate_f_certified",
    "remediation_directive",
    "remediation_directive_recap",
    "remediation_directive_graph",
    "remediation_directive_forensic",
    "critique_diff_envelope",
    "prior_recap_candidate",
    "prior_canonical_projection",
    "artifact_manifest",
    "stage_manifests",
    "output_1_sha256",
    "output_2_sha256",
    "output_3_sha256",
    "frozen_shas",
    "frozen_artifacts",
    "scorecards",
    "stage_scores",
    "healed_during_run",
    "healing_changes",
    "evaluation_receipts",
    "gate_results",
    "gate_receipts",
    "run_stats",
    "normalized_turns",
    "turns",
    "diarization_turns",
    "diarization_segments",
    "voice_clusters",
    "speaker_evidence",
    "audio_path",
    "source_audio",
    "audio_bytes",
    "pcm_data",
    "asr_words",
    "asr_transcript",
    "raw_turns",
    "parsed_turns",
    "output_1_text",
    "output_2_text",
    "output_3_text",
    "output_1_source",
    "output_2_source",
    "output_3_source",
    "has_prior_meeting_artifacts",
    "output_1_memoized",
    "output_2_memoized",
    "output_3_memoized",
    "circuit_breaker",
    "diagnostics_path",
    "attempt",
    "max_attempts",
    "hitl_role_receipts",
    "auto_role_receipts",
    "speaker_role_resolutions",
    "attendee_confirmation_receipt",
    "retry_count",
    "current_attempt",
    "remediation_attempt",
    "remediation_history",
    "remediation_ledger",
    "remediation_diff",
    "healed_artifacts",
    "qa_report",
    "judge_report",
    "final_decision",
    "eval_result",
    "checkpoint_dir",
    "checkpoint_path",
    "run_id",
    "session_id",
    "execution_id",
})


def _scalar_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _scalar_json_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    return None


def _clean_role_resolutions(resolutions: Any) -> Any:
    if not isinstance(resolutions, Mapping):
        return resolutions
    cleaned: dict[str, str] = {}
    for k, v in sorted(resolutions.items()):
        if isinstance(v, Mapping):
            raw = v.get("role") or v.get("resolved_role")
            c_val = str(raw).strip() if raw is not None else ""
        else:
            c_val = str(v).strip() if v is not None else ""
        if c_val and c_val.lower() not in ("none", "null", ""):
            cleaned[str(k).strip()] = c_val
    return cleaned


def _container_json_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_transcript_json_value(item) for item in value if not callable(item)]
    if isinstance(value, Mapping):
        return {
            str(k.value if isinstance(k, Enum) else k): _transcript_json_value(v)
            for k, v in value.items()
            if not str(k).startswith("_")
            and str(k) not in _TRANSCRIPT_OPERATIONAL
            and not str(k).startswith("prior_")
            and not callable(v)
        }
    return None


def _transcript_json_value(value: Any) -> Any:
    scalar = _scalar_json_value(value)
    if scalar is not None or value is None or isinstance(value, (str, bool, int)):
        return scalar
    container = _container_json_value(value)
    if container is not None:
        return container
    if is_dataclass(value) and not isinstance(value, type):
        try:
            return _transcript_json_value(asdict(value))
        except Exception:
            return str(value)
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    return str(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _transcript_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def transcript_synthesis_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Filters transient operational fields while preserving semantic metadata."""
    result = {}
    for key in sorted(metadata):
        k_str = str(key)
        if k_str.startswith("_") or k_str in _TRANSCRIPT_OPERATIONAL or k_str.startswith("prior_"):
            continue
        value = metadata[key]
        if callable(value):
            continue
        if key == "metadata" and isinstance(value, Mapping):
            value = transcript_synthesis_metadata(value)
        elif key == "hitl_role_resolutions":
            value = _clean_role_resolutions(value)
        try:
            result[key] = _transcript_json_value(value)
        except Exception:
            result[key] = str(value)
    return result


def _resolve_source_hashes(
    source_text: str, metadata: Mapping[str, Any], output_1_text: str | None
) -> dict[str, Any]:
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("Current source text must be loaded before transcript checkpoint reuse")
    raw = metadata.get("raw_source_sha256") or hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    extracted = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    o1_hash = hashlib.sha256(output_1_text.encode("utf-8")).hexdigest() if output_1_text else None
    return {
        "raw_sha256": raw,
        "extracted_sha256": extracted,
        "output_1_sha256": o1_hash,
        "extracted_char_count": len(source_text),
    }


def _collect_referenced_artifacts(output_dir: Path | None) -> dict[str, str]:
    if not output_dir:
        return {}
    ref_dir = output_dir / "referenced_artifacts"
    if not ref_dir.is_dir():
        return {}
    return {item.name: _file_digest(item) for item in sorted(ref_dir.iterdir()) if item.is_file()}


def _collect_metadata_attachments(metadata: Mapping[str, Any]) -> dict[str, str]:
    attachments = metadata.get("attachments") or metadata.get("attachment")
    if not isinstance(attachments, list):
        return {}
    collateral: dict[str, str] = {}
    for att in attachments:
        if isinstance(att, dict) and att.get("filename"):
            collateral[str(att["filename"])] = str(att.get("sha256") or "")
    return collateral


def _resolve_collateral(output_dir: Path | None, metadata: Mapping[str, Any]) -> dict[str, str]:
    collateral = _collect_referenced_artifacts(output_dir)
    collateral.update(_collect_metadata_attachments(metadata))
    return collateral


def _resolve_prompts() -> dict[str, str]:
    from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY
    prompt_keys = ("STAGE_02_RECAP", "STAGE_03_GRAPH", "JUDGE_RECAP", "JUDGE_GRAPH")
    return {
        key: hashlib.sha256(CANONICAL_PROMPT_REGISTRY.get_prompt(key, reload=True).encode("utf-8")).hexdigest()
        for key in prompt_keys
    }


def _resolve_hitl_context(metadata: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    if metadata.get("hitl_judge_resolver") is not None and not metadata.get("hitl_resolutions"):
        blockers.append("HITL_RESOLUTION_UNBOUND")
    ledger_path = metadata.get("hitl_ledger_path")
    ledger_digest = _file_digest(Path(ledger_path)) if ledger_path and Path(ledger_path).is_file() else None
    resolutions = {
        "roles": _clean_role_resolutions(metadata.get("hitl_role_resolutions")),
        "confirmed": bool(metadata.get("attendees_confirmed_by_operator")),
    }
    return {
        "ledger_sha256": ledger_digest,
        "resolutions_sha256": _digest(resolutions),
    }, blockers


def _code_digests(files: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for rel_path in files:
        p = _ROOT / rel_path
        if p.is_file():
            result[rel_path] = _file_digest(p)
    return result


def build_transcript_resume_binding(
    *,
    source_text: str,
    metadata: Mapping[str, Any],
    provider: Any,
    provider_type: str,
    model_name: str | None = None,
    reasoning_effort: str | None = None,
    output_1_text: str | None = None,
    output_dir: Path | None = None,
    judge_model: str | None = None,
    judge_effort: str | None = None,
    fast_judge: bool = False,
    judge_tier: str | None = None,
    context: Mapping[str, Any] | None = None,
    extraction_revision: str | None = None,
    judge_revision: str | None = None,
    execution_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an exact cryptographic identity for transcript Stage 02/03 checkpoint reuse."""
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG, OUTPUT3_CONTRACT
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
    from dual_pipeline.transcript_qa_core.alias_registry import load_learned_aliases  # noqa

    semantic = transcript_synthesis_metadata(metadata)
    meta_digest = metadata.get("_frozen_metadata_sha256")
    if not meta_digest:
        meta_digest = _digest(semantic)
        if isinstance(metadata, dict):
            metadata["_frozen_metadata_sha256"] = meta_digest

    source = _resolve_source_hashes(source_text, metadata, output_1_text)
    collateral = _resolve_collateral(output_dir, metadata)

    extraction, extraction_blockers = _provider_binding(
        provider, provider_type, model_name, reasoning_effort,
        extraction_revision or metadata.get("extraction_model_revision"),
    )
    judge, judge_blockers = _judge_binding(
        metadata, provider_type, model_name, reasoning_effort, judge_model, judge_effort,
        fast_judge, judge_tier, judge_revision or metadata.get("judge_model_revision"),
    )
    hitl_ctx, hitl_blockers = _resolve_hitl_context(metadata)

    blockers = list(extraction_blockers) + list(judge_blockers) + list(hitl_blockers)

    regen_spec = metadata.get("regenerate")
    if regen_spec:
        spec_str = str(regen_spec).lower()
        if "02" in spec_str or "03" in spec_str or "true" in spec_str:
            blockers.append("DELIBERATE_REGENERATION_REQUESTED")

    components = {
        "source": source,
        "collateral": collateral,
        "code_files": _code_digests(_TRANSCRIPT_CODE_FILES),
        "prompts": _resolve_prompts(),
        "extraction": extraction,
        "judge": judge,
        "hitl": hitl_ctx,
        "metadata_sha256": meta_digest,
        "context_sha256": _digest(transcript_synthesis_metadata(context or {})),
        "execution_options": transcript_synthesis_metadata(execution_options or {}),
        "learned_aliases_sha256": _digest(load_learned_aliases()),
        "schema_sha256": _file_digest(_ROOT / "contracts/dual_pipeline/schemas/evidence-envelope-intake.schema.json"),
        "output_contract_sha256": _digest(asdict(OUTPUT3_CONTRACT)),
        "prompt_config_sha256": _digest(asdict(CANONICAL_PROMPT_CONFIG)),
        "release_policy_sha256": CANONICAL_RELEASE_POLICY.compute_policy_digest(),
    }
    binding = {
        "schema_version": "transcript-resume-binding-1",
        "components": components,
        "reusable": not blockers,
        "reuse_blockers": sorted(set(blockers)),
    }
    return {**binding, "binding_sha256": _digest(binding)}
