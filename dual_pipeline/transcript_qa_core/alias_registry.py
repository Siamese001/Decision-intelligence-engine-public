"""
dual_pipeline/transcript_qa_core/alias_registry.py

Governed runtime accessor and atomic mutator for dynamic entity/system aliases.
Supports Layer 4 healing mesh and dynamic alias learning without code modifications.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_ALIASES_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "config"
    / "runtime"
    / "learned_aliases.json"
)

# Bootstrap hardcoded fallbacks
BOOTSTRAP_ALIASES: Dict[str, str] = {
    "system:technosilva": "system:technosylva",
    "system:tens_sharepoint": "system:tens-sharepoint",
    "org:technosilva": "org:technosylva",
    "system:entity-de48c15b616942cd9c58b93da7d7": "workstream:apex_trans-insights-hub",
    "entity-de48c15b616942cd9c58b93da7d7": "workstream:apex_trans-insights-hub",
    "system:person_anece_elakkari": "person:anece-elakkari",
    "person_anece_elakkari": "person:anece-elakkari",
}


def load_learned_aliases(aliases_path: Optional[Path] = None) -> Dict[str, str]:
    """Loads dynamic aliases merged on top of bootstrap defaults."""
    merged = dict(BOOTSTRAP_ALIASES)
    path = aliases_path or DEFAULT_ALIASES_PATH
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                custom = data.get("aliases", {})
                if isinstance(custom, dict):
                    for k, v in custom.items():
                        if isinstance(k, str) and isinstance(v, str):
                            merged[k.strip()] = v.strip()
        except Exception as e:
            logger.warning("Failed to load learned aliases from %s: %s", path, e)
    return merged


def register_learned_alias(
    from_id: str,
    to_id: str,
    aliases_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Atomically registers an approved alias into learned_aliases.json."""
    path = aliases_path or DEFAULT_ALIASES_PATH
    data = {
        "schema_version": "1.0.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Governed runtime store for dynamically learned entity and system aliases.",
        "aliases": dict(BOOTSTRAP_ALIASES),
    }
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data.update(loaded)
        except Exception:
            pass

    aliases = data.setdefault("aliases", {})
    aliases[from_id.strip()] = to_id.strip()
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)
    return load_learned_aliases(path)


def persist_learned_alias(
    from_id: str,
    to_id: str,
    proposal_id: Optional[str] = None,
    *,
    registry_path: Optional[Path] = None,
    aliases_path: Optional[Path] = None,
) -> Dict[str, str]:
    """Persists an approved alias mapping (alias for register_learned_alias)."""
    _ = proposal_id
    return register_learned_alias(from_id, to_id, aliases_path=registry_path or aliases_path)
