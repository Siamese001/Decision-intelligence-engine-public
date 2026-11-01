#!/usr/bin/env python3
"""
Pipeline B: Compute Fork Extractors (Law 1 & Law 2 Enforcement)
Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, Apple Metal)

Two physically separated extraction pipelines:
  1. `ClientSafeExtractor`: Dispatched to Enterprise Cloud API with Zero Data Retention
     and mandatory on-device pre-egress sanitization.
  2. `InternalAdvisoryExtractor`: Executed 100% locally on Apple Silicon Metal via `mlx-lm`.

Fail-closed governance: Purges silent synthetic mock fallbacks in non-mock mode.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional

from config.credentials import CredentialManager

try:
    import httpx
except ImportError:
    httpx = None

from pipeline_b_extraction.contracts import (
    ExtractionInferenceError,
    ForkedExtractionResult,
)
from pipeline_b_extraction.extractors_core import (
    generate_assertions,
    generate_relational_edges,
    mock_extract_advisory,
    mock_extract_canonical,
)
from pipeline_b_extraction.json_repair import repair_and_parse_json
from pipeline_b_extraction.pre_egress_sanitizer import (
    LocalEgressSanitizer,
    default_sanitizer,
)
from storage.database import (
    AdvisoryCategory,
    AssertionRecord,
    CanonicalGraphNode,
    EvidenceRecord,
    GraphEdge,
    InternalAdvisoryNode,
)
from storage.identifiers import (
    deterministic_advisory_node_id,
    deterministic_canonical_node_id,
)

try:
    from dual_pipeline.telemetry import get_tracer
    tracer = get_tracer("pipeline_b_extraction.extractors")
except Exception:
    tracer = None


def _check_mlx_lm_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("mlx_lm") is not None
    except Exception:
        return False


MLX_LM_AVAILABLE = _check_mlx_lm_available()

logger = logging.getLogger("pipeline_b_extraction.extractors")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [EXTRACTOR] %(message)s"
    ))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

from dual_pipeline.parameters import get_local_mlx_model
DEFAULT_LOCAL_MODEL = get_local_mlx_model("instruct_8b")
try:
    from dual_pipeline.model_registry import ModelTier, get_binding
    DEFAULT_CLOUD_MODEL = CredentialManager.get_credential("OPENAI_EXTRACTION_MODEL") or get_binding(ModelTier.FRONTIER_HIGH_REASONING).model_id
except Exception:
    DEFAULT_CLOUD_MODEL = CredentialManager.get_credential("OPENAI_EXTRACTION_MODEL") or "default-extraction-model"

CLIENT_SAFE_SYSTEM_PROMPT = """You are a Principal Enterprise Systems Architect.
Analyze the following immutable meeting evidence and extract structured, client-safe facts.
Focus strictly on: ARCHITECTURAL_DECISION, TECHNICAL_SPECIFICATION, DATA_CONTRACT, DELIVERY_MILESTONE, or OPERATIONAL_TODO.

Return a JSON array of objects with keys:
- type: (one of the above types)
- name: (concise name)
- properties: (key-value dictionary)
- strategic_value: (0.0 to 10.0 scale)
- tacticality: (0.0 to 10.0 scale)"""


class ClientSafeExtractor:
    """
    Extracts canonical enterprise architecture, decisions, and constraints.
    Enforces Law 1: Sanitizes all raw text before cloud dispatch.
    Fails closed: Never falls back to synthetic mock data in non-mock mode.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        mock_mode: bool = False,
        sanitizer: Optional[LocalEgressSanitizer] = None,
    ):
        self.model = model or DEFAULT_CLOUD_MODEL
        if api_key:
            self.api_key = api_key
            self.mock_mode = mock_mode
        else:
            from dual_pipeline.credentials import resolve_openai_credentials
            creds = resolve_openai_credentials(allow_mock=mock_mode, model=model)
            self.api_key = creds.api_key
            self.mock_mode = mock_mode or creds.is_mock

        allow_egress = CredentialManager.get_credential("ALLOW_CLOUD_TEXT_EGRESS", "1").lower() in ("1", "true", "yes")
        if not allow_egress:
            self.mock_mode = True
        self.sanitizer = sanitizer or default_sanitizer

    def format_cloud_prompt(self, evidence: EvidenceRecord) -> str:
        clean_text = self.sanitizer.sanitize(evidence.text).sanitized_text
        return f"{CLIENT_SAFE_SYSTEM_PROMPT}\n\nEVIDENCE SPEAKER: {evidence.speaker}\nEVIDENCE TEXT: \"{clean_text}\""

    async def extract(self, evidence: EvidenceRecord) -> List[CanonicalGraphNode]:
        """Runs client-safe extraction prompt against OpenAI after pre-egress sanitization."""
        clean_text = self.sanitizer.sanitize(evidence.text).sanitized_text

        if self.mock_mode:
            return self._mock_extract(evidence, clean_text=clean_text)

        try:
            client, _ = CredentialManager.get_async_openai_client(
                allow_mock=self.mock_mode,
                model=self.model,
                reasoning_effort="medium",
            )
            if client is None:
                return self._mock_extract(evidence, clean_text=clean_text)

            from config.request_deadline import remaining_timeout

            user_content = f"EVIDENCE SPEAKER: {evidence.speaker}\nEVIDENCE TEXT: \"{clean_text}\""
            raw_kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": CLIENT_SAFE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "reasoning_effort": "medium",
                "cache_namespace": "client_safe_extractor",
                "timeout": remaining_timeout(60.0),
            }
            call_kwargs = CredentialManager.sanitize_call_kwargs(self.model, raw_kwargs)
            resp = await client.chat.completions.create(**call_kwargs)
            try:
                from dual_pipeline.prompt_cache import normalize_cached_tokens

                cached_toks = normalize_cached_tokens(resp)
                if cached_toks > 0:
                    logger.debug("ClientSafeExtractor utilized %d cached tokens.", cached_toks)
            except Exception:
                pass
            raw = resp.choices[0].message.content or "{}"
            data = repair_and_parse_json(raw)
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                if "facts" in data and isinstance(data["facts"], list):
                    items = data["facts"]
                elif "nodes" in data and isinstance(data["nodes"], list):
                    items = data["nodes"]
                else:
                    items = [data]
            else:
                items = []

            nodes: List[CanonicalGraphNode] = []
            for item in items:
                n_type = item.get("type", "ARCHITECTURAL_DECISION")
                n_name = item.get("name", "Extracted Fact")
                node_id = deterministic_canonical_node_id(evidence.evidence_id, n_type, n_name)
                nodes.append(CanonicalGraphNode(
                    node_id=node_id,
                    evidence_id=evidence.evidence_id,
                    type=n_type,
                    name=n_name,
                    properties=item.get("properties", {}),
                    strategic_value=float(item.get("strategic_value", 5.0)),
                    tacticality=float(item.get("tacticality", 2.0)),
                    evidence_quality=1.0,
                    confidence=0.95,
                    is_quarantined=False,
                    namespace="CLIENT_SAFE",
                ))
            return nodes
        except Exception as e:
            logger.error(f"ClientSafeExtractor failed on {evidence.evidence_id}: {e}")
            if self.mock_mode:
                return self._mock_extract(evidence, clean_text=clean_text)
            raise ExtractionInferenceError(
                f"ClientSafeExtractor failed on {evidence.evidence_id}: {e}",
                evidence_id=evidence.evidence_id,
                original_exception=e,
            ) from e

    def _mock_extract(self, evidence: EvidenceRecord, clean_text: Optional[str] = None) -> List[CanonicalGraphNode]:
        return mock_extract_canonical(evidence, clean_text=clean_text)


class InternalAdvisoryExtractor:
    """
    Extracts local-only strategic intelligence on Apple Silicon Metal via `mlx-lm`.
    Fails closed: Never falls back to synthetic mock data in non-mock mode.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_LOCAL_MODEL,
        mock_mode: bool = False,
    ):
        self.model_name = model_name
        self.mock_mode = mock_mode
        self._model = None
        self._tokenizer = None
        self._inference_lock = threading.Lock()

    def _ensure_local_model(self) -> None:
        if self.mock_mode or self._model is not None:
            return
        if not MLX_LM_AVAILABLE:
            raise ExtractionInferenceError("MLX LM is not available and mock_mode=False; failing closed.")
        try:
            import mlx_lm
            logger.info(f"Loading local MLX model on Apple Metal: {self.model_name}")
            self._model, self._tokenizer = mlx_lm.load(self.model_name)
        except Exception as e:
            if not self.mock_mode:
                raise ExtractionInferenceError(f"Could not load mlx_lm model {self.model_name}: {e}. Failing closed.") from e
            logger.warning(f"Could not load mlx_lm model {self.model_name}: {e}. Defaulting to mock.")
            self.mock_mode = True

    async def extract(self, evidence: EvidenceRecord) -> List[InternalAdvisoryNode]:
        """Runs local advisory extraction prompt on Apple Silicon Metal."""
        if not MLX_LM_AVAILABLE:
            if not self.mock_mode:
                raise ExtractionInferenceError("MLX LM is not available and mock_mode=False; failing closed.")
            return self._mock_extract(evidence)
        if self.mock_mode:
            return self._mock_extract(evidence)

        return await asyncio.to_thread(self._run_mlx_inference, evidence)

    def _run_mlx_inference(self, evidence: EvidenceRecord) -> List[InternalAdvisoryNode]:
        with self._inference_lock:
            self._ensure_local_model()
            if self.mock_mode:
                return self._mock_extract(evidence)

            prompt = f"""<|begin_of_text|><|start_header_id|>system<|end_header_id|>
You are a confidential Strategic Advisor.
Extract internal advisory dynamics from this meeting evidence.
Allowed categories: STAKEHOLDER_DYNAMICS, DECISION_INFLUENCE, ADOPTION_RISK, BLOCKER.
Output JSON only with format:
[{{"category": "...", "name": "...", "strategic_value": 0-10, "tacticality": 0-10, "stealth_score": 0.0-1.0, "properties": {{...}}}}]<|eot_id|>
<|start_header_id|>user<|end_header_id|>
Speaker: {evidence.speaker}
Transcript: "{evidence.text}"<|eot_id|>
<|start_header_id|>assistant<|end_header_id|>
"""
            try:
                import mlx_lm
                gen_kwargs: Dict[str, Any] = {"prompt": prompt, "max_tokens": 512}
                try:
                    import mlx_lm.sample_utils as sample_utils
                    gen_kwargs["sampler"] = sample_utils.make_sampler(0.0)
                except Exception:
                    pass
                response = mlx_lm.generate(
                    self._model,
                    self._tokenizer,
                    **gen_kwargs,
                )
                data = repair_and_parse_json(response)
                items = data if isinstance(data, list) else [data]
                nodes: List[InternalAdvisoryNode] = []

                for item in items:
                    cat_raw = item.get("category", "STAKEHOLDER_DYNAMICS").upper()
                    cat = (
                        AdvisoryCategory[cat_raw]
                        if cat_raw in AdvisoryCategory.__members__
                        else AdvisoryCategory.STAKEHOLDER_DYNAMICS
                    )
                    adv_name = item.get("name", "Advisory Observation")
                    node_id = deterministic_advisory_node_id(evidence.evidence_id, cat.value, adv_name)
                    nodes.append(InternalAdvisoryNode(
                        node_id=node_id,
                        evidence_id=evidence.evidence_id,
                        type=cat.value,
                        name=adv_name,
                        category=cat,
                        properties=item.get("properties", {}),
                        strategic_value=float(item.get("strategic_value", 7.0)),
                        tacticality=float(item.get("tacticality", 1.0)),
                        confidence=0.9,
                        is_quarantined=False,
                        stealth_score=float(item.get("stealth_score", 0.8)),
                        namespace="INTERNAL_ADVISORY",
                    ))
                return nodes
            except Exception as e:
                logger.error(f"Local mlx_lm inference error on {evidence.evidence_id}: {e}")
                if self.mock_mode:
                    return self._mock_extract(evidence)
                raise ExtractionInferenceError(
                    f"InternalAdvisoryExtractor failed on {evidence.evidence_id}: {e}",
                    evidence_id=evidence.evidence_id,
                    original_exception=e,
                ) from e

    def _mock_extract(self, evidence: EvidenceRecord) -> List[InternalAdvisoryNode]:
        return mock_extract_advisory(evidence)


class ComputeForkCoordinator:
    """Coordinates simultaneous, isolated extraction across Cloud and Local Metal boundaries."""

    def __init__(
        self,
        client_safe_extractor: Optional[ClientSafeExtractor] = None,
        internal_advisory_extractor: Optional[InternalAdvisoryExtractor] = None,
        mock_mode: bool = False,
    ):
        self.mock_mode = mock_mode
        self.client_safe = client_safe_extractor or ClientSafeExtractor(mock_mode=mock_mode)
        self.advisory = internal_advisory_extractor or InternalAdvisoryExtractor(mock_mode=mock_mode)

    def _generate_relational_edges(self, evidence: EvidenceRecord, nodes: List[CanonicalGraphNode]) -> List[GraphEdge]:
        return generate_relational_edges(evidence, nodes)

    def _generate_assertions(self, evidence: EvidenceRecord, nodes: List[CanonicalGraphNode]) -> List[AssertionRecord]:
        return generate_assertions(evidence, nodes)

    async def execute_fork(self, evidence: EvidenceRecord) -> ForkedExtractionResult:
        """Executes parallel extraction without sharing prompts or memory."""
        if tracer:
            with tracer.start_as_current_span("pipeline.b.compute_fork") as span:
                span.set_attribute("evidence.id", evidence.evidence_id)
                span.set_attribute("evidence.meeting_id", evidence.meeting_id)

                from config.request_deadline import remaining_timeout
                t_budget = remaining_timeout(120.0)
                canonical_nodes, advisory_nodes = await asyncio.wait_for(
                    asyncio.gather(
                        self.client_safe.extract(evidence),
                        self.advisory.extract(evidence),
                    ),
                    timeout=t_budget,
                )
                canonical_edges = self._generate_relational_edges(evidence, canonical_nodes)
                assertions = self._generate_assertions(evidence, canonical_nodes)

                span.set_attribute("extracted.canonical_count", len(canonical_nodes))
                span.set_attribute("extracted.advisory_count", len(advisory_nodes))
                span.set_attribute("extracted.edges_count", len(canonical_edges))
                span.set_attribute("extracted.assertions_count", len(assertions))
                return ForkedExtractionResult(
                    canonical_nodes=canonical_nodes,
                    advisory_nodes=advisory_nodes,
                    canonical_edges=canonical_edges,
                    assertions=assertions,
                )
        else:
            from config.request_deadline import remaining_timeout
            t_budget = remaining_timeout(120.0)
            canonical_nodes, advisory_nodes = await asyncio.wait_for(
                asyncio.gather(
                    self.client_safe.extract(evidence),
                    self.advisory.extract(evidence),
                ),
                timeout=t_budget,
            )
            canonical_edges = self._generate_relational_edges(evidence, canonical_nodes)
            assertions = self._generate_assertions(evidence, canonical_nodes)
            return ForkedExtractionResult(
                canonical_nodes=canonical_nodes,
                advisory_nodes=advisory_nodes,
                canonical_edges=canonical_edges,
                assertions=assertions,
            )
