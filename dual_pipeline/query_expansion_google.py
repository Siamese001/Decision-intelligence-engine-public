"""Google Gemini Flash provider for post-runtime query expansion with cosine similarity gating."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from typing import Any, List, Optional, Sequence

from dual_pipeline.transcript_stages_core.external_synthesizer import (
    call_gemini_generate_content,
    clean_json_markdown,
)

logger = logging.getLogger(__name__)

QUERY_EXPANSION_SYSTEM_PROMPT = (
    "You are an expert retrieval optimization engine. Given a user query, generate 3 to 5 "
    "diverse, semantically aligned alternate query phrasings to improve recall across a knowledge graph. "
    "Maintain the core technical and strategic intent. "
    "Return a JSON object with a single key 'expansions' containing an array of strings."
)


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if len(v1) != len(v2) or len(v1) == 0:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a, b in zip(v1, v2)))  # self dot
    norm2 = math.sqrt(sum(b * b for a, b in zip(v1, v2)))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def compute_deterministic_vector(text: str, dim: int = 128) -> List[float]:
    """Compute normalized n-gram hash vector for deterministic offline similarity."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    tokens = [t for t in cleaned.split() if t]
    vec = [0.0] * dim

    # Word-level features
    for token in tokens:
        idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        vec[idx] += 2.0

    # Character trigrams for morphological robustness
    if len(cleaned) >= 3:
        for i in range(len(cleaned) - 2):
            trigram = cleaned[i : i + 3]
            idx = int(hashlib.md5(trigram.encode("utf-8")).hexdigest(), 16) % dim
            vec[idx] += 0.5

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        return [x / norm for x in vec]
    return vec


class GoogleQueryExpansionProvider:
    """Post-runtime query expansion engine powered by Gemini Flash with cosine quality gating."""

    def __init__(
        self,
        model: Optional[str] = None,
        similarity_threshold: float = 0.85,
        max_variants: int = 5,
        embedding_backend: Optional[Any] = None,
        deadline_seconds: float = 15.0,
    ) -> None:
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
        self.model = model or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH)
        self.similarity_threshold = similarity_threshold
        self.max_variants = max_variants
        self.embedding_backend = embedding_backend
        self.deadline_seconds = deadline_seconds
        self.last_latency_ms: float = 0.0
        self.last_ttft_ms: float = 0.0

    def _embed(self, text: str) -> List[float]:
        """Generate embedding vector using BGE-M3 backend if provided, otherwise deterministic vector."""
        if self.embedding_backend is not None and hasattr(self.embedding_backend, "encode_dense"):
            dense = self.embedding_backend.encode_dense([text])
            return list(dense[0])
        return compute_deterministic_vector(text)

    def expand_query(self, query: str, count: int = 3) -> List[str]:
        """Expand user query into filtered alternate phrasings meeting cosine similarity threshold."""
        query_clean = query.strip()
        if not query_clean:
            return []

        start_time = time.perf_counter()
        user_content = f"Target Query: {query_clean}\nGenerate {min(self.max_variants, max(3, count))} diverse reformulations."

        try:
            raw_response = call_gemini_generate_content(
                model=self.model,
                system_prompt=QUERY_EXPANSION_SYSTEM_PROMPT,
                user_content=user_content,
                json_mode=True,
                deadline_seconds=self.deadline_seconds,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            self.last_latency_ms = elapsed_ms
            # In single-turn API, TTFT approximates initial token arrival (~40% of completion time)
            self.last_ttft_ms = elapsed_ms * 0.40

            cleaned_json = clean_json_markdown(raw_response)
            parsed = json.loads(cleaned_json)
            expansions = parsed.get("expansions", [])
            if not isinstance(expansions, list):
                logger.warning("Query expansion returned non-list expansions: %s", parsed)
                return []

            # Cosine similarity filter (threshold >= self.similarity_threshold)
            query_vec = self._embed(query_clean)
            filtered_expansions: List[str] = []

            for exp in expansions:
                if not isinstance(exp, str) or not exp.strip():
                    continue
                cand_clean = exp.strip()
                if cand_clean.lower() == query_clean.lower():
                    continue
                cand_vec = self._embed(cand_clean)
                sim = cosine_similarity(query_vec, cand_vec)
                if sim >= self.similarity_threshold:
                    filtered_expansions.append(cand_clean)
                else:
                    logger.debug(
                        "Discarded expansion '%s' due to low cosine similarity (%.3f < %.3f)",
                        cand_clean,
                        sim,
                        self.similarity_threshold,
                    )

            return filtered_expansions[:count]
        except Exception as exc:
            self.last_latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning("Google query expansion failed: %s", exc)
            return []
