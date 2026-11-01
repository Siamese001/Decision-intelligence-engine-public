#!/usr/bin/env python3
"""Module alias pointing to pipeline_b_extraction.worker."""
from pipeline_b_extraction.worker import (
    DiscourseBatch,
    ExtractionQueueWorker,
    SlowPathWorker,
    WorkerConfig,
)

__all__ = [
    "DiscourseBatch",
    "ExtractionQueueWorker",
    "SlowPathWorker",
    "WorkerConfig",
]
