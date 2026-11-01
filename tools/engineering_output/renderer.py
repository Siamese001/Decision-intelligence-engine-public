"""Concise rendering utilities for engineering outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Union

from .counter import count_characters, count_words
from .models import ExecutionEnvironment


def render_concise_response(
    action: str,
    input_desc: str,
    environment: ExecutionEnvironment | str,
    observed_effect: str,
    limitation_or_next: Optional[str] = None,
) -> str:
    """Render a canonical concise engineering completion response."""
    env_str = environment.value if isinstance(environment, ExecutionEnvironment) else str(environment)
    parts = [
        f"Ran {action.strip()} with {input_desc.strip()} in {env_str}.",
        f"Observed {observed_effect.strip()}.",
    ]
    if limitation_or_next and limitation_or_next.strip():
        parts.append(limitation_or_next.strip())

    result = " ".join(parts)
    return result


def render_plan_only_response(
    plan_description: str,
    next_action: str = "Implementation has not started.",
) -> str:
    """Render a canonical plan-only completion response."""
    desc = plan_description.strip()
    nxt = next_action.strip()
    return f"The plan for {desc} is ready. {nxt}"


def render_failure_response(
    action: str,
    input_desc: str,
    environment: ExecutionEnvironment | str,
    observed_failure: str,
    next_action: str,
) -> str:
    """Render a canonical failure completion response without hiding limitations."""
    env_str = environment.value if isinstance(environment, ExecutionEnvironment) else str(environment)
    parts = [
        f"Attempted {action.strip()} with {input_desc.strip()} in {env_str}.",
        f"Failed with {observed_failure.strip()}.",
        f"Next action: {next_action.strip()}.",
    ]
    return " ".join(parts)


def render_deliverables_completion(
    action: str,
    scope: str,
    environment: ExecutionEnvironment | str,
    deliverables: Dict[str, Union[str, Path]],
    input_desc: Optional[str] = None,
    judge_verdict: Optional[str] = None,
    judge_score: Optional[float] = None,
    package_only: bool = True,
    limitation_or_next: Optional[str] = None,
) -> str:
    """Render a canonical engineering completion with mandatory outcome deliverable links."""
    env_str = environment.value if isinstance(environment, ExecutionEnvironment) else str(environment)
    isolation_str = "package-only isolation" if package_only else "canonical graph mutation"
    inp_str = f" with {input_desc.strip()}" if input_desc and input_desc.strip() else ""
    lines = [
        f"Executed {action.strip()}{inp_str} for {scope.strip()} in {env_str}.",
        f"Synthesized all deliverables under {isolation_str}:",
    ]
    for label, path in deliverables.items():
        if label.lower() in ("judge", "llm judge", "llm_judge", "evaluation") or "judge" in label.lower():
            continue
        p = Path(path)
        lines.append(f"- **{label}**: [`{p.name}`](file://{p})")
    if judge_verdict:
        judge_path = deliverables.get("LLM Judge Evaluation") or deliverables.get("judge") or deliverables.get("llm_judge_evaluation")
        score_str = f", composite score {judge_score}" if judge_score is not None else ""
        if judge_path:
            jp = Path(judge_path)
            lines.append(f"- **LLM Judge Evaluation**: [`{jp.name}`](file://{jp}) ({judge_verdict}{score_str})")
        else:
            lines.append(f"- **LLM Judge Evaluation**: {judge_verdict}{score_str}")
    if limitation_or_next and limitation_or_next.strip():
        lines.append("")
        lines.append(limitation_or_next.strip())
    return "\n".join(lines)

