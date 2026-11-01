"""
dual_pipeline.config
Pipeline configuration and runtime settings.
"""

from .pipeline_settings import (
    MAX_JUDGE_RETRIES_DEFAULT,
    MAX_NETWORK_RETRIES,
    MAX_PAYLOAD_RETRIES,
    NETWORK_BASE_DELAY_SECONDS,
    NETWORK_MAX_DELAY_SECONDS,
)

__all__ = [
    "MAX_PAYLOAD_RETRIES",
    "MAX_JUDGE_RETRIES_DEFAULT",
    "MAX_NETWORK_RETRIES",
    "NETWORK_BASE_DELAY_SECONDS",
    "NETWORK_MAX_DELAY_SECONDS",
]
