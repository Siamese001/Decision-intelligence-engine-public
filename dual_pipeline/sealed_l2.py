"""Sealed L2 artifact — tamper-evident boundary between L2 and X gates.

After L2 reasoning completes, the raw output and its provenance are sealed
with a canonical digest.  This enables post-hoc verification that the X
gates evaluated the exact output that L2 produced, without any intermediate
mutation.
"""
from __future__ import annotations

import json
from typing import Any

from .core import digest, stable_id, utc_now


def seal_l2_output(
    *,
    run_id: str,
    query_id: str,
    l2_decision_artifact_id: str,
    l2_disposition: str,
    l2_model: str | None,
    l2_answer: dict[str, Any] | None,
    l2_provider_call_count: int,
    l2_token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a sealed L2 artifact with canonical_digest.

    The digest covers the complete L2 output body (excluding
    ``canonical_digest`` itself) so that downstream X gates can verify
    the artifact has not been altered.

    Returns a dict conforming to the ``sealed-l2-artifact`` structure.
    """
    sealed_at = utc_now()
    body: dict[str, Any] = {
        "artifact_id": stable_id("sealed-l2", run_id, query_id, sealed_at),
        "run_id": run_id,
        "query_id": query_id,
        "l2_decision_artifact_id": l2_decision_artifact_id,
        "l2_disposition": l2_disposition,
        "l2_model": l2_model,
        "l2_provider_call_count": l2_provider_call_count,
        "l2_token_usage": l2_token_usage or {},
        "sealed_at": sealed_at,
    }

    # Include answer digest without leaking prompt/response content
    if l2_answer is not None:
        answer_json = json.dumps(l2_answer, sort_keys=True, separators=(",", ":"))
        body["answer_digest"] = digest(l2_answer)
        body["answer_length_chars"] = len(answer_json)
    else:
        body["answer_digest"] = None
        body["answer_length_chars"] = 0

    body["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(body),
    }

    return body
