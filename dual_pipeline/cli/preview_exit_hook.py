"""Preview Pass Exit Hook.

Prompts the operator via ask_user_question to auto-trigger knowledge graph
activation whenever an ingestion preview pass completes with a PASS verdict.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.agent_architecture_core.models import (
    CrossCuttingConcern,
    HookDefinition,
    HookTiming,
)

logger = logging.getLogger("nee.cli.preview_exit_hook")

EXIT_HOOK_DEFINITION = HookDefinition(
    id="hook:post-preview-activation-prompt",
    event="preview_pass_complete",
    timing=HookTiming.POST,
    cross_cutting_concern=CrossCuttingConcern.NOTIFICATIONS,
    non_intrusive=True,
    description=(
        "Prompts operator via ask_user_question to auto-trigger activation "
        "whenever an ingestion preview pass completes with a passing evaluation."
    ),
)


def should_prompt_preview_activation(
    result: Dict[str, Any],
    auto_mutate_on_pass: bool = False,
    mutate_graph: bool = False,
) -> bool:
    """Return True if result is a completed preview pass ready for activation."""
    if auto_mutate_on_pass or mutate_graph:
        return False
    if not isinstance(result, dict):
        return False
    if result.get("status") not in ("COMPLETE", "PACKAGE_SEALED"):
        return False
    if result.get("release_status") not in ("PASS", None) and result.get("status") != "PACKAGE_SEALED":
        return False
    if result.get("graph_mutated") or result.get("graph_mutation") is not None:
        return False

    pkg = result.get("intake_package")
    if not pkg or not Path(str(pkg)).is_file():
        out_dir = result.get("output_dir")
        if out_dir:
            cand = Path(str(out_dir)) / "intake_package.json"
            if cand.is_file():
                pkg = str(cand)
                result["intake_package"] = pkg
            else:
                cand_zip = Path(str(out_dir)) / "intake_package.zip"
                if cand_zip.is_file():
                    pkg = str(cand_zip)
                    result["intake_package"] = pkg
        if not pkg or not Path(str(pkg)).is_file():
            return False
    return True


def build_preview_activation_question(
    result: Dict[str, Any],
    reviewer: Optional[str] = None,
    rationale: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct a zero-jargon HITL prompt payload for ask_user_question."""
    pkg_path = str(result.get("intake_package", ""))
    out_dir = str(result.get("output_dir", ""))
    meeting_title = Path(out_dir).name if out_dir else "Source Deliverable"

    question = (
        f"HITL Decision: Auto-Trigger Knowledge Graph Activation\n\n"
        f"**Deliverable**: `{meeting_title}`\n"
        f"**Staged Package**: `{pkg_path}`\n\n"
        "Ingestion preview completed successfully and passed all QA gates. "
        "Would you like to activate and publish this package into the canonical knowledge graph now?"
    )
    from dual_pipeline.hitl_governance_core.question_builder import format_option_text

    options = [
        format_option_text(
            label="Publish and activate into knowledge graph now",
            confidence_pct=95,
            tier="HIGH",
            impact="Ingests staged entities, claims, and vectors into canonical graph storage.",
            recommended=True,
        ),
        format_option_text(
            label="Keep preview only without mutating graph",
            confidence_pct=10,
            tier="LOW",
            impact="Halts ingestion without mutating canonical knowledge graph.",
            recommended=False,
        ),
    ]
    return {
        "question": question,
        "options": options,
        "is_multi_select": False,
        "metadata": {
            "hook_id": EXIT_HOOK_DEFINITION.id,
            "intake_package": pkg_path,
            "output_dir": out_dir,
            "reviewer": reviewer or "Operator",
            "rationale": rationale or f"Auto-activated post-preview pass for {meeting_title}",
        },
    }


def run_auto_activation(
    package_path: str | Path,
    reviewer: Optional[str] = None,
    rationale: Optional[str] = None,
    runtime_root: Optional[str | Path] = None,
    graph: Optional[str | Path] = None,
    allow_dev_bge_m3: bool = False,
) -> Dict[str, Any]:
    """Execute intake-publish on the staged intake package."""
    from dual_pipeline.cli.handlers import _intake_publish
    from dual_pipeline.cli.parser import build_parser

    pkg_resolved = Path(package_path).expanduser().resolve()
    if not pkg_resolved.is_file():
        raise FileNotFoundError(f"Intake package not found for activation: {pkg_resolved}")

    cmd = [
        "intake-publish",
        "--package",
        str(pkg_resolved),
    ]
    if runtime_root:
        rt_p = Path(runtime_root).expanduser().resolve()
        if "output" not in rt_p.parts and (rt_p / "active_pair.json").is_file():
            cmd.extend(["--runtime-root", str(rt_p)])
    if graph:
        cmd.extend(["--graph", str(Path(graph).expanduser().resolve())])
    if allow_dev_bge_m3:
        cmd.extend(["--embedding-backend", "bge-m3-dev", "--allow-dev-bge-m3"])

    parser = build_parser()
    ns = parser.parse_args(cmd)
    if reviewer:
        ns.reviewer = str(reviewer)
    if rationale:
        ns.rationale = str(rationale)
    return _intake_publish(ns)


def execute_preview_exit_hook(
    result: Dict[str, Any],
    args: Optional[Any] = None,
    reviewer: Optional[str] = None,
    rationale: Optional[str] = None,
    auto_activate: bool = False,
) -> Dict[str, Any]:
    """Execute the exit hook on preview pass completion."""
    auto_mutate = getattr(args, "auto_mutate_on_pass", False)
    mutate_graph = getattr(args, "mutate_graph", False)

    if not should_prompt_preview_activation(result, auto_mutate, mutate_graph):
        return result

    pkg_path = str(result.get("intake_package", ""))
    question_payload = build_preview_activation_question(
        result, reviewer=reviewer, rationale=rationale
    )

    # 1. Direct programmatic auto-activation if requested
    if auto_activate:
        logger.info("[EXIT_HOOK] Auto-triggering graph activation for %s", pkg_path)
        mut_res = run_auto_activation(pkg_path, reviewer=reviewer, rationale=rationale)
        result["graph_mutation"] = mut_res
        result["graph_mutated"] = True
        result["preview_exit_hook"] = {
            "status": "AUTO_ACTIVATED",
            "receipt": mut_res,
        }
        return result

    # 2. Agent / Terminal / Headless Environment: Emit structured in-band hook prompt
    banner = (
        "\n" + "=" * 80 + "\n"
        "[EXIT HOOK: PREVIEW PASS COMPLETED & READY FOR ACTIVATION]\n"
        "The ingestion preview pass completed and passed all evaluation gates.\n"
        "Canonical knowledge graph was NOT mutated (--mutate-graph was not specified).\n\n"
        "ACTION REQUIRED: Prompt the operator via ask_user_question to auto-trigger activation:\n"
        f"  Question: {question_payload['question'].splitlines()[0]}\n"
        f"  Options:\n"
        f"    1. {question_payload['options'][0]}\n"
        f"    2. {question_payload['options'][1]}\n"
        f"  Staged Package: {pkg_path}\n"
        f"  Manual activation command:\n"
        f"    python nee.py intake-publish --package \"{pkg_path}\"\n"
        + "=" * 80 + "\n"
    )
    print(banner, file=sys.stderr, flush=True)

    result["preview_exit_hook"] = {
        "status": "PROMPT_EMITTED",
        "action": "ask_user_question",
        "question_payload": question_payload,
        "intake_package": pkg_path,
        "manual_command": f"python nee.py intake-publish --package \"{pkg_path}\"",
    }
    return result
