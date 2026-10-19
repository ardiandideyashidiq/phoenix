"""Value objects returned by the client."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    name: str = ""
    credit: int = 0
    email: str = ""


@dataclass(frozen=True, slots=True)
class DeviceModel:
    brand: str
    model: str


@dataclass(frozen=True, slots=True)
class FirmwareVersion:
    project: str
    market: str
    version: str
    date: str


@dataclass(frozen=True, slots=True)
class ShareLink:
    url: str
    password: str | None = None

    @property
    def share_id(self) -> str:
        parts = [part for part in urlparse(self.url).path.split("/") if part]
        return parts[-1] if parts else ""

    @classmethod
    def from_url(cls, url: str, password: str | None = None) -> ShareLink:
        """Build a link, taking ``?pwd=...`` from the URL when no password is given."""
        if not password:
            values = parse_qs(urlparse(url).query).get("pwd")
            if values:
                password = values[0]
        return cls(url=url, password=password or None)


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    file_id: str
    size: int = 0
    download_url: str = ""
