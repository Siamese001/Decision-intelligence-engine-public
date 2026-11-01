"""
Core package for governed output directory hierarchy validation.
"""
from __future__ import annotations

from .constants import (
    ALLOWED_ROOT_AUX_DIRS,
    ALLOWED_ROOT_FILES,
    BOILERPLATE_SUBFOLDER_PREFIXES,
    CONTROLLED_SUPPORTING_DIRS,
    CONTROLLED_SUPPORTING_FILES,
    DATE_FOLDER_REGEX,
    KNOWN_PROJECTS,
    LEGACY_UNSCOPED_MULTI_MEETING_DATES,
    PROHIBITED_CACHE_RECEIPTS,
    PROHIBITED_EXTENSIONS,
    PROJECT_AFFINITY_MAP,
    PROJECT_DIR_REGEX,
)
from .container import (
    _validate_meeting_container,
    validate_transcript_cache,
)
from .deconfliction import (
    _is_override_authorized,
    detect_meeting_duplicates,
)
from .helpers import (
    _extract_date_token,
    _extract_stem,
    _validate_project_affinity,
)

__all__ = [
    "ALLOWED_ROOT_AUX_DIRS",
    "ALLOWED_ROOT_FILES",
    "BOILERPLATE_SUBFOLDER_PREFIXES",
    "CONTROLLED_SUPPORTING_DIRS",
    "CONTROLLED_SUPPORTING_FILES",
    "DATE_FOLDER_REGEX",
    "KNOWN_PROJECTS",
    "LEGACY_UNSCOPED_MULTI_MEETING_DATES",
    "PROHIBITED_CACHE_RECEIPTS",
    "PROHIBITED_EXTENSIONS",
    "PROJECT_AFFINITY_MAP",
    "PROJECT_DIR_REGEX",
    "_extract_date_token",
    "_extract_stem",
    "_is_override_authorized",
    "_validate_meeting_container",
    "_validate_project_affinity",
    "detect_meeting_duplicates",
    "validate_transcript_cache",
]
