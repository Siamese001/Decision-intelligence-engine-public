"""tools.scheduled_task_core.findings

Implements Wave 3 durable finding ledger, stable finding identities,
reproduction tracking, patch verification states, strict closure predicates,
and schema validation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)

CONTRACT_ID = "urn:nee:scheduled-task-findings-ledger:1.0.0"
SCHEMA_VERSION = "1.0.0"


class FindingsLedgerError(Exception):
    """Base error for findings ledger violations."""


class InvalidFindingTransitionError(FindingsLedgerError):
    """Raised when an illegal finding lifecycle state transition is attempted."""


class FalseClosureError(FindingsLedgerError):
    """Raised when an invalid or spoofed closure (e.g. cancellation) is attempted."""


class UntestedPatchError(FindingsLedgerError):
    """Raised when an untested patch is claimed as verified."""


class FindingState(str, Enum):
    DETECTED = "DETECTED"
    REPRODUCED = "REPRODUCED"
    PATCH_PROPOSED = "PATCH_PROPOSED"
    PATCH_VERIFIED = "PATCH_VERIFIED"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    DEPLOYED_VERIFIED = "DEPLOYED_VERIFIED"
    DISPOSITIONED_FALSE_POSITIVE = "DISPOSITIONED_FALSE_POSITIVE"
    DISPOSITIONED_ACCEPTED_RISK = "DISPOSITIONED_ACCEPTED_RISK"
    RESOLVED = "RESOLVED"


class ClosureType(str, Enum):
    DEPLOYED_VERIFIED = "DEPLOYED_VERIFIED"
    DISPOSITIONED_FALSE_POSITIVE = "DISPOSITIONED_FALSE_POSITIVE"
    DISPOSITIONED_ACCEPTED_RISK = "DISPOSITIONED_ACCEPTED_RISK"
    RESOLVED_ON_ABSENCE = "RESOLVED_ON_ABSENCE"


@dataclass
class ReproductionEvidence:
    reproduced: bool
    fixture_path: str
    test_command: str
    failing_exit_code: int
    error_signature: str | None = None


@dataclass
class PatchVerificationEvidence:
    failing_before: bool
    passing_after: bool
    test_path: str
    test_command: str


@dataclass
class ClosureProof:
    closure_type: str
    operator: str
    rationale: str
    timestamp: str
    commit_sha: str | None = None


@dataclass
class DurableFinding:
    finding_id: str
    signature: str
    state: FindingState
    root_cause_hypothesis: str
    reproduction_evidence: ReproductionEvidence
    owner: str
    first_seen_utc: str
    last_seen_utc: str
    occurrence_count: int
    run_ids_observed: list[str]
    proposed_patch_digest: str | None = None
    patch_verification_evidence: PatchVerificationEvidence | None = None
    closure_proof: ClosureProof | None = None

    def record_observation(self, run_id: str, timestamp_utc: str | None = None) -> None:
        """Records an additional observation of an existing finding in a run."""
        now_iso = timestamp_utc or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        self.occurrence_count += 1
        self.last_seen_utc = now_iso
        if run_id not in self.run_ids_observed:
            self.run_ids_observed.append(run_id)

    def mark_reproduced(self, evidence: ReproductionEvidence) -> None:
        """Updates reproduction evidence; transitions state to REPRODUCED if reproduced=True."""
        self.reproduction_evidence = evidence
        if evidence.reproduced:
            self.state = FindingState.REPRODUCED

    def propose_patch(self, patch_digest: str, hypothesis: str | None = None) -> None:
        """Proposes a code patch candidate without claiming verification."""
        if not patch_digest:
            raise InvalidFindingTransitionError("Proposed patch requires a non-empty patch digest.")
        self.proposed_patch_digest = patch_digest
        if hypothesis:
            self.root_cause_hypothesis = hypothesis
        self.state = FindingState.PATCH_PROPOSED

    def verify_patch(self, verification: PatchVerificationEvidence) -> None:
        """Verifies local patch behavior against failing-before and passing-after test evidence."""
        if not verification.failing_before or not verification.passing_after:
            raise UntestedPatchError(
                "Patch verification requires failing_before=True and passing_after=True."
            )
        self.patch_verification_evidence = verification
        self.state = FindingState.PATCH_VERIFIED

    def request_authorization(self) -> None:
        """Marks a verified patch as awaiting operator deployment authorization."""
        if self.state != FindingState.PATCH_VERIFIED:
            raise InvalidFindingTransitionError(
                f"Cannot request authorization from state {self.state.value}; must be PATCH_VERIFIED."
            )
        self.state = FindingState.AWAITING_AUTHORIZATION

    def close(self, proof: ClosureProof) -> None:
        """Closes or dispositions finding under strict closure predicate."""
        # Antagonistic rule: cancellation cannot satisfy repair closure
        rationale_lower = (proof.rationale or "").lower()
        closure_type_str = str(proof.closure_type)
        if "cancel" in rationale_lower or "cancel" in closure_type_str.lower():
            raise FalseClosureError("Cancellation cannot satisfy repair closure.")

        try:
            c_type = ClosureType(closure_type_str)
        except ValueError:
            raise FalseClosureError(f"Invalid closure type: {proof.closure_type}")

        if c_type == ClosureType.DEPLOYED_VERIFIED:
            if self.state not in (FindingState.PATCH_VERIFIED, FindingState.AWAITING_AUTHORIZATION):
                raise FalseClosureError(
                    f"Cannot claim DEPLOYED_VERIFIED from state {self.state.value}. Local patch must be verified first."
                )
            if not self.patch_verification_evidence or not self.patch_verification_evidence.passing_after:
                raise FalseClosureError("Cannot claim DEPLOYED_VERIFIED without passing test evidence.")
            if not proof.commit_sha:
                raise FalseClosureError("DEPLOYED_VERIFIED closure requires commit_sha deployment proof.")
            self.state = FindingState.DEPLOYED_VERIFIED

        elif c_type in (ClosureType.DISPOSITIONED_FALSE_POSITIVE, ClosureType.DISPOSITIONED_ACCEPTED_RISK):
            if not proof.operator or not proof.rationale:
                raise FalseClosureError("Disposition requires operator identity and non-empty rationale.")
            self.state = FindingState(c_type.value)

        elif c_type == ClosureType.RESOLVED_ON_ABSENCE:
            self.state = FindingState.RESOLVED

        self.closure_proof = proof

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "signature": self.signature,
            "state": self.state.value if isinstance(self.state, FindingState) else str(self.state),
            "root_cause_hypothesis": self.root_cause_hypothesis,
            "reproduction_evidence": {
                "reproduced": self.reproduction_evidence.reproduced,
                "fixture_path": self.reproduction_evidence.fixture_path,
                "test_command": self.reproduction_evidence.test_command,
                "failing_exit_code": self.reproduction_evidence.failing_exit_code,
                **({"error_signature": self.reproduction_evidence.error_signature} if self.reproduction_evidence.error_signature else {})
            },
            "owner": self.owner,
            "first_seen_utc": self.first_seen_utc,
            "last_seen_utc": self.last_seen_utc,
            "occurrence_count": self.occurrence_count,
            "run_ids_observed": list(self.run_ids_observed),
            "proposed_patch_digest": self.proposed_patch_digest,
            "patch_verification_evidence": asdict(self.patch_verification_evidence) if self.patch_verification_evidence else None,
            "closure_proof": asdict(self.closure_proof) if self.closure_proof else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DurableFinding:
        repro_raw = data.get("reproduction_evidence") or {}
        repro = ReproductionEvidence(
            reproduced=bool(repro_raw.get("reproduced", False)),
            fixture_path=str(repro_raw.get("fixture_path", "")),
            test_command=str(repro_raw.get("test_command", "")),
            failing_exit_code=int(repro_raw.get("failing_exit_code", 0)),
            error_signature=repro_raw.get("error_signature"),
        )
        patch_raw = data.get("patch_verification_evidence")
        patch_verif = (
            PatchVerificationEvidence(
                failing_before=bool(patch_raw["failing_before"]),
                passing_after=bool(patch_raw["passing_after"]),
                test_path=str(patch_raw["test_path"]),
                test_command=str(patch_raw["test_command"]),
            )
            if patch_raw
            else None
        )
        closure_raw = data.get("closure_proof")
        closure = (
            ClosureProof(
                closure_type=str(closure_raw["closure_type"]),
                operator=str(closure_raw["operator"]),
                rationale=str(closure_raw["rationale"]),
                timestamp=str(closure_raw["timestamp"]),
                commit_sha=closure_raw.get("commit_sha"),
            )
            if closure_raw
            else None
        )
        return cls(
            finding_id=str(data["finding_id"]),
            signature=str(data["signature"]),
            state=FindingState(data["state"]),
            root_cause_hypothesis=str(data.get("root_cause_hypothesis", "")),
            reproduction_evidence=repro,
            owner=str(data.get("owner", "unassigned")),
            first_seen_utc=str(data["first_seen_utc"]),
            last_seen_utc=str(data["last_seen_utc"]),
            occurrence_count=int(data["occurrence_count"]),
            run_ids_observed=list(data["run_ids_observed"]),
            proposed_patch_digest=data.get("proposed_patch_digest"),
            patch_verification_evidence=patch_verif,
            closure_proof=closure,
        )


class DurableFindingsLedger:
    """Manages collection of durable findings across repeated scheduled runs."""

    def __init__(self, task_id: str, ledger_id: str | None = None, updated_at: str | None = None) -> None:
        self.task_id = task_id
        self.ledger_id = ledger_id or f"ledger-{task_id}"
        self.updated_at = updated_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        self.findings: dict[str, DurableFinding] = {}
        self._new_in_session: set[str] = set()
        self._repeated_in_session: set[str] = set()

    @staticmethod
    def compute_finding_id(task_id: str, signature: str) -> str:
        """Generates deterministic finding ID from task_id and defect signature."""
        digest = hashlib.sha256(f"{task_id}:{signature}".encode("utf-8")).hexdigest()[:16]
        return f"find-{digest}"

    def record_finding(
        self,
        signature: str,
        run_id: str,
        root_cause_hypothesis: str = "",
        owner: str = "unassigned",
        reproduction_evidence: ReproductionEvidence | None = None,
        timestamp_utc: str | None = None,
    ) -> DurableFinding:
        """Records a finding observation, deduplicating across runs."""
        finding_id = self.compute_finding_id(self.task_id, signature)
        now_iso = timestamp_utc or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        self.updated_at = now_iso

        if finding_id in self.findings:
            finding = self.findings[finding_id]
            finding.record_observation(run_id, now_iso)
            if root_cause_hypothesis and not finding.root_cause_hypothesis:
                finding.root_cause_hypothesis = root_cause_hypothesis
            if reproduction_evidence and not finding.reproduction_evidence.reproduced:
                finding.mark_reproduced(reproduction_evidence)
            self._repeated_in_session.add(finding_id)
            return finding

        repro = reproduction_evidence or ReproductionEvidence(
            reproduced=False,
            fixture_path="",
            test_command="",
            failing_exit_code=0,
        )
        finding = DurableFinding(
            finding_id=finding_id,
            signature=signature,
            state=FindingState.REPRODUCED if repro.reproduced else FindingState.DETECTED,
            root_cause_hypothesis=root_cause_hypothesis,
            reproduction_evidence=repro,
            owner=owner,
            first_seen_utc=now_iso,
            last_seen_utc=now_iso,
            occurrence_count=1,
            run_ids_observed=[run_id],
        )
        self.findings[finding_id] = finding
        self._new_in_session.add(finding_id)
        return finding

    def get_metrics(self) -> dict[str, int]:
        """Calculates finding lifecycle and coverage metrics."""
        total = len(self.findings)
        new_cnt = len(self._new_in_session)
        repeated_cnt = len(self._repeated_in_session)
        resolved_cnt = sum(
            1 for f in self.findings.values()
            if f.state in (FindingState.RESOLVED, FindingState.DEPLOYED_VERIFIED)
        )
        verified_patches = sum(
            1 for f in self.findings.values()
            if f.state in (FindingState.PATCH_VERIFIED, FindingState.AWAITING_AUTHORIZATION, FindingState.DEPLOYED_VERIFIED)
        )
        applied_repairs = sum(
            1 for f in self.findings.values()
            if f.state == FindingState.DEPLOYED_VERIFIED
        )
        dispositioned = sum(
            1 for f in self.findings.values()
            if f.state in (FindingState.DISPOSITIONED_FALSE_POSITIVE, FindingState.DISPOSITIONED_ACCEPTED_RISK)
        )
        return {
            "total_tracked_findings": total,
            "new_findings": new_cnt,
            "repeated_findings": repeated_cnt,
            "resolved_findings": resolved_cnt,
            "verified_patches": verified_patches,
            "verified_applied_repairs": applied_repairs,
            "dispositioned_findings": dispositioned,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": CONTRACT_ID,
            "schema_version": SCHEMA_VERSION,
            "ledger_id": self.ledger_id,
            "task_id": self.task_id,
            "updated_at": self.updated_at,
            "findings": [f.to_dict() for f in self.findings.values()],
            "metrics": self.get_metrics(),
        }

    def validate_schema(self) -> None:
        """Validates current state against contracts/findings-ledger.schema.json."""
        root = Path(__file__).resolve().parents[2]
        schema_path = root / "contracts" / "findings-ledger.schema.json"
        if not schema_path.is_file():
            raise FileNotFoundError(f"Schema not found at {schema_path}")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=self.to_dict(), schema=schema)

    def save(self, path: Path) -> None:
        """Validates schema and saves ledger to disk."""
        self.validate_schema()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DurableFindingsLedger:
        """Loads and parses a durable findings ledger from disk."""
        data = json.loads(path.read_text(encoding="utf-8"))
        ledger = cls(
            task_id=str(data["task_id"]),
            ledger_id=str(data["ledger_id"]),
            updated_at=str(data["updated_at"]),
        )
        for f_data in data.get("findings", []):
            finding = DurableFinding.from_dict(f_data)
            ledger.findings[finding.finding_id] = finding
        ledger.validate_schema()
        return ledger
