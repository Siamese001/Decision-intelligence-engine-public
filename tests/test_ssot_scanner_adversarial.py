"""
tests/test_ssot_scanner_adversarial.py

Adversarial and edge-case testing for multi-surface SSOT drift scanners,
covering boundary payloads, malformed inputs, and invariant bypass attempts.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.scan_ssot_configuration_violations import (
    check_parameter_file_parity,
    check_model_registry_approved_models_parity,
    check_rca_ssot_violations,
    build_configuration_inventory,
)


def _setup_isolated_fixture_repo(tmp_path: Path) -> Path:
    """Creates a minimal valid fixture repository mirroring canonical files."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    # 1. config/runtime/parameters.json
    runtime_dir = repo / "config" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    param_payload = {
        "schema_version": "1.0.0",
        "parameters": {
            "release_thresholds": {
                "output1_production_pass_threshold": 90,
                "output2_production_pass_threshold": 90,
                "output3_production_pass_threshold": 90,
                "advisory_score_floor": 80,
            },
            "output1": {},
            "output2": {
                "word_limits": {},
                "section_density_floors": {},
                "c21_speaker_tier_floors": {},
                "passive_hedging_threshold": 0.15,
            },
            "output3": {},
            "judge": {},
            "copilot": {
                "rigor": {"min_provenance_score": 0.15}
            },
            "audio_ingestion": {},
            "grounding": {},
            "hitl": {"ambiguity_margin_threshold": 0.2},
            "storage": {"sqlite_wal_busy_timeout_ms": 30000},
        }
    }
    (runtime_dir / "parameters.json").write_text(json.dumps(param_payload, indent=2), encoding="utf-8")

    # 2. config/runtime/model_registry.json
    model_payload = {
        "schema_version": "1.0.0",
        "approved_models": {
            "openai": ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
            "google": ["gemini-3.5-flash-lite", "gemini-3.8-flash"],
            "local": ["bge-m3"],
        },
        "model_tiers": {
            "FRONTIER_HIGH_REASONING": {
                "default_model": "gpt-6.1-sol",
                "provider": "openai",
                "allowed_fallbacks": ["gpt-6.1-sol", "gpt-5.6-sol"],
            }
        }
    }
    (runtime_dir / "model_registry.json").write_text(json.dumps(model_payload, indent=2), encoding="utf-8")

    # 3. config/governance/project_registry.json
    gov_dir = repo / "config" / "governance"
    gov_dir.mkdir(parents=True, exist_ok=True)
    proj_payload = {
        "schema_version": "1.0.0",
        "domains": {
            "NEA": {"workstreams": {"nea_dociq": {"short_id": "dociq"}}},
            "EDO": {"workstreams": {}},
            "APEX_TRANS": {"workstreams": {}},
            "NEE_IT": {"workstreams": {}},
            "ENTERPRISE_OPS": {"workstreams": {}},
            "INTERNAL_CONSULTING": {"workstreams": {}},
            "MARKET_INTELLIGENCE": {"workstreams": {}},
        },
        "aliases": {"dociq": "dociq"},
    }
    (gov_dir / "project_registry.json").write_text(json.dumps(proj_payload, indent=2), encoding="utf-8")

    # 4. dual_pipeline/parameters.py and release_thresholds.py
    dp_dir = repo / "dual_pipeline"
    dp_dir.mkdir(parents=True, exist_ok=True)
    (dp_dir / "parameters.py").write_text(
        'def get_copilot_rigor_min_provenance_score() -> float:\n    return get_float_parameter("copilot.rigor.min_provenance_score", 0.15)\n',
        encoding="utf-8"
    )
    (dp_dir / "release_thresholds.py").write_text(
        'OUTPUT1_PRODUCTION_PASS_THRESHOLD = 90\noutput1_pass_threshold = 90\n',
        encoding="utf-8"
    )

    # 5. tools/readiness_core.py
    tools_dir = repo / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "readiness_core.py").write_text(
        'stages = [\n    ("Gate 32: Direct Graph Write Path Linter", [])\n]\n',
        encoding="utf-8"
    )

    return repo


def test_adversarial_category_1_boundary_empty_and_zero_byte_files(tmp_path: Path):
    """Category 1: Boundary payload - 0-byte and missing configuration files fail closed."""
    repo = _setup_isolated_fixture_repo(tmp_path)
    param_file = repo / "config" / "runtime" / "parameters.json"

    # Truncate to 0-byte file
    param_file.write_text("", encoding="utf-8")
    violations = check_parameter_file_parity(repo)
    assert any("Failed parsing" in v for v in violations), f"Expected parse failure on 0-byte file: {violations}"

    # Remove file completely
    param_file.unlink()
    violations = check_parameter_file_parity(repo)
    assert any("Missing parameter file" in v for v in violations), f"Expected missing file violation: {violations}"


def test_adversarial_category_2_malformed_and_duplicate_json_keys(tmp_path: Path):
    """Category 2: Malformed input - duplicate keys in project_registry.json are caught by RCA_01."""
    repo = _setup_isolated_fixture_repo(tmp_path)
    proj_reg = repo / "config" / "governance" / "project_registry.json"

    # Inject duplicate JSON key
    duplicate_key_json = '{\n  "domains": {},\n  "aliases": {},\n  "duplicate_token": "a",\n  "duplicate_token": "b"\n}\n'
    proj_reg.write_text(duplicate_key_json, encoding="utf-8")

    violations = check_rca_ssot_violations(repo)
    rca_01_errs = [v for v in violations if v["rca_id"] == "RCA_01"]
    assert len(rca_01_errs) > 0, f"Expected RCA_01 duplicate key detection, got: {violations}"
    assert any("duplicate_token" in v["detail"] for v in rca_01_errs)


def test_adversarial_category_3_shadow_model_registry_bypass_attempt(tmp_path: Path):
    """Category 3: Invariant bypass attempt - shadow model_registry files trigger RCA_02."""
    repo = _setup_isolated_fixture_repo(tmp_path)

    # Inject shadow YAML model registry
    shadow_yaml = repo / "config" / "runtime" / "model_registry.yaml"
    shadow_yaml.write_text("model_tiers: {}\n", encoding="utf-8")

    # Inject shadow JSON model registry in dual_pipeline
    shadow_json_dir = repo / "dual_pipeline" / "config" / "runtime"
    shadow_json_dir.mkdir(parents=True, exist_ok=True)
    shadow_json = shadow_json_dir / "model_registry.json"
    shadow_json.write_text('{"shadow": true}\n', encoding="utf-8")

    violations = check_rca_ssot_violations(repo)
    rca_02_errs = [v for v in violations if v["rca_id"] == "RCA_02"]
    assert len(rca_02_errs) == 2, f"Expected exactly 2 shadow file violations, got: {rca_02_errs}"
    shadow_targets = [v["target"] for v in rca_02_errs]
    assert any("model_registry.yaml" in t for t in shadow_targets)
    assert any("dual_pipeline" in t for t in shadow_targets)


def test_adversarial_category_4_threshold_and_margin_drift_detection(tmp_path: Path):
    """Category 4: Invariant bypass attempt - drifted HITL margin and WAL timeout trigger RCA_03 and RCA_04."""
    repo = _setup_isolated_fixture_repo(tmp_path)
    param_file = repo / "config" / "runtime" / "parameters.json"
    data = json.loads(param_file.read_text(encoding="utf-8"))

    # Drift HITL ambiguity margin to 0.25 (vs 0.20)
    data["parameters"]["hitl"]["ambiguity_margin_threshold"] = 0.25
    # Drift SQLite WAL busy timeout to 5000 (vs 30000)
    data["parameters"]["storage"]["sqlite_wal_busy_timeout_ms"] = 5000
    param_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    violations = check_rca_ssot_violations(repo)
    rca_ids = {v["rca_id"] for v in violations}
    assert "RCA_03" in rca_ids, f"Expected RCA_03 (HITL margin drift), got: {violations}"
    assert "RCA_04" in rca_ids, f"Expected RCA_04 (WAL timeout drift), got: {violations}"


def test_adversarial_category_5_parameter_default_mismatch_detection(tmp_path: Path):
    """Category 5: Invariant bypass attempt - legacy default values trigger RCA_05."""
    repo = _setup_isolated_fixture_repo(tmp_path)

    # Inject legacy min_provenance_score default (0.3)
    (repo / "dual_pipeline" / "parameters.py").write_text(
        'def get_copilot_rigor_min_provenance_score() -> float:\n    min_provenance_score = 0.3\n',
        encoding="utf-8"
    )
    # Inject legacy output1_pass_threshold default (95)
    (repo / "dual_pipeline" / "release_thresholds.py").write_text(
        'output1_pass_threshold: int = 95\n',
        encoding="utf-8"
    )

    violations = check_rca_ssot_violations(repo)
    rca_05_errs = [v for v in violations if v["rca_id"] == "RCA_05"]
    assert len(rca_05_errs) == 2, f"Expected 2 RCA_05 default mismatch errors, got: {rca_05_errs}"


def test_adversarial_category_6_taxonomy_category_omission(tmp_path: Path):
    """Category 6: Invariant bypass attempt - missing domains trigger RCA_06."""
    repo = _setup_isolated_fixture_repo(tmp_path)
    proj_reg = repo / "config" / "governance" / "project_registry.json"

    # Corrupt domain taxonomy by removing APEX_TRANS and EDO
    corrupted_data = {
        "domains": {
            "NEA": {"workstreams": {}},
        },
        "aliases": {},
    }
    proj_reg.write_text(json.dumps(corrupted_data, indent=2), encoding="utf-8")

    violations = check_rca_ssot_violations(repo)
    rca_06_errs = [v for v in violations if v["rca_id"] == "RCA_06"]
    assert len(rca_06_errs) >= 1, f"Expected RCA_06 category omission error, got: {violations}"
    assert any("EDO" in v["detail"] for v in rca_06_errs)


def test_adversarial_category_7_machine_absolute_path_injection(tmp_path: Path):
    """Category 7: Invariant bypass attempt - machine absolute paths trigger RCA_07."""
    repo = _setup_isolated_fixture_repo(tmp_path)
    cfg_file = repo / "config" / "runtime" / "leaked_config.json"
    cfg_file.write_text('{\n  "path": "/Users/someone/workspace/secret"\n}\n', encoding="utf-8")

    violations = check_rca_ssot_violations(repo)
    rca_07_errs = [v for v in violations if v["rca_id"] == "RCA_07"]
    assert len(rca_07_errs) == 1, f"Expected RCA_07 machine absolute path error, got: {violations}"
    assert "leaked_config.json" in rca_07_errs[0]["target"]
