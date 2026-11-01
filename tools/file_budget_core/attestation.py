"""
Machine verification of file budget exemption attestations against AST reality.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from .mmi import calculate_ast_metrics

DEFAULT_MAX_EXTERNAL_IMPORTS = 12
DEFAULT_MAX_COGNITIVE_COMPLEXITY = 15
ATTESTATION_TOLERANCE = 2


def verify_attestations_against_ast(
    file_path: Path,
    classification: str,
    class_spec: Dict[str, Any],
    attestations: Dict[str, Any],
) -> Tuple[bool, str]:
    """Machine-verify declared exemption attestations against static AST analysis."""
    metrics = calculate_ast_metrics(file_path)

    # 1. Hard ceiling lines check
    ceiling_lines = class_spec.get("ceiling_lines", 900)
    if metrics.lines > ceiling_lines:
        return False, (
            f"Line count {metrics.lines} exceeds class ceiling of {ceiling_lines} lines "
            f"for classification '{classification}'."
        )

    # 2. Check required attestations
    req_keys = class_spec.get("required_attestations", [])
    for k in req_keys:
        if k not in attestations:
            return False, f"Missing mandatory attestation key: '{k}'."

    # 3. Branch logic verification
    if "zero_branch_logic" in req_keys or "zero_branch_logic" in attestations:
        if attestations.get("zero_branch_logic") is True and metrics.has_branch_logic:
            return False, "Attestation 'zero_branch_logic' is True, but branch logic (If/For/While/Match/Except) was detected in AST."

    # 4. Single responsibility rationale
    if "single_responsibility_rationale" in req_keys:
        rationale = str(attestations.get("single_responsibility_rationale", "")).strip()
        if len(rationale) < 10:
            return False, "Attestation 'single_responsibility_rationale' requires non-empty text (minimum 10 characters)."

    # 5. Cognitive complexity verification
    if "cognitive_complexity_ceiling_met" in req_keys:
        if attestations.get("cognitive_complexity_ceiling_met") is True:
            if "max_function_cc" in attestations:
                attested_cc = int(attestations["max_function_cc"])
                if metrics.max_cc > attested_cc + ATTESTATION_TOLERANCE:
                    return False, (
                        f"Stale attestation: actual max cognitive complexity ({metrics.max_cc}) "
                        f"exceeds attested value ({attested_cc} + {ATTESTATION_TOLERANCE} tolerance)."
                    )
            else:
                if metrics.max_cc > DEFAULT_MAX_COGNITIVE_COMPLEXITY:
                    return False, (
                        f"Actual max cognitive complexity ({metrics.max_cc}) exceeds standard ceiling "
                        f"({DEFAULT_MAX_COGNITIVE_COMPLEXITY}). Provide explicit 'max_function_cc' attestation."
                    )

    # 6. Fan-out (external imports) verification
    if "fan_out_ceiling_met" in req_keys:
        if attestations.get("fan_out_ceiling_met") is True:
            if metrics.unique_external_imports > DEFAULT_MAX_EXTERNAL_IMPORTS:
                return False, (
                    f"Fan-out ceiling violated: {metrics.unique_external_imports} external imports "
                    f"exceeds ceiling of {DEFAULT_MAX_EXTERNAL_IMPORTS} ({metrics.external_import_names})."
                )
            if "unique_external_imports" in attestations:
                attested_ext = int(attestations["unique_external_imports"])
                if metrics.unique_external_imports > attested_ext + ATTESTATION_TOLERANCE:
                    return False, (
                        f"Stale attestation: actual external imports ({metrics.unique_external_imports}) "
                        f"exceeds attested value ({attested_ext} + {ATTESTATION_TOLERANCE} tolerance)."
                    )

    return True, "Valid"
