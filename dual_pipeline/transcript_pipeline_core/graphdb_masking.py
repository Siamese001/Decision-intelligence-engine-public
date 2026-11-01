from pathlib import Path
from typing import Any, Dict, Optional


class GraphDBMasker:
    """
    Performs pre-flight masking to anonymize sensitive text (PII, entities)
    before L2 transmission by checking against the local GraphDB Canonical Enterprise Directory.
    Supports authentic SQLite database querying, honest caching, and operation provenance.
    """
    def __init__(self, semantic_cache: Optional[Any] = None, db_path: Optional[Path | str] = None):
        self.cache = semantic_cache
        self.db_path = Path(db_path) if db_path else None
        self._registered_entities: Dict[str, Dict[str, Any]] = {}
        self._memory_cache: Dict[str, str] = {}
        self._last_provenance: Dict[str, Any] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def register_entity(self, entity_name: str, **kwargs) -> None:
        """Registers an explicit entity into the local directory."""
        self._registered_entities[entity_name] = kwargs

    def register_entity_in_test_graph(self, entity_name: str, **kwargs) -> None:
        """Backward compatibility alias for explicit entity registration."""
        self.register_entity(entity_name, **kwargs)

    @property
    def _test_graph(self) -> Dict[str, Dict[str, Any]]:
        """Backward compatibility property returning registered entities."""
        return self._registered_entities

    def _load_db_entities(self) -> Dict[str, Dict[str, Any]]:
        """Loads entities from SQLite database if configured."""
        if not self.db_path or not self.db_path.exists():
            return {}

        entities = {}
        try:
            from storage.connection import create_sqlite_conn
            conn = create_sqlite_conn(self.db_path, readonly=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='canonical_graph_nodes'")
                if cur.fetchone():
                    cur.execute("SELECT name, type, properties FROM canonical_graph_nodes")
                    for row in cur.fetchall():
                        name, n_type, props = row[0], row[1], row[2]
                        entities[name] = {"role": n_type, "properties": props}
            finally:
                conn.close()
        except Exception:
            pass
        return entities

    def get_last_provenance(self) -> Dict[str, Any]:
        """Returns provenance metadata from the most recent masking operation."""
        return self._last_provenance

    def mask_payload(self, text: str) -> str:
        """
        Scans the text for known entities in the GraphDB, masking them.
        Uses Semantic Cache / memory cache to speed up resolution.
        """
        cache_key = f"mask_payload_{hash(text)}"
        if self.cache and hasattr(self.cache, "get"):
            cached_val = self.cache.get(cache_key)
            if cached_val is not None:
                self.cache_hits += 1
                return str(cached_val)
        elif cache_key in self._memory_cache:
            self.cache_hits += 1
            return self._memory_cache[cache_key]

        self.cache_misses += 1
        masked_text = text

        # Merge DB entities with explicitly registered entities (registered take precedence)
        all_entities = self._load_db_entities()
        all_entities.update(self._registered_entities)

        provenance_source = "sqlite" if (self.db_path and self.db_path.exists()) else "explicit_registry"
        entities_matched = 0

        for idx, (entity_name, metadata) in enumerate(all_entities.items(), start=1):
            if entity_name in text:
                entities_matched += 1
                role = metadata.get("role") or metadata.get("type") or "Unknown"
                replacement = f"[MASKED_ENTITY_{idx}:{role}]"
                masked_text = masked_text.replace(entity_name, replacement)

        self._last_provenance = {
            "source": provenance_source,
            "db_path": str(self.db_path) if self.db_path else None,
            "total_entities_available": len(all_entities),
            "entities_matched": entities_matched,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }

        if self.cache and hasattr(self.cache, "set"):
            self.cache.set(cache_key, masked_text)
        self._memory_cache[cache_key] = masked_text

        return masked_text
