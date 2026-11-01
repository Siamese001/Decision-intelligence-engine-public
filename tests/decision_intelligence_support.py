"""Trusted, source-bound fixtures for Decision Intelligence projection tests."""

from __future__ import annotations

import hashlib
import time
from typing import Iterable

from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.core import digest
from dual_pipeline.reference_monitor import ReferenceMonitor, SourceAssertionRegistry


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_assertion(
    source_assertion_id: str,
    *,
    classification: str = "internal",
    audiences: Iterable[str] = ("internal_consulting",),
    source_as_of: str = "2026-08-01T00:00:00Z",
    captured_at: str = "2026-08-01T00:00:00Z",
    revoked_at: str | None = None,
) -> dict[str, object]:
    return {
        "source_assertion_id": source_assertion_id,
        "source_id": f"source-{source_assertion_id}",
        "source_version_id": f"version-{source_assertion_id}",
        "snapshot_digest": _sha(f"snapshot:{source_assertion_id}"),
        "content_digest": _sha(f"content:{source_assertion_id}"),
        "locator": {
            "kind": "record",
            "value": source_assertion_id,
            "quote_digest": _sha(f"quote:{source_assertion_id}"),
        },
        "extraction_run_id": f"extract-{source_assertion_id}",
        "classification": classification,
        "audiences": list(audiences),
        "source_as_of": source_as_of,
        "captured_at": captured_at,
        "revoked_at": revoked_at,
    }


def monitor_for(
    source_assertion_ids: Iterable[str],
    *,
    classification: str = "internal",
    audiences: Iterable[str] = ("internal_consulting",),
    source_as_of: str = "2026-08-01T00:00:00Z",
    captured_at: str = "2026-08-01T00:00:00Z",
) -> ReferenceMonitor:
    return ReferenceMonitor(
        SourceAssertionRegistry(
            [
                source_assertion(
                    item,
                    classification=classification,
                    audiences=audiences,
                    source_as_of=source_as_of,
                    captured_at=captured_at,
                )
                for item in sorted(set(source_assertion_ids))
            ]
        )
    )


def authorization(
    *,
    audiences: Iterable[str] = ("internal_consulting",),
    classification_clearance: str = "restricted",
) -> AuthorizationContext:
    now = int(time.time())
    values = {
        "subject": "test-decision-intelligence-user",
        "issuer": "test-issuer",
        "relying_party": "test-relying-party",
        "token_id": "test-token",
        "issued_at": now - 60,
        "expires_at": now + 900,
        "classification_clearance": classification_clearance,
        "audiences": tuple(sorted(set(audiences))),
        "capabilities": ("query",),
        "authorized_resources": {},
        "approved_model_manifest_digest": None,
        "token_digest": _sha("test-token"),
    }
    return AuthorizationContext(**values, context_digest=digest(values))
