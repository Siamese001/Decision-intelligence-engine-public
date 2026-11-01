"""Structural monotonicity guard enforcing non-decreasing core entity/assertion metrics."""

from __future__ import annotations

from typing import Any, Iterable


class StructuralMonotonicityError(RuntimeError):
    """Raised when an unauthorized structural deletion or count decrease is detected."""

    pass


# Core components that must not decrease without explicit authorization
DEFAULT_MONITORED_COMPONENTS = (
    "source",
    "source_version",
    "evidence",
    "evidence_item",
    "internal_entity",
    "external_concept",
    "canonical_assertion",
    "candidate_assertion",
    "candidate_search_document",
    "search_document",
    "audit_event",
)


def _resolve_allowed_deletions(
    allow_deletions: bool | Iterable[str],
    all_keys: Iterable[str],
) -> set[str]:
    """Resolve allowed deletion set from boolean or iterable."""
    if allow_deletions is True:
        return set(all_keys)
    if isinstance(allow_deletions, (set, list, tuple)):
        return set(allow_deletions)
    return set()


def verify_structural_monotonicity(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    *,
    allow_deletions: bool | Iterable[str] = False,
    monitored_components: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Verify that graph entity and assertion counts do not silently decrease.

    Args:
        before_counts: Mapping of component names to counts before mutation.
        after_counts: Mapping of component names to counts after mutation.
        allow_deletions: If True, all count drops are permitted. If an iterable of component names,
            drops are permitted only for those specific components.
        monitored_components: Iterable of components to enforce. Defaults to DEFAULT_MONITORED_COMPONENTS.

    Returns:
        Validation dictionary with status, is_monotonic, violations list, and deltas mapping.

    Raises:
        StructuralMonotonicityError: If an unauthorized count reduction is detected.
    """
    targets = (
        set(monitored_components)
        if monitored_components is not None
        else set(DEFAULT_MONITORED_COMPONENTS)
    )
    all_keys = set(before_counts.keys()) | set(after_counts.keys())
    allowed_set = _resolve_allowed_deletions(allow_deletions, all_keys)

    deltas: dict[str, int] = {}
    violations: list[dict[str, Any]] = []

    for comp in sorted(all_keys):
        if comp not in targets:
            continue
        c_before = before_counts.get(comp, 0)
        c_after = after_counts.get(comp, 0)
        delta = c_after - c_before
        deltas[comp] = delta

        if delta < 0 and comp not in allowed_set:
            violations.append(
                {
                    "component": comp,
                    "before": c_before,
                    "after": c_after,
                    "delta": delta,
                }
            )

    if violations:
        details = ", ".join(
            f"{v['component']} ({v['before']} -> {v['after']}, delta={v['delta']})"
            for v in violations
        )
        msg = f"Structural monotonicity violation: unauthorized deletion in {len(violations)} component(s): {details}"
        raise StructuralMonotonicityError(msg)

    return {
        "status": "PASS",
        "is_monotonic": True,
        "violations": [],
        "deltas": deltas,
        "allow_deletions_applied": sorted(allowed_set) if allowed_set else False,
    }
