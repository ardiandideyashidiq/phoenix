"""Configuration, endpoints and protocol constants."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

BASE_URL = "https://phoenixservicetool.com"
FRBOX_API = "https://fra315.api.aliyunpds.com"
FRBOX_ORIGIN = "https://frbox.transsion.com"
USER_AGENT = "PhoenixServiceTool/10.5.0"

EP_LOGIN = "/api/auth/login"
EP_MODELS = "/api/models"
EP_SERVER_INFO = "/api/server/info"
EP_SERVER_STATUS = "/api/server/status"
EP_TRANSSION_OP = "/api/server/transsion/operation"
EP_REGISTER = "/register"

CMD_FIRMWARE_LIST = "firmware_list"
CMD_FIRMWARE_LINK = "firmware_link"

DEFAULT_SESSION_FILE = Path.home() / ".phoenix_session.json"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration. Environment variables override the defaults."""

    base_url: str = BASE_URL
    timeout: float = 30.0
    verify_ssl: bool = True
    session_file: Path = DEFAULT_SESSION_FILE
    user_agent: str = USER_AGENT
    frbox_api: str = FRBOX_API

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "base_url": os.environ.get("PHOENIX_BASE_URL", BASE_URL),
            "timeout": float(os.environ.get("PHOENIX_TIMEOUT", "30")),
            "verify_ssl": not _env_bool("PHOENIX_NO_VERIFY_SSL", False),
            "session_file": Path(os.environ.get("PHOENIX_SESSION_FILE", str(DEFAULT_SESSION_FILE))),
            "user_agent": os.environ.get("PHOENIX_USER_AGENT", USER_AGENT),
            "frbox_api": os.environ.get("PHOENIX_FRBOX_API", FRBOX_API),
        }
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]

    def with_overrides(self, **overrides: object) -> Settings:
        return replace(self, **overrides)  # type: ignore[arg-type]
