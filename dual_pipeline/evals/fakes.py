"""Network-free provider scenarios used only after sealed retrieval in evals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core import digest
from ..index import retrieval_metadata_for_record
from ..l2 import (
    L2ProviderRequest,
    L2ProviderResponse,
    ModelPolicy,
    TokenUsage,
    analyze_request_intent,
)
from ..query import reason_over_packet
from ..x1_quality import (
    QualityModelPolicy,
    QualityProviderRequest,
    QualityProviderResponse,
    QualityUsage,
)


@dataclass(frozen=True)
class RecordedEvalRequest:
    model: str
    purpose: str


class _EvalReasoningProvider:
    provider_id = "sealed-eval-fake-provider"

    def __init__(self, factory: "SealedEvalProviderFactory", policy: ModelPolicy) -> None:
        self._factory = factory
        self._policy = policy

    def generate(self, request: L2ProviderRequest) -> L2ProviderResponse:
        return self._factory.generate(self._policy, request)


class SealedEvalProviderFactory:
    """Return grounded local answers after retrieval has sealed the evidence packet."""

    def __init__(self, *, auto_bind_material: bool = False) -> None:
        self.initialized_models: list[str] = []
        self.requests: list[RecordedEvalRequest] = []
        self.auto_bind_material = auto_bind_material

    def __call__(self, policy: ModelPolicy) -> _EvalReasoningProvider:
        self.initialized_models.append(policy.model)
        return _EvalReasoningProvider(self, policy)

    def generate(self, policy: ModelPolicy, request: L2ProviderRequest) -> L2ProviderResponse:
        if request.evidence_packet is None:
            raise AssertionError("sealed eval provider requires a sealed evidence packet")
        self.requests.append(RecordedEvalRequest(model=policy.model, purpose=request.purpose.value))
        output: dict[str, Any] = reason_over_packet(
            dict(request.plan), dict(request.evidence_packet)
        )
        if self.auto_bind_material:
            _bind_required_material_records(
                output=output,
                plan=request.plan,
                evidence_packet=request.evidence_packet,
            )
        if request.plan.get("query_type") == "cross_workstream_synthesis" and not output[
            "patterns"
        ]:
            # Cross-workstream synthesis requires an explicit pattern section.
            # If the deterministic mapper found no opportunity-to-pain mapping,
            # reuse sealed approved titles rather than inventing a pattern.
            output["patterns"] = [
                item["record"]["title"]
                for item in request.evidence_packet.get("approved", [])[:3]
                if isinstance(item.get("record", {}).get("title"), str)
                and item["record"]["title"].strip()
            ]
        # The sealed-eval synthesizer deliberately keeps opportunities terse.
        # The external-L2 output contract, however, requires every opportunity
        # to say what evidence it addresses.  Use an existing factual approved
        # record only as an explicit, traceable fallback; this never adds a fact
        # or citation that was not present in the sealed evidence packet.
        factual_approved = [
            item
            for item in request.evidence_packet.get("approved", [])
            if item.get("retrieval_metadata", {}).get("answer_use")
            == "factual_answer"
        ]
        if factual_approved:
            fallback = factual_approved[0]["record"]
            for opportunity in output["opportunities"]:
                if opportunity["addresses"]:
                    continue
                opportunity["addresses"] = [fallback["title"]]
                opportunity["citations"] = list(
                    dict.fromkeys(opportunity["citations"] + [fallback["record_id"]])
                )
                opportunity["evidence_ids"] = list(
                    dict.fromkeys(opportunity["evidence_ids"] + fallback["evidence_ids"])
                )
        if (
            not output["next_best_actions"]
            and analyze_request_intent(request.question).recommendation
        ):
            basis_citations = [
                citation
                for field in ("current_state", "pain_points")
                for row in output.get(field, [])
                if isinstance(row, dict)
                for citation in row.get("citations", [])
                if isinstance(citation, str)
            ][:2]
            if basis_citations:
                output["next_best_actions"] = [
                    {
                        "action": (
                            "Validate the separated approved facts and candidate "
                            "hypotheses with the accountable workstream owner before "
                            "taking action."
                        ),
                        "basis_citations": basis_citations,
                    }
                ]
        usage = TokenUsage(
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=50,
            total_tokens=150,
            usage_reported=True,
        )
        return L2ProviderResponse(
            output=output,
            response_digest=digest(output),
            model=policy.model,
            usage=usage,
            latency_ms=1.0,
        )


def _bind_required_material_records(
    *,
    output: dict[str, Any],
    plan: dict[str, Any],
    evidence_packet: dict[str, Any],
) -> None:
    """Make the sealed evaluator citation-complete for required material IDs.

    The evaluator is intentionally deterministic. It must exercise the same
    material-citation obligation that a compliant external provider receives,
    while leaving genuine retrieval gaps to the pre-L2 fail-closed gate.
    """

    required = evidence_packet.get("required_material_record_ids_by_lane", {})
    if not isinstance(required, dict):
        return

    items_by_id: dict[tuple[str, str], dict[str, Any]] = {}
    for lane in ("approved", "candidate", "external_public"):
        rows = evidence_packet.get(lane, [])
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict) or not isinstance(item.get("record"), dict):
                continue
            record_id = item["record"].get("record_id")
            if isinstance(record_id, str):
                items_by_id[(lane, record_id)] = item

    cited_record_ids = {
        citation
        for field in (
            "current_state",
            "pain_points",
            "opportunities",
            "hypotheses",
            "source_evidence",
            "counterevidence",
            "proposed_assertions",
        )
        for row in output.get(field, [])
        if isinstance(row, dict)
        for citation in row.get("citations", [])
        if isinstance(citation, str)
    }

    def answer_use(item: dict[str, Any], lane: str) -> str:
        metadata = item.get("retrieval_metadata", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("answer_use"), str):
            return metadata["answer_use"]
        record = item["record"]
        return retrieval_metadata_for_record(
            record["record_type"], lane
        )["answer_use"]

    def evidence_ids(item: dict[str, Any]) -> list[str]:
        record = item["record"]
        values = [value for value in record.get("evidence_ids", []) if isinstance(value, str)]
        if values:
            return list(dict.fromkeys(values))
        return list(
            dict.fromkeys(
                value["evidence_id"]
                for value in item.get("evidence", [])
                if isinstance(value, dict) and isinstance(value.get("evidence_id"), str)
            )
        )

    for lane in ("approved", "candidate", "external_public"):
        record_ids = required.get(lane, [])
        if not isinstance(record_ids, list):
            continue
        for record_id in record_ids:
            if not isinstance(record_id, str) or record_id in cited_record_ids:
                continue
            item = items_by_id.get((lane, record_id))
            if item is None:
                continue
            record = item["record"]
            ids = evidence_ids(item)
            if not ids:
                continue
            use = answer_use(item, lane)
            row = {
                "citations": [record_id],
                "evidence_ids": ids,
                "lane": lane,
            }
            if use == "factual_answer":
                row["statement"] = record["retrieval_text"]
                output.setdefault("current_state", []).append(row)
            elif use == "guidance" and lane == "approved":
                output.setdefault("guidance", []).append(
                    row | {"label": "GUIDANCE_NOT_FACT", "guidance": record["retrieval_text"]}
                )
            elif use == "candidate_hypothesis" and lane == "candidate":
                output.setdefault("hypotheses", []).append(
                    row | {"hypothesis": f"CANDIDATE: {record['retrieval_text']}"}
                )
            elif use == "citation_only" and lane == "candidate":
                output.setdefault("source_evidence", []).append(
                    row
                    | {
                        "label": "CANDIDATE_SOURCE_EVIDENCE",
                        "excerpt": record["retrieval_text"],
                    }
                )
            elif (
                plan.get("query_type") == "opportunity_nba"
                and use
                in {
                    "factual_answer",
                    "strategic_context",
                    "decision_constraint",
                    "external_analogy",
                }
                and lane in {"approved", "external_public"}
                and output.get("opportunities")
            ):
                opportunity = output["opportunities"][0]
                opportunity["citations"] = list(
                    dict.fromkeys(opportunity.get("citations", []) + [record_id])
                )
                opportunity["evidence_ids"] = list(
                    dict.fromkeys(opportunity.get("evidence_ids", []) + ids)
                )
            else:
                continue
            cited_record_ids.add(record_id)


class _EvalQualityProvider:
    provider_id = "sealed-eval-quality-fake-provider"

    def __init__(
        self,
        factory: "SealedEvalQualityProviderFactory",
        policy: QualityModelPolicy,
    ) -> None:
        self._factory = factory
        self._policy = policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        return self._factory.review(self._policy, request)


class SealedEvalQualityProviderFactory:
    """Network-free challenger/judge outcomes for sealed eval cases only."""

    def __init__(self, judge_verdicts: list[str]) -> None:
        self._judge_verdicts = list(judge_verdicts)
        self.initialized_models: list[str] = []
        self.requests: list[tuple[str, str]] = []

    def __call__(self, policy: QualityModelPolicy) -> _EvalQualityProvider:
        self.initialized_models.append(policy.model)
        return _EvalQualityProvider(self, policy)

    def review(
        self,
        policy: QualityModelPolicy,
        request: QualityProviderRequest,
    ) -> QualityProviderResponse:
        self.requests.append((policy.model, request.role.value))
        if request.role.value == "challenger":
            output: dict[str, Any] = {
                "verdict": "PASS",
                "defect_codes": [],
                "evidence_refs": [],
                "confidence": "high",
            }
        else:
            if not self._judge_verdicts:
                raise AssertionError("eval quality judge verdict sequence is exhausted")
            verdict = self._judge_verdicts.pop(0)
            if verdict == "PASS":
                output = {
                    "verdict": "PASS",
                    "defect_codes": [],
                    "evidence_refs": [],
                    "confidence": "high",
                }
            else:
                approved = request.evidence_packet.get("approved", [])
                if not approved:
                    raise AssertionError("quality eval defect requires approved sealed evidence")
                record_id = approved[0]["record"]["record_id"]
                output = {
                    "verdict": verdict,
                    "defect_codes": ["CLIENT_JARGON"],
                    "evidence_refs": [record_id],
                    "confidence": "high",
                }
        return QualityProviderResponse(
            output=output,
            response_digest=digest(output),
            model=policy.model,
            usage=QualityUsage(input_tokens=25, output_tokens=10, total_tokens=35),
            latency_ms=1.0,
        )
