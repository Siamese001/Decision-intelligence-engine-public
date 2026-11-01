"""
tests/test_configuration_contracts.py

Strict configuration contract and schema validation test suite.
Validates:
1. `config/runtime/parameters.json`: Type constraints, boundary limits, and release policy consistency.
2. `config/runtime/model_registry.json`: Completeness of ModelTier mapping and fallback model integrity.
3. `config/governance/project_registry.json`: Governed project schemas and organization boundaries.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS_JSON = ROOT / "config" / "runtime" / "parameters.json"
MODEL_REGISTRY_JSON = ROOT / "config" / "runtime" / "model_registry.json"
PROJECT_REGISTRY_JSON = ROOT / "config" / "governance" / "project_registry.json"


def test_parameters_json_contract():
    """Verify parameters.json adheres to schema and valid operational boundaries."""
    assert PARAMETERS_JSON.exists(), "Missing config/runtime/parameters.json"
    data = json.loads(PARAMETERS_JSON.read_text(encoding="utf-8"))

    assert data.get("schema_version") == "1.0.0"
    assert "parameters" in data
    params = data["parameters"]

    # Storage
    assert "storage" in params
    assert isinstance(params["storage"]["sqlite_wal_busy_timeout_ms"], int)
    assert params["storage"]["sqlite_wal_busy_timeout_ms"] >= 1000

    # Timeouts
    assert "timeouts" in params
    timeouts = params["timeouts"]
    assert timeouts["llm_sync_seconds"] > 0
    assert timeouts["audio_ingest_seconds"] > 0
    assert timeouts["retrieval_steady_state_latency_ms"] <= 7500.0
    assert timeouts["database_busy_timeout_seconds"] > 0

    # HITL
    assert "hitl" in params
    assert params["hitl"]["ambiguity_margin_threshold"] in (0.15, 0.20, 0.30)

    # Release Thresholds
    assert "release_thresholds" in params
    rt = params["release_thresholds"]
    assert rt["output1_production_pass_threshold"] == 90
    assert rt["output2_production_pass_threshold"] == 90
    assert rt["output3_production_pass_threshold"] == 90
    assert rt["advisory_score_floor"] == 80


def test_model_registry_json_contract():
    """Verify model_registry.json contains all ModelTiers, approved_models, and valid fallbacks."""
    assert MODEL_REGISTRY_JSON.exists(), "Missing config/runtime/model_registry.json"
    data = json.loads(MODEL_REGISTRY_JSON.read_text(encoding="utf-8"))

    assert data.get("schema_version") == "1.0.0"
    assert "approved_models" in data
    assert "openai" in data["approved_models"]

    from config.credentials import GOOGLE_APPROVED_MODELS, OPENAI_APPROVED_MODELS
    assert set(data["approved_models"]["openai"]) == OPENAI_APPROVED_MODELS
    assert set(data["approved_models"]["google"]) == GOOGLE_APPROVED_MODELS

    assert "model_tiers" in data
    tiers = data["model_tiers"]
    assert len(tiers) > 0

    for tier_name, tier_def in tiers.items():
        assert "default_model" in tier_def
        assert "provider" in tier_def
        if tier_def.get("provider") == "openai":
            assert tier_def["default_model"] in OPENAI_APPROVED_MODELS, (
                f"Tier {tier_name} default_model '{tier_def['default_model']}' not in OPENAI_APPROVED_MODELS"
            )
            for fb in tier_def.get("allowed_fallbacks", []):
                assert fb in OPENAI_APPROVED_MODELS, (
                    f"Tier {tier_name} fallback '{fb}' not in OPENAI_APPROVED_MODELS"
                )
        elif tier_def.get("provider") == "google":
            assert tier_def["default_model"] in GOOGLE_APPROVED_MODELS, (
                f"Tier {tier_name} default_model '{tier_def['default_model']}' not in GOOGLE_APPROVED_MODELS"
            )
            for fb in tier_def.get("allowed_fallbacks", []):
                assert fb in GOOGLE_APPROVED_MODELS, (
                    f"Tier {tier_name} fallback '{fb}' not in GOOGLE_APPROVED_MODELS"
                )


def test_project_registry_json_contract():
    """Verify project_registry.json has valid project definitions and aliases."""
    assert PROJECT_REGISTRY_JSON.exists(), "Missing config/governance/project_registry.json"
    data = json.loads(PROJECT_REGISTRY_JSON.read_text(encoding="utf-8"))

    assert data.get("schema_version") == "1.0.0"
    assert "known_projects" in data
    projects = data["known_projects"]
    assert isinstance(projects, list)
    assert len(projects) > 0

    for proj in projects:
        assert isinstance(proj, str)
        assert len(proj.strip()) > 0

    # Project Aliases
    assert "project_aliases" in data
    aliases = data["project_aliases"]
    assert isinstance(aliases, dict)
    assert "lens" in aliases
    assert aliases["lens"] == "project_lens"

    from dual_pipeline.parameters import get_project_aliases, resolve_project_name
    assert get_project_aliases()["lens"] == "project_lens"
    assert resolve_project_name("lens") == "project_lens"
    assert resolve_project_name("Project Lens") == "project_lens"
    assert resolve_project_name("nexus") == "project_nexus"
    assert resolve_project_name("sherpa") == "project_beacon"
