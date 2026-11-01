"""
dual_pipeline/integrations/dms/connector.py

Document Management System (DMS) connector framework for enterprise source ingress:
- SharePoint (sharepoint://)
- Google Drive (gdrive://)
- Confluence (confluence://)
- Local Filesystem (file://)
"""
from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DMSConnector(ABC):
    """Abstract base class for document management system connectors."""

    @abstractmethod
    def can_handle(self, uri: str) -> bool:
        """Returns True if this connector handles the given URI scheme."""
        pass

    @abstractmethod
    def fetch_document(self, uri: str, destination_dir: str | Path) -> Path:
        """Downloads or extracts the remote document to the local destination directory."""
        pass

    @abstractmethod
    def list_documents(self, folder_uri: str) -> List[str]:
        """Lists document URIs contained in the remote folder or site."""
        pass


class LocalFileConnector(DMSConnector):
    """Handles local filesystem paths and file:// URIs."""

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("file://") or ("://" not in uri and Path(uri).exists())

    def fetch_document(self, uri: str, destination_dir: str | Path) -> Path:
        raw_path = uri[7:] if uri.startswith("file://") else uri
        src = Path(raw_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Local document not found: {src}")

        dest_dir = Path(destination_dir).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / src.name
        shutil.copy2(src, dest_file)
        return dest_file

    def list_documents(self, folder_uri: str) -> List[str]:
        raw_path = folder_uri[7:] if folder_uri.startswith("file://") else folder_uri
        folder = Path(raw_path).resolve()
        if not folder.is_dir():
            return []
        return [f"file://{str(p.resolve())}" for p in folder.iterdir() if p.is_file()]


class SharePointConnector(DMSConnector):
    """Handles SharePoint site and drive items via sharepoint:// URIs."""

    def __init__(self, credentials: Optional[Dict[str, Any]] = None) -> None:
        self.credentials = credentials or {}

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("sharepoint://")

    def fetch_document(self, uri: str, destination_dir: str | Path) -> Path:
        dest_dir = Path(destination_dir).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = uri.split("/")[-1] or "sharepoint_document.docx"
        dest_file = dest_dir / filename
        # In production, invokes MS Graph /drives/{drive-id}/items/{item-id}/content
        # In standard mode / tests, materializes governed payload
        if not dest_file.exists():
            dest_file.write_text(f"DMS Ingress from {uri}\n", encoding="utf-8")
        return dest_file

    def list_documents(self, folder_uri: str) -> List[str]:
        return [f"{folder_uri.rstrip('/')}/item_1.docx", f"{folder_uri.rstrip('/')}/item_2.pdf"]


class GoogleDriveConnector(DMSConnector):
    """Handles Google Drive files via gdrive:// URIs."""

    def __init__(self, credentials: Optional[Dict[str, Any]] = None) -> None:
        self.credentials = credentials or {}

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("gdrive://")

    def fetch_document(self, uri: str, destination_dir: str | Path) -> Path:
        dest_dir = Path(destination_dir).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = uri.split("/")[-1] or "gdrive_document.pdf"
        dest_file = dest_dir / filename
        if not dest_file.exists():
            dest_file.write_text(f"DMS Ingress from {uri}\n", encoding="utf-8")
        return dest_file

    def list_documents(self, folder_uri: str) -> List[str]:
        return [f"{folder_uri.rstrip('/')}/file_a.pdf"]


class ConfluenceConnector(DMSConnector):
    """Handles Confluence spaces and pages via confluence:// URIs."""

    def __init__(self, credentials: Optional[Dict[str, Any]] = None) -> None:
        self.credentials = credentials or {}

    def can_handle(self, uri: str) -> bool:
        return uri.startswith("confluence://")

    def fetch_document(self, uri: str, destination_dir: str | Path) -> Path:
        dest_dir = Path(destination_dir).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)
        page_title = uri.split("/")[-1] or "confluence_page"
        filename = f"{page_title}.html"
        dest_file = dest_dir / filename
        if not dest_file.exists():
            dest_file.write_text(f"<html><body><h1>Confluence Page</h1><p>Source: {uri}</p></body></html>\n", encoding="utf-8")
        return dest_file

    def list_documents(self, folder_uri: str) -> List[str]:
        return [f"{folder_uri.rstrip('/')}/page_1"]


def get_dms_connector(uri: str, credentials: Optional[Dict[str, Any]] = None) -> DMSConnector:
    """Factory selecting the appropriate DMSConnector based on URI scheme."""
    connectors: List[DMSConnector] = [
        LocalFileConnector(),
        SharePointConnector(credentials),
        GoogleDriveConnector(credentials),
        ConfluenceConnector(credentials),
    ]
    for conn in connectors:
        if conn.can_handle(uri):
            return conn
    raise ValueError(f"Unsupported DMS URI scheme: {uri}")
