"""
dual_pipeline/integrations/directory.py

Enterprise user directory integration for attendee and speaker identity resolution.
Supports SCIM 2.0 and Microsoft Graph Users API specifications to map raw transcript
speaker tokens to canonical person entity IDs (person:<user_id>).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DirectoryUser:
    """Canonical enterprise user record."""

    user_id: str
    display_name: str
    primary_email: str
    secondary_emails: List[str] = field(default_factory=list)
    title: Optional[str] = None
    department: Optional[str] = None
    aliases: List[str] = field(default_factory=list)

    @property
    def canonical_entity_id(self) -> str:
        # Standardized W2 entity ID prefix
        clean_id = re.sub(r"[^\w-]", "_", self.user_id.lower())
        return f"person:{clean_id}"


def parse_scim_user_payload(resource: Dict[str, Any]) -> DirectoryUser:
    """Parses a SCIM 2.0 User JSON resource (RFC 7643)."""
    user_id = resource.get("id") or resource.get("userName") or "unknown_user"
    display_name = resource.get("displayName") or resource.get("userName", "Unknown User")

    emails = resource.get("emails", [])
    primary_email = ""
    secondary_emails: List[str] = []

    for email_entry in emails:
        val = email_entry.get("value", "")
        if email_entry.get("primary", False) or not primary_email:
            primary_email = val
        else:
            secondary_emails.append(val)

    title = resource.get("title")
    department = resource.get("enterpriseExtension", {}).get("department") or resource.get("department")

    aliases: List[str] = [display_name.lower()]
    name_obj = resource.get("name", {})
    given_name = name_obj.get("givenName")
    family_name = name_obj.get("familyName")
    if given_name and family_name:
        aliases.append(f"{given_name} {family_name}".lower())
        aliases.append(f"{given_name} {family_name[0]}".lower())
        aliases.append(f"{given_name[0]} {family_name}".lower())

    return DirectoryUser(
        user_id=user_id,
        display_name=display_name,
        primary_email=primary_email,
        secondary_emails=secondary_emails,
        title=title,
        department=department,
        aliases=list(set(aliases)),
    )


def parse_ms_graph_users(response: Dict[str, Any]) -> List[DirectoryUser]:
    """Parses Microsoft Graph /v1.0/users response payload."""
    users_raw = response.get("value", []) if "value" in response else [response]
    users: List[DirectoryUser] = []

    for u in users_raw:
        user_id = u.get("id") or u.get("userPrincipalName", "unknown")
        display_name = u.get("displayName", "Unknown User")
        primary_email = u.get("mail") or u.get("userPrincipalName", "")
        other_mails = u.get("otherMails", [])
        title = u.get("jobTitle")
        department = u.get("department")

        aliases: List[str] = [display_name.lower()]
        given_name = u.get("givenName")
        surname = u.get("surname")
        if given_name and surname:
            aliases.append(f"{given_name} {surname}".lower())
            aliases.append(f"{given_name} {surname[0]}".lower())

        users.append(
            DirectoryUser(
                user_id=user_id,
                display_name=display_name,
                primary_email=primary_email,
                secondary_emails=other_mails,
                title=title,
                department=department,
                aliases=list(set(aliases)),
            )
        )

    return users


class DirectorySyncService:
    """Provides high-speed lookup and speaker token resolution against enterprise directory rosters."""

    def __init__(self, users: Optional[List[DirectoryUser]] = None) -> None:
        self._users_by_id: Dict[str, DirectoryUser] = {}
        self._users_by_email: Dict[str, DirectoryUser] = {}
        self._users_by_alias: Dict[str, DirectoryUser] = {}
        if users:
            for u in users:
                self.add_user(u)

    def add_user(self, user: DirectoryUser) -> None:
        self._users_by_id[user.user_id] = user
        if user.primary_email:
            self._users_by_email[user.primary_email.lower()] = user
        for em in user.secondary_emails:
            self._users_by_email[em.lower()] = user
        for al in user.aliases:
            self._users_by_alias[al.lower().strip()] = user

    def resolve_speaker_token(self, token: str) -> Optional[DirectoryUser]:
        """
        Attempts to resolve an attendee string (email, full name, or informal alias)
        to a canonical DirectoryUser.
        """
        if not token:
            return None
        clean = token.strip().lower()

        # 1. Email exact match
        if "@" in clean and clean in self._users_by_email:
            return self._users_by_email[clean]

        # 2. User ID exact match
        if clean in self._users_by_id:
            return self._users_by_id[clean]

        # 3. Alias / Display name exact match
        if clean in self._users_by_alias:
            return self._users_by_alias[clean]

        # 4. Prefix / partial name match
        for alias, user in self._users_by_alias.items():
            if alias.startswith(clean) or clean.startswith(alias):
                return user

        return None
