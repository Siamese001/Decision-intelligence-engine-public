"""
dual_pipeline/card_archival.py

Card Projection Archival and Freshness Verification for Knowledge Graph Projections.
Preserves superseded card projections into cards/archive/<generation_id>/ with
[SUPERSEDED] banners and diff receipts, and verifies freshness against active_pair.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import read_json, sha256_bytes, utc_now


def parse_card_frontmatter(content: str) -> dict[str, str]:
    """Extracts key-value pairs from YAML frontmatter in card markdown."""
    meta: dict[str, str] = {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def archive_superseded_card(
    target_file: Path,
    *,
    new_generation_id: str,
    new_graph_sha256: str,
    new_content: str | None = None,
) -> Path | None:
    """Archives existing card markdown to cards/archive/<prior_gen>/ if superseded."""
    target_p = Path(target_file)
    if not target_p.exists():
        return None
    prior_content = target_p.read_text(encoding="utf-8")
    meta = parse_card_frontmatter(prior_content)
    prior_gen = meta.get("generation_id", "gen_unknown")
    prior_sha = meta.get("graph_sha256", "sha_unknown")

    if prior_gen == new_generation_id and prior_sha == new_graph_sha256:
        return None

    archive_dir = target_p.parent / "archive" / prior_gen
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / target_p.name

    banner = (
        "> [!WARNING]\n"
        f"> **[SUPERSEDED]**: This card projection was compiled from Knowledge Graph generation `{prior_gen}` "
        f"(graph SHA `{prior_sha}`) and has been superseded by generation `{new_generation_id}`.\n"
        f"> Superseded at: `{utc_now()}`. Retained for historical provenance under GraphDB Governance Invariants.\n\n"
    )
    archive_path.write_text(banner + prior_content, encoding="utf-8")

    diff_receipt = {
        "schema_version": "1.0.0",
        "card_id": meta.get("card_id", target_p.stem),
        "prior_generation_id": prior_gen,
        "prior_graph_sha256": prior_sha,
        "new_generation_id": new_generation_id,
        "new_graph_sha256": new_graph_sha256,
        "archived_at": utc_now(),
        "archived_path": str(archive_path),
        "prior_line_count": len(prior_content.splitlines()),
        "new_line_count": len(new_content.splitlines()) if new_content else None,
        "prior_digest": sha256_bytes(prior_content.encode("utf-8")),
        "new_digest": sha256_bytes(new_content.encode("utf-8")) if new_content else None,
    }
    receipt_path = archive_dir / f"{target_p.stem}_diff_receipt.json"
    receipt_path.write_text(json.dumps(diff_receipt, indent=2), encoding="utf-8")
    return archive_path


def _load_active_pair(active_pair: dict[str, Any] | Path | str) -> dict[str, Any]:
    if not isinstance(active_pair, (str, Path)):
        return dict(active_pair)
    ap_path = Path(active_pair).resolve()
    if ap_path.is_dir():
        ap_path = ap_path / "active_pair.json"
    if not ap_path.is_file():
        raise FileNotFoundError(f"active_pair file not found at {ap_path}")
    return read_json(ap_path)


def _scan_card(
    card_path: Path,
    expected_gen: Any,
    expected_sha: Any,
) -> tuple[dict[str, Any], bool] | None:
    if "archive" in card_path.parts or card_path.name.lower() == "readme.md":
        return None
    content = card_path.read_text(encoding="utf-8")
    meta = parse_card_frontmatter(content)
    card_gen = meta.get("generation_id")
    card_sha = meta.get("graph_sha256")
    card_id = meta.get("card_id", card_path.stem)

    is_match = (card_gen == expected_gen and card_sha == expected_sha)
    entry = {
        "card_id": card_id,
        "file": str(card_path),
        "card_generation_id": card_gen,
        "expected_generation_id": expected_gen,
        "card_graph_sha256": card_sha,
        "expected_graph_sha256": expected_sha,
    }
    return entry, is_match


def verify_card_freshness(
    cards_dir: Path | str,
    active_pair: dict[str, Any] | Path | str,
) -> dict[str, Any]:
    """Verifies that all card projections in cards_dir match active_pair generation and graph_sha256."""
    cards_p = Path(cards_dir).resolve()
    ap_data = _load_active_pair(active_pair)
    expected_gen = ap_data.get("generation_id")
    expected_sha = ap_data.get("graph_sha256")

    fresh_cards: list[dict[str, Any]] = []
    stale_cards: list[dict[str, Any]] = []

    if cards_p.exists():
        for card_path in sorted(cards_p.glob("*.md")):
            result = _scan_card(card_path, expected_gen, expected_sha)
            if result is None:
                continue
            entry, is_match = result
            if is_match:
                fresh_cards.append(entry)
            else:
                stale_cards.append(entry)

    is_fresh = len(stale_cards) == 0 and len(fresh_cards) > 0
    return {
        "status": "PASS" if len(stale_cards) == 0 else "DRIFT_DETECTED",
        "is_fresh": is_fresh,
        "expected_generation_id": expected_gen,
        "expected_graph_sha256": expected_sha,
        "total_cards_scanned": len(fresh_cards) + len(stale_cards),
        "fresh_count": len(fresh_cards),
        "drift_count": len(stale_cards),
        "fresh_cards": fresh_cards,
        "stale_cards": stale_cards,
    }

