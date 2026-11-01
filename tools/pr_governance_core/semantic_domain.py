"""Dense Semantic Domain Classifier.

Projects branch semantic payloads (title, summary, topics) into dense
vector space and computes cosine similarity against canonical Domain
Archetype Centroids, applying modality constraints and 15% ambiguity margin gating.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tools.pr_governance_core.domain_models import (
    ConfidenceTier,
    DomainClassificationEnvelope,
    ModalityScope,
)

_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "pr_domain_taxonomy.json"


def _tokenize(text: str) -> List[str]:
    """Extract normalized alphanumeric word stems and tokens."""
    return [w.lower() for w in re.findall(r"[A-Za-z0-9]+", text) if len(w) > 1]


class SemanticDomainClassifier:
    """Multi-dimensional semantic classifier against canonical domain centroids."""

    def __init__(self, taxonomy_path: Optional[Path] = None):
        self.taxonomy_path = taxonomy_path or _TAXONOMY_PATH
        self.domains: Dict[str, dict] = {}
        self.centroids: Dict[str, Dict[str, float]] = {}
        self.idf: Dict[str, float] = {}
        self.modality_map: Dict[str, ModalityScope] = {}
        self._load_and_train()

    def _load_and_train(self) -> None:
        if not self.taxonomy_path.exists():
            return
        tax = json.loads(self.taxonomy_path.read_text(encoding="utf-8"))
        raw_domains = tax.get("domains", [])

        corpus: List[List[str]] = []
        domain_tokens_map: Dict[str, List[str]] = {}

        for d in raw_domains:
            label = d["label"]
            self.domains[label] = d

            # Track modality
            mod_str = d.get("modality", "UNKNOWN")
            self.modality_map[label] = (
                ModalityScope(mod_str)
                if mod_str in ModalityScope.__members__
                else ModalityScope.UNKNOWN
            )

            # Build semantic anchor document for this domain
            doc_parts = [
                d.get("description", ""),
                " ".join(d.get("name_keywords", [])),
                " ".join(d.get("semantic_anchors", [])),
            ]
            tokens = _tokenize(" ".join(doc_parts))
            domain_tokens_map[label] = tokens
            corpus.append(tokens)

        # Compute document frequency & IDF across all domains
        n_docs = max(1, len(corpus))
        df: Dict[str, int] = {}
        for doc in corpus:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1

        self.idf = {term: math.log((n_docs + 1) / (count + 1)) + 1.0 for term, count in df.items()}

        # Build normalized TF-IDF centroid vector for each domain
        for label, tokens in domain_tokens_map.items():
            self.centroids[label] = self._vectorize(tokens)

    def _vectorize(self, tokens: List[str]) -> Dict[str, float]:
        """Convert token stream to normalized TF-IDF vector."""
        tf: Dict[str, float] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0.0) + 1.0

        vec: Dict[str, float] = {}
        norm_sq = 0.0
        for t, count in tf.items():
            weight = (1.0 + math.log(count)) * self.idf.get(t, 1.0)
            vec[t] = weight
            norm_sq += weight * weight

        norm = math.sqrt(norm_sq) or 1.0
        return {t: w / norm for t, w in vec.items()}

    def _cosine_similarity(self, vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
        """Compute cosine similarity between two unit vectors."""
        common = set(vec_a.keys()).intersection(vec_b.keys())
        return sum(vec_a[k] * vec_b[k] for k in common)

    def classify(
        self,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        topics: Optional[List[str]] = None,
        modality: ModalityScope = ModalityScope.UNKNOWN,
    ) -> DomainClassificationEnvelope:
        """Classify branch semantic context against domain centroids with 15% ambiguity gating."""
        if not self.centroids:
            return DomainClassificationEnvelope(domain="Enhancement", modality=modality, confidence=0.5)

        # Build query vector
        query_text = f"{title or ''} {summary or ''} {' '.join(topics or [])}"
        tokens = _tokenize(query_text)
        if not tokens:
            return DomainClassificationEnvelope(domain="Enhancement", modality=modality, confidence=0.5)

        q_vec = self._vectorize(tokens)

        # Calculate scores with modality masking
        scores: List[Tuple[str, float]] = []
        for label, centroid in self.centroids.items():
            domain_mod = self.modality_map.get(label, ModalityScope.UNKNOWN)

            # Modality compatibility check:
            # If modality is strictly DOCUMENT_WRITTEN, MEETING_SPOKEN domains are disqualified
            if modality == ModalityScope.DOCUMENT_WRITTEN and domain_mod == ModalityScope.MEETING_SPOKEN:
                continue
            if modality == ModalityScope.MEETING_SPOKEN and domain_mod == ModalityScope.DOCUMENT_WRITTEN:
                continue

            sim = self._cosine_similarity(q_vec, centroid)
            scores.append((label, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        if not scores or scores[0][1] <= 0.0:
            # Fallback if no term overlap
            return DomainClassificationEnvelope(domain="Enhancement", modality=modality, confidence=0.5)

        top_domain, top_score = scores[0]
        second_domain, second_score = scores[1] if len(scores) > 1 else ("", 0.0)

        # Calibrate raw score into confidence in [0.70, 0.98]
        calibrated_conf = min(0.98, max(0.70, 0.70 + (top_score * 0.35)))

        margin = top_score - second_score
        secondary = None
        ambiguity_margin = None

        if len(scores) > 1 and margin < 0.15:
            # Ambiguity boundary: top 2 are within 15%
            ambiguity_margin = round(margin, 3)
            secondary = second_domain
            rationale = (
                f"Classified as '{top_domain}' (score={top_score:.2f}) with close secondary affinity "
                f"to '{second_domain}' (score={second_score:.2f}, Δ={margin:.2f} < 0.15 margin)."
            )
        else:
            rationale = (
                f"Decisively classified as '{top_domain}' with semantic score {top_score:.2f} "
                f"(decisive margin Δ={margin:.2f} >= 0.15)."
            )

        citations = [f"top_centroid_score={top_score:.3f}"]
        if title:
            citations.append(f"title='{title}'")

        tier = ConfidenceTier.HIGH if calibrated_conf >= 0.85 else ConfidenceTier.MEDIUM

        return DomainClassificationEnvelope(
            domain=top_domain,
            sub_domain=topics[0] if topics else None,
            modality=modality,
            confidence=round(calibrated_conf, 3),
            confidence_tier=tier,
            rationale=rationale,
            evidence_citations=citations,
            secondary_domain=secondary,
            ambiguity_margin=ambiguity_margin,
            raw_scores={d: round(s, 3) for d, s in scores[:5]},
        )
