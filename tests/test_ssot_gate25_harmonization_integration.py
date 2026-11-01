"""Wave 4 tests: Cross-surface threshold drift scanners and Gate 25 integration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools.audit_governance_harmonization_core import (
    HarmonizationReport,
    THRESHOLD_REGISTRY,
    scan_threshold_drift,
)
from tools.scan_ssot_configuration_violations import (
    check_governance_harmonization_parity,
    check_parameter_file_parity,
)


def test_expanded_threshold_registry_entries_present() -> None:
    """Verify all Wave 4 quantitative invariants are registered in THRESHOLD_REGISTRY."""
    registered_names = {entry[0] for entry in THRESHOLD_REGISTRY}
    required_names = {
        "INV-AGENTS-MD-MAX-SIZE",
        "INV-SSOT-LINK-02",
        "INV-PR-QUEUE-MAX-WORDS",
        "INV-HITL-MIN-CONFIDENCE-FLOOR",
        "INV-HITL-ENTROPY-CEILING",
        "INV-HITL-UNRESOLVED-ENTITY-MARGIN",
        "INV-COMPLEXITY-MAX",
        "INV-GRAPH-MUTATION-PHYSICAL-PROOF",
        "Release Production Pass Threshold",
    }
    assert required_names.issubset(registered_names)


def test_threshold_drift_scanner_catches_new_invariants(tmp_path: Path) -> None:
    """Verify scan_threshold_drift catches drifted values across the new Wave 4 invariants."""
    drifted_rule = tmp_path / "drifted_rule.md"
    drifted_rule.write_text(
        "\n".join(
            [
                "| `INV-AGENTS-MD-MAX-SIZE` | `16384` bytes |",
                "| `INV-SSOT-LINK-02` | `<= 60` words |",
                "| `INV-PR-QUEUE-MAX-WORDS` | `1500` words |",
                "The autonomous confidence floor is 70%.",
                "The entropy escalation ceiling is 0.85.",
                "The unresolved entity margin threshold is 25%.",
                "Max cognitive complexity: 20.",
                "Requires 3 mandatory physical checks.",
                "output1_production_pass_threshold = 95",
            ]
        ),
        encoding="utf-8",
    )
    report = HarmonizationReport(
        scan_timestamp=datetime.now(timezone.utc).isoformat(),
        repo_root=str(tmp_path),
        total_files_scanned=1,
    )
    scan_threshold_drift([drifted_rule], report)
    titles = {f.title for f in report.findings}
    assert "Threshold Drift: INV-AGENTS-MD-MAX-SIZE" in titles
    assert "Threshold Drift: INV-SSOT-LINK-02" in titles
    assert "Threshold Drift: INV-PR-QUEUE-MAX-WORDS" in titles
    assert "Threshold Drift: INV-HITL-MIN-CONFIDENCE-FLOOR" in titles
    assert "Threshold Drift: INV-HITL-ENTROPY-CEILING" in titles
    assert "Threshold Drift: INV-HITL-UNRESOLVED-ENTITY-MARGIN" in titles
    assert "Threshold Drift: INV-COMPLEXITY-MAX" in titles
    assert "Threshold Drift: INV-GRAPH-MUTATION-PHYSICAL-PROOF" in titles
    assert "Threshold Drift: Release Production Pass Threshold" in titles


def test_gate25_harmonization_parity_passes_clean() -> None:
    """Verify Gate 25 check_governance_harmonization_parity and check_parameter_file_parity pass with 0 violations."""
    assert check_parameter_file_parity() == []
    assert check_governance_harmonization_parity() == []
