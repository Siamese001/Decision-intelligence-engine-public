"""Persistence handlers for shift-left HITL decisions and alias mappings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("nee.hitl.persistence")


def _write_decision_ledger(paths: List[Path], decisions: List[Dict[str, Any]]) -> None:
    """Write deduplicated decision records to ledger paths."""
    for lp in paths:
        try:
            existing: List[Dict[str, Any]] = []
            if lp.is_file():
                data = json.loads(lp.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    existing = data
            seen_ids = {d.get("decision_id") for d in existing if isinstance(d, dict)}
            for dec in decisions:
                if dec.get("decision_id") not in seen_ids:
                    existing.append(dec)
                    seen_ids.add(dec.get("decision_id"))
            lp.parent.mkdir(parents=True, exist_ok=True)
            lp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed writing hitl_decision_ledger at %s: %s", lp, e)


def persist_shift_left_hitl_decisions(
    decisions: List[Dict[str, Any]],
    output_dir: Union[Path, str],
    runtime_root: Union[Path, str],
    graph_path: Optional[Union[Path, str]] = None,
) -> None:
    """Persist operator exception decisions to hitl_decision_ledger and canonical_alias."""
    if not decisions:
        return
    out_p = Path(output_dir).resolve()
    rt_root = Path(runtime_root).resolve()
    ledger_paths = list(dict.fromkeys([out_p / "hitl_decision_ledger.json", rt_root / "hitl_decision_ledger.json"]))
    _write_decision_ledger(ledger_paths, decisions)
    if graph_path:
        from dual_pipeline.graph_writer_core.inbox_mutations import sync_canonical_aliases_to_graph  # noqa: lazy-decouple

        sync_canonical_aliases_to_graph(Path(graph_path).resolve(), decisions)
