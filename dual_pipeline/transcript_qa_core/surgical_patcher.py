"""
Surgical AST & Section-Level Patcher for Output 02 and Markdown Artifacts.

Enforces surgical delta repairs on failing sections while guaranteeing that
protected passing sections remain byte-identical and invariant.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from .healing_mesh import DeterministicHealer

logger = logging.getLogger(__name__)


class ProtectedPathMutationError(ValueError):
    """Raised when a patch attempts to modify or regresses a protected section."""
    pass


def _check_protected_target(norm_target: str, protected: List[str], target_section: str) -> None:
    if norm_target in protected:
        raise ProtectedPathMutationError(
            f"Target section '{target_section}' is declared as a protected path and cannot be patched!"
        )


def _match_heading(heading: str, target: str, normalize_fn: Any) -> bool:
    nh = normalize_fn(heading)
    nt = normalize_fn(target)
    if nh == nt:
        return True
    base_h = re.sub(r"\s*\([^)]*\)", "", nh).strip()
    base_t = re.sub(r"\s*\([^)]*\)", "", nt).strip()
    return bool(base_h and base_t and base_h == base_t)


def _replace_sections(
    sections: List[Tuple[str, str]],
    norm_target: str,
    target_section: str,
    new_section_content: str,
    normalize_fn: Any,
) -> Tuple[List[Tuple[str, str]], bool]:
    matched_indices = [
        i for i, (heading, _) in enumerate(sections)
        if _match_heading(heading, target_section, normalize_fn)
    ]
    if len(matched_indices) > 1:
        raise ValueError(
            f"Duplicate target heading '{target_section}' in artifact ({len(matched_indices)} occurrences); cannot safely patch."
        )
    if not matched_indices:
        raise ValueError(
            f"Target section '{target_section}' not found in artifact; missing-target insertion rejected."
        )

    formatted_body = new_section_content.strip() + "\n\n"
    new_sections: List[Tuple[str, str]] = []
    for i, (heading, body) in enumerate(sections):
        if i == matched_indices[0]:
            new_sections.append((heading, formatted_body))
        else:
            new_sections.append((heading, body))

    return new_sections, True


def _verify_invariance(
    protected: List[str],
    original_map: Dict[str, str],
    new_sections: List[Tuple[str, str]],
    normalize_fn: Any,
) -> None:
    for prot_name in protected:
        if prot_name in ("header", "preamble"):
            continue
        orig_body = original_map.get(prot_name)
        if orig_body is None:
            continue
        curr_body = next(
            (body for heading, body in new_sections if _match_heading(heading, prot_name, normalize_fn)),
            None,
        )
        if curr_body is None or curr_body != orig_body:
            raise ProtectedPathMutationError(
                f"Invariance violation: Protected section '{prot_name}' was mutated during surgical patching!"
            )


class SurgicalPatcher:
    """Applies surgical modifications to specific markdown sections with byte-level invariance proofs."""

    @staticmethod
    def parse_markdown_sections(text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """
        Parses markdown into (preamble_header, list_of_(section_heading, section_body)).
        Preserves exact order and formatting.
        """
        lines = text.splitlines(keepends=True)
        header_lines: List[str] = []
        sections: List[Tuple[str, List[str]]] = []
        current_heading: Optional[str] = None
        current_body: List[str] = []

        h_pattern = re.compile(r"^(#{1,2})\s+(?!(?:Subject\b))(.+)$")

        for line in lines:
            h_match = h_pattern.match(line.rstrip("\r\n"))
            if h_match:
                if current_heading is None:
                    header_lines = current_body
                else:
                    sections.append((current_heading, current_body))
                current_heading = f"{h_match.group(1)} {h_match.group(2).strip()}"
                current_body = []
            else:
                current_body.append(line)

        if current_heading is None:
            header_lines = current_body
        else:
            sections.append((current_heading, current_body))

        reconstructed_sections = [(heading, "".join(body)) for heading, body in sections]
        return "".join(header_lines), reconstructed_sections

    @staticmethod
    def normalize_section_name(name: str) -> str:
        """Normalize section name for exact matching (e.g., 'sections[Bounded Execution Wedge]')."""
        clean = re.sub(r"^sections\[['\"]?", "", name)
        clean = re.sub(r"['\"]?\]$", "", clean)
        clean = re.sub(r"^##\s*", "", clean)
        clean = re.sub(r"^#\s*", "", clean)
        return clean.strip().lower()

    @classmethod
    def apply_surgical_patch(
        cls,
        original_text: str,
        target_section: str,
        new_section_content: str,
        protected_sections: Optional[List[str]] = None,
        auto_heal: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        output_1: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Applies new_section_content exclusively to target_section."""
        protected = [cls.normalize_section_name(p) for p in (protected_sections or [])]
        norm_target = cls.normalize_section_name(target_section)
        _check_protected_target(norm_target, protected, target_section)

        header, sections = cls.parse_markdown_sections(original_text)
        original_map = {cls.normalize_section_name(h): b for h, b in sections}

        new_sections, target_found = _replace_sections(
            sections, norm_target, target_section, new_section_content, cls.normalize_section_name
        )

        reconstructed = header + "".join(f"{h}\n{b}" if h.startswith("#") else f"## {h}\n{b}" for h, b in new_sections)
        _verify_invariance(protected, original_map, new_sections, cls.normalize_section_name)

        if auto_heal:
            reconstructed, changes = DeterministicHealer.heal_output_2(reconstructed, metadata, output_1=output_1)
            post_header, post_sections = cls.parse_markdown_sections(reconstructed)

            restored_sections = []
            for h, b in post_sections:
                norm_h = cls.normalize_section_name(h)
                if norm_h in protected and norm_h in original_map:
                    orig_h = next((oh for oh, _ in sections if cls.normalize_section_name(oh) == norm_h), h)
                    restored_sections.append((orig_h, original_map[norm_h]))
                elif norm_h == norm_target:
                    orig_target_h = next((h_name for h_name, _ in new_sections if cls.normalize_section_name(h_name) == norm_target), h)
                    target_body = new_section_content.strip() + "\n\n"
                    restored_sections.append((orig_target_h, target_body))
                else:
                    restored_sections.append((h, b))
            post_sections = restored_sections

            final_header = header if any(p in ("header", "preamble") for p in protected) else post_header
            reconstructed = final_header + "".join(f"{h}\n{b}" if h.startswith("#") else f"## {h}\n{b}" for h, b in post_sections)
            _verify_invariance(protected, original_map, post_sections, cls.normalize_section_name)
        else:
            changes = []

        audit = {
            "target_section": target_section,
            "target_found": target_found,
            "protected_sections_verified": [p for p in protected if p in original_map],
            "deterministic_healing_applied": changes,
        }
        return reconstructed, audit

    @classmethod
    def apply_critique_envelope(
        cls,
        original_text: str,
        critique_envelope: Dict[str, Any],
        replacement_content: str,
        metadata: Optional[Dict[str, Any]] = None,
        auto_heal: bool = False,
        output_1: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Applies a patch directed by a structured critique_diff_envelope."""
        if not original_text:
            return "", {"target_section": "", "target_found": False, "skipped": "empty_original_text"}
        target_path = critique_envelope.get("target_path", "") if isinstance(critique_envelope, dict) else ""
        if not target_path:
            return original_text, {"target_section": "", "target_found": False, "skipped": "missing_target_path"}
        protected_paths = critique_envelope.get("protected_paths", []) if isinstance(critique_envelope, dict) else []
        return cls.apply_surgical_patch(
            original_text=original_text,
            target_section=target_path,
            new_section_content=replacement_content,
            protected_sections=protected_paths,
            auto_heal=auto_heal,
            metadata=metadata,
            output_1=output_1,
        )

    @classmethod
    def apply_json_critique_patch(
        cls,
        original_json: Union[str, Dict[str, Any]],
        critique_diff_or_envelope: Union[Dict[str, Any], str],
        metadata: Optional[Dict[str, Any]] = None,
        protected_entities: Optional[List[str]] = None,
        output_1: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Applies surgical JSON mutations to Output 03 graph projection with invariance proofs."""
        from .json_surgical_patcher import JsonSurgicalPatcher

        return JsonSurgicalPatcher.apply_patch(
            original_json=original_json,
            critique_diff_or_envelope=critique_diff_or_envelope,
            metadata=metadata,
            protected_entities=protected_entities,
            output_1=output_1,
        )

    @classmethod
    def apply_output3_critique_patch(
        cls,
        original_json: str,
        critique_envelope: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        output_1: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Applies surgical critique repairs to an Output 03 JSON candidate.
        Repairs specific entity, relationship, or evidence gaps without destroying
        the passing subgraph or regressing high-water-mark candidate scores.
        """
        import json
        if not original_json or not original_json.strip():
            return original_json, {"patch_applied": False, "reason": "empty_input"}

        try:
            data = json.loads(original_json)
        except Exception as e:
            logger.warning(f"SurgicalPatcher Output 03: JSON parse failure: {e}")
            return original_json, {"patch_applied": False, "reason": "invalid_json"}

        mutations = data.setdefault("graph_mutations", {})
        changes: List[str] = []

        if isinstance(critique_envelope, dict):
            patch_entities = critique_envelope.get("entity_upserts") or critique_envelope.get("entities") or []
            if isinstance(patch_entities, list) and patch_entities:
                existing_ents = mutations.setdefault("entity_upserts", [])
                existing_ids = {e.get("id") for e in existing_ents if isinstance(e, dict)}
                for pe in patch_entities:
                    if isinstance(pe, dict) and pe.get("id"):
                        if pe["id"] not in existing_ids:
                            existing_ents.append(pe)
                            existing_ids.add(pe["id"])
                            changes.append(f"Injected critique entity '{pe['id']}'")

            patch_signals = critique_envelope.get("relationship_signals") or critique_envelope.get("signals") or []
            if isinstance(patch_signals, list) and patch_signals:
                existing_sigs = mutations.setdefault("relationship_signals", [])
                for ps in patch_signals:
                    if isinstance(ps, dict):
                        existing_sigs.append(ps)
                        changes.append(f"Injected critique signal '{ps.get('signal_id', 'unknown')}'")

            patch_evidence = critique_envelope.get("evidence_index") or {}
            if isinstance(patch_evidence, dict) and patch_evidence:
                ev_idx = data.setdefault("evidence_index", {})
                for k, v in patch_evidence.items():
                    if k not in ev_idx:
                        ev_idx[k] = v
                        changes.append(f"Injected critique evidence '{k}'")

        from .healing_mesh import DeterministicHealer
        has_critique_changes = bool(changes)
        healed_text, healer_changes = DeterministicHealer.heal_output_3(
            json.dumps(data), metadata=metadata, output_1=output_1
        )
        changes.extend(healer_changes)
        audit = {
            "patch_applied": has_critique_changes,
            "changes_applied": changes,
            "entity_count": len(mutations.get("entity_upserts", [])),
            "signal_count": len(mutations.get("relationship_signals", [])),
        }
        return healed_text, audit
