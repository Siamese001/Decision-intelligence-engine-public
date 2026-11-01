import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class PreflightAutoScaffolder:
    """Scans the first 1000 tokens of the transcript to auto-scaffold missing Tier-1 metadata."""
    def __init__(self, provider):
        self.provider = provider

    def execute(self, transcript: str) -> Dict[str, Any]:
        logger.info("Executing Preflight Auto-Scaffolder...")
        prompt = (
            "Extract meeting metadata from the following transcript snippet.\n"
            "Return JSON with:\n"
            "- meeting_name\n"
            "- date (MM/DD/YYYY)\n"
            "- time\n"
            "- attendee_roster (list of names and roles/orgs)"
        )
        snippet = transcript[:4000] # ~1000 tokens

        try:
            # Use dedicated lightweight metadata synthesis if provider supports it, falling back to graph synthesis
            if hasattr(self.provider, "synthesize_metadata"):
                result = self.provider.synthesize_metadata(prompt, snippet)
            else:
                result = self.provider.synthesize_graph(prompt, snippet)
            return json.loads(result)
        except Exception as e:
            logger.warning(f"Auto-scaffolder failed: {e}")
            return {}
