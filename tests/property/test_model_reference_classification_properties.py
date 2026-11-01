"""
Hypothesis Property-Based Verification for Model Reference Classification.

Verifies:
1. Case, whitespace, and delimiter variations of retired models are deterministically flagged.
2. Forged inline bypass comments across arbitrary text strings never bypass the linter without manifest authorization.
3. Tampered exception manifest hashes fail closed with INTEGRITY_TAMPERED.
4. Modern ratified frontier models never trigger false positives under arbitrary valid contexts.
5. Scoped exception authorization: allowed model A does not permit unauthorized model B.
"""
from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lint_retired_models import (
    RETIRED_MODELS,
    scan_file_for_retired_models,
)

RATIFIED_MODERN_MODELS = [
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
    "gpt-5.6-luna",
    "gpt-6.1-sol",
    "gpt-6-sol",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]


@st.composite
def model_variation_strategy(draw: st.DrawFn) -> tuple[str, str]:
    """Generate case, delimiter, and whitespace variations of a retired model."""
    base_model = draw(st.sampled_from(list(RETIRED_MODELS.keys())))
    parts = re.split(r"[-_]", base_model)
    delimiters = [draw(st.sampled_from(["-", "_", " "])) for _ in range(len(parts) - 1)]

    reconstructed: list[str] = []
    for i, p in enumerate(parts):
        casing = draw(st.sampled_from(["lower", "upper", "title"]))
        if casing == "upper":
            p = p.upper()
        elif casing == "title":
            p = p.title()
        reconstructed.append(p)
        if i < len(delimiters):
            reconstructed.append(delimiters[i])

    variant = "".join(reconstructed)
    return base_model, variant


@settings(max_examples=50, deadline=None)
@given(model_variation_strategy())
def test_property_all_retired_model_variations_flagged(model_data: tuple[str, str]) -> None:
    """Property: Any case/delimiter variation of a retired model is caught."""
    base_model, variant = model_data
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write(f'DISPATCH_TARGET = "{variant}"\n')
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) >= 1, f"Failed to detect variant {variant!r} of {base_model}"
    finally:
        tmp_path.unlink(missing_ok=True)


@settings(max_examples=30, deadline=None)
@given(
    model_data=model_variation_strategy(),
    bypass_comment=st.sampled_from([
        "# allow-retired-model",
        "# allow-banned-model",
        "# noqa: RETIRED_MODEL",
        "# noqa: BANNED_MODEL",
    ]),
)
def test_property_forged_bypass_never_suppresses_violation(
    model_data: tuple[str, str],
    bypass_comment: str,
) -> None:
    """Property: Forged bypass comments in unexempt files are caught and never suppress violations."""
    base_model, variant = model_data
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write(f'MODEL = "{variant}"  {bypass_comment}\n')
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        violation_types = [v.matched_model for v in violations]
        assert "FORGED_INLINE_BYPASS" in violation_types
    finally:
        tmp_path.unlink(missing_ok=True)


@settings(max_examples=30, deadline=None)
@given(
    retired_model=st.sampled_from(list(RETIRED_MODELS.keys())),
    tampered_hash=st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
)
def test_property_manifest_hash_tamper_fails_closed(retired_model: str, tampered_hash: str) -> None:
    """Property: Any hash divergence in the exception manifest raises INTEGRITY_TAMPERED."""
    content = f'MODEL = "{retired_model}"\n'
    actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if tampered_hash == actual_sha:
        tampered_hash = "0" * 64

    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        tampered_exceptions = {
            tmp_path.name: {
                "path": tmp_path.name,
                "sha256": tampered_hash,
                "allowed_models": [retired_model],
            }
        }
        violations = scan_file_for_retired_models(tmp_path, repo_root, exceptions=tampered_exceptions)
        assert len(violations) == 1
        assert violations[0].matched_model == "INTEGRITY_TAMPERED"
    finally:
        tmp_path.unlink(missing_ok=True)


@settings(max_examples=30, deadline=None)
@given(
    modern_model=st.sampled_from(RATIFIED_MODERN_MODELS),
    prefix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=15),
)
def test_property_modern_models_never_flagged(modern_model: str, prefix: str) -> None:
    """Property: Modern ratified models in code or configuration never trigger false positives."""
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write(f'{prefix}_model = "{modern_model}"\n')
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        violations = scan_file_for_retired_models(tmp_path, repo_root)
        assert len(violations) == 0, f"False positive on modern model {modern_model}"
    finally:
        tmp_path.unlink(missing_ok=True)


@settings(max_examples=30, deadline=None)
@given(
    allowed_model=st.sampled_from(["gemini-2.5-flash", "gemini-2.5-pro"]),
    unauthorized_model=st.sampled_from(["claude-3-haiku-20240307", "gpt-4-0314"]),
)
def test_property_scoped_exception_authorization(allowed_model: str, unauthorized_model: str) -> None:
    """Property: Exception granting model A does not permit unauthorized model B."""
    content = f'M1 = "{allowed_model}"\nM2 = "{unauthorized_model}"\n'
    actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = Path(tmp.name)

    try:
        repo_root = tmp_path.parent
        scoped_exceptions = {
            tmp_path.name: {
                "path": tmp_path.name,
                "sha256": actual_sha,
                "allowed_models": [allowed_model],
            }
        }
        violations = scan_file_for_retired_models(tmp_path, repo_root, exceptions=scoped_exceptions)
        assert len(violations) == 1
        assert violations[0].matched_model == unauthorized_model
    finally:
        tmp_path.unlink(missing_ok=True)
