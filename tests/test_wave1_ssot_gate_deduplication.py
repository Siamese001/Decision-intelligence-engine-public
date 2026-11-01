"""Wave 1 tests: Deduplicate SSOT, Parameter, Contract, and Harmonization Scans."""
from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
from unittest.mock import patch

from tools.audit_governance_harmonization_core.common import (
    REPO_ROOT,
    clear_harmonization_caches,
)
from tools.lint_parameter_references import ParameterReferenceVisitor, load_declared_parameter_keys
from tools.scan_ssot_configuration_violations import (
    check_governance_harmonization_parity,
    check_model_registry_approved_models_parity,
    check_parameter_file_parity,
    check_rca_ssot_violations,
)
from tools.ssot_config_engine import DECLARED_PARAMETER_KEYS, UnifiedSSOTASTVisitor


def test_stage25_parses_parameters_once_and_reads_governance_files_once() -> None:
    """Verify Stage 25 parses parameters.json once and reads each governance file at most once."""
    clear_harmonization_caches()
    read_counts: Counter[Path] = Counter()
    orig_read_text = Path.read_text
    param_resolved = (REPO_ROOT / "config" / "runtime" / "parameters.json").resolve()
    param_json_loads_count = 0
    orig_json_loads = json.loads

    def _spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        read_counts[self.resolve()] += 1
        return orig_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def _spy_json_loads(s: str | bytes, *args: object, **kwargs: object) -> object:
        nonlocal param_json_loads_count
        if isinstance(s, str) and '"output1_production_pass_threshold"' in s and '"ambiguity_margin_threshold"' in s:
            param_json_loads_count += 1
        return orig_json_loads(s, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch.object(Path, "read_text", autospec=True, side_effect=_spy_read_text),
        patch("json.loads", side_effect=_spy_json_loads),
    ):
        assert check_parameter_file_parity() == []
        assert check_model_registry_approved_models_parity() == []
        assert check_rca_ssot_violations() == []
        assert check_governance_harmonization_parity() == []

    assert param_json_loads_count == 1
    assert read_counts[param_resolved] == 1
    agents_md = (REPO_ROOT / "AGENTS.md").resolve()
    assert read_counts[agents_md] == 1


def test_stage25_harmonization_scopes_unrelated_files_and_skips_duplicate_pairwise_rules() -> None:
    """Verify Stage 25 skips full governance walk on non-governance diffs and omits duplicate Stage 24 scan."""
    with patch("tools.audit_rules_budget.scan_rule_pairwise_duplicates") as mock_pairwise:
        assert check_governance_harmonization_parity(files=["dual_pipeline/core.py"]) == []
        assert check_governance_harmonization_parity() == []
        assert mock_pairwise.call_count == 0


def test_unified_ssot_visitor_enforces_undeclared_parameter_keys() -> None:
    """Verify UnifiedSSOTASTVisitor and ParameterReferenceVisitor share undeclared key detection."""
    declared = load_declared_parameter_keys()
    assert len(declared) > 20
    assert declared == DECLARED_PARAMETER_KEYS

    sample_code = (
        'from dual_pipeline.parameters import get_float_parameter\n'
        'val_ok = get_float_parameter("hitl.ambiguity_margin_threshold")\n'
        'val_bad = get_float_parameter("nonexistent.bogus_parameter_key")\n'
    )
    tree = ast.parse(sample_code)
    visitor = UnifiedSSOTASTVisitor(
        rel_path="dual_pipeline/sample_module.py",
        lines=sample_code.splitlines(),
        declared_keys=declared,
        check_undeclared_keys=True,
    )
    visitor.visit(tree)
    rules = [v.rule for v in visitor.violations]
    assert "UNDECLARED_PARAMETER_KEY" in rules
    assert "hitl.ambiguity_margin_threshold" in visitor.referenced_keys

    ref_visitor = ParameterReferenceVisitor("dual_pipeline/sample_module.py", declared)
    ref_visitor.visit(tree)
    assert len(ref_visitor.violations) == 1
    assert ref_visitor.violations[0].rule == "UNDECLARED_PARAMETER_KEY"
