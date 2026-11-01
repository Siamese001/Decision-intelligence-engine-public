from typing import Any, Dict

from dual_pipeline.l2_policy_core.models import ModelPolicy
from dual_pipeline.l2_providers_core.types import L2ProviderError, ProviderFailureCode


def validate_context_window(request_metrics: Dict[str, Any], model_policy: ModelPolicy) -> None:
    """
    Conservative token estimate (1 token per 3.5 chars) against model context limits.
    Raises L2ProviderError(BUDGET_EXHAUSTED) if the limit is exceeded.
    """
    total_chars = request_metrics.get("request_input_chars", 0)
    estimated_tokens = total_chars / 3.5

    limit = getattr(model_policy, "context_window_tokens", None)
    if limit is not None and estimated_tokens > limit:
        raise L2ProviderError(
            code=ProviderFailureCode.BUDGET_EXHAUSTED,
            safe_message=f"BUDGET_EXHAUSTED: Estimated tokens {estimated_tokens:.0f} exceeds context window limit of {limit}."
        )

