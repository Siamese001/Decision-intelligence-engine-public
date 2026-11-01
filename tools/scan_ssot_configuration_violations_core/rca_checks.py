"""tools.scan_ssot_configuration_violations_core.rca_checks

RCA_01 through RCA_08 dynamic SSOT invariant checks.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, List, Optional

from tools.audit_governance_harmonization_core.common import (
    load_json_with_dup_check,
    read_text_safe,
)


def _check_rca_01_duplicate_keys(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    critical_json_paths = [
        root / "config" / "governance" / "project_registry.json",
        root / "config" / "runtime" / "parameters.json",
        root / "config" / "runtime" / "model_registry.json",
        root / "dual_pipeline" / "transcript_metadata_core" / "canonical_enterprise_directory.json",
        root / "config" / "runtime" / "speaker_memory.json",
    ]
    for cp in critical_json_paths:
        if not cp.exists():
            continue
        try:
            _data, dupes = load_json_with_dup_check(cp)
            for d in dupes:
                violations.append({
                    "rca_id": "RCA_01",
                    "target": str(cp.relative_to(root)),
                    "issue": "DUPLICATE_JSON_KEY",
                    "detail": f"Duplicate key '{d}' detected in {cp.name}",
                })
        except Exception as exc:
            violations.append({
                "rca_id": "RCA_01",
                "target": str(cp.relative_to(root)),
                "issue": "INVALID_JSON",
                "detail": f"Failed parsing JSON: {exc}",
            })
    return violations


def _check_rca_02_shadow_model_registry(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    shadow_files = [
        root / "config" / "runtime" / "model_registry.yaml",
        root / "dual_pipeline" / "config" / "runtime" / "model_registry.json",
    ]
    for sf in shadow_files:
        if sf.exists():
            violations.append({
                "rca_id": "RCA_02",
                "target": str(sf.relative_to(root)),
                "issue": "SHADOW_MODEL_REGISTRY",
                "detail": f"Shadow model registry file must not exist: {sf.name}",
            })
    return violations


def _check_rca_03_hitl_margin(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    param_path = root / "config" / "runtime" / "parameters.json"
    if not param_path.exists():
        return violations
    try:
        pdata, _ = load_json_with_dup_check(param_path)
        hitl_margin = pdata.get("parameters", {}).get("hitl", {}).get("ambiguity_margin_threshold")
        if hitl_margin != 0.2:
            violations.append({
                "rca_id": "RCA_03",
                "target": "config/runtime/parameters.json",
                "issue": "HITL_MARGIN_DRIFT",
                "detail": f"parameters.json hitl.ambiguity_margin_threshold is {hitl_margin}, expected 0.20",
            })
    except Exception as exc:
        violations.append({
            "rca_id": "RCA_03",
            "target": "config/runtime/parameters.json",
            "issue": "PARAM_READ_ERROR",
            "detail": str(exc),
        })
    return violations


def _check_rca_04_wal_timeout(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    param_path = root / "config" / "runtime" / "parameters.json"
    if not param_path.exists():
        return violations
    try:
        pdata, _ = load_json_with_dup_check(param_path)
        wal_timeout = pdata.get("parameters", {}).get("storage", {}).get("sqlite_wal_busy_timeout_ms")
        if wal_timeout != 30000:
            violations.append({
                "rca_id": "RCA_04",
                "target": "config/runtime/parameters.json",
                "issue": "STORAGE_WAL_TIMEOUT_DRIFT",
                "detail": f"parameters.json storage.sqlite_wal_busy_timeout_ms is {wal_timeout}, expected 30000",
            })
    except Exception as exc:
        violations.append({
            "rca_id": "RCA_04",
            "target": "config/runtime/parameters.json",
            "issue": "PARAM_READ_ERROR",
            "detail": str(exc),
        })
    return violations


def _check_rca_05_parameter_defaults(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    params_py = root / "dual_pipeline" / "parameters.py"
    if params_py.exists():
        content = read_text_safe(params_py)
        if re.search(r"min_provenance_score\s*(?::\s*float)?\s*=\s*0\.3\b", content):
            violations.append({
                "rca_id": "RCA_05",
                "target": "dual_pipeline/parameters.py",
                "issue": "PARAMETER_DEFAULT_MISMATCH",
                "detail": "dual_pipeline/parameters.py has min_provenance_score default of 0.3, expected 0.15 matching parameters.json",
            })
    rt_py = root / "dual_pipeline" / "release_thresholds.py"
    if rt_py.exists():
        content = read_text_safe(rt_py)
        if re.search(r"output1_pass_threshold\s*(?::\s*int)?\s*=\s*95\b", content):
            violations.append({
                "rca_id": "RCA_05",
                "target": "dual_pipeline/release_thresholds.py",
                "issue": "RELEASE_THRESHOLD_DEFAULT_MISMATCH",
                "detail": "dual_pipeline/release_thresholds.py has output1_pass_threshold default of 95, expected 90 matching parameters.json",
            })
    return violations


def _check_rca_06_project_taxonomy(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    proj_reg = root / "config" / "governance" / "project_registry.json"
    if not proj_reg.exists():
        return violations
    try:
        preg_data, _ = load_json_with_dup_check(proj_reg)
        cats = preg_data.get("domains") or preg_data.get("categories", {})
        required_cats = {"NEA", "EDO", "APEX_TRANS", "NEE_IT", "ENTERPRISE_OPS", "INTERNAL_CONSULTING", "MARKET_INTELLIGENCE"}
        missing_cats = required_cats - set(cats.keys())
        if missing_cats:
            violations.append({
                "rca_id": "RCA_06",
                "target": "config/governance/project_registry.json",
                "issue": "MISSING_CATEGORIES",
                "detail": f"Missing canonical categories/domains: {sorted(missing_cats)}",
            })
        all_workstream_ids = set()
        for dom in cats.values():
            for ws_id, ws_info in dom.get("workstreams", {}).items():
                all_workstream_ids.add(ws_id)
                all_workstream_ids.add(ws_info.get("short_id", ""))
        aliases = preg_data.get("aliases", {})
        if "dociq" not in all_workstream_ids and "dociq" not in aliases.values():
            violations.append({
                "rca_id": "RCA_06",
                "target": "config/governance/project_registry.json",
                "issue": "MISSING_DOCIQ_PROJECT",
                "detail": "Workstream 'dociq' missing from registered workstreams or aliases",
            })
    except Exception as exc:
        violations.append({
            "rca_id": "RCA_06",
            "target": "config/governance/project_registry.json",
            "issue": "READ_ERROR",
            "detail": str(exc),
        })
    return violations


def _check_rca_07_machine_paths(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    runtime_cfg = root / "config" / "runtime"
    if not runtime_cfg.is_dir():
        return violations
    for conf_file in runtime_cfg.glob("*.json"):
        try:
            text = read_text_safe(conf_file)
            if re.search(r"/Users/[a-zA-Z0-9_-]+/", text):
                violations.append({
                    "rca_id": "RCA_07",
                    "target": str(conf_file.relative_to(root)),
                    "issue": "MACHINE_ABSOLUTE_PATH",
                    "detail": f"Machine absolute path detected in {conf_file.name}",
                })
        except Exception:
            pass
    return violations


def _check_rca_08_gate_count(root: Path) -> List[Dict[str, str]]:
    violations: List[Dict[str, str]] = []
    readiness_py = root / "tools" / "readiness_core.py"
    manifest_json = root / "config" / "governance" / "gate_manifest.json"
    if not readiness_py.exists():
        return violations
    try:
        rcontent = read_text_safe(readiness_py)
        if "Gate 32: Direct Graph Write Path Linter" not in rcontent:
            violations.append({
                "rca_id": "RCA_08",
                "target": "tools/readiness_core.py",
                "issue": "GATE_COUNT_MISMATCH",
                "detail": "readiness_core.py does not define Gate 32: Direct Graph Write Path Linter",
            })
        if manifest_json.exists():
            mdata, _ = load_json_with_dup_check(manifest_json)
            total_reg = mdata.get("total_registered_stages")
            m_stages = mdata.get("stages", [])
            if total_reg != 34 or len(m_stages) != 34:
                violations.append({
                    "rca_id": "RCA_08",
                    "target": "config/governance/gate_manifest.json",
                    "issue": "GATE_MANIFEST_COUNT_MISMATCH",
                    "detail": f"gate_manifest.json total_registered_stages={total_reg} (stages={len(m_stages)}), expected 34",
                })
    except Exception as exc:
        violations.append({
            "rca_id": "RCA_08",
            "target": "tools/readiness_core.py",
            "issue": "READ_ERROR",
            "detail": str(exc),
        })
    return violations


def check_rca_ssot_violations(repo_root: Optional[Path] = None) -> List[Dict[str, str]]:
    """Execute dynamic validation of RCA_01 through RCA_08 SSOT checks."""
    root = repo_root or Path(__file__).resolve().parents[2]
    violations: List[Dict[str, str]] = []
    for check_fn in (
        _check_rca_01_duplicate_keys,
        _check_rca_02_shadow_model_registry,
        _check_rca_03_hitl_margin,
        _check_rca_04_wal_timeout,
        _check_rca_05_parameter_defaults,
        _check_rca_06_project_taxonomy,
        _check_rca_07_machine_paths,
        _check_rca_08_gate_count,
    ):
        violations.extend(check_fn(root))
    return violations
