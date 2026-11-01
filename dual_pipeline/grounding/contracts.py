"""dual_pipeline.grounding.contracts

Layer 0 grounding and claim validation contracts.
Provides versioned models and fail-closed JSON schema validators for:
1. SourceLocator: typed evidence pointers (transcript paragraph, file line, etc.)
2. ClaimRecord: structured subject/predicate/object facts bound to assertion IDs
3. ClaimManifest: immutable container of claims with payload and graph bindings
4. DeliveryReceipt: cryptographic certification receipt for grounded release
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = ROOT / "contracts" / "dual_pipeline" / "schemas"

CLAIM_MANIFEST_SCHEMA_PATH = SCHEMAS_DIR / "claim-manifest.schema.json"
DELIVERY_RECEIPT_SCHEMA_PATH = SCHEMAS_DIR / "delivery-receipt.schema.json"
COMMON_SCHEMA_PATH = SCHEMAS_DIR / "common.schema.json"


def compute_rfc8259_digest(payload: Dict[str, Any]) -> str:
    """Compute deterministic RFC-8259 SHA-256 digest of dictionary."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_validator(schema_path: Path) -> jsonschema.Draft202012Validator:
    """Instantiate Draft202012Validator with common schema referencing registry."""
    schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
    common_data = json.loads(COMMON_SCHEMA_PATH.read_text(encoding="utf-8"))
    common_res = Resource.from_contents(common_data)
    registry = Registry().with_resource(common_data["$id"], common_res)
    return jsonschema.Draft202012Validator(schema_data, registry=registry)


_MANIFEST_VALIDATOR: Optional[jsonschema.Draft202012Validator] = None
_RECEIPT_VALIDATOR: Optional[jsonschema.Draft202012Validator] = None


def get_manifest_validator() -> jsonschema.Draft202012Validator:
    global _MANIFEST_VALIDATOR
    if _MANIFEST_VALIDATOR is None:
        _MANIFEST_VALIDATOR = _build_validator(CLAIM_MANIFEST_SCHEMA_PATH)
    return _MANIFEST_VALIDATOR


def get_receipt_validator() -> jsonschema.Draft202012Validator:
    global _RECEIPT_VALIDATOR
    if _RECEIPT_VALIDATOR is None:
        _RECEIPT_VALIDATOR = _build_validator(DELIVERY_RECEIPT_SCHEMA_PATH)
    return _RECEIPT_VALIDATOR


@dataclass(frozen=True)
class SourceLocator:
    """Typed locator pinpointing precise source truth without format conflation."""
    locator_type: str  # transcript_paragraph, file_line, git_commit, document_section, url
    source_uri: str
    locator_value: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "locator_type": self.locator_type,
            "source_uri": self.source_uri,
            "locator_value": self.locator_value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceLocator:
        return cls(
            locator_type=data["locator_type"],
            source_uri=data["source_uri"],
            locator_value=data["locator_value"],
        )


@dataclass(frozen=True)
class ClaimRecord:
    """Structured, verifiable claim binding subject/predicate/object to graph assertions."""
    claim_id: str
    subject: str
    predicate: str
    object: str
    scope: str
    negation: bool
    assertion_ids: List[str]
    evidence_lane: str  # approved, candidate, external_public
    epistemic_status: str  # approved, candidate, disputed, superseded, retracted
    source_locators: List[SourceLocator]
    qualifiers: List[str] = field(default_factory=list)
    temporal_validity: Optional[Dict[str, Optional[str]]] = None
    supporting_assertion_ids: List[str] = field(default_factory=list)
    contradicting_assertion_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "scope": self.scope,
            "qualifiers": list(self.qualifiers),
            "negation": self.negation,
            "temporal_validity": self.temporal_validity,
            "assertion_ids": list(self.assertion_ids),
            "evidence_lane": self.evidence_lane,
            "epistemic_status": self.epistemic_status,
            "source_locators": [loc.to_dict() for loc in self.source_locators],
            "supporting_assertion_ids": list(self.supporting_assertion_ids),
            "contradicting_assertion_ids": list(self.contradicting_assertion_ids),
        }
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClaimRecord:
        return cls(
            claim_id=data["claim_id"],
            subject=data["subject"],
            predicate=data["predicate"],
            object=data["object"],
            scope=data.get("scope", "PROJECT_LEVEL"),
            qualifiers=list(data.get("qualifiers", [])),
            negation=bool(data.get("negation", False)),
            temporal_validity=data.get("temporal_validity"),
            assertion_ids=list(data["assertion_ids"]),
            evidence_lane=data.get("evidence_lane", "approved"),
            epistemic_status=data.get("epistemic_status", "approved"),
            source_locators=[SourceLocator.from_dict(loc) for loc in data["source_locators"]],
            supporting_assertion_ids=list(data.get("supporting_assertion_ids", [])),
            contradicting_assertion_ids=list(data.get("contradicting_assertion_ids", [])),
        )


@dataclass
class ClaimManifest:
    """Immutable manifest binding candidate output claims to exact graph state and payload."""
    manifest_id: str
    payload_digest: str
    runtime_root: str
    graph_generation_id: str
    graph_sha256: str
    claims: List[ClaimRecord]
    created_at: Optional[str] = None
    canonical_digest: Optional[Dict[str, str]] = None
    schema_version: str = "1.0.0"
    contract_id: str = "urn:nee:dual-pipeline:claim-manifest:1.0.0"
    artifact_id: Optional[str] = None
    extensions: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        body = {
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id or self.manifest_id,
            "manifest_id": self.manifest_id,
            "payload_digest": self.payload_digest,
            "runtime_root": str(self.runtime_root),
            "graph_generation_id": self.graph_generation_id,
            "graph_sha256": self.graph_sha256,
            "claims": [c.to_dict() for c in self.claims],
            "created_at": self.created_at or datetime.now(timezone.utc).isoformat(),
            "extensions": dict(self.extensions),
        }
        unsigned = dict(body)
        raw_digest = compute_rfc8259_digest(unsigned)
        body["canonical_digest"] = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": raw_digest,
        }
        return body

    def validate(self) -> None:
        """Validate against JSON schema fail-closed."""
        data = self.to_dict()
        validator = get_manifest_validator()
        errors = list(validator.iter_errors(data))
        if errors:
            raise jsonschema.ValidationError(f"ClaimManifest schema validation failed: {errors[0].message}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClaimManifest:
        validator = get_manifest_validator()
        validator.validate(data)
        claims = [ClaimRecord.from_dict(c) for c in data["claims"]]
        return cls(
            manifest_id=data["manifest_id"],
            payload_digest=data["payload_digest"],
            runtime_root=data["runtime_root"],
            graph_generation_id=data["graph_generation_id"],
            graph_sha256=data["graph_sha256"],
            claims=claims,
            created_at=data.get("created_at"),
            canonical_digest=data.get("canonical_digest"),
            schema_version=data.get("schema_version", "1.0.0"),
            contract_id=data.get("contract_id", "urn:nee:dual-pipeline:claim-manifest:1.0.0"),
            artifact_id=data.get("artifact_id"),
            extensions=dict(data.get("extensions", {})),
        )


@dataclass
class DeliveryReceipt:
    """Sealed cryptographic certification receipt for release or blocking of output."""
    receipt_id: str
    manifest_id: str
    manifest_digest: str
    payload_digest: str
    runtime_root: str
    graph_generation_id: str
    graph_sha256: str
    status: str  # CERTIFIED, REJECTED, BLOCKED
    reason_code: str
    total_claims_count: int
    supported_claims_count: int
    contradicted_claims_count: int
    delivery_surface: str
    audience: str
    word_count: int
    certified_at: Optional[str] = None
    canonical_digest: Optional[Dict[str, str]] = None
    schema_version: str = "1.0.0"
    contract_id: str = "urn:nee:dual-pipeline:delivery-receipt:1.0.0"
    artifact_id: Optional[str] = None
    extensions: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        body = {
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id or self.receipt_id,
            "receipt_id": self.receipt_id,
            "manifest_id": self.manifest_id,
            "manifest_digest": self.manifest_digest,
            "payload_digest": self.payload_digest,
            "runtime_root": str(self.runtime_root),
            "graph_generation_id": self.graph_generation_id,
            "graph_sha256": self.graph_sha256,
            "status": self.status,
            "reason_code": self.reason_code,
            "total_claims_count": self.total_claims_count,
            "supported_claims_count": self.supported_claims_count,
            "contradicted_claims_count": self.contradicted_claims_count,
            "delivery_surface": self.delivery_surface,
            "audience": self.audience,
            "word_count": self.word_count,
            "certified_at": self.certified_at or datetime.now(timezone.utc).isoformat(),
            "extensions": dict(self.extensions),
        }
        unsigned = dict(body)
        raw_digest = compute_rfc8259_digest(unsigned)
        body["canonical_digest"] = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": raw_digest,
        }
        return body

    def validate(self) -> None:
        """Validate against JSON schema fail-closed."""
        data = self.to_dict()
        validator = get_receipt_validator()
        errors = list(validator.iter_errors(data))
        if errors:
            raise jsonschema.ValidationError(f"DeliveryReceipt schema validation failed: {errors[0].message}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeliveryReceipt:
        validator = get_receipt_validator()
        validator.validate(data)
        return cls(
            receipt_id=data["receipt_id"],
            manifest_id=data["manifest_id"],
            manifest_digest=data["manifest_digest"],
            payload_digest=data["payload_digest"],
            runtime_root=data["runtime_root"],
            graph_generation_id=data["graph_generation_id"],
            graph_sha256=data["graph_sha256"],
            status=data["status"],
            reason_code=data["reason_code"],
            total_claims_count=data["total_claims_count"],
            supported_claims_count=data["supported_claims_count"],
            contradicted_claims_count=data["contradicted_claims_count"],
            delivery_surface=data["delivery_surface"],
            audience=data["audience"],
            word_count=data["word_count"],
            certified_at=data.get("certified_at"),
            canonical_digest=data.get("canonical_digest"),
            schema_version=data.get("schema_version", "1.0.0"),
            contract_id=data.get("contract_id", "urn:nee:dual-pipeline:delivery-receipt:1.0.0"),
            artifact_id=data.get("artifact_id"),
            extensions=dict(data.get("extensions", {})),
        )
