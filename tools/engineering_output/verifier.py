"""Observation verification and response behavior checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .counter import (
    count_characters,
    count_words,
    find_forbidden_jargon_sections,
    find_forbidden_process_sections,
    validate_text_limits,
)
from .models import ExecutionEnvironment, Observation, ValidationResult

# Crash patterns indicating an unhandled crash rather than governed rejection
CRASH_PATTERNS = [
    r"Traceback \(most recent call last\):",
    r"\bSegmentation fault\b",
    r"\bSIGSEGV\b",
    r"\bSyntaxError:\s",
    r"\bIndentationError:\s",
    r"\bpanic:\s",
]


def verify_state_change(
    pre_state: Any,
    post_state: Any,
    is_success_claim: bool = True,
) -> Tuple[bool, List[str]]:
    """Reject success claims where pre-state and post-state are identical."""
    errors: List[str] = []
    if is_success_claim and pre_state is not None and post_state is not None:
        if pre_state == post_state:
            errors.append(
                "Unchanged state rejected: Pre-execution state and post-execution state "
                "are identical despite success claim."
            )
    return len(errors) == 0, errors


def verify_rollback_observation(obs: Observation) -> Tuple[bool, List[str]]:
    """Validate rollback claims require independent proof of content and cache rejection."""
    errors: List[str] = []
    if obs.is_rollback:
        if not obs.rollback_approved_accessible:
            errors.append(
                "Rollback claim rejected: Missing independent observation that approved "
                "content is active and retrievable."
            )
        if not obs.rollback_incompatible_rejected:
            errors.append(
                "Rollback claim rejected: Missing independent observation that incompatible "
                "cached data cannot be retrieved."
            )
    return len(errors) == 0, errors


def verify_observation_freshness(
    obs: Observation,
    current_source_hash: Optional[str] = None,
    latest_attempt_succeeded: Optional[bool] = None,
) -> Tuple[bool, List[str]]:
    """Reject stale observations or earlier successes masked by a later failure."""
    errors: List[str] = []
    if current_source_hash and obs.source_files_hash:
        if current_source_hash != obs.source_files_hash:
            errors.append(
                "Stale observation rejected: Target source files have changed since observation "
                "was recorded (uncommitted edits or branch drift)."
            )
    if latest_attempt_succeeded is False:
        errors.append(
            "Unresolved later failure: A subsequent run attempt failed; earlier successful "
            "observation cannot conceal a later failure."
        )
    return len(errors) == 0, errors


def verify_observation_completeness(obs: Observation) -> Tuple[bool, List[str]]:
    """Validate all required observation fields are populated."""
    errors: List[str] = []
    if not obs.action or not obs.action.strip():
        errors.append("Observation missing normal action description.")
    if not obs.input_description or not obs.input_description.strip():
        errors.append("Observation missing meaningful input description.")
    if not obs.observed_effect or not obs.observed_effect.strip():
        errors.append("Observation missing independently observed effect.")
    if obs.environment == ExecutionEnvironment.UNSPECIFIED:
        errors.append(
            "Execution environment must be explicitly declared (local, sandbox, or production)."
        )
    return len(errors) == 0, errors


def verify_crash_not_disguised(
    error_output: str,
    expected_rejection: bool = False,
) -> Tuple[bool, List[str]]:
    """Ensure an unhandled crash or traceback is not disguised as governed rejection."""
    errors: List[str] = []
    if expected_rejection and error_output:
        for pat in CRASH_PATTERNS:
            if re.search(pat, error_output):
                errors.append(
                    f"Unrelated crash disguised as expected rejection: pattern '{pat}' detected."
                )
                break
    return len(errors) == 0, errors


def _check_limitation_retention(
    response_text: str,
    limitations: List[str],
    input_context: str = "",
) -> List[str]:
    """Ensure declared material limitations are not silently omitted from output."""
    errors: List[str] = []
    norm_resp = response_text.lower()
    norm_ctx = input_context.lower()
    stopwords = {"this", "that", "with", "from", "into", "during", "then", "have", "been", "were", "some", "items", "more"}

    for lim in limitations:
        lim_words = [
            re.sub(r"\W+", "", w.lower())
            for w in lim.split()
            if len(w) > 3 and w.lower() not in stopwords
        ]
        distinctive = [w for w in lim_words if w and w not in norm_ctx]
        check_words = distinctive if distinctive else lim_words
        if check_words and not any(w in norm_resp for w in check_words):
            errors.append(
                f"Omitted material limitation: Response does not mention declared limitation '{lim}'."
            )
    return errors


def validate_engineering_response(
    response_text: str,
    observation: Optional[Observation] = None,
    plan_only: bool = False,
    require_observed_behavior: bool = True,
    exclude_deliverable_urls: bool = True,
) -> ValidationResult:
    """Validate engineering turn response against concise rules and behavior observation."""
    errors: List[str] = []
    warnings: List[str] = []
    w_count = count_words(response_text)
    c_count = count_characters(response_text, exclude_deliverable_urls=exclude_deliverable_urls)

    # 1. Text limits, forbidden process sections, & zero jargon
    errors.extend(validate_text_limits(response_text, exclude_deliverable_urls=exclude_deliverable_urls))
    errors.extend(find_forbidden_process_sections(response_text))
    jargon_violations = find_forbidden_jargon_sections(response_text)
    errors.extend(jargon_violations)

    # 2. Plan-only validation
    if plan_only:
        norm = response_text.lower()
        if "plan" not in norm:
            errors.append("Plan-only response must explicitly reference the plan.")
        if any(term in norm for term in ("all tests passed", "successfully verified behavior", "observed effect")):
            errors.append("Plan-only response must not claim functional or behavioral execution success.")
        return ValidationResult(
            is_valid=(len(errors) == 0),
            word_count=w_count,
            char_count=c_count,
            errors=errors,
            warnings=warnings,
            details={"type": "plan_only", "jargon_violations": jargon_violations},
        )

    # 3. Observation validation if provided
    if observation is not None:
        c_ok, c_errs = verify_observation_completeness(observation)
        errors.extend(c_errs)

        s_ok, s_errs = verify_state_change(
            observation.pre_state_digest,
            observation.post_state_digest,
            observation.is_success,
        )
        errors.extend(s_errs)

        r_ok, r_errs = verify_rollback_observation(observation)
        errors.extend(r_errs)

        if observation.limitations:
            ctx = f"{observation.action} {observation.input_description}"
            errors.extend(_check_limitation_retention(response_text, observation.limitations, input_context=ctx))
    elif require_observed_behavior:
        # Check that response mentions an action and observed effect or outcome
        norm = response_text.lower()
        has_action = any(term in norm for term in ("ran", "executed", "attempted", "tested", "built", "verified"))
        has_effect = any(term in norm for term in ("observed", "result", "passed", "failed", "output", "effect"))
        if not (has_action and has_effect):
            errors.append(
                "Engineering completion response must lead with action/input and observed effect."
            )

    return ValidationResult(
        is_valid=(len(errors) == 0),
        word_count=w_count,
        char_count=c_count,
        errors=errors,
        warnings=warnings,
        details={"type": "engineering_completion", "jargon_violations": jargon_violations},
    )


def verify_deliverable_links(
    response_text: str,
    expected_links: Dict[str, Union[str, Path]],
    is_package_only: bool = True,
    expected_judge_verdict: Optional[str] = None,
    verify_files_on_disk: bool = True,
) -> Tuple[bool, List[str]]:
    """Verify deliverable links in an ingestion completion response.

    Validates:
    1. All expected link labels and targets are present.
    2. File paths resolve to existing files on disk (if verify_files_on_disk is True).
    3. Package-only isolation is preserved (no claim of canonical graph mutation).
    4. Judge verdict is truthful (e.g. failure is not masked as PASS).
    """
    errors: List[str] = []
    # 1. Parse markdown links [label](target)
    link_pattern = re.compile(r"\[([^\]]+)\]\((file:///[^)]+)\)")
    found_links = {m.group(1): m.group(2) for m in link_pattern.finditer(response_text)}

    for label, expected_path in expected_links.items():
        exp_str = str(expected_path)
        # Check if expected path appears in any found link target
        target_found = any(exp_str in target for target in found_links.values())
        if not target_found:
            errors.append(f"Missing required deliverable link for '{label}' targeting '{exp_str}'.")
        elif verify_files_on_disk:
            clean_path = exp_str.replace("file://", "")
            p = Path(clean_path)
            if not p.is_file():
                errors.append(f"Deliverable link target does not exist on disk: '{exp_str}'.")

    # 2. Package-only isolation check
    if is_package_only:
        norm = response_text.lower()
        if "graph mutated" in norm or "published to canonical graph" in norm or "active graph updated" in norm:
            errors.append("Package-only response cannot claim active graph publication or mutation.")

    # 3. Judge verdict truthfulness
    if expected_judge_verdict:
        norm = response_text.lower()
        if expected_judge_verdict.upper() == "FAIL" and ("pass" in norm or "passed" in norm):
            errors.append("Response claims PASS despite failed judge evaluation.")

    return len(errors) == 0, errors


def verify_mutation_and_promotion_proof(response_text: str) -> Tuple[bool, List[str]]:
    """Enforce mandatory two-table Mutation & Promotion Proof in operator outputs."""
    from tools.graph_mutation_proof_core.formatter import (
        verify_mutation_and_promotion_proof_markdown,
    )

    return verify_mutation_and_promotion_proof_markdown(response_text)


