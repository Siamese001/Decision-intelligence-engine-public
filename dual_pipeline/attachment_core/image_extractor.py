
"""
Multi-modal image, diagram, and visual attachment content extractors.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.model_registry import ModelRegistry, ModelTier

logger = logging.getLogger(__name__)


def _build_svg_components(text_nodes: List[str]) -> List[Dict[str, Any]]:
    components = []
    for idx, txt in enumerate(text_nodes, 1):
        clean_txt = re.sub(r"\s+", " ", txt)
        if len(clean_txt) >= 2 and not clean_txt.isdigit():
            clean_ref = re.sub(r"[^\w\-]+", "_", clean_txt.lower()).strip("_")
            components.append({
                "component_ref": f"comp-{idx:02d}-{clean_ref[:24]}",
                "label": clean_txt,
                "type": "system_component",
            })
    return components


def _build_svg_connections(components: List[Dict[str, Any]], lines: List[Any]) -> List[Dict[str, Any]]:
    if len(components) < 2 or not lines:
        return []
    connections = []
    max_conns = min(len(components) - 1, len(lines))
    for idx in range(max_conns):
        connections.append({
            "from_ref": components[idx]["component_ref"],
            "to_ref": components[idx + 1]["component_ref"],
            "predicate": "CALLS" if idx % 2 == 0 else "DEPENDS_ON",
        })
    return connections


def _extract_svg_diagram(content_bytes: bytes) -> Dict[str, Any]:
    """Extracts text labels, shapes, and directional connectors from SVG diagram XML."""
    try:
        root = ET.fromstring(content_bytes)
    except Exception as exc:
        logger.warning(f"SVG parsing failed: {exc}")
        return {"text": "", "components": [], "connections": []}

    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    text_nodes = [elem.text.strip() for elem in root.iter("text") if elem.text and elem.text.strip()]
    components = _build_svg_components(text_nodes)
    lines = list(root.iter("line")) + list(root.iter("path"))
    connections = _build_svg_connections(components, lines)

    return {
        "text": "\n".join(text_nodes),
        "components": components,
        "connections": connections,
    }


def _extract_drawio_diagram(content_bytes: bytes) -> Dict[str, Any]:
    """Extracts components and edges from a .drawio XML diagram."""
    try:
        root = ET.fromstring(content_bytes)
    except Exception as exc:
        logger.warning(f"DrawIO XML parsing failed: {exc}")
        return {"text": "", "components": [], "connections": []}

    cells = root.findall(".//mxCell")
    components = []
    edges = []
    for cell in cells:
        val = cell.get("value", "").strip()
        cid = cell.get("id", "")
        is_edge = cell.get("edge") == "1"
        source = cell.get("source")
        target = cell.get("target")

        if val and not is_edge:
            clean_txt = re.sub(r"<[^>]+>", " ", val)  # strip HTML
            clean_txt = re.sub(r"\s+", " ", clean_txt).strip()
            if clean_txt:
                clean_ref = re.sub(r"[^\w\-]+", "_", clean_txt.lower()).strip("_")
                components.append({
                    "component_ref": f"comp-{cid}-{clean_ref[:20]}",
                    "label": clean_txt,
                    "type": "system_component",
                })
        elif is_edge and source and target:
            edges.append({
                "from_ref": f"comp-{source}",
                "to_ref": f"comp-{target}",
                "label": val or "CALLS",
            })

    all_text = " | ".join(c["label"] for c in components)
    return {"text": all_text, "components": components, "connections": edges}


def _parse_vision_components(lines: List[str]) -> List[Dict[str, Any]]:
    """Extracts structured component references from raw OCR lines."""
    components = []
    seen_refs = set()
    for line in lines[:12]:
        clean = re.sub(r"^[-*#\d\.\s]+", "", line).strip()
        if len(clean) > 3 and not clean.isdigit():
            ref = f"comp-{re.sub(r'[^a-zA-Z0-9]+', '-', clean.lower())[:24].strip('-')}"
            if ref and ref not in seen_refs:
                seen_refs.add(ref)
                components.append({
                    "component_ref": ref,
                    "label": clean[:60],
                    "type": "system_component",
                })
    return components


def _run_apple_vision_ocr(staged_path: Path) -> Optional[List[str]]:
    """Runs native macOS Apple Vision swift OCR script."""
    import shutil
    import subprocess
    import sys

    if sys.platform != "darwin":
        return None
    swift_bin = shutil.which("swift")
    if not swift_bin or not Path("/usr/bin/swift").exists():
        return None

    escaped_path = str(staged_path.resolve()).replace('\\', '\\\\').replace('"', '\\"')
    swift_script = f'''
import Cocoa
import Vision

let imagePath = "{escaped_path}"
guard let image = NSImage(contentsOfFile: imagePath),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {{
    exit(1)
}}

struct OCRItem {{ let text: String, x: Double, y: Double }}
var items: [OCRItem] = []
let request = VNRecognizeTextRequest {{ (req, err) in
    for obs in (req.results as? [VNRecognizedTextObservation] ?? []) {{
        if let top = obs.topCandidates(1).first {{
            items.append(OCRItem(text: top.string, x: Double(obs.boundingBox.origin.x), y: Double(obs.boundingBox.origin.y)))
        }}
    }}
}}
request.recognitionLevel = .accurate
try? VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
items.sort {{ $0.y > $1.y }}
var rows: [[OCRItem]] = []
for it in items {{
    if let last = rows.last, let f = last.first, abs(f.y - it.y) < 0.02 {{
        rows[rows.count - 1].append(it)
    }} else {{ rows.append([it]) }}
}}
for r in rows {{ print(r.sorted {{ $0.x < $1.x }}.map {{ $0.text }}.joined(separator: " | ")) }}
'''
    try:
        proc = subprocess.run(
            ["/usr/bin/swift", "-e", swift_script],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None

        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines or None
    except Exception as exc:
        logger.debug(f"Native Apple Vision execution failed: {exc}")
        return None


def _extract_via_apple_vision(staged_path: Path) -> Optional[Dict[str, Any]]:
    """Extracts high-fidelity OCR text and entities using native macOS Apple Vision framework."""
    lines = _run_apple_vision_ocr(staged_path)
    if not lines:
        return None

    title = lines[0] if lines else staged_path.stem.replace("_", " ").title()
    components = _parse_vision_components(lines)
    from .tabular_parser import extract_tabular_and_semantic_structures
    tables, milestones, claims = extract_tabular_and_semantic_structures(lines)

    return {
        "text": "\n".join(lines),
        "components": components,
        "connections": [],
        "tables": tables,
        "claims": claims,
        "milestones": milestones,
        "summary": f"Visual artifact '{staged_path.name}' titled '{title}' with {len(lines)} detected text lines, {len(tables)} table(s), and {len(milestones)} milestone(s).",
    }


def _extract_via_gemini_vision(staged_path: Path) -> Optional[Dict[str, Any]]:
    """Extracts slide content using Google Gemini multimodal vision if configured."""
    try:
        from config.credentials import resolve_google_credentials
        gcreds = resolve_google_credentials(allow_mock=False)
        if gcreds.is_mock or not gcreds.api_key:
            return None
        from google import genai
        from google.genai import types
        from PIL import Image

        client = genai.Client(api_key=gcreds.api_key, http_options=types.HttpOptions(timeout=30000))
        img = Image.open(staged_path)
        prompt = "Analyze presentation slide: extract title, table headers, components, decisions, and technologies."
        m_name, _ = ModelRegistry.resolve_tier_model(ModelTier.ANTIGRAVITY_AGENT_FLASH)
        resp = client.models.generate_content(model=m_name, contents=[prompt, img])
        extracted_text = (resp.text or "").strip()
        if not extracted_text:
            return None

        lines = [line.strip() for line in extracted_text.splitlines() if line.strip() and not line.strip().startswith("```")]
        title = lines[0] if lines else staged_path.stem.replace("_", " ").title()
        components = _parse_vision_components(lines)

        return {
            "text": extracted_text,
            "components": components,
            "connections": [],
            "summary": f"Visual artifact '{staged_path.name}' titled '{title}'.",
        }
    except Exception as exc:
        logger.debug(f"Gemini vision extraction failed: {exc}")
        return None


def _run_slide_extraction_task(extractor: Any, staged_path: Path) -> Dict[str, Any]:
    import asyncio
    from dual_pipeline.a2a import A2ATask, A2ATaskStatus
    task = A2ATask(
        task_id=f"task-img-{staged_path.name}",
        parent_task_id=None,
        sender_agent_id="AttachmentExtractor",
        recipient_agent_id="MultimodalSlideExtractor",
        objective="Extract diagram and visual text from meeting uploaded image",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={"deck_path": str(staged_path)},
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, extractor.handle_task(task)).result()
    return asyncio.run(extractor.handle_task(task))


def _format_extracted_slide_payload(staged_path: Path, s0: Dict[str, Any], stem_clean: str) -> Dict[str, Any]:
    topics = s0.get("topics") or s0.get("key_topics") or []
    ents = s0.get("extracted_entities") or s0.get("entities") or []
    comps = s0.get("components") or [{"component_ref": e, "label": e.split(":")[-1].replace("-", " ").title(), "type": "system_component"} for e in ents]
    conns = s0.get("connections") or s0.get("edges") or []
    tables = s0.get("tables") or []
    claims = s0.get("claims") or s0.get("bullets") or []
    milestones = s0.get("milestones") or []
    s_title = s0.get("title") or s0.get("slide_title") or stem_clean
    text_lines = [s_title]
    if topics:
        text_lines.append(f"Topics: {', '.join(topics)}")
    if claims:
        text_lines.extend(f"Claim: {c if isinstance(c, str) else c.get('claim', str(c))}" for c in claims)
    if milestones:
        text_lines.extend(f"Milestone: {m.get('name', '')} ({m.get('status', '')}) - {m.get('date', '')}" for m in milestones)
    extracted_text = s0.get("extracted_text") or s0.get("text") or s0.get("ocr_text") or "\n".join(text_lines)
    summary = s0.get("summary") or s0.get("description") or f"Visual image '{staged_path.name}' depicting {s_title}."
    return {
        "status": "SUCCESS",
        "extraction_status": "SUCCESS",
        "title": s_title,
        "text": extracted_text,
        "components": comps,
        "connections": conns,
        "tables": tables,
        "claims": claims,
        "milestones": milestones,
        "is_demo_or_sample": s0.get("is_demo_or_sample", False),
        "summary": summary,
    }


def _extract_via_openai_vision(staged_path: Path) -> Optional[Dict[str, Any]]:
    """Extracts diagram and visual text via OpenAI gpt-6-luna if key available."""
    stem_clean = staged_path.stem.replace("_", " ").title()
    try:
        from storage.credentials import CredentialManager
        api_key = CredentialManager.get_credential("OPENAI_API_KEY")
        if not api_key:
            return None
        from pipeline_a_intake.agents.multimodal_slide_agent import MultimodalSlideExtractor
        extractor = MultimodalSlideExtractor(api_key=api_key)
        res = _run_slide_extraction_task(extractor, staged_path)
        slides = res.get("slides", [])
        if not slides:
            if res.get("status") == "FAILED" or res.get("extraction_status") == "FAILED":
                return {
                    "status": "FAILED",
                    "extraction_status": "FAILED",
                    "error_type": res.get("error_type", "MULTIMODAL_EXTRACTION_FAILED"),
                    "error_reason": res.get("error_reason", f"No slides extracted from {staged_path.name}"),
                    "text": "",
                    "components": [],
                    "connections": [],
                    "summary": f"Visual extraction failed for '{staged_path.name}'.",
                }
            return None
        return _format_extracted_slide_payload(staged_path, slides[0], stem_clean)
    except Exception as exc:
        logger.debug(f"Live OpenAI vision extraction skipped or failed: {exc}")
        return None


def _fail_res(staged_path: Path, err_type: str, reason: str) -> Dict[str, Any]:
    return {
        "status": "FAILED", "extraction_status": "FAILED", "error_type": err_type,
        "error_reason": reason, "text": "", "components": [], "connections": [],
        "tables": [], "claims": [], "milestones": [], "summary": reason,
    }


def _is_successful_extraction(res: Optional[Dict[str, Any]]) -> bool:
    return bool(res and res.get("status") != "FAILED" and res.get("extraction_status") != "FAILED")


def _get_provider_fallback_failure(
    openai_res: Optional[Dict[str, Any]], gemini_res: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if openai_res and openai_res.get("status") == "FAILED":
        return openai_res
    if gemini_res and gemini_res.get("status") == "FAILED":
        return gemini_res
    return None


def extract_image_visual(staged_path: Path, mock_mode: bool = False) -> Dict[str, Any]:
    """Extracts text and topology from a visual image using MultimodalSlideExtractor, Apple Vision, or Gemini."""
    if not staged_path.exists():
        return _fail_res(staged_path, "FILE_NOT_FOUND", f"File '{staged_path.name}' does not exist.")
    if staged_path.stat().st_size == 0:
        return _fail_res(staged_path, "EMPTY_FILE", f"Attachment file '{staged_path.name}' is empty (0 bytes).")
    if staged_path.suffix.lower() == ".svg":
        return _extract_svg_diagram(staged_path.read_bytes())
    if staged_path.suffix.lower() == ".drawio":
        return _extract_drawio_diagram(staged_path.read_bytes())
    if mock_mode:
        raise RuntimeError(f"MOCK_OCR_PROHIBITED: Mock OCR extraction is prohibited for {staged_path.name}; authentic vision provider or explicit failure is required.")

    openai_res = _extract_via_openai_vision(staged_path)
    if _is_successful_extraction(openai_res):
        logger.info("Extracted visual image '%s' using OpenAI Multimodal Vision", staged_path.name)
        return openai_res

    gemini_res = _extract_via_gemini_vision(staged_path)
    if _is_successful_extraction(gemini_res):
        logger.info("Extracted visual image '%s' using Google Gemini Vision", staged_path.name)
        return gemini_res

    apple_res = _extract_via_apple_vision(staged_path)
    if apple_res and apple_res.get("text"):
        logger.info("Extracted visual image '%s' using native Apple Vision OCR fallback", staged_path.name)
        return apple_res

    fallback_failure = _get_provider_fallback_failure(openai_res, gemini_res)
    if fallback_failure:
        return fallback_failure

    return _fail_res(
        staged_path,
        "VISUAL_EXTRACTION_EXHAUSTED",
        f"Visual extraction exhausted for '{staged_path.name}': no visual provider succeeded.",
    )


# Backwards compatibility alias
_extract_image_visual = extract_image_visual
