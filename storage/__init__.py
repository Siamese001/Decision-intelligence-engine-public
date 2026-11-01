"""Dual-Artery Enterprise Hybrid Architecture Storage Layer.

Provides WAL-enabled durable storage for immutable evidence, durable work queues,
canonical graph nodes, and SQLCipher/Keychain-protected internal advisory storage.
"""
from sqlite3 import Connection, Error as StorageError
from storage.credentials import CredentialManager
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
    WorkItem,
    WorkItemState,
)

__all__ = [
    "CredentialManager",
    "DatabaseManager",
    "EvidenceRecord",
    "WorkItem",
    "CanonicalGraphNode",
    "InternalAdvisoryNode",
    "WorkItemState",
    "AdvisoryCategory",
    "Connection",
    "StorageError",
]

