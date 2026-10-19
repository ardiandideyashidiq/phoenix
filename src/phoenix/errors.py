"""Exception hierarchy for the Phoenix client."""

from __future__ import annotations


class PhoenixError(Exception):
    """Base error for all Phoenix client failures."""


class AuthError(PhoenixError):
    """Login failed or the saved token is no longer valid."""


class OperationError(PhoenixError):
    """The server rejected an operation or returned an unusable payload."""


class ShareError(PhoenixError):
    """A frbox.transsion.com share cannot be resolved."""


class DownloadError(PhoenixError):
    """A firmware file cannot be written to disk."""
