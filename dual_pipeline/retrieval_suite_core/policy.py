"""Policy loader for retrieval suite generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import read_json
from .constants import GENERATOR_VERSION, RETRIEVAL_SUITE_GENERATION_POLICY_PATH
from .exceptions import AutomaticSuiteBuildError


def _load_generation_policy(
    path: Path = RETRIEVAL_SUITE_GENERATION_POLICY_PATH,
) -> dict[str, Any]:
    policy = read_json(path)
    required = {
        "schema_version",
        "policy_id",
        "policy_version",
        "owner",
        "generator_version",
        "max_records_per_group",
        "core_top_k",
        "delta_top_k",
        "semantic_templates",
        "alias_template",
        "relationship_template",
        "hard_negative_query",
        "acl_boundary_audiences",
    }
    if set(policy) != required:
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_FIELDS",
            "retrieval suite generation policy fields are malformed",
        )
    if policy["schema_version"] != "1.0.0":
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_VERSION",
            "unsupported retrieval suite generation policy schema",
        )
    if policy["generator_version"] != GENERATOR_VERSION:
        raise AutomaticSuiteBuildError(
            "GENERATOR_VERSION_MISMATCH",
            f"expected {GENERATOR_VERSION}, observed {policy['generator_version']}",
        )
    if not 1 <= int(policy["max_records_per_group"]) <= 100:
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_GROUP_SIZE",
            "max_records_per_group must be between 1 and 100",
        )
    if not 1 <= int(policy["core_top_k"]) <= 100:
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_CORE_TOP_K",
            "core_top_k must be between 1 and 100",
        )
    if not 1 <= int(policy["delta_top_k"]) <= 100:
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_DELTA_TOP_K",
            "delta_top_k must be between 1 and 100",
        )
    templates = policy["semantic_templates"]
    if (
        not isinstance(templates, list)
        or len(templates) < 3
        or len({" ".join(str(item).casefold().split()) for item in templates}) < 3
    ):
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_SEMANTIC_TEMPLATES",
            "at least three distinct semantic templates are required",
        )
    audiences = policy["acl_boundary_audiences"]
    if (
        not isinstance(audiences, list)
        or not audiences
        or len(audiences) != len(set(audiences))
    ):
        raise AutomaticSuiteBuildError(
            "GENERATION_POLICY_ACL_AUDIENCES",
            "ACL boundary audiences must be a nonempty unique list",
        )
    return policy
