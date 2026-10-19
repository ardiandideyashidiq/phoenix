"""Persist the Bearer token between runs."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from loguru import logger

from .models import Session


class SessionStore:
    """JSON-backed session file, readable only by the owner."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> Session | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text())
            token = data.get("token")
            if not token:
                return None
            session = Session(
                token=str(token),
                name=str(data.get("name") or ""),
                credit=int(data.get("credit") or 0),
                email=str(data.get("email") or ""),
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Cannot read session {}: {}", self.path, exc)
            return None
        logger.debug("Loaded session for {}", session.email or session.name or "unknown user")
        return session

    def save(self, session: Session) -> None:
        payload = {
            "token": session.token,
            "name": session.name,
            "credit": session.credit,
            "email": session.email,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".phoenix-session-")
        try:
            with os.fdopen(handle, "w") as stream:
                json.dump(payload, stream, indent=2)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        logger.debug("Saved session to {}", self.path)

    def clear(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True
