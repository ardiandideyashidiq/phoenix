"""Transsion firmware catalogue and share-link generation."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from loguru import logger

from .client import PhoenixClient
from .config import CMD_FIRMWARE_LINK, CMD_FIRMWARE_LIST
from .errors import OperationError
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


class FirmwareService:
    """Firmware operations layered on top of :class:`PhoenixClient`."""

    def __init__(self, client: PhoenixClient) -> None:
        self.client = client

    def list(self, project: str) -> list[FirmwareVersion]:
        """List firmware versions for a project. Costs no credits."""
        project = normalize_project(project)
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
        logger.info("Found {} firmware version(s) for {}", len(versions), project)
        return versions

    def link(self, project: str, version: str) -> ShareLink:
        """Resolve a version to a frbox share link. Costs one credit."""
        project = normalize_project(project)
        version = (version or "").strip()
        if not version:
            raise ValueError("version is required")

        data = self.client.operation(CMD_FIRMWARE_LINK, project=project, version=version)
        url = decode_payload(data.get("data"))
        if not url:
            raise OperationError("Server returned no share link")
        password = str(data.get("pwd") or "") or None
        logger.info("Shared {} ({})", version, "password protected" if password else "no password")
        return ShareLink.from_url(url, password)
