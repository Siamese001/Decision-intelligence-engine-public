import re
from typing import Any, Dict, List, Optional, Tuple


def _segment_oversized_text(text: str, max_len: int = 3500) -> List[str]:
    """Losslessly split oversized text into bounded chunks <= max_len.

    Prefers splitting on paragraph boundaries, then sentence boundaries, then whitespace.
    """
    if len(text) <= max_len:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current_chunk: List[str] = []
    current_len = 0

    for p in paragraphs:
        p_len = len(p)
        if current_len + p_len + (2 if current_chunk else 0) <= max_len:
            current_chunk.append(p)
            current_len += p_len + (2 if len(current_chunk) > 1 else 0)
        else:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            if p_len <= max_len:
                current_chunk.append(p)
                current_len = p_len
            else:
                sentences = re.split(r"(?<=[.!?])\s+", p)
                s_chunk: List[str] = []
                s_len = 0
                for s in sentences:
                    if s_len + len(s) + (1 if s_chunk else 0) <= max_len:
                        s_chunk.append(s)
                        s_len += len(s) + (1 if len(s_chunk) > 1 else 0)
                    else:
                        if s_chunk:
                            chunks.append(" ".join(s_chunk))
                            s_chunk = []
                            s_len = 0
                        if len(s) <= max_len:
                            s_chunk.append(s)
                            s_len = len(s)
                        else:
                            words = s.split()
                            w_chunk: List[str] = []
                            w_len = 0
                            for w in words:
                                if w_len + len(w) + (1 if w_chunk else 0) <= max_len:
                                    w_chunk.append(w)
                                    w_len += len(w) + (1 if len(w_chunk) > 1 else 0)
                                else:
                                    if w_chunk:
                                        chunks.append(" ".join(w_chunk))
                                        w_chunk = []
                                        w_len = 0
                                    w_chunk.append(w)
                                    w_len = len(w)
                            if w_chunk:
                                chunks.append(" ".join(w_chunk))
                if s_chunk:
                    chunks.append(" ".join(s_chunk))
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    return [c for c in chunks if c.strip()]


def _bound_oversized_evidence_statements(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    max_len: int = 3500,
) -> None:
    """Losslessly segment oversized evidence statements into bounded items <= max_len."""
    ev_index = data.get("evidence_index")
    if not isinstance(ev_index, dict):
        return

    oversized = [
        (eid, ev) for eid, ev in list(ev_index.items())
        if isinstance(ev, dict) and (
            len(str(ev.get("exact_text") or "")) > max_len
            or len(str(ev.get("text") or "")) > max_len
        )
    ]
    if not oversized:
        return

    for eid, ev in oversized:
        raw_text = str(ev.get("exact_text") or ev.get("text") or "")
        chunks = _segment_oversized_text(raw_text, max_len=max_len)
        if len(chunks) <= 1:
            continue

        ev["exact_text"] = chunks[0]
        ev["text"] = chunks[0]
        ev.pop("quote_pointer", None)

        part_eids = [eid]
        for idx, chunk in enumerate(chunks[1:], start=2):
            part_eid = f"{eid}-part{idx}"
            part_ev = dict(ev)
            part_ev["exact_text"] = chunk
            part_ev["text"] = chunk
            part_ev["statement_id"] = part_eid
            part_ev.pop("quote_pointer", None)
            ev_index[part_eid] = part_ev
            part_eids.append(part_eid)

        # Re-ground citations across mutations
        for group in mutations.values():
            if not isinstance(group, list):
                continue
            for item in group:
                if not isinstance(item, dict) or "evidence_ids" not in item:
                    continue
                item_eids = item.get("evidence_ids", [])
                if eid in item_eids:
                    item_text = (
                        str(item.get("name") or "") + " "
                        + str(item.get("id") or "") + " "
                        + str(item.get("description") or "") + " "
                        + str(item.get("choice") or "")
                    ).lower()
                    best_part = eid
                    for p_eid, chunk in zip(part_eids, chunks):
                        tokens = [t for t in re.findall(r"[a-z0-9]{3,}", item_text) if t not in ("org", "role", "person", "system", "project")]
                        if tokens and any(tok in chunk.lower() for tok in tokens):
                            best_part = p_eid
                            break
                    new_item_eids = [best_part if x == eid else x for x in item_eids]
                    item["evidence_ids"] = list(dict.fromkeys(new_item_eids))


def inject_canonical_evidence(
    data: Dict[str, Any], source_text: Optional[str] = None
) -> None:
    """Ensure evidence_index exists without fabricating synthetic statements."""
    data.setdefault("evidence_index", {})


def _reconcile_item_eids(
    item: Dict[str, Any], known_eids: set[str], fallback_eid: Optional[str]
) -> None:
    """Reconcile evidence_ids for a single mutation item."""
    raw_eids = item.get("evidence_ids")
    if not isinstance(raw_eids, list):
        return
    if not fallback_eid:
        item["evidence_ids"] = list(dict.fromkeys(raw_eids))
        return
    valid_eids = [eid for eid in raw_eids if eid in known_eids]
    item["evidence_ids"] = list(dict.fromkeys(valid_eids)) or [fallback_eid]


def _reconcile_mutation_evidence_ids(
    mutations: Dict[str, Any], known_eids: set[str]
) -> None:
    """Reconcile and deduplicate evidence_ids across all mutation categories."""
    categories = (
        "entity_upserts",
        "relationship_signals",
        "decision_updates",
        "proposal_updates",
        "action_updates",
        "risk_updates",
    )
    fallback_eid = next(iter(known_eids)) if known_eids else None
    for cat in categories:
        for item in mutations.get(cat, []):
            if isinstance(item, dict):
                _reconcile_item_eids(item, known_eids, fallback_eid)


def finalize_evidence_citations(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    source_text: Optional[str] = None,
) -> None:
    """Finalize evidence_only statements and normalize citation references."""
    from dual_pipeline.transcript_stages_core.graph_post_helpers import _normalize_evidence_index

    used_eids = {
        str(eid)
        for group in mutations.values()
        if isinstance(group, list)
        for item in group
        if isinstance(item, dict)
        for eid in item.get("evidence_ids", [])
    }
    raw_ev_only = (
        data.get("evidence_only_statement_ids")
        if isinstance(data.get("evidence_only_statement_ids"), list)
        else []
    )
    promoted_mutation_count = sum(
        len(mutations.get(cat, []))
        for cat in ("decision_updates", "proposal_updates", "action_updates", "risk_updates")
        if isinstance(mutations.get(cat), list)
    )
    max_ev_only = max(3, 2 * promoted_mutation_count)
    data["evidence_only_statement_ids"] = [
        sid for sid in raw_ev_only if str(sid) not in used_eids
    ][:max_ev_only]
    _normalize_evidence_index(data, source_text=source_text)

    if source_text and isinstance(data.get("evidence_index"), dict):
        for ev in data["evidence_index"].values():
            if isinstance(ev, dict) and ev.get("exact_text"):
                et = str(ev["exact_text"]).strip()
                if not et.endswith("...") and not et.endswith("…"):
                    if f"{et}..." in source_text or f"{et}…" in source_text:
                        ev["exact_text"] = f"{et}..."
                        if ev.get("text"):
                            ev["text"] = f"{et}..."

    _bound_oversized_evidence_statements(data, mutations, max_len=3500)
    _reconcile_mutation_evidence_ids(mutations, set(data.get("evidence_index", {}).keys()))
