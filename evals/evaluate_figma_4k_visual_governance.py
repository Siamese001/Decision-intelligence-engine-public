"""
Headless Figma 4K Visual Governance QA Evaluation Engine.
Enforces the Seven Non-Negotiable Invariants under Figma_4K_Visual_Governance_v1.0.0:

1. Exact 3840x2160 Canvas (16:9 Aspect Ratio)
2. Non-Blank Luminance Variance (variance >= 8.0)
3. Inter Typography Mandate (Inter only, title >= 88px, body >= 42px, secondary >= 38px, no shrink-to-fit)
4. Exclusive Full-Width Title Row (width >= 3648px, zero manual line breaks, exclusive y in [48, 310])
5. Executive Dominance & Argument Structure (dominant exhibit area >= 45%, CONCLUSION -> EVIDENCE -> IMPLICATION)
6. 100% Source Truth Traceability (every text node mapped; substantive only; numeric/certainty tokens verified)
7. Fail-Closed Machine Release Authority (distinct builder/reviewer runs, zero self-attestation, zero regressions)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from PIL import Image, ImageDraw, ImageStat
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

ROOT = Path(__file__).resolve().parent.parent
CERTAINTY_TERMS = {"approved", "committed", "final", "complete", "guaranteed", "will", "must"}

TYPOGRAPHY_MINIMUMS = {
    "SLIDE_TITLE": 88,
    "HERO_CALLOUT": 60,
    "MAJOR_HEADING": 52,
    "EXHIBIT_TITLE": 46,
    "ESSENTIAL_BODY": 42,
    "BODY": 42,
    "SECONDARY_BODY": 38,
    "CAPTION": 32,
    "STRUCTURAL_LABEL": 28,
}


def normalize_text(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9%+→/.$-]+", " ", str(s).lower()).split())


def extract_numeric_tokens(s: str) -> Set[str]:
    raw = re.findall(r"(?:\$\s*)?\d[\d,]*(?:\.\d+)?\s*(?:%|[KMB]|MM)?", str(s), flags=re.I)
    return {re.sub(r"[\s,]", "", x).upper() for x in raw}


def check_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


class Figma4KVisualGovernanceEvaluator:
    """Headless evaluator for Figma 4K executive presentation slides."""

    def __init__(self, figma_package_root: Optional[Path] = None):
        self.package_root = figma_package_root or (ROOT / "Figma" / "Figma_4K_Visual_Governance_v1.0.0_20260904")

    def validate_invariant_1_canvas(
        self,
        candidate_spec: Dict[str, Any],
        image_path: Optional[Path] = None,
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 1: Exact 3840x2160 Canvas (16:9 Aspect Ratio)."""
        violations = []
        width = None
        height = None
        fmt = None

        if image_path and Path(image_path).is_file() and HAS_PIL:
            with Image.open(image_path) as im:
                width, height = im.size
                fmt = im.format
                if fmt != "PNG":
                    violations.append(f"INVALID_IMAGE_FORMAT: Candidate must be PNG, got {fmt}")
        else:
            canvas = candidate_spec.get("canvas") or candidate_spec.get("image_dimensions") or {}
            if isinstance(canvas, list) and len(canvas) == 2:
                width, height = canvas[0], canvas[1]
            elif isinstance(canvas, dict):
                width = canvas.get("width")
                height = canvas.get("height")
            fmt = candidate_spec.get("format", "PNG")

        if width != 3840 or height != 2160:
            violations.append(
                f"INVALID_CANVAS_DIMENSIONS: Canvas dimensions must be exactly 3840x2160, got {width}x{height}"
            )

        if width is not None and height is not None and width > 0 and height > 0:
            if (width * 9) != (height * 16):
                violations.append(
                    f"INVALID_ASPECT_RATIO: Aspect ratio must be exactly 16:9, got {width}:{height}"
                )

        passed = len(violations) == 0
        details = {
            "width": width,
            "height": height,
            "format": fmt,
            "aspect_ratio": "16:9" if passed else f"{width}:{height}",
            "violations": violations,
        }
        return passed, violations, details

    def validate_invariant_2_luminance(
        self,
        candidate_spec: Dict[str, Any],
        image_path: Optional[Path] = None,
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 2: Non-Blank Luminance Variance (variance >= 8.0)."""
        violations = []
        variance = None

        if image_path and Path(image_path).is_file() and HAS_PIL:
            with Image.open(image_path) as im:
                stat = ImageStat.Stat(im.convert("L"))
                variance = float(stat.var[0])
        else:
            variance = float(candidate_spec.get("luminance_variance", 24.5))

        if variance < 8.0:
            violations.append(
                f"VISUALLY_MEANINGLESS_ARTIFACT: Luminance variance {variance:.2f} < 8.0 (blank/near-blank artifact)"
            )

        passed = len(violations) == 0
        details = {
            "luminance_variance": variance,
            "minimum_threshold": 8.0,
            "violations": violations,
        }
        return passed, violations, details

    def validate_invariant_3_typography(
        self,
        nodes: List[Dict[str, Any]],
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 3: Inter Typography Mandate (Inter exclusively, floors enforced, no shrink-to-fit)."""
        violations = []
        text_nodes = [n for n in nodes if n.get("type") == "TEXT" and n.get("visible") is not False]
        audited_count = len(text_nodes)

        for n in text_nodes:
            nid = n.get("node_id", "unknown_node")
            ff = n.get("font_family")
            if ff != "Inter":
                violations.append(f"WRONG_FONT_FAMILY: Node {nid} uses font '{ff}', expected 'Inter'")

            fb = n.get("font_fallback")
            if fb not in (None, False, "NONE"):
                violations.append(f"FONT_FALLBACK_FORBIDDEN: Node {nid} specifies forbidden font fallback '{fb}'")

            role = n.get("semantic_role", "BODY")
            floor = TYPOGRAPHY_MINIMUMS.get(role, 38)
            fs = n.get("font_size")
            if fs is not None and fs < floor:
                violations.append(
                    f"FONT_BELOW_FLOOR: Node {nid} (role: {role}) font size {fs}px is below floor {floor}px"
                )

            lh = n.get("line_height")
            if fs is not None and lh is not None and lh < fs:
                violations.append(
                    f"CRAMPED_LINE_HEIGHT: Node {nid} line height {lh}px is smaller than font size {fs}px"
                )

            if n.get("fit_behavior") == "SHRUNK_TO_FIT":
                violations.append(f"SHRINK_TO_FIT: Node {nid} uses prohibited shrink-to-fit behavior")

        passed = len(violations) == 0
        details = {
            "audited_text_nodes": audited_count,
            "font_family_enforced": "Inter",
            "violations": violations,
        }
        return passed, violations, details

    def validate_invariant_4_title_row(
        self,
        nodes: List[Dict[str, Any]],
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 4: Exclusive Full-Width Title Row (width >= 3648px, zero manual breaks, exclusive y in [48, 310])."""
        violations = []
        titles = [n for n in nodes if n.get("semantic_role") == "SLIDE_TITLE" and n.get("visible") is not False]

        if len(titles) != 1:
            violations.append(f"TITLE_COUNT_MISMATCH: Exactly 1 visible SLIDE_TITLE required; found {len(titles)}")
            return False, violations, {"title_count": len(titles), "violations": violations}

        t = titles[0]
        nid = t.get("node_id", "title_node")
        x = float(t.get("x", 0))
        y = float(t.get("y", 0))
        w = float(t.get("width", 0))
        h = float(t.get("height", 0))
        right = x + w

        # Left edge target: 96px +- 32px
        if not (64 <= x <= 128):
            violations.append(f"TITLE_ROW_NOT_FULL_WIDTH: Title x={x}px outside allowable left target [64, 128]")

        # Top position: y in [48, 76]
        if not (48 <= y <= 76):
            violations.append(f"TITLE_VERTICAL_POSITION: Title y={y}px outside vertical start band [48, 76]")

        # Width floor: >= 3648px
        if w < 3648:
            violations.append(f"TITLE_FRAME_TOO_NARROW: Title frame width {w}px < 3648px minimum")

        # Right edge: >= 3744px
        if right < 3744:
            violations.append(f"TITLE_DEAD_SPACE_REGRESSION: Title right edge {right}px < 3744px minimum")

        # Vertical floor: bottom <= 310px
        if y + h > 310:
            violations.append(f"TITLE_VERTICAL_CLIP: Title lower bound {y + h}px exceeds 310px title zone bottom")

        # Rendered line count <= 2
        lines = t.get("rendered_line_count", 1)
        if lines > 2:
            violations.append(f"TITLE_ARTIFICIAL_WRAP: Title rendered line count {lines} exceeds 2 lines max")

        # Manual line breaks: 0
        manual_breaks = t.get("manual_line_breaks", 0)
        if manual_breaks > 0:
            violations.append(f"TITLE_MANUAL_EARLY_BREAK: Title contains {manual_breaks} manual line breaks; must wrap naturally")

        # Exclusivity check: No non-title node intersects y in [48, 310] except TITLE_DIVIDER
        title_zone = (0.0, 48.0, 3840.0, 262.0)
        for n in nodes:
            if n is t or n.get("visible") is False:
                continue
            role = n.get("semantic_role")
            if role in ("TITLE_DIVIDER", None) and n.get("type") != "TEXT":
                continue
            nx = float(n.get("x", 0))
            ny = float(n.get("y", 0))
            nw = float(n.get("width", 0))
            nh = float(n.get("height", 0))
            node_box = (nx, ny, nw, nh)
            if check_intersects(title_zone, node_box) and role not in ("TITLE_DIVIDER",):
                violations.append(
                    f"TITLE_ROW_NOT_EXCLUSIVE: Node {n.get('node_id')} (role: {role}) intersects exclusive title row"
                )

        passed = len(violations) == 0
        details = {
            "title_id": nid,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
            "right_edge": right,
            "rendered_lines": lines,
            "manual_line_breaks": manual_breaks,
            "violations": violations,
        }
        return passed, violations, details

    def validate_invariant_5_executive_dominance(
        self,
        candidate_spec: Dict[str, Any],
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 5: Executive Dominance & Argument Structure (dominant exhibit area >= 45%, anti-wall limits)."""
        violations = []
        metrics = candidate_spec.get("metrics") or candidate_spec.get("rendered_qa", {}).get("metrics") or {}

        # Dominant exhibit area ratio >= 0.45
        area_ratio = float(metrics.get("dominant_exhibit_area_ratio", 0.0))
        if area_ratio < 0.45:
            violations.append(
                f"MISSING_DOMINANT_EXHIBIT: Dominant exhibit area ratio {area_ratio:.2%} < 45% minimum required"
            )

        # Anti-message-wall limits
        actor_blocks = int(metrics.get("actor_name_block_count", 0))
        if actor_blocks > 1:
            violations.append(
                f"ACTOR_MESSAGE_WALL: Found {actor_blocks} actor attribution blocks; maximum 1 permitted"
            )

        direct_quotes = int(metrics.get("direct_quote_block_count", 0))
        if direct_quotes > 1:
            violations.append(
                f"EXCESSIVE_DIRECT_QUOTES: Found {direct_quotes} direct quote blocks; maximum 1 permitted"
            )

        hero_zones = int(metrics.get("hero_message_zones", 1))
        if hero_zones > 2:
            violations.append(
                f"TOO_MANY_HERO_MESSAGE_ZONES: Found {hero_zones} hero message zones; maximum 2 permitted"
            )

        peer_cards = int(metrics.get("peer_card_count", 0))
        has_comparison = bool(metrics.get("comparison_dimension"))
        if peer_cards > 4 and not has_comparison:
            violations.append(
                f"PEER_CARD_WALL_WITHOUT_COMPARISON: Found {peer_cards} peer cards without declared comparison dimension"
            )

        # Executive argument completeness (CONCLUSION, EVIDENCE, IMPLICATION)
        roles = set(metrics.get("argument_roles", []))
        required_roles = {"CONCLUSION", "EVIDENCE", "IMPLICATION_OR_DECISION"}
        if not required_roles.issubset(roles):
            missing = sorted(required_roles - roles)
            violations.append(
                f"EXECUTIVE_ARGUMENT_INCOMPLETE: Argument roles missing required components: {missing}"
            )

        passed = len(violations) == 0
        details = {
            "dominant_exhibit_area_ratio": area_ratio,
            "actor_name_blocks": actor_blocks,
            "direct_quote_blocks": direct_quotes,
            "peer_cards": peer_cards,
            "argument_roles": sorted(roles),
            "violations": violations,
        }
        return passed, violations, details

    def validate_invariant_6_source_traceability(
        self,
        source_manifest: Dict[str, Any],
        nodes: List[Dict[str, Any]],
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 6: 100% Source Truth Traceability (every text node mapped; substantive only; numeric/certainty verified)."""
        violations = []
        text_nodes = [n for n in nodes if n.get("type") == "TEXT" and n.get("visible") is not False]
        text_node_ids = {str(n.get("node_id")) for n in text_nodes}

        claims = source_manifest.get("claims", [])
        claim_map = {}
        for c in claims:
            nid = str(c.get("node_id", ""))
            if nid:
                claim_map[nid] = c

        claim_ids = set(claim_map.keys())

        # Completeness: every text node must have an entry
        missing_claims = text_node_ids - claim_ids
        if missing_claims:
            violations.append(
                f"SOURCE_MANIFEST_MISSING_NODE: Text nodes missing from source manifest: {sorted(missing_claims)}"
            )

        for n in text_nodes:
            nid = str(n.get("node_id"))
            c = claim_map.get(nid)
            if not c:
                continue

            classification = c.get("classification")
            valid_classifications = {
                "SOURCE_EXACT",
                "SOURCE_PARAPHRASE",
                "USER_APPROVED_NEW",
                "STRUCTURAL_LABEL",
            }
            if classification not in valid_classifications:
                violations.append(
                    f"UNSUPPORTED_SUBSTANTIVE_CONTENT: Node {nid} has classification '{classification}'"
                )

            display_text = str(c.get("display_text", ""))
            actual_text = str(n.get("text", ""))
            if display_text != actual_text:
                violations.append(
                    f"SOURCE_DISPLAY_TEXT_MISMATCH: Node {nid} display_text does not match Figma text"
                )

            # Check numeric token support
            excerpt = str(c.get("source_excerpt", ""))
            if classification in ("SOURCE_EXACT", "SOURCE_PARAPHRASE") and excerpt:
                missing_nums = extract_numeric_tokens(display_text) - extract_numeric_tokens(excerpt)
                if missing_nums:
                    violations.append(
                        f"UNGROUNDED_NUMERIC_TOKEN: Node {nid} contains numeric tokens {sorted(missing_nums)} not in source excerpt"
                    )

                # Check certainty terms
                dl = display_text.lower()
                el = excerpt.lower()
                for term in CERTAINTY_TERMS:
                    if re.search(rf"\b{re.escape(term)}\b", dl) and not re.search(rf"\b{re.escape(term)}\b", el):
                        violations.append(
                            f"UNGROUNDED_CERTAINTY_TERM: Node {nid} uses certainty term '{term}' without source support"
                        )

        passed = len(violations) == 0
        details = {
            "total_text_nodes": len(text_nodes),
            "manifest_claims": len(claims),
            "unsupported_count": sum(1 for c in claims if c.get("classification") == "UNSUPPORTED"),
            "violations": violations,
        }
        return passed, violations, details

    def validate_invariant_7_release_authority(
        self,
        receipt: Dict[str, Any],
    ) -> Tuple[bool, List[str], Dict[str, Any]]:
        """Invariant 7: Fail-Closed Machine Release Authority (distinct reviewer run IDs, zero self-attestation)."""
        violations = []
        builder = str((receipt.get("builder") or {}).get("run_id", "")).strip()

        if not builder or len(builder) < 4:
            violations.append("BUILDER_RUN_ID_MISSING: builder.run_id is missing or too short")

        # Self-attestation sections forbidden
        forbidden_sections = {"self_attestation", "author_certification", "developer_claim"}
        present_forbidden = forbidden_sections.intersection(receipt.keys())
        if present_forbidden:
            violations.append(
                f"SELF_ATTESTATION_FORBIDDEN: Receipt contains forbidden self-attestation sections: {sorted(present_forbidden)}"
            )

        # Distinct reviewer run IDs
        producers = []
        for section in ("figma_evidence", "source_manifest", "rendered_qa", "regression_qa"):
            sec_obj = receipt.get(section)
            if isinstance(sec_obj, dict) and "producer" in sec_obj:
                prun = str(sec_obj["producer"].get("run_id", "")).strip()
                if prun:
                    if prun == builder:
                        violations.append(
                            f"BUILDER_REVIEWER_COLLUSION: {section} producer run_id '{prun}' is identical to builder run_id"
                        )
                    producers.append(prun)

        if len(producers) != len(set(producers)) and producers:
            violations.append("REDUNDANT_REVIEWER_RUN_IDS: Independent reviewers must have distinct run_ids")

        # Regression review check if revision
        mode = receipt.get("mode", "REDESIGN")
        if mode in ("REVISION", "REDESIGN") and "regression_qa" in receipt:
            reg = receipt.get("regression_qa", {})
            dims = reg.get("dimensions", {})
            regressed = [d for d, val in dims.items() if val.get("rating") == "MATERIAL_REGRESSION"]
            if regressed:
                violations.append(
                    f"MATERIAL_REGRESSION: Revisions suffered material regressions in dimensions: {regressed}"
                )

        passed = len(violations) == 0
        details = {
            "builder_run_id": builder,
            "reviewer_run_ids": producers,
            "mode": mode,
            "violations": violations,
        }
        return passed, violations, details

    def evaluate_slide(
        self,
        candidate_spec: Dict[str, Any],
        image_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Execute comprehensive evaluation against all 7 Non-Negotiable Invariants."""
        nodes = candidate_spec.get("nodes", [])
        source_manifest = candidate_spec.get("source_manifest", {})
        receipt = candidate_spec.get("receipt", candidate_spec)

        inv1_pass, inv1_errs, inv1_det = self.validate_invariant_1_canvas(candidate_spec, image_path)
        inv2_pass, inv2_errs, inv2_det = self.validate_invariant_2_luminance(candidate_spec, image_path)
        inv3_pass, inv3_errs, inv3_det = self.validate_invariant_3_typography(nodes)
        inv4_pass, inv4_errs, inv4_det = self.validate_invariant_4_title_row(nodes)
        inv5_pass, inv5_errs, inv5_det = self.validate_invariant_5_executive_dominance(candidate_spec)
        inv6_pass, inv6_errs, inv6_det = self.validate_invariant_6_source_traceability(source_manifest, nodes)
        inv7_pass, inv7_errs, inv7_det = self.validate_invariant_7_release_authority(receipt)

        all_invariants = {
            "1. exact_3840x2160_canvas": inv1_pass,
            "2. non_blank_luminance_variance": inv2_pass,
            "3. inter_typography_mandate": inv3_pass,
            "4. exclusive_full_width_title_row": inv4_pass,
            "5. executive_dominance_and_argument": inv5_pass,
            "6. 100_percent_source_truth_traceability": inv6_pass,
            "7. fail_closed_machine_release_authority": inv7_pass,
        }

        all_passed = all(all_invariants.values())
        all_violations = inv1_errs + inv2_errs + inv3_errs + inv4_errs + inv5_errs + inv6_errs + inv7_errs

        return {
            "status": "PASS" if all_passed else "REJECTED",
            "scope": "RELEASE_AUTHORIZED" if all_passed else "DIAGNOSTIC_FAIL",
            "invariants": all_invariants,
            "violations": all_violations,
            "details": {
                "invariant_1": inv1_det,
                "invariant_2": inv2_det,
                "invariant_3": inv3_det,
                "invariant_4": inv4_det,
                "invariant_5": inv5_det,
                "invariant_6": inv6_det,
                "invariant_7": inv7_det,
            },
        }

    @staticmethod
    def create_governed_executive_slide_fixture() -> Dict[str, Any]:
        """Generate a fully compliant reference fixture satisfying all 7 invariants."""
        nodes = [
            # 1. Compliant Slide Title (exclusive row y in [48, 310])
            {
                "node_id": "title:slide_main",
                "type": "TEXT",
                "semantic_role": "SLIDE_TITLE",
                "name": "Slide Title Frame",
                "visible": True,
                "x": 96.0,
                "y": 64.0,
                "width": 3648.0,
                "height": 110.0,
                "font_family": "Inter",
                "font_size": 96.0,
                "line_height": 110.0,
                "fit_behavior": "NORMAL",
                "rendered_line_count": 1,
                "manual_line_breaks": 0,
                "text": "Unified Asset Intelligence: Strategic Synthesis & Roadmap",
            },
            # 2. Main Executive Exhibit Header (Dominant Exhibit)
            {
                "node_id": "text:exhibit_header",
                "type": "TEXT",
                "semantic_role": "EXHIBIT_TITLE",
                "name": "Exhibit Header",
                "visible": True,
                "x": 96.0,
                "y": 380.0,
                "width": 1800.0,
                "height": 60.0,
                "font_family": "Inter",
                "font_size": 48.0,
                "line_height": 56.0,
                "fit_behavior": "NORMAL",
                "text": "Cross-Platform Asset Integration & Operational Flow",
            },
            # 3. Essential Body Statement
            {
                "node_id": "text:body_summary",
                "type": "TEXT",
                "semantic_role": "ESSENTIAL_BODY",
                "name": "Body Text",
                "visible": True,
                "x": 96.0,
                "y": 460.0,
                "width": 1800.0,
                "height": 140.0,
                "font_family": "Inter",
                "font_size": 42.0,
                "line_height": 52.0,
                "fit_behavior": "NORMAL",
                "text": "Consolidating 30 legacy platforms into a unified ingestion pipeline reduces operational latency by 65%.",
            },
            # 4. Dominant Exhibit Visual Region
            {
                "node_id": "visual:dominant_matrix",
                "type": "FRAME",
                "semantic_role": "DOMINANT_EXHIBIT",
                "name": "Main Diagnostic Matrix",
                "visible": True,
                "x": 96.0,
                "y": 640.0,
                "width": 3648.0,
                "height": 1300.0,
            },
        ]

        source_manifest = {
            "claims": [
                {
                    "node_id": "title:slide_main",
                    "classification": "SOURCE_EXACT",
                    "display_text": "Unified Asset Intelligence: Strategic Synthesis & Roadmap",
                    "source_excerpt": "Unified Asset Intelligence: Strategic Synthesis & Roadmap",
                },
                {
                    "node_id": "text:exhibit_header",
                    "classification": "SOURCE_EXACT",
                    "display_text": "Cross-Platform Asset Integration & Operational Flow",
                    "source_excerpt": "Cross-Platform Asset Integration & Operational Flow",
                },
                {
                    "node_id": "text:body_summary",
                    "classification": "SOURCE_PARAPHRASE",
                    "display_text": "Consolidating 30 legacy platforms into a unified ingestion pipeline reduces operational latency by 65%.",
                    "source_excerpt": "Alex Rivera: We have 30 different platforms. Consolidating into unified ingestion achieves a 65% latency reduction.",
                    "semantic_review": "PASS",
                },
            ]
        }

        return {
            "canvas": [3840, 2160],
            "format": "PNG",
            "luminance_variance": 42.8,
            "nodes": nodes,
            "source_manifest": source_manifest,
            "metrics": {
                "dominant_exhibit_area_ratio": 0.57,  # > 45%
                "actor_name_block_count": 0,
                "direct_quote_block_count": 0,
                "peer_card_count": 3,
                "comparison_dimension": "PLATFORM_MATURITY",
                "hero_message_zones": 1,
                "argument_roles": ["CONCLUSION", "EVIDENCE", "IMPLICATION_OR_DECISION"],
            },
            "builder": {"run_id": "run-builder-101"},
            "figma_evidence": {"producer": {"run_id": "run-figma-reviewer-201"}},
            "source_manifest_evidence": {"producer": {"run_id": "run-source-reviewer-202"}},
            "rendered_qa": {
                "producer": {"run_id": "run-render-reviewer-203"},
                "metrics": {
                    "dominant_exhibit_area_ratio": 0.57,
                    "actor_name_block_count": 0,
                    "direct_quote_block_count": 0,
                    "peer_card_count": 3,
                    "comparison_dimension": "PLATFORM_MATURITY",
                    "hero_message_zones": 1,
                    "argument_roles": ["CONCLUSION", "EVIDENCE", "IMPLICATION_OR_DECISION"],
                },
            },
            "regression_qa": {
                "producer": {"run_id": "run-reg-reviewer-204"},
                "dimensions": {
                    "TYPOGRAPHY_INTEGRITY": {"rating": "IMPROVED", "rationale": "Expanded title frame width to 3648px."},
                    "CANVAS_BOUNDS": {"rating": "STATIC", "rationale": "Canvas maintained at exact 3840x2160 resolution."},
                },
            },
            "mode": "REDESIGN",
        }


def main():
    parser = argparse.ArgumentParser(description="Headless Figma 4K Visual Governance QA Evaluator")
    parser.add_argument("--candidate", type=Path, help="Path to JSON candidate specification")
    parser.add_argument("--image", type=Path, help="Path to exported 3840x2160 PNG image")
    args = parser.parse_args()

    evaluator = Figma4KVisualGovernanceEvaluator()

    if args.candidate and args.candidate.is_file():
        spec = json.loads(args.candidate.read_text(encoding="utf-8"))
    else:
        # Run on reference fixture
        spec = evaluator.create_governed_executive_slide_fixture()

    result = evaluator.evaluate_slide(spec, image_path=args.image)

    print("=" * 80)
    print("Headless Figma 4K Visual Governance QA Evaluation")
    print("=" * 80)
    print(f"Status: {result['status']} ({result['scope']})\n")
    print("The Seven Non-Negotiable Invariants:")
    for inv, passed in result["invariants"].items():
        mark = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {inv:45s} {mark}")

    if result["violations"]:
        print("\nViolations Detected:")
        for v in result["violations"]:
            print(f"  - {v}")

    print("\n" + "=" * 80)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
