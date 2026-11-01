"""Receipt discovery and runtime inventory indexing."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from config.credentials import CredentialManager

from ..assurance import (
    INTERNAL_OPERATIONAL,
    assurance_status_label,
    internal_operational_status,
    pair_assurance_tier,
)
from ..runtime_root import resolve_authoritative_runtime_root
from .constants import (
    PAIR_REQUIRED_FIELDS,
)
from .inventory_cache import (
    compute_inventory_cache_key,
    read_cached_inventory,
    resolve_pair_and_root,
    write_cached_inventory,
)
from .retrieval_discovery import (
    _artifact_digest,
    _retrieval_evaluation_paths,
    _retrieval_evaluation_record,
    _retrieval_suite_paths,
    _retrieval_suite_record,
    collect_retrieval_evaluations,
    collect_retrieval_suites,
)
from .primitives import (
    _digest,
    _iter_reference_values,
    _path_is_within,
    _read_json,
    _resolve_reference,
    _resolved,
    _walk_for_active_pairs,
)


def discover_receipt_references(repository_root: Path) -> list[dict[str, Any]]:
    """Find runtime/evidence roots explicitly named by checked-in receipts."""

    repository_root = _resolved(repository_root)
    references: dict[tuple[str, str], dict[str, Any]] = {}
    for relative_root in ("governance", "approvals", "archive/approvals", "reports"):
        scan_root = repository_root / relative_root
        if not scan_root.is_dir():
            continue
        for source in scan_root.rglob("*.json"):
            try:
                artifact = _read_json(source)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for kind, raw in _iter_reference_values(artifact):
                path = _resolve_reference(raw, repository_root)
                key = (kind, str(path).casefold())
                references.setdefault(
                    key,
                    {
                        "kind": kind,
                        "path": str(path),
                        "exists": path.exists(),
                        "source": str(_resolved(source)),
                    },
                )
    return sorted(
        references.values(),
        key=lambda value: (
            str(value["kind"]),
            str(value["path"]).casefold(),
            str(value["source"]).casefold(),
        ),
    )


def _pair_record(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    try:
        pair, runtime_root = resolve_pair_and_root(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return (
            {
                "active_pair_path": str(_resolved(path)),
                "runtime_root": str(_resolved(path.parent)),
                "parse_status": "FAIL",
            },
            [
                {
                    "code": "ACTIVE_PAIR_INVALID",
                    "path": str(_resolved(path)),
                    "detail": str(exc),
                }
            ],
        )
    missing = [field for field in PAIR_REQUIRED_FIELDS if field not in pair]
    if missing:
        issues.append(
            {
                "code": "ACTIVE_PAIR_FIELDS_MISSING",
                "path": str(_resolved(path)),
                "detail": ", ".join(missing),
            }
        )
    graph_path = runtime_root / str(pair.get("graph_path") or "")
    sidecar_path = runtime_root / str(pair.get("sidecar_path") or "")
    if pair.get("graph_path") and not graph_path.is_file():
        issues.append(
            {
                "code": "ACTIVE_GRAPH_MISSING",
                "path": str(_resolved(graph_path)),
                "detail": "active_pair.json graph_path does not resolve to a file",
            }
        )
    if pair.get("sidecar_path") and not sidecar_path.is_file():
        issues.append(
            {
                "code": "ACTIVE_SIDECAR_MISSING",
                "path": str(_resolved(sidecar_path)),
                "detail": "active_pair.json sidecar_path does not resolve to a file",
            }
        )
    assurance_tier = pair_assurance_tier(pair)
    assurance: dict[str, Any] = {
        "assurance_tier": assurance_tier,
        "operational_status": "NOT_APPLICABLE",
        "assurance_status": assurance_status_label(
            assurance_tier,
            "NOT_APPLICABLE",
        ),
        "qualification_id": None,
        "qualification_digest": None,
    }
    if assurance_tier == INTERNAL_OPERATIONAL:
        try:
            assurance = internal_operational_status(runtime_root, pair)
        except Exception as exc:
            issues.append(
                {
                    "code": "INTERNAL_OPERATIONAL_EVIDENCE_INVALID",
                    "path": str(_resolved(path)),
                    "detail": str(exc),
                }
            )
            assurance = {
                "assurance_tier": assurance_tier,
                "operational_status": "INVALID",
                "assurance_status": "internal-operational evidence invalid",
                "qualification_id": None,
                "qualification_digest": None,
            }
    return (
        {
            "active_pair_path": str(_resolved(path)),
            "runtime_root": str(runtime_root),
            "parse_status": "PASS",
            "activated_at": pair.get("activated_at"),
            "embedding_backend": pair.get("embedding_backend"),
            "generation_id": pair.get("generation_id"),
            "graph_path": str(_resolved(graph_path)) if pair.get("graph_path") else None,
            "graph_sha256": pair.get("graph_sha256"),
            "model_id": pair.get("model_id"),
            "model_manifest_digest": pair.get("model_manifest_digest"),
            "model_revision": pair.get("model_revision"),
            "production_qualified": pair.get("production_qualified"),
            "assurance_tier": assurance["assurance_tier"],
            "operational_status": assurance["operational_status"],
            "assurance_status": assurance["assurance_status"],
            "qualification_id": assurance.get("qualification_id"),
            "qualification_digest": assurance.get("qualification_digest"),
            "sidecar_path": (str(_resolved(sidecar_path)) if pair.get("sidecar_path") else None),
            "sidecar_sha256": pair.get("sidecar_sha256"),
        },
        issues,
    )


def _query_scope_key(
    path: Path,
    runtime_root: Path | None,
    evidence_root: Path | None = None,
) -> str:
    """Namespace repeatable run IDs by their owning runtime/evidence scope."""

    if runtime_root is not None:
        return f"runtime:{str(_resolved(runtime_root)).casefold()}"
    if evidence_root is not None:
        return f"evidence:{str(_resolved(evidence_root)).casefold()}"
    return f"artifact:{str(_resolved(path.parent)).casefold()}"


def _query_record(
    path: Path,
    runtime_root: Path | None = None,
    evidence_root: Path | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    try:
        value = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, []
    backend = value.get("embedding_backend")
    run_id = value.get("run_id")
    if not isinstance(backend, str) or not backend.strip():
        return None, []
    if not isinstance(run_id, str) or not run_id.strip():
        return None, []

    report_binding = value.get("retrieval_evidence")
    report_fields: dict[str, Any] = {
        "retrieval_report_binding_status": "legacy_absent",
        "retrieval_report_id": None,
        "retrieval_report_digest": None,
        "retrieval_report_json_path": None,
        "retrieval_report_markdown_path": None,
        "retrieval_coverage_counts": None,
        "retrieval_terminal_outcome": None,
        "retrieval_selected_record_counts_by_lane": None,
        "retrieval_l2_visible_record_count": None,
    }
    issues: list[dict[str, str]] = []
    if isinstance(report_binding, dict):
        json_path_value = report_binding.get("report_json_path")
        markdown_path_value = report_binding.get("report_markdown_path")
        json_path = (
            _resolved(Path(json_path_value))
            if isinstance(json_path_value, str) and json_path_value.strip()
            else _resolved(path.parent / "retrieval_evidence_report.json")
        )
        if not json_path.is_file() and (path.parent / "retrieval_evidence_report.json").is_file():
            json_path = _resolved(path.parent / "retrieval_evidence_report.json")

        markdown_path = (
            _resolved(Path(markdown_path_value))
            if isinstance(markdown_path_value, str) and markdown_path_value.strip()
            else _resolved(path.parent / "retrieval_evidence.md")
        )
        if not markdown_path.is_file() and (path.parent / "retrieval_evidence.md").is_file():
            markdown_path = _resolved(path.parent / "retrieval_evidence.md")
        try:
            report = _read_json(json_path)
            canonical = report.get("canonical_digest")
            report_digest = canonical.get("value") if isinstance(canonical, dict) else None
            expected_digest = report_binding.get("report_digest") or value.get(
                "retrieval_evidence_report_digest"
            )
            if (
                report.get("contract_id") != "urn:nee:dual-pipeline:retrieval-evidence-report:1.0.0"
                or report.get("run_id") != run_id
                or report_digest != _artifact_digest(report)
                or report_digest != expected_digest
                or not markdown_path.is_file()
            ):
                raise ValueError(
                    "retrieval report contract, digest, run, or markdown binding is invalid"
                )
            report_fields = {
                "retrieval_report_binding_status": "valid",
                "retrieval_report_id": report.get("report_id"),
                "retrieval_report_digest": report_digest,
                "retrieval_report_json_path": str(json_path),
                "retrieval_report_markdown_path": str(markdown_path),
                "retrieval_coverage_counts": report_binding.get("coverage_counts"),
                "retrieval_terminal_outcome": report_binding.get("terminal_coverage_disposition"),
                "retrieval_selected_record_counts_by_lane": report_binding.get(
                    "selected_record_counts_by_lane"
                ),
                "retrieval_l2_visible_record_count": report_binding.get("l2_visible_record_count"),
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report_fields["retrieval_report_binding_status"] = "invalid"
            issues.append(
                {
                    "code": "QUERY_RETRIEVAL_REPORT_INVALID",
                    "path": str(json_path),
                    "detail": f"run {run_id}: {exc}",
                }
            )

    terminal_query = (
        value.get("status") in {"COMPLETED", "BLOCKED"}
        and isinstance(value.get("x3_outcome"), str)
        and value["x3_outcome"].startswith("X3")
    )
    chain_digest = value.get("learning_event_chain_digest")
    shadow_observability_eligible = (
        terminal_query
        and value.get("audit_status") == "PASS"
        and isinstance(chain_digest, str)
        and len(chain_digest) == 64
        and value.get("graph_sha256_before") is not None
        and value.get("graph_sha256_before") == value.get("graph_sha256_after")
    )
    return {
        "path": str(_resolved(path)),
        "runtime_root": (str(_resolved(runtime_root)) if runtime_root is not None else None),
        "evidence_root": (str(_resolved(evidence_root)) if evidence_root is not None else None),
        "evidence_kind": "live_query",
        "run_id": run_id,
        "status": value.get("status"),
        "embedding_backend": backend,
        "generation_id": value.get("generation_id"),
        "graph_sha256_before": value.get("graph_sha256_before"),
        "graph_sha256_after": value.get("graph_sha256_after"),
        "production_qualified": value.get("production_qualified"),
        "assurance_tier": value.get("assurance_tier"),
        "operational_status": value.get("operational_status"),
        "qualification_id": value.get("qualification_id"),
        "qualification_digest": value.get("qualification_digest"),
        "dense_executed": value.get("dense_executed"),
        "sparse_executed": value.get("sparse_executed"),
        "bm25_executed": value.get("bm25_executed"),
        "colbert_executed": value.get("colbert_executed"),
        "graph_traversal_executed": value.get("graph_traversal_executed"),
        "graph_requested_hops": value.get("graph_requested_hops"),
        "graph_max_hop_reached": value.get("graph_max_hop_reached"),
        "connected_evidence_map_id": value.get("connected_evidence_map_id"),
        "connected_evidence_map_digest": value.get("connected_evidence_map_digest"),
        "approved_record_count": value.get("approved_record_count"),
        "candidate_record_count": value.get("candidate_record_count"),
        "audit_status": value.get("audit_status"),
        "x3_outcome": value.get("x3_outcome"),
        "l1_controller": (
            dict(value["l1_controller"])
            if isinstance(value.get("l1_controller"), Mapping)
            else None
        ),
        "terminal_query": terminal_query,
        "shadow_observability_eligible": shadow_observability_eligible,
        **report_fields,
    }, issues


def _query_paths(
    runtime_roots: Iterable[Path], evidence_roots: Iterable[Path]
) -> list[tuple[Path, Path | None, Path | None]]:
    paths: dict[Path, tuple[Path | None, Path | None]] = {}
    for root in runtime_roots:
        runtime_root = _resolved(root)
        runs = runtime_root / "runs"
        if runs.is_dir():
            for path in runs.rglob("query_summary.json"):
                paths[_resolved(path)] = (runtime_root, None)
    for root in evidence_roots:
        resolved = _resolved(root)
        if not resolved.is_dir():
            continue
        for path in resolved.rglob("query_summary.json"):
            paths.setdefault(_resolved(path), (None, resolved))
        for path in resolved.rglob("answer.json"):
            paths.setdefault(_resolved(path), (None, resolved))
    return sorted(
        [
            (path, runtime_root, evidence_root)
            for path, (runtime_root, evidence_root) in paths.items()
        ],
        key=lambda item: str(item[0]).casefold(),
    )


def _l1_terminal_paths(
    runtime_roots: Iterable[Path], evidence_roots: Iterable[Path]
) -> list[tuple[Path, Path | None, Path | None]]:
    """Find sealed L1 controller terminals in every explicit evidence scope."""

    paths: dict[Path, Path | None] = {}
    for root in runtime_roots:
        runtime_root = _resolved(root)
        runs = runtime_root / "runs"
        if runs.is_dir():
            for path in runs.rglob("query_plan_terminal.json"):
                paths[_resolved(path)] = (runtime_root, None)
    for root in evidence_roots:
        evidence_root = _resolved(root)
        if evidence_root.is_dir():
            for path in evidence_root.rglob("query_plan_terminal.json"):
                paths.setdefault(_resolved(path), (None, evidence_root))
    return sorted(
        [
            (path, runtime_root, evidence_root)
            for path, (runtime_root, evidence_root) in paths.items()
        ],
        key=lambda item: str(item[0]).casefold(),
    )


def _l1_terminal_record(
    path: Path,
    runtime_root: Path | None,
    evidence_root: Path | None,
    queries_by_run: Mapping[str, list[Mapping[str, Any]]],
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Validate one terminal and bind it to its terminal query summary."""

    try:
        value = _read_json(path)
        canonical = value.get("canonical_digest")
        if (
            value.get("contract_id") != "urn:nee:dual-pipeline:query-plan-terminal:1.0.0"
            or not isinstance(canonical, dict)
            or canonical.get("algorithm") != "sha256"
            or canonical.get("scope") != "artifact_without_canonical_digest"
            or canonical.get("value") != _artifact_digest(value)
            or value.get("terminal_reason")
            not in {
                "COMPLETED",
                "EXPLICIT_GAP",
                "NO_PROGRESS",
                "BUDGET_EXHAUSTED",
                "UNAUTHORIZED",
                "CONFLICT",
                "HITL_REQUIRED",
                "SHADOW",
            }
            or not isinstance(value.get("final_evidence_report_digest"), str)
            or len(value["final_evidence_report_digest"]) != 64
        ):
            raise ValueError("L1 terminal contract, digest, or final evidence binding is invalid")
        run_id = path.parent.name
        candidates = queries_by_run.get(run_id, [])
        if runtime_root is not None:
            runtime_root_value = str(_resolved(runtime_root))
            candidates = [
                item for item in candidates if item.get("runtime_root") == runtime_root_value
            ]
        elif evidence_root is not None:
            evidence_root_value = str(_resolved(evidence_root))
            candidates = [
                item for item in candidates if item.get("evidence_root") == evidence_root_value
            ]
        else:
            candidates = [
                item
                for item in candidates
                if _path_is_within(str(path), Path(str(item["path"])).parent)
            ]
        if len(candidates) != 1:
            raise ValueError("L1 terminal has no bound terminal query summary")
        query = candidates[0]
        controller = query.get("l1_controller")
        if (
            not isinstance(controller, Mapping)
            or controller.get("terminal_digest") != canonical["value"]
        ):
            raise ValueError("query summary does not bind the L1 terminal digest")
        if controller.get("final_evidence_report_digest") != value["final_evidence_report_digest"]:
            raise ValueError("query summary final evidence report does not bind the L1 terminal")
        return (
            {
                "path": str(_resolved(path)),
                "runtime_root": str(_resolved(runtime_root)) if runtime_root else None,
                "run_id": run_id,
                "embedding_backend": query.get("embedding_backend"),
                "generation_id": query.get("generation_id"),
                "terminal_digest": canonical["value"],
                "terminal_reason": value["terminal_reason"],
                "coverage_complete": value.get("coverage_complete"),
                "permitted_next_stage": value.get("permitted_next_stage"),
                "final_evidence_report_digest": value["final_evidence_report_digest"],
                "graph_sha256_before": value.get("graph_sha256_before"),
                "graph_sha256_after": value.get("graph_sha256_after"),
            },
            None,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return (
            None,
            {
                "code": "L1_TERMINAL_INVALID",
                "path": str(_resolved(path)),
                "detail": str(exc),
            },
        )


def _find_pair_paths(search_roots: Iterable[Path], scope: str) -> set[Path]:
    pair_paths: set[Path] = set()
    for root in search_roots:
        direct = root / "active_pair.json"
        if direct.is_file():
            pair_paths.add(_resolved(direct))
        elif scope != "main":
            pair_paths.update(_walk_for_active_pairs(root))
    return pair_paths


def _collect_query_evidence(
    roots: Iterable[Path],
    referenced_evidence_roots: set[Path],
    issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    query_by_identity: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for path, runtime_root, evidence_root in _query_paths(roots, referenced_evidence_roots):
        record, query_issues = _query_record(path, runtime_root, evidence_root)
        issues.extend(query_issues)
        if record is None:
            continue
        identity = (
            _query_scope_key(path, runtime_root, evidence_root),
            str(record["run_id"]),
            str(record["embedding_backend"]),
            str(record.get("generation_id") or ""),
        )
        existing = query_by_identity.get(identity)
        if existing is None:
            record["artifact_paths"] = [record["path"]]
            query_by_identity[identity] = record
            continue
        comparable_fields = set(existing) - {"artifact_paths", "path"}
        conflicts = [
            field for field in sorted(comparable_fields) if existing.get(field) != record.get(field)
        ]
        if conflicts:
            issues.append(
                {
                    "code": "QUERY_EVIDENCE_CONFLICT",
                    "path": record["path"],
                    "detail": (
                        f"run {record['run_id']} conflicts with {existing['path']} "
                        f"on fields: {', '.join(conflicts)}"
                    ),
                }
            )
        existing["artifact_paths"] = sorted(
            {*existing["artifact_paths"], record["path"]},
            key=str.casefold,
        )
    return sorted(
        query_by_identity.values(),
        key=lambda value: (
            str(
                value.get("runtime_root") or value.get("evidence_root") or value["path"]
            ).casefold(),
            str(value["run_id"]).casefold(),
            str(value["embedding_backend"]).casefold(),
            str(value.get("generation_id") or "").casefold(),
        ),
    )


def _collect_l1_terminals(
    roots: Iterable[Path],
    referenced_evidence_roots: set[Path],
    queries_by_run: dict[str, list[Mapping[str, Any]]],
    issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    l1_controller_terminals: list[dict[str, Any]] = []
    for path, runtime_root, evidence_root in _l1_terminal_paths(roots, referenced_evidence_roots):
        record, issue = _l1_terminal_record(
            path,
            runtime_root,
            evidence_root,
            queries_by_run,
        )
        if issue is not None:
            issues.append(issue)
        elif record is not None:
            l1_controller_terminals.append(record)
    l1_controller_terminals.sort(
        key=lambda value: (
            str(value["run_id"]).casefold(),
            str(value["terminal_digest"]).casefold(),
        )
    )
    return l1_controller_terminals


def discover_inventory(
    repository_root: Path,
    *,
    extra_roots: Iterable[Path] = (),
    scope: str = "all",
    use_cache: bool = True,
) -> dict[str, Any]:
    """Inventory all discoverable runtime roots and retrieval evidence."""
    repository_root = _resolved(repository_root)
    authoritative_root = resolve_authoritative_runtime_root(repository_root)
    explicit_extra_roots = sorted(
        {_resolved(path) for path in extra_roots}, key=lambda value: str(value).casefold()
    )
    environment_roots = sorted(
        {
            _resolved(Path(value))
            for value in CredentialManager.get_credential("NEE_RUNTIME_EVIDENCE_ROOTS", "").split(
                os.pathsep
            )
            if value.strip()
        },
        key=lambda value: str(value).casefold(),
    )
    references = discover_receipt_references(repository_root)
    referenced_runtime_roots = {
        _resolved(Path(item["path"]))
        for item in references
        if item["kind"] == "runtime_root" and item["exists"]
    }
    referenced_evidence_roots = {
        _resolved(Path(item["path"]))
        for item in references
        if item["kind"] == "evidence_root" and item["exists"]
    }
    search_roots = {
        authoritative_root,
        *explicit_extra_roots,
        *environment_roots,
        *referenced_runtime_roots,
    }
    if scope != "main":
        search_roots.add(repository_root)

    cache_dir = authoritative_root.parent / "cache"
    cache_file = cache_dir / "runtime_evidence_inventory.json"
    cache_key = f"{scope}:{compute_inventory_cache_key(search_roots)}"

    if use_cache:
        cached = read_cached_inventory(cache_file, cache_key)
        if cached is not None:
            return cached

    pair_paths = _find_pair_paths(search_roots, scope)
    runtime_pairs: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for path in sorted(pair_paths, key=lambda value: str(value).casefold()):
        record, pair_issues = _pair_record(path)
        runtime_pairs.append(record)
        issues.extend(pair_issues)

    query_runtime_roots = {
        _resolved(Path(item["runtime_root"])) for item in runtime_pairs if item.get("runtime_root")
    } | referenced_runtime_roots

    search_universe = query_runtime_roots | set(explicit_extra_roots) | set(environment_roots)

    query_evidence = _collect_query_evidence(
        search_universe,
        referenced_evidence_roots,
        issues,
    )
    queries_by_run: dict[str, list[Mapping[str, Any]]] = {}
    for item in query_evidence:
        if isinstance(item.get("run_id"), str):
            queries_by_run.setdefault(str(item["run_id"]), []).append(item)

    l1_controller_terminals = _collect_l1_terminals(
        search_universe,
        referenced_evidence_roots,
        queries_by_run,
        issues,
    )
    terminal_query_observability = [
        {
            "run_id": item["run_id"],
            "status": item["status"],
            "x3_outcome": item["x3_outcome"],
            "retrieval_report_binding_status": item["retrieval_report_binding_status"],
            "retrieval_terminal_outcome": item["retrieval_terminal_outcome"],
        }
        for item in query_evidence
        if item["shadow_observability_eligible"]
    ]

    retrieval_evaluations = collect_retrieval_evaluations(
        search_universe,
        referenced_evidence_roots,
        issues,
    )
    retrieval_evaluation_suites = collect_retrieval_suites(
        search_universe,
        referenced_evidence_roots,
        issues,
    )

    inventory = {
        "schema_version": "1.0.0",
        "repository_root": str(repository_root),
        "scope": {
            "repository_tree_searched": True,
            "receipt_references_followed": True,
            "extra_roots": [str(path) for path in explicit_extra_roots],
            "environment_roots": [str(path) for path in environment_roots],
        },
        "reference_roots": references,
        "runtime_pairs": runtime_pairs,
        "query_evidence": query_evidence,
        "l1_controller_terminals": l1_controller_terminals,
        "query_observability": {
            "admission_policy": "sealed_terminal_query_only",
            "eligible_run_count": len(terminal_query_observability),
            "runs": terminal_query_observability,
        },
        "retrieval_evaluation_suites": retrieval_evaluation_suites,
        "retrieval_evaluations": retrieval_evaluations,
        "issues": sorted(
            issues,
            key=lambda value: (
                value["code"],
                value["path"].casefold(),
                value["detail"],
            ),
        ),
    }
    inventory["inventory_digest"] = _digest(inventory)
    if use_cache:
        write_cached_inventory(cache_file, cache_key, inventory)
    return inventory
