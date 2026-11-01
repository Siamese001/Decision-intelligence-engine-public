from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import hashlib

@dataclass
class BoundedContext:
    """Wave 7 Context Contract: Supplies authorized prior-source and graph context."""
    authorized_scope: str
    source_ids: List[str] = field(default_factory=list)
    graph_generation: Optional[str] = None
    runtime_generation: Optional[str] = None
    retrieval_policy: str = "STRICT_BOUNDARY"
    retrieval_version: str = "1.0.0"
    bounds: Dict[str, Any] = field(default_factory=dict)
    availability_status: str = "UNAVAILABLE"
    
    # Payload
    entities: List[Dict[str, Any]] = field(default_factory=list)
    assertions: List[Dict[str, Any]] = field(default_factory=list)
    prior_session_context: str = ""
    
    @property
    def digest(self) -> str:
        payload = f"{self.authorized_scope}|{','.join(sorted(self.source_ids))}|{self.graph_generation}|{self.availability_status}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        
    def is_available(self) -> bool:
        return self.availability_status == "AVAILABLE"
