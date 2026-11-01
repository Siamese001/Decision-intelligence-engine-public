"""Human-facing Domain label for PR queue rows (config: config/pr_domain_taxonomy.json).

Independent of the P-level rubric: the rubric decides *priority*, this decides *what the branch is about*.
Resolution Order (Multi-Tier Domain Intelligence Agent):
1. Archive prefix -> Archive
2. Cached classification envelope hit (reports/domain_classification_cache.json)
3. Structural & Modality Introspection (metadata.json, 03_*.json, AST analysis)
4. Dense Semantic Domain Centroid Projection (BGE-M3 / TF-IDF Vector Space)
5. Branch-name keywords & dominant changed paths
6. Conventional-commit prefix fallback
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tools.pr_governance_core.domain_cache import (
    load_cached_envelope,
    store_cached_envelope,
)
from tools.pr_governance_core.domain_models import (
    ConfidenceTier,
    DomainClassificationEnvelope,
    ModalityScope,
)
from tools.pr_governance_core.modality_introspector import introspect_modality
from tools.pr_governance_core.semantic_domain import SemanticDomainClassifier

_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "config" / "pr_domain_taxonomy.json"


@lru_cache(maxsize=4)
def load_taxonomy(path: Optional[str] = None) -> Dict[str, Any]:
    target = Path(path) if path else _TAXONOMY_PATH
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _tokens(branch: str) -> List[str]:
    return [t for t in re.split(r"[/\-_.]+", branch.lower()) if t]


def _relevant(paths: Iterable[str], ignored: List[str]) -> List[str]:
    return [p for p in paths if p and not any(p.startswith(i) for i in ignored)]


def _deliverable(paths: List[str], spec: Dict[str, Any]) -> bool:
    if not paths or not spec:
        return False
    hits = [
        p for p in paths
        if any(p.startswith(x) for x in spec.get("path_prefixes", []))
        and not any(p.startswith(x) for x in spec.get("excluded_prefixes", []))
    ]
    return len(hits) / len(paths) >= float(spec.get("min_share", 0.5))


def _by_name(tokens: List[str], domains: List[Dict[str, Any]]) -> Optional[str]:
    token_set = set(tokens[1:]) if len(tokens) > 1 else set(tokens)  # skip conventional prefix (feat/fix/...)
    for d in domains:
        if token_set.intersection(d.get("name_keywords", [])):
            return str(d["label"])
    return None


def _by_paths(paths: List[str], domains: List[Dict[str, Any]]) -> Optional[str]:
    best, best_n = None, 0
    for d in domains:
        n = sum(1 for p in paths if any(p.startswith(x) for x in d.get("path_prefixes", [])))
        if n > best_n:
            best, best_n = str(d["label"]), n
    return best


def _resolve_deliverable_envelope(
    spec: Dict[str, Any],
    intro: Any,
    classifier: SemanticDomainClassifier,
) -> DomainClassificationEnvelope:
    modality_labels = spec.get("modality_labels", {})
    source_type = getattr(intro, "source_type", None) or ""
    if source_type == "external_research":
        return DomainClassificationEnvelope(
            domain=modality_labels.get("EXTERNAL_RESEARCH", "External Research"),
            modality=ModalityScope.DOCUMENT_WRITTEN,
            confidence=0.95,
            rationale="External research and market intelligence deliverable",
            evidence_citations=getattr(intro, "evidence_citations", []),
        )
    if source_type == "client_document":
        return DomainClassificationEnvelope(
            domain=modality_labels.get("DOCUMENT_WRITTEN", "Client Document"),
            modality=ModalityScope.DOCUMENT_WRITTEN,
            confidence=0.95,
            rationale="Written client document deliverable",
            evidence_citations=getattr(intro, "evidence_citations", []),
        )
    if intro.modality == ModalityScope.DOCUMENT_WRITTEN:
        env = classifier.classify(
            title=intro.title,
            summary=intro.summary_text,
            topics=intro.topics,
            modality=ModalityScope.DOCUMENT_WRITTEN,
        )
        if env.domain in ("Enhancement", "Live Meeting", "Meeting Deliverable"):
            env.domain = modality_labels.get("DOCUMENT_WRITTEN", "Client Document")
        return env
    elif intro.modality == ModalityScope.MEETING_SPOKEN:
        return DomainClassificationEnvelope(
            domain=modality_labels.get("MEETING_SPOKEN", "Live Meeting"),
            modality=ModalityScope.MEETING_SPOKEN,
            confidence=0.95,
            rationale="Spoken meeting deliverable with audio/transcript assets",
            evidence_citations=intro.evidence_citations,
        )
    default_lbl = str(spec.get("default_label", "Live Meeting"))
    return DomainClassificationEnvelope(
        domain=default_lbl,
        modality=intro.modality,
        confidence=0.85,
        rationale="Default deliverable majority path",
    )


def _resolve_fallback_envelope(
    name: str,
    paths: List[str],
    tax: Dict[str, Any],
    intro: Any,
) -> DomainClassificationEnvelope:
    tokens = _tokens(name)
    domains = list(tax.get("domains", []))
    if any(t in tokens for t in ("pr", "prq", "queue", "observability", "governance", "hook", "hooks", "ci")):
        domains = [d for d in domains if d.get("label") == "Governance"] + [
            d for d in domains if d.get("label") != "Governance"
        ]
    elif "rca" in tokens:
        domains = [d for d in domains if d.get("label") != "Live Meeting"] + [
            d for d in domains if d.get("label") == "Live Meeting"
        ]
    label = _by_name(tokens, domains) or _by_paths(paths, domains)
    if label:
        return DomainClassificationEnvelope(
            domain=label,
            modality=intro.modality,
            confidence=0.80,
            confidence_tier=ConfidenceTier.MEDIUM,
            rationale="Resolved via deterministic domain taxonomy keywords/paths",
        )
    prefix = name.split("/", 1)[0].lower() if "/" in name else ""
    fallback_label = str(tax.get("prefix_fallback", {}).get(prefix, tax.get("default_label", "Enhancement")))
    return DomainClassificationEnvelope(
        domain=fallback_label,
        modality=intro.modality,
        confidence=0.60,
        confidence_tier=ConfidenceTier.LOW,
        rationale=f"Fallback resolved from conventional branch prefix '{prefix}'",
    )


def classify_domain_envelope(
    branch: str,
    changed_files: Iterable[str],
    taxonomy: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
    use_cache: bool = True,
) -> DomainClassificationEnvelope:
    """Classify branch domain using the multi-tier Domain Intelligence Agent."""
    name = (branch or "").strip()
    files = list(changed_files or [])
    root = repo_root or Path.cwd()

    if taxonomy is not None and not taxonomy:
        return DomainClassificationEnvelope(
            domain="—",
            modality=ModalityScope.UNKNOWN,
            confidence=0.0,
            rationale="Empty taxonomy",
        )

    if use_cache and taxonomy is None:
        cached = load_cached_envelope(name, files)
        if cached:
            return cached

    tax = taxonomy if taxonomy is not None else load_taxonomy()
    if not tax:
        return DomainClassificationEnvelope(
            domain="—",
            modality=ModalityScope.UNKNOWN,
            confidence=0.0,
            rationale="Missing taxonomy",
        )
    if name.startswith("stash@"):
        env = DomainClassificationEnvelope(
            domain="Housekeeping",
            modality=ModalityScope.UNKNOWN,
            confidence=1.0,
            rationale="Git stash entry",
        )
        if use_cache and taxonomy is None:
            store_cached_envelope(name, files, env)
        return env

    if any(name.startswith(p) for p in tax.get("archive_prefixes", [])):
        env = DomainClassificationEnvelope(
            domain=str(tax.get("archive_label", "Archive")),
            modality=ModalityScope.UNKNOWN,
            confidence=1.0,
            rationale="Archive branch prefix matched",
        )
        if use_cache and taxonomy is None:
            store_cached_envelope(name, files, env)
        return env

    paths = _relevant(files, tax.get("ignored_path_prefixes", []))
    spec = tax.get("deliverable", {})
    intro = introspect_modality(paths, root)
    if taxonomy is not None:
        return _resolve_fallback_envelope(name, paths, tax, intro)

    classifier = SemanticDomainClassifier(taxonomy_path=_TAXONOMY_PATH)

    if _deliverable(paths, spec):
        env = _resolve_deliverable_envelope(spec, intro, classifier)
        if use_cache:
            store_cached_envelope(name, files, env)
        return env

    if intro.modality == ModalityScope.DOCUMENT_WRITTEN:
        if getattr(intro, "source_type", None) == "external_research":
            env = DomainClassificationEnvelope(
                domain="External Research",
                modality=ModalityScope.DOCUMENT_WRITTEN,
                confidence=0.95,
                rationale="External research and market intelligence markdown document",
                evidence_citations=intro.evidence_citations,
            )
        else:
            env = classifier.classify(
                title=intro.title,
                summary=intro.summary_text,
                topics=intro.topics,
                modality=ModalityScope.DOCUMENT_WRITTEN,
            )
        if use_cache:
            store_cached_envelope(name, files, env)
        return env

    tokens = _tokens(name)
    if intro.modality == ModalityScope.CODE_GOVERNANCE or any(
        p.startswith(("tools/pr_", ".agents/", ".githooks/", "governance/")) for p in paths
    ):
        env = _resolve_fallback_envelope(name, paths, tax, intro)
        if env.domain in ("Governance", "Testing"):
            if use_cache:
                store_cached_envelope(name, files, env)
            return env

    semantic_env = classifier.classify(
        title=name,
        summary=" ".join(tokens),
        topics=tokens,
        modality=intro.modality,
    )
    if "rca" in tokens and semantic_env.domain == "Live Meeting":
        semantic_env.confidence = 0.0
    if semantic_env.confidence >= 0.80 and semantic_env.domain not in ("Enhancement", "Housekeeping"):
        if use_cache:
            store_cached_envelope(name, files, semantic_env)
        return semantic_env

    env = _resolve_fallback_envelope(name, paths, tax, intro)
    if use_cache:
        store_cached_envelope(name, files, env)
    return env


def classify_domain(
    branch: str,
    changed_files: Iterable[str],
    taxonomy: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> str:
    """Return a single high-signal Domain label (e.g. 'Thought Leadership', 'Live Meeting', 'Governance')."""
    envelope = classify_domain_envelope(branch, changed_files, taxonomy, repo_root=repo_root)
    return envelope.domain
