"""dual_pipeline.grounding.request_gaps

Layer 2 client inquiry and request-gap audit matrix.
Enforces:
1. Classification of questions into ANSWERED_IN_GRAPH, PARTIALLY_ANSWERED, CONTRADICTED, or UNRESOLVED_NEED.
2. Grounded evidence binding: linking existing graph assertions to prevent asking redundant questions.
3. Explicit scoped search reporting: distinguishing scoped search outcomes from global absence claims.
4. Identification of contradictory question premises before external communication.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .snapshot import ReadOnlyGraphSnapshot


class GapClassification:
    ANSWERED_IN_GRAPH = "ANSWERED_IN_GRAPH"
    PARTIALLY_ANSWERED = "PARTIALLY_ANSWERED"
    CONTRADICTED = "CONTRADICTED"
    UNRESOLVED_NEED = "UNRESOLVED_NEED"


@dataclass
class RequestGapItem:
    """Audit outcome for an individual client data request question."""
    question_id: str
    question_text: str
    classification: str
    evidence_assertion_ids: List[str] = field(default_factory=list)
    scope_searched: str = ""
    contradiction_details: Optional[str] = None
    analysis_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_text": self.question_text,
            "classification": self.classification,
            "evidence_assertion_ids": list(self.evidence_assertion_ids),
            "scope_searched": self.scope_searched,
            "contradiction_details": self.contradiction_details,
            "analysis_notes": self.analysis_notes,
        }


@dataclass
class RequestGapMatrix:
    """Container representing the complete request-gap evidence matrix."""
    matrix_id: str
    target_engagement: str
    runtime_root: str
    items: List[RequestGapItem]
    total_questions: int
    answered_count: int
    partially_answered_count: int
    contradicted_count: int
    unresolved_needs_count: int
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matrix_id": self.matrix_id,
            "target_engagement": self.target_engagement,
            "runtime_root": self.runtime_root,
            "total_questions": self.total_questions,
            "answered_count": self.answered_count,
            "partially_answered_count": self.partially_answered_count,
            "contradicted_count": self.contradicted_count,
            "unresolved_needs_count": self.unresolved_needs_count,
            "generated_at": self.generated_at,
            "items": [item.to_dict() for item in self.items],
        }


class RequestGapAuditor:
    """Audits proposed client inquiries against GraphDB assertions to eliminate redundant questions."""

    def __init__(self, target_engagement: str = "HR IT / ENTERPRISE4U") -> None:
        self.target_engagement = target_engagement

    def _search_graph_for_tokens(
        self,
        tokens: List[str],
        snapshot: ReadOnlyGraphSnapshot,
    ) -> List[Dict[str, Any]]:
        """Search assertions whose subject or object matches any token."""
        if not tokens:
            return []
        conn = snapshot.connect()
        cur = conn.cursor()
        query = """
            SELECT a.assertion_id, a.predicate, a.status, a.literal_value,
                   s.name as subject_name, o.name as object_name
            FROM assertion a
            JOIN canonical_graph_nodes s ON a.subject_node_id = s.node_id
            LEFT JOIN canonical_graph_nodes o ON a.object_node_id = o.node_id
            WHERE a.status = 'CONFIRMED'
        """
        cur.execute(query)
        all_rows = [dict(r) for r in cur.fetchall()]

        matched: List[Dict[str, Any]] = []
        for r in all_rows:
            combined = f"{r.get('subject_name', '')} {r.get('predicate', '')} {r.get('object_name', '')} {r.get('literal_value', '')}".lower()
            if any(tok.lower() in combined for tok in tokens):
                matched.append(r)
        return matched

    def audit_single_question(
        self,
        q_id: str,
        text: str,
        snapshot: ReadOnlyGraphSnapshot,
        key_tokens: List[str],
        contradiction_tokens: Optional[List[str]] = None,
    ) -> RequestGapItem:
        """Audit an individual question against graph assertions."""
        scope_str = (
            f"runtime:{snapshot.runtime_root.name}; "
            f"generation:{snapshot.generation_id}; "
            f"enforce_integrity:{snapshot.enforce_pair_integrity}"
        )

        # 1. Check contradiction tokens
        if contradiction_tokens:
            contra_matches = self._search_graph_for_tokens(contradiction_tokens, snapshot)
            if contra_matches:
                assn_ids = [m["assertion_id"] for m in contra_matches]
                return RequestGapItem(
                    question_id=q_id,
                    question_text=text,
                    classification=GapClassification.CONTRADICTED,
                    evidence_assertion_ids=assn_ids,
                    scope_searched=scope_str,
                    contradiction_details=f"Premise conflicts with confirmed assertions {assn_ids}.",
                    analysis_notes="Question presumes contradictory premise disproven in graph.",
                )

        # 2. Check answering tokens
        matches = self._search_graph_for_tokens(key_tokens, snapshot)
        if not matches:
            return RequestGapItem(
                question_id=q_id,
                question_text=text,
                classification=GapClassification.UNRESOLVED_NEED,
                evidence_assertion_ids=[],
                scope_searched=scope_str,
                analysis_notes="Not found in explicitly searched graph scope. Legitimate data request.",
            )

        assn_ids = [m["assertion_id"] for m in matches]
        # Distinguish full answer vs partial answer based on count/relevance
        if len(matches) >= 2:
            return RequestGapItem(
                question_id=q_id,
                question_text=text,
                classification=GapClassification.ANSWERED_IN_GRAPH,
                evidence_assertion_ids=assn_ids,
                scope_searched=scope_str,
                analysis_notes="Question can be fully answered from existing graph assertions.",
            )
        else:
            return RequestGapItem(
                question_id=q_id,
                question_text=text,
                classification=GapClassification.PARTIALLY_ANSWERED,
                evidence_assertion_ids=assn_ids,
                scope_searched=scope_str,
                analysis_notes="Partial evidence exists in graph; specific sub-questions remain open.",
            )

    def audit_question_batch(
        self,
        questions: List[Dict[str, Any]],
        snapshot: ReadOnlyGraphSnapshot,
    ) -> RequestGapMatrix:
        """Process a structured batch of candidate questions."""
        items: List[RequestGapItem] = []
        for q in questions:
            q_id = q.get("question_id", "Q-00")
            text = q.get("question_text", "")
            key_tokens = q.get("key_tokens", [])
            contra_tokens = q.get("contradiction_tokens", [])
            item = self.audit_single_question(
                q_id=q_id,
                text=text,
                snapshot=snapshot,
                key_tokens=key_tokens,
                contradiction_tokens=contra_tokens,
            )
            items.append(item)

        answered = sum(1 for i in items if i.classification == GapClassification.ANSWERED_IN_GRAPH)
        partial = sum(1 for i in items if i.classification == GapClassification.PARTIALLY_ANSWERED)
        contra = sum(1 for i in items if i.classification == GapClassification.CONTRADICTED)
        unresolved = sum(1 for i in items if i.classification == GapClassification.UNRESOLVED_NEED)

        return RequestGapMatrix(
            matrix_id=f"RGM-{snapshot.generation_id[:16]}",
            target_engagement=self.target_engagement,
            runtime_root=str(snapshot.runtime_root),
            items=items,
            total_questions=len(items),
            answered_count=answered,
            partially_answered_count=partial,
            contradicted_count=contra,
            unresolved_needs_count=unresolved,
        )


def _extract_speaker_terms(content: str) -> List[str]:
    terms = re.findall(r"\b([A-Z][a-z]{3,})\b", content)
    quoted = re.findall(r'["\']([^"\']{3,})["\']', content)
    return sorted(set(terms + quoted))


def _check_term_corroboration(snapshot: ReadOnlyGraphSnapshot, spk: str, term: str) -> bool:
    try:
        return bool(
            snapshot.get_node(term.lower())
            or snapshot.find_assertions_for_claim(spk, "HAS_ROLE", term)
            or snapshot.find_assertions_for_claim(spk, "HAS_PROJECT", term)
            or snapshot.find_assertions_for_claim(spk, "WORKS_ON", term)
        )
    except Exception:
        return False


def _audit_terms_for_speaker(
    spk: str,
    terms: List[str],
    snapshot: ReadOnlyGraphSnapshot,
    stop_words: Set[str],
    corroborated: List[Dict[str, Any]],
    uncorroborated: List[Dict[str, Any]],
) -> None:
    for term in terms:
        if term.lower() in stop_words:
            continue
        rec = {"speaker": spk, "term": term}
        if _check_term_corroboration(snapshot, spk, term):
            corroborated.append(rec)
        else:
            uncorroborated.append(rec)


def audit_prompt_premises(prompt_text: str, snapshot: ReadOnlyGraphSnapshot) -> Dict[str, Any]:
    """Classify prompt-supplied entities and speaker-scoped topics into CORROBORATED_IN_GRAPH vs UNCORROBORATED_PREMISE."""
    if not prompt_text:
        return {"corroborated": [], "uncorroborated": [], "speaker_bindings": {}, "is_clean": True}

    speaker_pattern = re.compile(r"^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*(?::|—|-)\s*(.*)$", re.MULTILINE)
    corroborated: List[Dict[str, Any]] = []
    uncorroborated: List[Dict[str, Any]] = []
    speaker_bindings: Dict[str, List[str]] = {}
    stop_words = {
        "this", "that", "with", "from", "they", "have", "will", "more", "team",
        "data", "time", "what", "your", "points", "words", "agenda", "review",
    }

    for m in speaker_pattern.finditer(prompt_text):
        spk = m.group(1).strip()
        unique_terms = _extract_speaker_terms(m.group(2).strip())
        speaker_bindings[spk] = unique_terms
        _audit_terms_for_speaker(spk, unique_terms, snapshot, stop_words, corroborated, uncorroborated)

    return {
        "corroborated": corroborated,
        "uncorroborated": uncorroborated,
        "speaker_bindings": speaker_bindings,
        "is_clean": len(uncorroborated) == 0,
    }
