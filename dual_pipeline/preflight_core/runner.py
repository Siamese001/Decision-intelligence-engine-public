"""Preflight runner executing all system and security controls."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..core import digest, utc_now, write_json_exclusive
from .checks import (
    check_cpu,
    check_locked_environment,
    check_model_manifest,
    check_offline_bge_m3_smoke,
    check_path_separation,
    check_pip_dependencies,
    check_production_authorization,
    check_report_destination,
    check_runtime_contract,
    check_runtime_root,
    check_seed_graph,
    check_tracked_secrets,
    load_seed_contract,
)
from .helpers import _blocked, _dispatch, _fail, _safe_check
from .models import PreflightConfig


def run_preflight(config: PreflightConfig) -> dict[str, Any]:
    """Execute every preflight control and write a sealed machine-readable receipt."""

    supplied_config = config
    path_separation_fn = _dispatch("check_path_separation", check_path_separation)
    path_separation = path_separation_fn(supplied_config)
    if path_separation["status"] != "PASS":
        raise ValueError(
            "unsafe preflight path layout; no runtime or receipt was created: "
            f"{path_separation['error']}"
        )
    config = replace(
        config,
        runtime_root=config.runtime_root.resolve(),
        graph=config.graph.resolve(),
        model_path=config.model_path.resolve(),
        model_manifest=config.model_manifest.resolve(),
        report=config.report.resolve(),
        repo_root=config.repo_root.resolve(),
    )
    checks: dict[str, dict[str, Any]] = {}
    checks["path_separation"] = path_separation
    checks["report_destination"] = _safe_check(
        _dispatch("check_report_destination", check_report_destination),
        config.report,
        config.runtime_root,
    )
    checks["runtime_root"] = _safe_check(
        _dispatch("check_runtime_root", check_runtime_root), config.runtime_root
    )
    checks["runtime_contract"] = _safe_check(
        _dispatch("check_runtime_contract", check_runtime_contract),
        config.repo_root / "requirements" / "runtime-direct.json",
    )
    checks["pip_dependencies"] = _safe_check(
        _dispatch("check_pip_dependencies", check_pip_dependencies)
    )
    checks["locked_environment"] = _safe_check(
        _dispatch("check_locked_environment", check_locked_environment),
        config.repo_root / "requirements" / "runtime-cpu.lock.txt",
    )
    checks["cpu"] = _safe_check(
        _dispatch("check_cpu", check_cpu),
        config.repo_root / "requirements" / "runtime-direct.json",
    )
    checks["model_manifest"] = _safe_check(
        _dispatch("check_model_manifest", check_model_manifest),
        config.model_path,
        config.model_manifest,
        config.model_revision,
        config.repo_root / "requirements" / "bge-m3-model.json",
    )
    seed_contract = _safe_check(
        _dispatch("load_seed_contract", load_seed_contract), config.repo_root
    )
    if seed_contract["status"] == "PASS":
        checks["seed_graph"] = _safe_check(
            _dispatch("check_seed_graph", check_seed_graph),
            config.graph,
            seed_contract["expected_sha256"],
        )
        checks["seed_graph"]["contract"] = {
            key: value for key, value in seed_contract.items() if key != "status"
        }
    else:
        checks["seed_graph"] = seed_contract
    checks["tracked_secret_scan"] = _safe_check(
        _dispatch("check_tracked_secrets", check_tracked_secrets), config.repo_root
    )

    smoke_prerequisites = tuple(
        name
        for name in (
            "runtime_contract",
            "pip_dependencies",
            "locked_environment",
            "cpu",
            "model_manifest",
        )
        if checks[name]["status"] != "PASS"
    )
    if smoke_prerequisites:
        checks["offline_bge_m3_smoke"] = _blocked(*smoke_prerequisites)
    else:
        checks["offline_bge_m3_smoke"] = _safe_check(
            _dispatch("check_offline_bge_m3_smoke", check_offline_bge_m3_smoke),
            config.model_path,
            config.model_manifest,
            config.model_revision,
        )
        if (
            checks["offline_bge_m3_smoke"]["status"] == "PASS"
            and checks["offline_bge_m3_smoke"].get("model_manifest_digest")
            != checks["model_manifest"]["model_manifest_digest"]
        ):
            checks["offline_bge_m3_smoke"] = _fail(
                "loaded backend manifest digest differs from the preflight verification"
            )

    if checks["model_manifest"]["status"] == "PASS":
        checks["production_authorization"] = _safe_check(
            _dispatch("check_production_authorization", check_production_authorization),
            config,
            checks["model_manifest"]["model_manifest_digest"],
        )
    else:
        checks["production_authorization"] = _blocked("model_manifest")

    if seed_contract["status"] == "PASS":
        checks["seed_graph_final"] = _safe_check(
            _dispatch("check_seed_graph", check_seed_graph),
            config.graph,
            seed_contract["expected_sha256"],
        )
    else:
        checks["seed_graph_final"] = _blocked("seed_graph")
    checks["model_manifest_final"] = _safe_check(
        _dispatch("check_model_manifest", check_model_manifest),
        config.model_path,
        config.model_manifest,
        config.model_revision,
        config.repo_root / "requirements" / "bge-m3-model.json",
    )
    if (
        checks["model_manifest_final"]["status"] == "PASS"
        and checks["model_manifest"]["status"] == "PASS"
        and {
            key: checks["model_manifest_final"].get(key)
            for key in (
                "model_manifest_digest",
                "file_count",
                "snapshot_size_bytes",
            )
        }
        != {
            key: checks["model_manifest"].get(key)
            for key in (
                "model_manifest_digest",
                "file_count",
                "snapshot_size_bytes",
            )
        }
    ):
        checks["model_manifest_final"] = _fail(
            "model snapshot changed during preflight"
        )
    checks["runtime_root_final"] = _safe_check(
        _dispatch("check_runtime_root", check_runtime_root), config.runtime_root
    )

    required = [name for name in checks if name != "production_authorization"]
    if config.strict_production or checks["production_authorization"]["status"] != "SKIP":
        required.append("production_authorization")
    failures = [name for name in required if checks[name]["status"] != "PASS"]
    production_ready = not failures and checks["production_authorization"]["status"] == "PASS"
    authorization_requested = (
        config.strict_production
        or any(
            (
                config.auth_token,
                config.auth_public_key,
                config.auth_issuer,
                config.auth_audience,
                config.copilot_package,
                config.architecture_package,
                config.pdlc_question_file,
                config.approval_id,
                config.copilot_decisions,
                config.architecture_decisions,
            )
        )
    )
    receipt: dict[str, Any] = {
        "schema_version": "1.0.0",
        "initiative_id": "NEE-KG-DUAL-PIPELINE-01",
        "receipt_type": "bge_m3_production_preflight",
        "generated_at": utc_now(),
        "mode": (
            "strict-production"
            if config.strict_production
            else "production-authorization-verified"
            if authorization_requested
            else "infrastructure-only"
        ),
        "status": "PASS" if not failures else "FAIL",
        "production_execution_ready": production_ready,
        "model_id": "BAAI/bge-m3",  # noqa: SSOT_EXEMPT
        "model_revision": config.model_revision,
        "seed_graph_sha256": checks.get("seed_graph", {}).get("sha256"),
        "model_manifest_digest": checks.get("model_manifest", {}).get(
            "model_manifest_digest"
        ),
        "required_checks": required,
        "failed_checks": failures,
        "checks": checks,
    }
    receipt["receipt_digest"] = digest(receipt)
    if checks["report_destination"]["status"] == "PASS":
        write_json_exclusive(config.report, receipt)
    return receipt
