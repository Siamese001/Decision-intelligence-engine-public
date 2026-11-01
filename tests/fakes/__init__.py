"""
tests/fakes/__init__.py

Central export package for shared test credentials and environment helpers.
Non-LLM HTTP and subprocess fakes eradicated under Wave 4 governance.
"""

from __future__ import annotations

from .credentials_fake import (
    SAFE_MOCK_KEYS,
    FakeCredentials,
    safe_credentials_environment,
)

__all__ = [
    "FakeCredentials",
    "SAFE_MOCK_KEYS",
    "safe_credentials_environment",
]
