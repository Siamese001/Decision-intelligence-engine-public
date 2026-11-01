"""Constants and contracts for production preflight."""
from __future__ import annotations

import re

from ..core import ROOT

RUNTIME_CONTRACT = ROOT / "requirements" / "runtime-direct.json"
RELEASE_STATE = ROOT / "release_state.json"
PACKAGE_MANIFEST = ROOT / "manifest.json"
MODEL_PIN_CONTRACT = ROOT / "requirements" / "bge-m3-model.json"
REQUIRED_QUALIFICATION_CAPABILITIES = (
    "knowledge:intake",
    "knowledge:query",
    "knowledge:release",
    "knowledge:production-qualify",
)
SMOKE_TEXT = "NEE production preflight validates dense sparse and ColBERT retrieval."
PIP_CHECK_TIMEOUT_SECONDS = 120

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "private_key",
        re.compile(b"-----BEGIN " + b"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("hugging_face_token", re.compile(rb"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{20,}")),
    ("openai_api_key", re.compile(rb"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(rb"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    (
        "aws_temporary_access_key",
        re.compile(rb"(?<![A-Z0-9])ASIA[0-9A-Z]{16}(?![A-Z0-9])"),
    ),
    (
        "compact_jwt",
        re.compile(
            rb"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\."
            rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}"
        ),
    ),
)
