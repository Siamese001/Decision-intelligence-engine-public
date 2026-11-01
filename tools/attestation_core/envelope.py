"""tools/attestation_core/envelope.py

In-toto v1 Statement and DSSE (Dead Simple Signing Envelope) formatting for readiness attestations.
Conforms to in-toto Statement v1 standard: https://in-toto.io/Statement/v1
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
READINESS_PREDICATE_TYPE = "https://decision-intelligence.engine/attestation/readiness/v1"
PR_REVIEW_PREDICATE_TYPE = "https://decision-intelligence.engine/attestation/pr-review/v1"


def create_in_toto_statement(
    tree_digest: str,
    predicate: Dict[str, Any],
    predicate_type: str = READINESS_PREDICATE_TYPE,
    subject_name: str = "git-tree",
) -> Dict[str, Any]:
    """Builds a canonical in-toto Statement v1 envelope bound to the tree digest."""
    return {
        "_type": IN_TOTO_STATEMENT_TYPE,
        "subject": [
            {
                "name": subject_name,
                "digest": {
                    "sha256": tree_digest,
                },
            }
        ],
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def wrap_dsse_envelope(
    statement: Dict[str, Any],
    signatures: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Wraps in-toto Statement in a DSSE envelope with base64 payload."""
    payload_bytes = json.dumps(statement, sort_keys=True).encode("utf-8")
    payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    return {
        "payloadType": "application/vnd.in-toto+json",
        "payload": payload_b64,
        "payload_sha256": payload_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signatures": signatures
        or [
            {
                "keyid": "local-qualification-identity",
                "sig": f"sha256:{payload_hash}",
            }
        ],
    }


def unwrap_dsse_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts statement from DSSE envelope, supporting both raw statement and wrapped payload."""
    if "_type" in envelope and "subject" in envelope:
        return envelope
    if "payload" in envelope:
        payload_b64 = envelope["payload"]
        payload_bytes = base64.b64decode(payload_b64)
        return json.loads(payload_bytes.decode("utf-8"))
    raise ValueError("Unrecognized attestation format: neither in-toto Statement nor DSSE envelope")


def verify_statement_subject(statement: Dict[str, Any], expected_tree_digest: str) -> bool:
    """Verifies that the statement's subject digest matches the expected tree digest exactly."""
    subjects = statement.get("subject", [])
    for subj in subjects:
        digest_map = subj.get("digest", {})
        if digest_map.get("sha256") == expected_tree_digest:
            return True
    return False
