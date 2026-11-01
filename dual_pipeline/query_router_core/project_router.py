"""Project directory router for transcript and ingestion pipelines.

Intelligently selects the target project directory for a meeting transcript,
strictly conforming to KNOWN_PROJECTS.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Sequence

from storage.credentials import CredentialManager

logger = logging.getLogger("dual_pipeline.query_router_core.project_router")


def _resolve_client(client: Optional[Any], model: str, reasoning_effort: str) -> Optional[Any]:
    """Resolves or initializes an OpenAI client."""
    if client is not None:
        return client
    try:
        c, _ = CredentialManager.get_openai_client(
            allow_mock=False,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        return c
    except Exception:
        return None


def _execute_llm_routing(
    client: Any,
    model: str,
    reasoning_effort: str,
    metadata: Dict[str, Any],
    transcript_snippet: str,
    known_projects_list: Sequence[str],
) -> Optional[str]:
    """Dispatches the dynamic project router LLM prompt."""
    from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
    prompt_template = CANONICAL_PROMPT_REGISTRY.get_prompt("DYNAMIC_PROJECT_ROUTER")
    system_prompt = prompt_template.format(
        known_projects_json=json.dumps(known_projects_list, separators=(',', ':'), sort_keys=True)
    )

    user_content = json.dumps(metadata, separators=(",", ":"), sort_keys=True)
    if transcript_snippet:
        user_content += f"\n\nSnippet:\n{transcript_snippet[:1000]}"

    from config.request_deadline import remaining_timeout

    try:
        raw_kwargs = {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "messages": [
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_content},
            ],
            "max_completion_tokens": 2000,
            "response_format": {"type": "json_object"},
            "cache_namespace": "dynamic_project_router",
            "timeout": remaining_timeout(30.0),
        }
        call_kwargs = CredentialManager.sanitize_call_kwargs(model, raw_kwargs)
        response = client.chat.completions.create(**call_kwargs)

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw.strip())
        project = data.get("project")
        confidence = float(data.get("confidence", 1.0) or 0.0)

        if project in known_projects_list and confidence >= 0.70:
            logger.info(f"Dynamic router selected project: {project} (confidence: {confidence:.2f})")
            return project
        if project:
            logger.warning(f"LLM returned project '{project}' with low confidence {confidence:.2f} < 0.70. Falling back.")
        return None
    except Exception as e:
        logger.error(f"Dynamic routing LLM call failed: {e}")
        return None


def _extract_orgs_from_text(text: str) -> set[str]:
    orgs = set()
    s = text.lower()
    if "@google" in s or "google" in s:
        orgs.add("google")
    if "@partner_advisory" in s or "partner_advisory" in s:
        orgs.add("partner_advisory")
    return orgs


def _extract_orgs_from_item(item: Any) -> set[str]:
    if isinstance(item, dict) and item.get("org"):
        return {str(item["org"]).lower()}
    if isinstance(item, str):
        return _extract_orgs_from_text(item)
    return set()


def _extract_orgs(metadata: Dict[str, Any]) -> set[str]:
    orgs = set()
    roster = metadata.get("roster")
    if isinstance(roster, list):
        for r in roster:
            orgs.update(_extract_orgs_from_item(r))

    attendees = metadata.get("attendees")
    if isinstance(attendees, dict):
        orgs.update(str(k).lower() for k in attendees)
    elif isinstance(attendees, list):
        for a in attendees:
            orgs.update(_extract_orgs_from_item(a))
    elif isinstance(attendees, str):
        orgs.update(_extract_orgs_from_text(attendees))

    return orgs


def _resolve_project_from_roster(metadata: Dict[str, Any], known_projects: Sequence[str]) -> Optional[str]:
    """Resolves project based on attendee organization domains and affiliations."""
    if "partner_alliances" not in known_projects:
        return None

    orgs = _extract_orgs(metadata)
    has_google = any("google" in o or "gcp" in o for o in orgs)
    has_partner_advisory = any("partner_advisory" in o for o in orgs)
    has_apex_global = any("apex_global" in o or "apex" in o or "nea" in o for o in orgs)

    # Joint partner alliances (Google + PartnerAdvisory) without explicit customer anchor
    if has_google and has_partner_advisory and not has_apex_global:
        return "partner_alliances"

    # Pure internal partner_advisory
    if has_partner_advisory and len(orgs) == 1 and "internal_consulting" in known_projects:
        return "internal_consulting"

    return None


def _resolve_deterministic_project(metadata: Dict[str, Any], known_projects_list: Sequence[str]) -> Optional[str]:
    """Deterministically resolves project from workstream, title, explicit metadata, roster, or file paths."""
    ws = metadata.get("workstream")
    if ws:
        from dual_pipeline.parameters import resolve_project_name
        res_ws = resolve_project_name(ws)
        if res_ws in known_projects_list:
            logger.info(f"Dynamic router resolved project '{res_ws}' from explicit workstream metadata")
            return res_ws

    meeting_name = str(metadata.get("meeting_name") or metadata.get("title") or "")
    if meeting_name:
        det_proj = _resolve_project_from_title(meeting_name, known_projects_list)
        if det_proj:
            explicit_proj = metadata.get("project")
            container_projects = ("auto", "nea", "project_nea", "project_nexus", "internal_consulting")
            if not explicit_proj or explicit_proj in container_projects or det_proj == explicit_proj:
                logger.info(f"Dynamic router deterministically resolved project '{det_proj}' from title '{meeting_name}'")
                return det_proj

    if metadata.get("project") in known_projects_list:
        logger.info(f"Dynamic router bypassing LLM. Using explicit metadata project: {metadata['project']}")
        return metadata["project"]

    roster_proj = _resolve_project_from_roster(metadata, known_projects_list)
    if roster_proj:
        logger.info(f"Dynamic router deterministically resolved project '{roster_proj}' from attendee roster")
        return roster_proj

    meta_proj = _resolve_from_metadata(metadata, known_projects_list)
    if meta_proj:
        logger.info(f"Dynamic router resolved project '{meta_proj}' from metadata paths")
        return meta_proj

    return None


def _is_offline_or_dry_run(metadata: Dict[str, Any], dry_run: bool, allow_llm: bool) -> bool:
    """Checks whether LLM routing is disabled via dry-run, offline mode, or explicit flag."""
    import os
    if dry_run or metadata.get("dry_run") or metadata.get("dry_run_graph"):
        return True
    if os.environ.get("NEE_DRY_RUN") in ("1", "true", "True"):
        return True
    if not allow_llm or metadata.get("allow_llm") is False:
        return True
    if metadata.get("provider") == "offline" or metadata.get("provider_type") == "offline":
        return True
    return False


def determine_project_directory(
    metadata: Dict[str, Any],
    transcript_snippet: str = "",
    api_key: Optional[str] = None,
    client: Optional[Any] = None,
    model: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    allow_llm: bool = True,
    dry_run: bool = False,
) -> Optional[str]:
    """Uses an LLM (resolved via ModelTier.FAST_ROUTING) to intelligently determine

    the target project directory for the given meeting, strictly conforming to KNOWN_PROJECTS.
    """
    from dual_pipeline.model_registry import ModelTier, resolve_tier_model
    from dual_pipeline.parameters import get_known_projects

    default_model, default_effort = resolve_tier_model(ModelTier.FAST_ROUTING)
    model = model or default_model
    reasoning_effort = reasoning_effort or default_effort

    known_projects_list = sorted(list(get_known_projects()))

    deterministic_result = _resolve_deterministic_project(metadata, known_projects_list)
    if deterministic_result:
        return deterministic_result

    if _is_offline_or_dry_run(metadata, dry_run, allow_llm):
        return None

    resolved_client = _resolve_client(client, model, reasoning_effort)
    if resolved_client is None:
        logger.warning("OpenAI client not initialized. Cannot run dynamic routing. Falling back to manual check.")
        return None

    return _execute_llm_routing(
        client=resolved_client,
        model=model,
        reasoning_effort=reasoning_effort,
        metadata=metadata,
        transcript_snippet=transcript_snippet,
        known_projects_list=known_projects_list,
    )


def _get_project_aliases() -> Dict[str, str]:
    from dual_pipeline.parameters import get_project_aliases
    return get_project_aliases()


def _resolve_project_mention(clean_q: str, known_projects: Sequence[str]) -> Optional[str]:
    aliases = _get_project_aliases()
    for alias, proj in sorted(aliases.items(), key=lambda x: -len(x[0])):
        pattern = rf"\b{re.escape(alias)}\b"
        if re.search(pattern, clean_q) and proj in known_projects:
            return proj
    return None


def _resolve_project_from_title(title: str, known_projects: Sequence[str]) -> Optional[str]:
    """Deterministically resolves project from meeting title with prefix precedence."""
    clean_t = title.strip()
    if not clean_t:
        return None
    clean_lower = clean_t.lower()
    if (
        "partner_advisory" in clean_lower
        and ("google" in clean_lower or "gcp" in clean_lower)
        and "partner_alliances" in known_projects
    ):
        return "partner_alliances"
    # 1. High-confidence prefix match: "NEA: ...", "NEA - ...", "NEA_...", "Project Lens - ..."
    aliases = _get_project_aliases()
    for alias, proj in sorted(aliases.items(), key=lambda x: -len(x[0])):
        prefix_pattern = rf"^{re.escape(alias)}(?:[\s_\-:\/]|$)"
        if re.search(prefix_pattern, clean_t, re.IGNORECASE) and proj in known_projects:
            return proj

    # 2. General word boundary mention
    mention_proj = _resolve_project_mention(clean_t.lower(), known_projects)
    if mention_proj:
        return mention_proj

    # 3. Token-based inference from title keywords
    from dual_pipeline.project_aliases import infer_project_from_title
    inferred = infer_project_from_title(clean_t)
    if inferred and inferred in known_projects:
        return inferred

    return None


def _resolve_from_metadata(
    metadata: Dict[str, Any], known_projects: Sequence[str]
) -> Optional[str]:
    meta_proj = metadata.get("project")
    if meta_proj in known_projects:
        return meta_proj

    active_doc = (
        metadata.get("active_document")
        or metadata.get("active_file")
        or metadata.get("file_path")
        or metadata.get("input_path")
        or metadata.get("input")
    )
    if active_doc and isinstance(active_doc, str):
        for proj in known_projects:
            if f"output/{proj}/" in active_doc or f"/{proj}/" in active_doc:
                return proj
        if "market_intelligence" in known_projects and (
            "/market_intelligence/" in active_doc
            or "market_intel" in active_doc
            or "weekly_update" in active_doc
        ):
            return "market_intelligence"

    return None


def determine_query_project(
    question: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Determines the target project identifier for a user query."""
    from dual_pipeline.parameters import get_known_projects
    from dual_pipeline.system_directory import resolve_system_project

    known_projects = get_known_projects()

    partner_signal = infer_project_from_keyword_signals(question, check_partner_alliances=True)
    if partner_signal == "partner_alliances" and "partner_alliances" in known_projects:
        return "partner_alliances"

    system_proj = resolve_system_project(question)
    if system_proj and system_proj in known_projects:
        logger.info(
            f"Query project resolved from enterprise system entity: {system_proj} (query: {question[:60]})"
        )
        return system_proj

    direct_proj = _resolve_project_mention(question.lower(), known_projects)
    if direct_proj:
        return direct_proj

    if metadata:
        return _resolve_from_metadata(metadata, known_projects)

    return None


_KEYWORD_SIGNAL_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("sales immersion", "sales learning", "partner_advisory sales", "internal_consulting"), "internal_consulting"),
    (("nexus", "reid"), "project_nexus"),
    (("lens",), "project_lens"),
    (("sherpa",), "project_beacon"),
    (("rewire", "apex"), "project_atlas"),
    (("conduit",), "conduit"),
    (("nea", "skantze"), "nea"),
    (("partner_advisory", "internal"), "internal_consulting"),
    (("market_intel", "weekly_trend", "market_trend", "energy_utilit", "weekly_update"), "market_intelligence"),
    (("enterprise", "data mesh", "datamesh", "architecture"), "enterprise_architecture"),
)


def infer_project_from_keyword_signals(text: str, *, check_partner_alliances: bool = True) -> Optional[str]:
    """Deterministically resolves project from filename stem or raw text keyword cues."""
    low = (text or "").lower()
    if not low:
        return None
    if (
        check_partner_alliances
        and ("google" in low or "gcp" in low)
        and ("partner_advisory" in low or "partner" in low or "alex" in low)
    ):
        return "partner_alliances"
    for cues, project_id in _KEYWORD_SIGNAL_RULES:
        if any(c in low for c in cues):
            return project_id
    return None


__all__ = ["determine_project_directory", "determine_query_project", "infer_project_from_keyword_signals"]
