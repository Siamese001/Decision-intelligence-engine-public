import time
from typing import Any, Dict, List

from dual_pipeline.grounding.chunking_engine import ChunkingEngine
from dual_pipeline.grounding.colbert_maxsim_auditor import ColbertMaxSimAuditor
from dual_pipeline.validation.graph_topology_filter import GraphTopologyFilter
from dual_pipeline.validation.inverse_coverage_auditor import InverseCoverageAuditor
from dual_pipeline.validation.nli_contradiction_gate import NLIContradictionGate


class MasterVectorEvaluator:
    """
    Unified evaluator replacing legacy LLM judges.
    Executes entirely using deterministic AST regexes and BGE-M3/ColBERT semantic vector evaluation.
    Zero generative tokens used.
    """
    def __init__(self, active_graph_edges=None, mock=False):
        if mock:
            raise ValueError("MOCK_VECTORS_PROHIBITED: MasterVectorEvaluator does not permit mock vector evaluation.")
        self.mock = False
        self.chunking_engine = ChunkingEngine()
        self.grounding_auditor = ColbertMaxSimAuditor(threshold=0.72)
        self.coverage_auditor = InverseCoverageAuditor(coverage_threshold=0.90)
        self.topology_filter = GraphTopologyFilter(active_graph_edges)
        self.contradiction_gate = NLIContradictionGate()

    def _check_deterministic_regexes(self, text: str) -> bool:
        """Deterministic AST regexes (C-19 firewall, PII, monotonic timestamps)."""
        from dual_pipeline.pii_scanner import scan_pii
        findings = scan_pii(text)
        return len(findings) == 0

    def verify_pii_hygiene(self, text: str) -> bool:
        """Verifies text passes C-19 and PII hygiene without leakage."""
        return self._check_deterministic_regexes(text)

    def evaluate_release(self,
                         raw_transcript: str,
                         recap_text: str,
                         candidate_claims: List[Dict[str, Any]],
                         active_historical_facts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Orchestrates the entire evaluation suite.
        Returns final verdicts and token usage (always 0).
        """
        start_time = time.time()

        # 1. Deterministic Checks
        if not self._check_deterministic_regexes(raw_transcript):
            return {"passed": False, "reason": "Regex/PII failure", "generative_tokens_used": 0}

        # 2. Chunking & Grounding (Wave 2)
        transcript_chunks = self.chunking_engine.chunk(raw_transcript)
        coarse_texts = [c["text"] for c in transcript_chunks["coarse_chunks"]]

        grounded_claims = []
        for claim in candidate_claims:
            res = self.grounding_auditor.audit_claim(claim.get("text", ""), coarse_texts)
            if not res["grounded"]:
                return {"passed": False, "reason": f"Claim not grounded: {claim.get('text')}", "generative_tokens_used": 0}
            grounded_claims.append(claim)

        # 3. Topology Filter & Contradiction Gate (Wave 3)
        valid_active_facts = self.topology_filter.filter_active_facts(active_historical_facts)
        for claim in grounded_claims:
            conflict_res = self.contradiction_gate.evaluate_conflict(valid_active_facts, claim.get("text", ""))
            if conflict_res["has_contradiction"]:
                return {"passed": False, "reason": f"Contradicts active fact: {conflict_res['conflicting_fact']}", "generative_tokens_used": 0}

        # 4. Inverse Coverage Auditor (Wave 4)
        recap_sentences = recap_text.split(".") # Simple proxy
        recap_sentences = [s.strip() for s in recap_sentences if len(s.strip()) > 5]

        coverage_res = self.coverage_auditor.audit_coverage(transcript_chunks["turns"], recap_sentences)
        if not coverage_res["passed"]:
            return {"passed": False, "reason": "Insufficient strategic coverage in recap", "generative_tokens_used": 0}

        elapsed = time.time() - start_time

        return {
            "passed": True,
            "reason": "All vector gates passed",
            "generative_tokens_used": 0,
            "evaluation_time_ms": elapsed * 1000,
            "grounded_claims_count": len(grounded_claims)
        }
