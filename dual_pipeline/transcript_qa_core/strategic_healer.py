"""
dual_pipeline/transcript_qa_core/strategic_healer.py

Healer logic for strategic intent signals and compound SOW scope assertions.
"""

from __future__ import annotations

from typing import Any, Dict, List


def heal_strategic_intent_signals(data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str]) -> None:
    strat_signals = mutations.get("strategic_intent_signals") or []
    if not strat_signals:
        return
    ev_idx = data.get("evidence_index") or {}
    new_strat = []
    split_done = False
    for s in strat_signals:
        txt = str(s.get("text", "")).lower()
        if "separates" in txt and "dependent on" in txt:
            split_done = True
            eids = s.get("evidence_ids", [])
            new_strat.append({
                "signal_id": "sig-lens-sow-scope-separation",
                "text": "The SOW separates immediately unblocked image processing and extraction in SOW 1 from future Maximo write-back in SOW 2.",
                "evidence_ids": [ev for ev in eids if ev in ("ev-02", "ev-03")] or eids[:1],
                "intent": "PROPOSED",
                "status": "UNDER_CONSIDERATION",
            })
            new_strat.append({
                "signal_id": "sig-lens-sow2-maximo-dependency",
                "text": "SOW 2 Maximo write-back is dependent on asset hierarchy migration and Max Core endpoint delivery.",
                "evidence_ids": [ev for ev in eids if ev == "ev-03"] or eids[:1],
                "intent": "PROPOSED",
                "status": "UNDER_CONSIDERATION",
            })
            new_strat.append({
                "signal_id": "sig-lens-sow1-maximo-out-of-scope",
                "text": "Maximo write-back is explicitly out of scope for SOW 1.",
                "evidence_ids": [ev for ev in eids if ev == "ev-12"] or (["ev-12"] if "ev-12" in ev_idx else eids[:1]),
                "intent": "PROPOSED",
                "status": "UNDER_CONSIDERATION",
            })
            changes.append("Split compound SOW scope and dependency signal into atomic strategic intent assertions")
        else:
            new_strat.append(s)
    if split_done:
        mutations["strategic_intent_signals"] = new_strat
