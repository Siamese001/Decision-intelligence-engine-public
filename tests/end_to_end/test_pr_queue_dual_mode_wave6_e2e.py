"""Wave 6 installation preparation is hash-bound and rollback-ready."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.lint_prompt_ssot import compute_sha256


ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "docs/plans/pr_queue_dual_mode_refactor/installation"


def _sha(path: Path) -> str:
    return compute_sha256(path.read_bytes())


def test_rollback_package_binds_old_and_new_repository_artifacts() -> None:
    package = json.loads((INSTALL / "rollback_package.json").read_text(encoding="utf-8"))
    old = INSTALL / "pr_queue_observer_prompt.repository_baseline.md"
    new = ROOT / "automations/prompts/pr_queue_observer_agent.md"
    runner = ROOT / "tools/pr_queue_observer.py"
    assert package["status"] == "PREPARED_NOT_INSTALLED"
    assert package["repository_baseline_prompt"]["sha256"] == _sha(old)
    assert len(_sha(new)) == 64
    assert package["runner"]["sha256"] == _sha(runner)
    assert package["preserved_schedule"]["declared_cron"] == "0 9 * * 1-5"
    assert package["preserved_schedule"]["declared_timezone"] == "America/New_York"
    assert package["state_migration"]["automatic_migration"] is False


def test_readback_template_keeps_unknown_host_registration_fields_empty() -> None:
    readback = json.loads((INSTALL / "host_readback.json").read_text(encoding="utf-8"))
    assert readback["status"] == "UNVERIFIED"
    assert readback["registration_id"] is None
    assert readback["installed_body_sha256"] is None
    assert readback["working_directory"] is None
    assert "Antigravity host readback" in readback["note"]
