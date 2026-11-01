"""Structural & Modality Introspector.

Inspects branch files, deliverable manifests (metadata.json, 03_*.json),
document markdown headers, and Python ASTs to determine modality and semantic
payload without relying on fragile branch names or path regexes.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set

from tools.pr_governance_core.domain_models import ModalityScope


@dataclass
class ModalityIntrospectionResult:
    """Detailed structural introspection evidence."""
    modality: ModalityScope
    title: Optional[str] = None
    summary_text: Optional[str] = None
    topics: List[str] = field(default_factory=list)
    evidence_citations: List[str] = field(default_factory=list)
    is_deliverable: bool = False
    source_type: Optional[str] = None
    detected_modules: List[str] = field(default_factory=list)


def _safe_read_json(path: Path) -> Optional[dict]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return None


def _safe_read_text(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return ""


def _is_external_research_context(path_str: str, title: Optional[str], summary: Optional[str], project: Optional[str] = None) -> bool:
    combined = f"{path_str} {title or ''} {summary or ''} {project or ''}".lower()
    research_signals = (
        "market_intelligence",
        "market-intelligence",
        "market intelligence",
        "thought_leadership",
        "thought leadership",
        "market trends",
        "weekly energy",
        "utilities market",
        "regulatory",
        "ferc",
        "fpsc",
        "whitepaper",
        "external research",
    )
    return any(sig in combined for sig in research_signals)


def introspect_deliverable(files: List[str], repo_root: Path) -> Optional[ModalityIntrospectionResult]:
    """Inspect deliverable directories under output/ for metadata.json or 03_*.json."""
    output_files = [f for f in files if f.startswith("output/") and not f.startswith("output/transcript_cache/")]
    if not output_files or (len(output_files) / max(1, len(files))) < 0.5:
        return None

    # Find unique deliverable session directories (depth >= 3, e.g. output/<project>/<date>/<session>)
    dirs = {Path(f).parent for f in output_files if len(Path(f).parent.parts) >= 3}
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        full_d = repo_root / d
        if not full_d.exists():
            continue

        meta_files = [full_d / "metadata.json"] + list(full_d.rglob("metadata.json"))
        for meta_path in meta_files:
            meta = _safe_read_json(meta_path)
            if meta:
                source_type = str(meta.get("source_type", "")).lower()
                canonical_modality = str(meta.get("canonical_modality", "")).upper()
                is_doc = bool(meta.get("is_document", False))
                title = meta.get("title") or meta.get("meeting_name")
                project = str(meta.get("project", ""))

                citations = [f"{meta_path.name}: source_type={source_type}"]
                if is_doc or source_type == "document" or canonical_modality == "DOCUMENT":
                    is_ext = _is_external_research_context(str(d), title, str(meta.get("summary", "")), project)
                    topics = (
                        ["external_research", "market_intelligence", "thought_leadership"]
                        if is_ext
                        else ["client_document", "client_deliverable", "project_charter"]
                    )
                    return ModalityIntrospectionResult(
                        modality=ModalityScope.DOCUMENT_WRITTEN,
                        title=title,
                        summary_text=f"Written document: {title or ('External Research' if is_ext else 'Client Document')}",
                        topics=topics,
                        evidence_citations=citations,
                        is_deliverable=True,
                        source_type="external_research" if is_ext else "client_document",
                    )
                elif source_type in ("audio", "meeting") or canonical_modality in ("AUDIO", "MEETING_TRANSCRIPT") or meta.get("audio_file") or meta.get("meeting_name"):
                    return ModalityIntrospectionResult(
                        modality=ModalityScope.MEETING_SPOKEN,
                        title=title,
                        summary_text=f"Spoken meeting: {title or 'Meeting Session'}",
                        topics=["live_meeting", "audio_transcript", "speech"],
                        evidence_citations=citations,
                        is_deliverable=True,
                        source_type=source_type or "meeting",
                    )

        # Check QA gates report or release decision for mode=document
        decision_files = [full_d / "final_release_decision.json", full_d / "qa_gates_report.json"] + list(full_d.rglob("final_release_decision.json")) + list(full_d.rglob("qa_gates_report.json"))
        for dec_path in decision_files:
            dec = _safe_read_json(dec_path)
            if dec and dec.get("mode") == "document":
                proj_files = list(full_d.glob("*canonical_signal_projection.json")) + list(full_d.rglob("*canonical_signal_projection.json"))
                title = None
                summary_text = ""
                if proj_files:
                    proj = _safe_read_json(proj_files[0])
                    if proj:
                        title = proj.get("meeting_id")
                        summary_text = str(proj.get("_strategic_analysis") or "")
                is_ext = _is_external_research_context(str(d), title, summary_text)
                return ModalityIntrospectionResult(
                    modality=ModalityScope.DOCUMENT_WRITTEN,
                    title=title or d.name.replace("_", " "),
                    summary_text=summary_text or ("External Research Document" if is_ext else "Client Document Deliverable"),
                    topics=["external_research", "market_intelligence"] if is_ext else ["client_document", "client_deliverable"],
                    evidence_citations=[f"{dec_path.name}: mode=document"],
                    is_deliverable=True,
                    source_type="external_research" if is_ext else "client_document",
                )

        # Check 03_*.json
        projection_files = list(full_d.glob("*canonical_signal_projection.json")) + list(full_d.rglob("*canonical_signal_projection.json"))
        if projection_files:
            proj = _safe_read_json(projection_files[0])
            if proj:
                strategic_analysis = proj.get("_strategic_analysis")
                title = proj.get("meeting_id")
                citations = [f"{projection_files[0].name}: projection loaded"]
                if strategic_analysis and _is_external_research_context(str(d), title, str(strategic_analysis)):
                    return ModalityIntrospectionResult(
                        modality=ModalityScope.DOCUMENT_WRITTEN,
                        title=title,
                        summary_text=str(strategic_analysis),
                        topics=["external_research", "market_intelligence"],
                        evidence_citations=citations,
                        is_deliverable=True,
                        source_type="external_research",
                    )

    # Check if output files are external research by path
    if any(_is_external_research_context(f, None, None) for f in output_files):
        first_out = output_files[0]
        return ModalityIntrospectionResult(
            modality=ModalityScope.DOCUMENT_WRITTEN,
            title=Path(first_out).parent.name.replace("_", " "),
            summary_text="External research and market intelligence deliverable",
            topics=["external_research", "market_intelligence"],
            evidence_citations=[Path(first_out).name],
            is_deliverable=True,
            source_type="external_research",
        )

    # Check if output files are spoken meeting transcripts (01_*_live_transcript.md, 01_*_hitl_resolved_transcript.md, 02_*_executive_recap.md)
    if any(
        "live_transcript" in f or "hitl_resolved_transcript" in f or "executive_recap" in f or "copilot_" in f
        for f in output_files
    ):
        return ModalityIntrospectionResult(
            modality=ModalityScope.MEETING_SPOKEN,
            title=Path(output_files[0]).parent.name.replace("_", " "),
            summary_text="Spoken meeting transcript and synthesis deliverables",
            topics=["live_meeting", "audio_transcript", "speech"],
            evidence_citations=[Path(output_files[0]).name],
            is_deliverable=True,
            source_type="meeting",
        )

    # Check if output files are generic client project documents under output/<project>/
    if any(f.endswith(".md") or f.endswith(".json") or f.endswith(".pdf") or f.endswith(".docx") for f in output_files):
        first_out = output_files[0]
        is_ext = _is_external_research_context(first_out, None, None)
        return ModalityIntrospectionResult(
            modality=ModalityScope.DOCUMENT_WRITTEN,
            title=Path(first_out).stem.replace("_", " "),
            summary_text="External research document" if is_ext else "Client project document deliverable",
            topics=["external_research", "market_intelligence"] if is_ext else ["client_document", "client_deliverable"],
            evidence_citations=[Path(first_out).name],
            is_deliverable=True,
            source_type="external_research" if is_ext else "client_document",
        )

    return None


def introspect_markdown_documents(files: List[str], repo_root: Path) -> Optional[ModalityIntrospectionResult]:
    """Inspect documents/ markdown files for external research, client documents, and decision intelligence headers."""
    doc_files = [f for f in files if f.startswith("documents/") and f.endswith(".md")]
    for f in doc_files:
        full_f = repo_root / f
        text = _safe_read_text(full_f)
        if not text:
            if _is_external_research_context(f, None, None):
                return ModalityIntrospectionResult(
                    modality=ModalityScope.DOCUMENT_WRITTEN,
                    title=Path(f).stem.replace("_", " "),
                    summary_text="External research and market intelligence update",
                    topics=["external_research", "market_intelligence"],
                    evidence_citations=[f],
                    is_deliverable=False,
                    source_type="external_research",
                )
            continue

        lines = text.splitlines()
        title = None
        topics = []
        for line in lines[:15]:
            if line.startswith("# "):
                title = line[2:].strip()
                break

        lower_text = text[:2000].lower()
        is_ext = _is_external_research_context(f, title, lower_text)
        if is_ext:
            topics.extend(["external_research", "market_intelligence"])
        if "regulatory" in lower_text or "ferc" in lower_text or "fpsc" in lower_text:
            topics.append("regulatory_finance")
        if "decision intelligence framing" in lower_text and not is_ext:
            topics.append("decision_intelligence")
        if not topics:
            topics.append("client_document")

        citations = [f"{f}: title={title or 'Document'}"]
        return ModalityIntrospectionResult(
            modality=ModalityScope.DOCUMENT_WRITTEN,
            title=title,
            summary_text=text[:500],
            topics=topics,
            evidence_citations=citations,
            is_deliverable=False,
            source_type="external_research" if is_ext else "markdown_document",
        )

    return None


def _extract_ast_imports(py_files: List[str], repo_root: Path) -> Set[str]:
    modules: Set[str] = set()
    for f in py_files:
        full_f = repo_root / f
        text = _safe_read_text(full_f)
        if not text:
            continue
        try:
            tree = ast.parse(text, filename=f)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        modules.add(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module)
        except Exception:
            pass
    return modules


def introspect_python_code(files: List[str], repo_root: Path) -> Optional[ModalityIntrospectionResult]:
    """Inspect Python ASTs to determine code architecture, platform, or governance modality."""
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return None

    modules = _extract_ast_imports(py_files, repo_root)
    mod_str = " ".join(modules).lower()

    # Path heuristics
    has_gov_path = any(any(p in ("tools", ".agents", ".github", "hooks", ".githooks") for p in Path(f).parts) for f in py_files)
    has_plat_path = any(any(p in ("storage", "pipeline_c_retrieval", "database") for p in Path(f).parts) for f in py_files)

    is_governance = has_gov_path or any(k in mod_str for k in ("governance", "pr_priority", "ci_gates"))
    is_platform = has_plat_path or any(k in mod_str for k in ("retrieval", "storage", "sqlite", "bge"))
    is_architecture = any(k in mod_str for k in ("spine", "architecture"))

    if is_governance:
        modality = ModalityScope.CODE_GOVERNANCE
    elif is_architecture:
        modality = ModalityScope.CODE_ARCHITECTURE
    elif is_platform:
        modality = ModalityScope.CODE_PLATFORM
    else:
        modality = ModalityScope.CODE_PLATFORM

    detected = sorted(list(modules))[:10]
    return ModalityIntrospectionResult(
        modality=modality,
        title=f"Code change: {len(py_files)} Python file(s)",
        summary_text=f"Touches {', '.join(py_files[:3])}",
        topics=["python_code", modality.value.lower()],
        evidence_citations=[f"{len(py_files)} py files parsed"],
        is_deliverable=False,
        detected_modules=detected,
    )


def introspect_modality(
    changed_files: Iterable[str],
    repo_root: Optional[Path] = None,
) -> ModalityIntrospectionResult:
    """Execute multi-tier structural introspection over changed files."""
    root = repo_root or Path.cwd()
    files = list(changed_files or [])

    # 1. Deliverables under output/ take precedence for modality detection
    deliv_res = introspect_deliverable(files, root)
    if not deliv_res and (root / ".worktrees").is_dir():
        for wt_dir in (root / ".worktrees").iterdir():
            if wt_dir.is_dir() and not wt_dir.name.startswith("."):
                deliv_res = introspect_deliverable(files, wt_dir)
                if deliv_res:
                    break
    if deliv_res:
        return deliv_res

    # 2. Markdown documents under documents/
    doc_res = introspect_markdown_documents(files, root)
    if doc_res:
        return doc_res

    # 3. Python code AST analysis
    code_res = introspect_python_code(files, root)
    if code_res:
        return code_res

    # Default fallback
    return ModalityIntrospectionResult(
        modality=ModalityScope.UNKNOWN,
        title="Unknown Change Set",
        summary_text="",
        topics=[],
        evidence_citations=["No structural modality matched"],
        is_deliverable=False,
    )
