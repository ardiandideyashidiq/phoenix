"""Phoenix Service Tool API client."""

from __future__ import annotations

from .client import PhoenixClient
from .config import Settings
from .downloader import Downloader
from .errors import AuthError, DownloadError, OperationError, PhoenixError, ShareError
from .firmware import FirmwareService
from .frbox import FrboxClient
from .models import DeviceModel, FirmwareVersion, RemoteFile, Session, ShareLink

__all__ = [
    "AuthError",
    "DeviceModel",
    "DownloadError",
    "Downloader",
    "FirmwareService",
    "FirmwareVersion",
    "FrboxClient",
    "OperationError",
    "PhoenixClient",
    "PhoenixError",
    "RemoteFile",
    "Session",
    "Settings",
    "ShareError",
    "ShareLink",
]
