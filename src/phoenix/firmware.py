"""Transsion firmware catalogue and share-link generation."""

from __future__ import annotations

import base64
import binascii
import json
import string
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from .client import PhoenixClient
from .config import CMD_FIRMWARE_LINK, CMD_FIRMWARE_LIST
from .errors import OperationError, PhoenixError
from .models import FirmwareVersion, ShareLink


def decode_payload(value: Any) -> str:
    """Decode a base64 ``data`` field, tolerating plain-text payloads."""
    text = str(value or "").strip()
    if not text:
        return ""
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return text


def decode_json(value: Any) -> Any:
    text = decode_payload(value)
    if not text:
        raise OperationError("Server returned an empty payload")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OperationError(f"Cannot parse server payload: {text[:200]}") from exc


def normalize_project(project: str) -> str:
    normalized = (project or "").strip().upper()
    if not normalized:
        raise ValueError("project is required")
    return normalized


# The server keeps a separate catalogue per variant, e.g. ``X6871``,
# ``X6871(ANDROID 15)``, ``X6871(ANDROID 16)`` and ``X670-T``. A bare code
# only returns the oldest group, so probe the variants Transsion ships.
ANDROID_LEVELS = range(12, 19)
PROJECT_SUFFIXES = string.ascii_uppercase
MAX_PROBES = 8


def candidate_projects(project: str) -> list[str]:
    """Return the project plus its Android and letter-suffixed catalogues."""
    project = normalize_project(project)
    if "(" in project or "-" in project:
        return [project]
    return [
        project,
        *(f"{project}(ANDROID {level})" for level in ANDROID_LEVELS),
        *(f"{project}-{suffix}" for suffix in PROJECT_SUFFIXES),
    ]


class FirmwareService:
    """Firmware operations layered on top of :class:`PhoenixClient`."""

    def __init__(self, client: PhoenixClient) -> None:
        self.client = client

    def _list_one(self, project: str) -> list[FirmwareVersion]:
        data = self.client.operation(CMD_FIRMWARE_LIST, project=project)
        payload = decode_json(data.get("data"))
        if not isinstance(payload, list):
            raise OperationError("Expected a firmware list")

        versions = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            version = str(item.get("version") or "").strip()
            if not version:
                continue
            versions.append(
                FirmwareVersion(
                    project=str(item.get("project") or project).strip(),
                    market=str(item.get("market") or "").strip(),
                    version=version,
                    date=str(item.get("date") or "").strip(),
                )
            )
        return versions

    def _fetch_all(self, projects: list[str]) -> dict[str, list[FirmwareVersion]]:
        """Fetch every catalogue; the base project fails hard, variants softly."""
        base, *variants = projects
        catalogues = {base: self._list_one(base)}
        if variants:
            # The session is only read and its connection pool is thread-safe.
            with ThreadPoolExecutor(max_workers=min(MAX_PROBES, len(variants))) as pool:
                catalogues.update(pool.map(self._fetch_variant, variants))
        return catalogues

    def _fetch_variant(self, project: str) -> tuple[str, list[FirmwareVersion]]:
        try:
            return project, self._list_one(project)
        except PhoenixError as exc:
            logger.warning("Skipping project {}: {}", project, exc)
            return project, []

    def list(self, project: str) -> list[FirmwareVersion]:
        """List firmware across the project and its variants. Free."""
        projects = candidate_projects(project)
        catalogues = self._fetch_all(projects)
        unique: dict[str, FirmwareVersion] = {}
        for candidate in projects:
            for version in catalogues[candidate]:
                unique.setdefault(version.version, version)
        versions = sorted(unique.values(), key=lambda item: item.date, reverse=True)
        logger.info("Found {} firmware version(s) for {}", len(versions), projects[0])
        return versions

    def _project_for(self, project: str, version: str) -> str:
        """Find the catalogue that owns ``version`` when given a bare code."""
        candidates = candidate_projects(project)
        if len(candidates) == 1:
            return candidates[0]
        catalogues = self._fetch_all(candidates)
        for candidate in candidates:
            if any(item.version == version for item in catalogues[candidate]):
                return candidate
        return candidates[0]

    def link(self, project: str, version: str) -> ShareLink:
        """Resolve a version to a frbox share link. Costs one credit."""
        project = normalize_project(project)
        version = (version or "").strip()
        if not version:
            raise ValueError("version is required")

        project = self._project_for(project, version)
        data = self.client.operation(CMD_FIRMWARE_LINK, project=project, version=version)
        url = decode_payload(data.get("data"))
        if not url:
            raise OperationError("Server returned no share link")
        password = str(data.get("pwd") or "") or None
        logger.info("Shared {} ({})", version, "password protected" if password else "no password")
        return ShareLink.from_url(url, password)
