"""Exact, source-verified identity for document synthesis/QA checkpoint reuse."""
from __future__ import annotations

import hashlib
import inspect
import json
import math
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[2]
# These values control execution/publication, never document interpretation.
_OPERATIONAL = frozenset({
    "request_id", "run_id", "attempt", "attempt_id", "resume", "force_rerun",
    "output_dir", "target_output_dir", "staging_dir", "worktree_dir", "worktree_branch",
    "source_path", "input_path", "input", "metadata_path", "presentation_path",
    "graph_path", "runtime_root", "evidence_root", "generation_id", "active_generation",
    "active_generation_id", "current_graph_generation", "current_graph_sha256", "base_generation_id",
    "expected_base_graph_version", "graph_sha256", "enforce_worktree", "allow_duplicate",
    "collision_existing_slug", "collision_existing_path", "dry_run_graph", "mutate_graph",
    "auto_mutate_on_pass", "preserve_partial", "partial_deliverables_saved",
    "partial_deliverables_error", "dlq_id", "status", "execution_receipt",
    "execution_timing", "execution_evidence_path", "timing", "started_at", "finished_at", "elapsed_ms",
    "state_machine", "source_text", "forensic_transcript", "raw_transcript",
    "transcript_text", "prior_graph_candidate", "prior_recap_candidate",
    "remediation_directive", "remediation_directive_graph",
    "remediation_directive_forensic", "remediation_directive_recap",
    "hitl_judge_resolver", "hitl_ledger_path",
    "document_resume_binding", "document_resume_cache", "document_resume", "resume_binding",
    "document_patch_request", "extraction_effort", "judge_effort", "fast_judge",
    "circuit_breaker", "5_agent_intelligence_report", "needs_approval_after_staging",
    "package_only", "deliverable_dir", "deliverable_receipt_path", "conversion_artifact",
    "conversion_cache", "no_cache", "no_subfolder", "progress_mode", "auto_reroute",
    "backtest", "bounded_context", "outputs", "required_deliverables", "source_coverage",
    "source_units", "source_manifest", "source_preflight", "retrieval_preflight",
    "intake_preflight", "date_filename", "time_filename", "filename_slug", "meeting_name",
    "source_file_hash", "raw_source_sha256",
    "document_ingestion_plan", "document_ingestion_plan_path", "document_archetype",
    "max_mutation_budget", "document_signal_profile", "signal_tier",
    "strategic_value_score", "perspective_origin", "recommended_extraction_mode",
    "requires_graph_delta_hitl", "enable_5_agent_intelligence",
    "enable_unit_batch_extraction", "verified_stage_checkpoint_hashes",
    "additive_unit_gap_remediation", "retry_count", "max_attempts",
    "structural_census",
})
_ADAPTER_FILES = (
    "dual_pipeline/document_pipeline_core/content_loader.py",
    "dual_pipeline/document_pipeline_core/docx_coverage.py", "dual_pipeline/docx_source.py",
    "dual_pipeline/canonical_pipeline/content_loader.py", "dual_pipeline/evidence_field_limits.py",
)
_PROCESSING_PATTERNS = (
    "dual_pipeline/document_pipeline_core/*.py", "dual_pipeline/transcript_models*.py",
    "dual_pipeline/canonical_pipeline/document_resume_binding.py", "dual_pipeline/canonical_pipeline/evaluator.py",
    "dual_pipeline/transcript_qa_core/alias_registry.py", "dual_pipeline/token_optimization.py",
    "dual_pipeline/transcript_models_core/**/*.py", "dual_pipeline/transcript_stages_core/graph_*.py",
    "dual_pipeline/transcript_stages_core/synthesizers.py",
    "dual_pipeline/transcript_stages_core/external_synthesizer.py",
    "dual_pipeline/transcript_stages_core/offline_synthesizer.py",
    "dual_pipeline/transcript_qa_core/graph_*.py", "dual_pipeline/transcript_qa_core/gate3*.py",
    "dual_pipeline/transcript_qa_core/healing_mesh.py", "dual_pipeline/transcript_qa_core/semantic_linters.py",
    "dual_pipeline/signal_extraction.py", "dual_pipeline/prompts/contracts/output3_contract.py",
    "dual_pipeline/prompts/contracts/output_contract.py", "dual_pipeline/llm_judge_core/judges/graph*.py",
    "dual_pipeline/llm_judge_core/judge_clarification.py", "dual_pipeline/release_thresholds.py",
    "dual_pipeline/release_policy.py", "dual_pipeline/parameters.py", "dual_pipeline/temporal_ssot.py",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _json_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, (str, Enum)) for key in value):
            raise ValueError("Resume binding requires string metadata keys")
        return {str(key.value if isinstance(key, Enum) else key): _json_value(item)
                for key, item in value.items()}
    raise ValueError("Resume binding requires finite, JSON-compatible semantic metadata")


def _digest(value: Any) -> str:
    encoded = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_synthesis_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Use this SAME projection in the real document prompt and its cache identity.

    Unknown semantic fields are retained. Only named operational fields and private
    fields are omitted; graph-dependent context, approvals and source labels remain.
    """
    result = {}
    for key in sorted(metadata):
        if key.startswith("_") or key in _OPERATIONAL:
            continue
        value = metadata[key]
        if key == "metadata" and isinstance(value, Mapping):
            value = document_synthesis_metadata(value)
        if isinstance(value, (str, int, float, bool, list, dict, type(None))):
            result[key] = _json_value(value)
    return result


def _source_binding(source_text: str, metadata: Mapping[str, Any], source_path: Any,
                    raw_source_bytes: bytes | None) -> dict[str, Any]:
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("Current source text must be loaded before document checkpoint reuse")
    extracted = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if source_path is not None:
        path = Path(source_path)
        if not path.is_file() and (_ROOT / path).is_file():
            path = _ROOT / path
        if path.is_file():
            raw, kind, fmt = _file_digest(path), "file_bytes", path.suffix.lower()
            if raw_source_bytes is not None and hashlib.sha256(raw_source_bytes).hexdigest() != raw:
                raise ValueError("Provided raw source bytes disagree with current source file")
        elif raw_source_bytes is not None:
            raw, kind, fmt = hashlib.sha256(raw_source_bytes).hexdigest(), "file_bytes", path.suffix.lower()
        elif metadata.get("raw_source_sha256"):
            raw, kind, fmt = str(metadata["raw_source_sha256"]), "file_bytes", path.suffix.lower()
        else:
            raw, kind, fmt = _file_digest(path), "file_bytes", path.suffix.lower()
    else:
        raw = hashlib.sha256(raw_source_bytes if raw_source_bytes is not None else source_text.encode("utf-8")).hexdigest()
        kind, fmt = "inline_bytes", str(metadata.get("source_format") or "text")
    observed = metadata.get("raw_source_sha256")
    if observed and observed != raw:
        raise ValueError("Source changed after extraction; reload before checkpoint reuse")
    return {"raw_sha256": raw, "extracted_sha256": extracted, "source_kind": kind,
            "source_format": fmt, "extracted_char_count": len(source_text)}


def _code_binding(paths: tuple[str, ...], *, patterns: bool = False) -> dict[str, str]:
    selected: set[Path] = set()
    for path in paths:
        if patterns:
            selected.update(item for item in _ROOT.glob(path) if item.is_file())
        else:
            candidate = _ROOT / path
            if not candidate.is_file():
                raise ValueError(f"Missing required resume binding dependency: {path}")
            selected.add(candidate)
    if not selected:
        raise ValueError("No code dependencies available for document resume binding")
    return {str(path.relative_to(_ROOT)): _file_digest(path) for path in sorted(selected)}


def _declared(provider: Any, *names: str) -> str | None:
    for name in names:
        value = getattr(provider, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _provider_source_digest(cls: type) -> str | None:
    try:
        source = inspect.getsourcefile(cls)
    except TypeError:
        source = None
    return _file_digest(Path(source)) if source and Path(source).is_file() else None


def _resolve_provider_revision(provider: Any, revision: str | None, model: str | None = None) -> str | None:
    if revision is not None:
        return revision.strip() if isinstance(revision, str) and revision.strip() else None
    rev = _declared(provider, "d03_model_revision", "model_revision")
    return rev.strip() if isinstance(rev, str) and rev.strip() else None


def _collect_provider_blockers(
    offline: bool, model: str | None, declared_revision: str | None,
    code_digest: str | None, classifier: Any,
) -> list[str]:
    blockers = []
    if not offline and not model:
        blockers.append("EXTRACTION_MODEL_UNBOUND")
    if not offline and not declared_revision:
        blockers.append("EXTRACTION_MODEL_REVISION_UNBOUND")
    if not code_digest:
        blockers.append("EXTRACTION_IMPLEMENTATION_UNBOUND")
    if classifier is not None and not isinstance(classifier, bool):
        blockers.append("EXTRACTION_SETTINGS_UNBOUND")
    return blockers


def _resolve_provider_settings(
    provider: Any, model: str | None, reasoning_effort: str | None, classifier: Any,
) -> dict[str, Any]:
    from dual_pipeline.transcript_stages_core import synthesizers
    settings = {
        "reasoning_effort": _declared(provider, "reasoning_effort") or reasoning_effort,
        "response_format": "json_object",
        "use_complexity_classifier": classifier,
        "max_completion_tokens": synthesizers.DEFAULT_SYNTHESIS_MAX_TOKENS,
        "deadline_seconds": synthesizers.DEFAULT_SYNTHESIS_DEADLINE_SEC,
    }
    if model and model.startswith("gemini"):
        from dual_pipeline.transcript_stages_core.external_synthesizer import (
            _build_gemini_generation_config, _resolve_gemini_thinking_level, call_gemini_generate_content,
        )
        level = _resolve_gemini_thinking_level(model, None, settings["reasoning_effort"])
        settings["generation_config"] = _build_gemini_generation_config(level, None, True, None)
        settings["max_completion_tokens"] = settings["generation_config"]["maxOutputTokens"]
        settings["deadline_seconds"] = inspect.signature(call_gemini_generate_content).parameters["deadline_seconds"].default
    return settings


def _provider_binding(provider: Any, provider_type: str, model_name: str | None,
                      reasoning_effort: str | None, revision: str | None) -> tuple[dict[str, Any], list[str]]:
    from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
    cls = type(provider)
    code_digest = _provider_source_digest(cls)
    offline = cls is OfflineTranscriptSynthesizer and provider_type == "offline"
    model = _declared(provider, "d03_model", "model")
    declared_revision = _resolve_provider_revision(provider, revision, model)
    classifier = getattr(provider, "use_complexity_classifier", None)
    blockers = _collect_provider_blockers(offline, model, declared_revision, code_digest, classifier)
    if classifier is not None and not isinstance(classifier, bool):
        classifier = None
    settings = _resolve_provider_settings(provider, model, reasoning_effort, classifier)
    return {"provider_type": provider_type, "provider_class": f"{cls.__module__}.{cls.__qualname__}",
            "implementation_sha256": code_digest, "requested_model": model_name,
            "declared_model": model, "declared_revision": declared_revision,
            "identity_scope": "deterministic_fixture_code" if offline else "provider_declared_revision",
            "settings": _json_value(settings)}, blockers


def _resolve_judge_model_and_effort(
    metadata: Mapping[str, Any], model_name: str | None, reasoning_effort: str | None,
    judge_model: str | None, judge_effort: str | None, fast_judge: bool, judge_tier: str | None,
) -> tuple[str, str, str | None]:
    from dual_pipeline.model_registry import ModelTier, resolve_tier_model, resolve_tier_model_name
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG
    from config.credentials import CredentialManager
    tier = judge_tier or metadata.get("judge_tier")
    if not judge_model and tier:
        selected = {"fast": ModelTier.FAST_ROUTING, "standard": ModelTier.FRONTIER_REASONING}
        judge_model, tier_effort = resolve_tier_model(selected.get(str(tier).lower(), ModelTier.DIALECTICAL_JUDGE))
        judge_effort = judge_effort or tier_effort
    default_model, default_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
    model = judge_model or model_name or default_model
    input_effort = judge_effort or reasoning_effort
    effort = input_effort or default_effort or "high"
    frontier = resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING).lower()
    if fast_judge and input_effort is None and model.lower() != frontier and "astra" not in model.lower():
        effort = "low"
    local = CredentialManager.is_local_execution_mode()
    model, effort = CANONICAL_PROMPT_CONFIG.judge.resolve_model_and_effort(
        requested_model=model, requested_effort=effort, use_local=local,
    )
    return model, effort, tier


def _judge_binding(metadata: Mapping[str, Any], provider_type: str, model_name: str | None,
                   reasoning_effort: str | None, judge_model: str | None, judge_effort: str | None,
                   fast_judge: bool, judge_tier: str | None, revision: str | None) -> tuple[dict[str, Any], list[str]]:
    from config.credentials import CredentialManager
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG
    model, effort, tier = _resolve_judge_model_and_effort(
        metadata, model_name, reasoning_effort, judge_model, judge_effort, fast_judge, judge_tier
    )
    local = CredentialManager.is_local_execution_mode()
    offline = provider_type == "offline"
    revision = revision.strip() if isinstance(revision, str) and revision.strip() else None
    blockers = [] if offline or revision else ["JUDGE_MODEL_REVISION_UNBOUND"]
    if local and not offline:
        blockers.append("JUDGE_EFFECTIVE_LOCAL_MODEL_UNBOUND")
    return {"declared_model": model, "declared_revision": revision, "reasoning_effort": effort,
            "identity_scope": "deterministic_fixture_code" if offline else "provider_declared_revision",
            "local_mode": local, "fast_judge": bool(fast_judge), "judge_tier": tier,
            "execution_config": _json_value(asdict(CANONICAL_PROMPT_CONFIG.judge))}, blockers


def build_document_resume_binding(
    *, source_text: str, metadata: Mapping[str, Any], provider: Any, provider_type: str,
    model_name: str | None = None, reasoning_effort: str | None = None,
    source_path: str | Path | None = None, raw_source_bytes: bytes | None = None,
    judge_model: str | None = None, judge_effort: str | None = None, fast_judge: bool = False,
    judge_tier: str | None = None, context: Mapping[str, Any] | None = None,
    extraction_revision: str | None = None, judge_revision: str | None = None,
    execution_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an exact identity AFTER fresh source/context load, before any reuse.

    Missing live model revisions disable reuse; model names are never promoted to
    invented revisions. This identity does not grant release/write authorization.
    Runtime fallback model/settings must be compared with this declared identity
    before saving reusable evidence. Do not treat ``reusable=False`` as an error.
    """
    from dual_pipeline.document_pipeline_core.executor import _build_document_user_content
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG, CANONICAL_PROMPT_REGISTRY, OUTPUT3_CONTRACT
    from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
    from dual_pipeline.transcript_qa_core.alias_registry import load_learned_aliases
    semantic = document_synthesis_metadata(metadata)
    source = _source_binding(source_text, metadata, source_path, raw_source_bytes)
    extraction, blockers = _provider_binding(
        provider, provider_type, model_name, reasoning_effort,
        extraction_revision or metadata.get("extraction_model_revision"),
    )
    judge, judge_blockers = _judge_binding(
        metadata, provider_type, model_name, reasoning_effort, judge_model, judge_effort,
        fast_judge, judge_tier, judge_revision or metadata.get("judge_model_revision"),
    )
    blockers.extend(judge_blockers)
    if metadata.get("hitl_judge_resolver") is not None and not metadata.get("hitl_resolutions"):
        blockers.append("HITL_RESOLUTION_UNBOUND")
    ledger = metadata.get("hitl_ledger_path")
    ledger_digest = _file_digest(Path(ledger)) if ledger else None
    prompts = {key: hashlib.sha256(CANONICAL_PROMPT_REGISTRY.get_prompt(key, reload=True).encode("utf-8")).hexdigest()
               for key in ("STAGE_03_DOCUMENT_GRAPH", "JUDGE_GRAPH")}
    user_content = _build_document_user_content(source_text, semantic, metadata.get("remediation_directive"))
    prompts["document_user_content"] = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
    components = {
        "source": source, "adapter_code": _code_binding(_ADAPTER_FILES),
        "processing_code": _code_binding(_PROCESSING_PATTERNS, patterns=True),
        "prompts": prompts, "extraction": extraction, "judge": judge,
        "metadata_sha256": _digest(semantic), "context_sha256": _digest(document_synthesis_metadata(context or {})),
        "hitl_ledger_sha256": ledger_digest, "execution_options": document_synthesis_metadata(execution_options or {}),
        "learned_aliases_sha256": _digest(load_learned_aliases()),
        "schema_sha256": _file_digest(_ROOT / "contracts/dual_pipeline/schemas/evidence-envelope-intake.schema.json"),
        "output_contract_sha256": _digest(asdict(OUTPUT3_CONTRACT)),
        "prompt_config_sha256": _digest(asdict(CANONICAL_PROMPT_CONFIG)),
        "release_policy_sha256": CANONICAL_RELEASE_POLICY.compute_policy_digest(),
    }
    binding = {"schema_version": "document-resume-binding-1", "components": components,
               "reusable": not blockers, "reuse_blockers": sorted(set(blockers))}
    return {**binding, "binding_sha256": _digest(binding)}
