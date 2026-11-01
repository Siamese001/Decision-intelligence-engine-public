"""
dual_pipeline/hitl_governance_core/validation.py

Presentation formatting validation and repository governance control checks for HITL.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .surfacing import REQUIRED_HITL_PHRASES

_CONFIDENCE_LABEL_RE = re.compile(
    r"(?:\bconfidence\b\s*[:\-]|\bconf\s*:|\b(?:high|medium|low)\b\s*\(\s*\d+(?:\.\d+)?%\s*\)|\boption\s+[a-z0-9]+\s+\d+(?:\.\d+)?%)",
    re.IGNORECASE,
)


def _from_list(payload: list[Any]) -> tuple[dict[str, Any] | None, list[str]]:
    if len(payload) == 0:
        return None, ["HITL_VIOLATION_EMPTY_PRESENTATION: No decision presented."]
    if len(payload) > 1:
        return None, [
            f"HITL_VIOLATION_BULK_DECISION: Presented {len(payload)} decisions simultaneously. "
            "Decisions must be presented strictly one at a time."
        ]
    item = payload[0]
    if isinstance(item, dict):
        return item, []
    return None, ["HITL_VIOLATION_INVALID_DECISION_STRUCTURE: Decision must be a mapping."]


def _from_dict(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    for multi_key in ("questions", "decisions", "items"):
        val = payload.get(multi_key)
        if isinstance(val, list):
            if len(val) > 1:
                return None, [
                    f"HITL_VIOLATION_BULK_DECISION: Field '{multi_key}' contains "
                    f"{len(val)} items. Decisions must be presented strictly one at a time."
                ]
            if len(val) == 1:
                item = val[0]
                if isinstance(item, dict):
                    return item, []
                return None, ["HITL_VIOLATION_INVALID_DECISION_STRUCTURE: Decision must be a mapping."]
    return payload, []


def _extract_single_decision(payload: Any) -> tuple[dict[str, Any] | None, list[str]]:
    if isinstance(payload, list):
        return _from_list(payload)
    if isinstance(payload, dict):
        return _from_dict(payload)
    return None, ["HITL_VIOLATION_INVALID_TYPE: Decision payload must be a dict or single-element list."]


def _validate_option_item(idx: int, opt: Any, require_layman: bool = True) -> list[str]:
    errors: list[str] = []
    from .layman_clarity import scan_for_jargon

    if isinstance(opt, dict):
        has_conf = ("confidence" in opt or "confidence_level" in opt or "confidence_score" in opt)
        label = opt.get("label") or opt.get("text") or opt.get("option") or ""
        if not has_conf:
            has_conf = bool(_CONFIDENCE_LABEL_RE.search(str(label)))
        if not has_conf:
            errors.append(f"HITL_VIOLATION_MISSING_CONFIDENCE: Option {idx} is missing an assigned confidence level.")

        if require_layman:
            for field in ("label", "plain_english_summary", "impact_statement"):
                val = opt.get(field)
                if val and isinstance(val, str):
                    for j in scan_for_jargon(val):
                        errors.append(f"HITL_VIOLATION_JARGON_IN_OPTION: Option {idx} field '{field}' contains forbidden jargon: {j}")
        return errors

    if isinstance(opt, str):
        if not _CONFIDENCE_LABEL_RE.search(opt):
            errors.append(f"HITL_VIOLATION_MISSING_CONFIDENCE: Option {idx} ('{opt}') is missing an assigned confidence level.")
        if require_layman:
            for j in scan_for_jargon(opt):
                errors.append(f"HITL_VIOLATION_JARGON_IN_OPTION: Option {idx} contains forbidden jargon: {j}")
        return errors

    return [f"HITL_VIOLATION_INVALID_OPTION_TYPE: Option {idx} must be a dict or string."]


def validate_hitl_presentation(payload: Any, require_layman: bool = True) -> list[str]:
    """Validate that a proposed HITL decision prompt adheres to atomic optionated confidence and layman rules."""
    decision, errors = _extract_single_decision(payload)
    if errors:
        return errors
    if not isinstance(decision, dict):
        return ["HITL_VIOLATION_INVALID_DECISION_STRUCTURE: Decision must be a mapping."]

    if require_layman:
        from .layman_clarity import scan_for_jargon
        question_text = str(decision.get("question") or "")
        if question_text:
            for j in scan_for_jargon(question_text):
                errors.append(f"HITL_VIOLATION_JARGON_IN_QUESTION: Question contains forbidden jargon: {j}")

    options = decision.get("options")
    if not options or not isinstance(options, list):
        return ["HITL_VIOLATION_MISSING_OPTIONS: Decision must provide an enumerated list of concrete options."]

    if len(options) < 2:
        errors.append(f"HITL_VIOLATION_INSUFFICIENT_OPTIONS: Decision has {len(options)} options; at least 2 are required.")

    for idx, opt in enumerate(options, start=1):
        errs = _validate_option_item(idx, opt, require_layman=require_layman)
        errors.extend(errs)

    return errors


def _check_agents_md(repository_root: Path, errors: list[str]) -> None:
    agents_path = repository_root / "AGENTS.md"
    if not agents_path.is_file():
        errors.append("AGENTS.md is missing from repository root.")
    else:
        agents_text = agents_path.read_text(encoding="utf-8")
        for phrase in REQUIRED_HITL_PHRASES:
            if phrase not in agents_text:
                errors.append(f"AGENTS.md is missing required HITL governance phrase: '{phrase}'")


def _check_hooks_json(repository_root: Path, errors: list[str]) -> None:
    hooks_path = repository_root / ".agents" / "hooks.json"
    if not hooks_path.is_file():
        errors.append(".agents/hooks.json is missing.")
    else:
        try:
            hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))
            hook_ids = {h.get("id") for h in hooks_data.get("hooks", [])}
            if "nee-hitl-atomic-guard" not in hook_ids:
                errors.append(".agents/hooks.json is missing 'nee-hitl-atomic-guard' hook.")
        except Exception as e:
            errors.append(f".agents/hooks.json is invalid JSON: {e}")


def _check_rules_and_conformance(repository_root: Path, errors: list[str]) -> None:
    rules_path = repository_root / ".agents" / "rules" / "hitl-decisions.md"
    if not rules_path.is_file():
        errors.append(".agents/rules/hitl-decisions.md is missing.")
    else:
        rule_text = rules_path.read_text(encoding="utf-8")
        for required_heading in ("Atomic Presentation", "Confidence Levels"):
            if required_heading not in rule_text:
                errors.append(f".agents/rules/hitl-decisions.md is missing '{required_heading}' section.")
        if not any(h in rule_text for h in ("Ambiguity Margin", "Ambiguity Gating", "15% Confidence Margin Rule")):
            errors.append(".agents/rules/hitl-decisions.md is missing 'Ambiguity Margin' section.")

    conformance_path = repository_root / ".agents" / "rules" / "conformance-map.md"
    if not conformance_path.is_file():
        errors.append(".agents/rules/conformance-map.md is missing.")
    else:
        conf_text = conformance_path.read_text(encoding="utf-8")
        if "HITL decisions are atomic, optionated, and confidence-scored" not in conf_text:
            errors.append(".agents/rules/conformance-map.md does not map the HITL control section.")


def _check_ide_config(errors: list[str]) -> None:
    ide_config_path = Path.home() / ".gemini" / "config" / "config.json"
    if not ide_config_path.is_file():
        return
    try:
        cfg = json.loads(ide_config_path.read_text(encoding="utf-8"))
        user_settings = cfg.get("userSettings", {})
        if user_settings.get("artifactReviewMode") == "ARTIFACT_REVIEW_MODE_TURBO":
            errors.append(
                "IDE configuration (~/.gemini/config/config.json) has artifactReviewMode=ARTIFACT_REVIEW_MODE_TURBO. "
                "Must be ARTIFACT_REVIEW_MODE_ALWAYS to prevent synthetic auto-approvals."
            )
        if user_settings.get("autoExecutionPolicy") in (
            "CASCADE_COMMANDS_AUTO_EXECUTION_EAGER",
            "CASCADE_COMMANDS_AUTO_EXECUTION_AUTO",
        ):
            errors.append(
                f"IDE configuration (~/.gemini/config/config.json) has autoExecutionPolicy={user_settings.get('autoExecutionPolicy')}. "
                "Must be CASCADE_COMMANDS_AUTO_EXECUTION_OFF to prevent synthetic auto-execution of implementation plans."
            )
    except Exception as e:
        errors.append(f"Unable to parse IDE configuration at {ide_config_path}: {e}")


def validate_hitl_governance_controls(repository_root: Path) -> list[str]:
    """Verify that AGENTS.md, .agents/hooks.json, rules, and conformance mapping enforce HITL controls."""
    repository_root = Path(repository_root).resolve()
    errors: list[str] = []
    _check_agents_md(repository_root, errors)
    _check_hooks_json(repository_root, errors)
    _check_rules_and_conformance(repository_root, errors)
    _check_ide_config(errors)
    return errors
