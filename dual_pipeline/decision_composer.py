"""Readable composition for typed Decision Intelligence results."""

from __future__ import annotations

from typing import Any, Mapping

from .decision_models import validate_decision_result


def compose_decision_markdown(value: Mapping[str, Any]) -> str:
    result = validate_decision_result(value)
    lines = [
        f"# Decision result: {result['terminal_state']}",
        "",
        f"**Question:** {result['decision_frame']['decision_question']}",
        f"**Confidence:** {result['confidence']['band']}",
        "",
    ]
    recommendation = result["recommended_option"]
    if recommendation is None:
        lines.extend(
            [
                "## Recommendation",
                "",
                "No supported recommendation was produced.",
                "",
                result["recommendation_rationale"],
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Recommendation",
                "",
                f"**{recommendation['label']}**",
                "",
                result["recommendation_rationale"],
                "",
            ]
        )
    lines.extend(["## Options and trade-offs", ""])
    trade_by_option = {row["option_id"]: row for row in result["trade_off_matrix"]}
    for option in result["considered_options"]:
        trade = trade_by_option.get(option["option_id"], {})
        lines.append(
            "- **{label}** — supports={supports}, opposes={opposes}, "
            "mixed={mixed}, unknown={unknown}".format(
                label=option["label"],
                supports=trade.get("supports", 0),
                opposes=trade.get("opposes", 0),
                mixed=trade.get("mixed", 0),
                unknown=trade.get("unknown", 0),
            )
        )
    if not result["considered_options"]:
        lines.append("- No evidence-bound options were available.")
    lines.extend(["", "## Risks and unknowns", ""])
    for risk in result["risks"]:
        lines.append(f"- Risk: {risk.get('title', risk.get('text', 'Unknown risk'))}")
    for unknown in result["unknowns"]:
        lines.append(f"- Unknown: {unknown}")
    if not result["risks"] and not result["unknowns"]:
        lines.append("- None identified in the visible evidence packet.")
    lines.extend(["", "## Evidence", ""])
    for conclusion in result["material_conclusions"]:
        citations = ", ".join(conclusion["evidence_ids"])
        lines.append(f"- {conclusion['conclusion']} — {citations}")
    if not result["material_conclusions"]:
        lines.append("- No material conclusion was released.")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["compose_decision_markdown"]
