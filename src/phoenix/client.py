"""HTTP client for the Phoenix Service Tool API."""

from __future__ import annotations

from typing import Any

import requests
from loguru import logger

from .config import (
    EP_LOGIN,
    EP_MODELS,
    EP_SERVER_INFO,
    EP_SERVER_STATUS,
    EP_TRANSSION_OP,
    Settings,
)
from .errors import AuthError, OperationError, PhoenixError
from .models import DeviceModel, Session
from .session import SessionStore

JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


class PhoenixClient:
    """Authenticated access to phoenixservicetool.com."""

    def __init__(
        self,
        settings: Settings | None = None,
        http: requests.Session | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.http = http or requests.Session()
        self.http.headers.update({"User-Agent": self.settings.user_agent, **JSON_HEADERS})
        self.store = SessionStore(self.settings.session_file)
        self.session: Session | None = None

    # --- transport ---------------------------------------------------

    def _url(self, path: str) -> str:
        return self.settings.base_url.rstrip("/") + path

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        verify: bool | None = None,
    ) -> requests.Response:
        url = self._url(path)
        request_kwargs: dict[str, Any] = {
            "json": payload,
            "timeout": timeout if timeout is not None else self.settings.timeout,
            "verify": self.settings.verify_ssl if verify is None else verify,
        }
        logger.debug("{} {} payload={}", method, url, payload)
        try:
            response = self.http.request(method, url, **request_kwargs)
        except requests.exceptions.SSLError as exc:
            if not request_kwargs["verify"]:
                raise PhoenixError(f"TLS error contacting {url}: {exc}") from exc
            logger.warning("TLS verification failed, retrying without verification: {}", exc)
            request_kwargs["verify"] = False
            response = self.http.request(method, url, **request_kwargs)
        except requests.exceptions.RequestException as exc:
            raise PhoenixError(f"Cannot reach {url}: {exc}") from exc
        logger.debug("HTTP {} -> {}", response.status_code, url)
        return response

    def _parse(self, response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise OperationError(
                f"Non-JSON response (HTTP {response.status_code}): {response.text[:300]}"
            ) from exc
        if not isinstance(data, dict):
            raise OperationError(f"Unexpected response: {str(data)[:300]}")
        if (
            response.status_code == 401
            or data.get("status") == "Unauthenticated"
            or data.get("message") == "Unauthenticated."
        ):
            raise AuthError(str(data.get("message") or "Authentication required"))
        return data

    def _post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._parse(self._request("POST", path, payload=payload or {}, **kwargs))

    # --- session -----------------------------------------------------

    def _apply(self, session: Session) -> None:
        self.session = session
        self.http.headers["Authorization"] = f"Bearer {session.token}"

    def _drop(self) -> None:
        self.session = None
        self.http.headers.pop("Authorization", None)

    def login(self, email: str, password: str, *, remember: bool = True) -> Session:
        data = self._post(EP_LOGIN, {"email": email, "password": password})
        token = data.get("token")
        if not token:
            raise AuthError(str(data.get("message") or f"Login failed ({data.get('status', 'unknown')})"))
        session = Session(
            token=str(token),
            name=str(data.get("name") or ""),
            credit=int(data.get("credit") or 0),
            email=email,
        )
        self._apply(session)
        if remember:
            self.store.save(session)
        logger.info("Logged in as {} (credit {})", session.name or email, session.credit)
        return session

    def restore(self, *, validate: bool = True) -> Session | None:
        """Load the saved token and optionally check it against the server."""
        session = self.store.load()
        if session is None:
            return None
        self._apply(session)
        if validate and not self.is_authenticated():
            logger.info("Saved session is no longer valid")
            self._drop()
            return None
        return session

    def is_authenticated(self) -> bool:
        if self.session is None:
            return False
        try:
            self._post(EP_MODELS, {}, timeout=min(10.0, self.settings.timeout))
        except AuthError:
            return False
        except PhoenixError as exc:
            logger.warning("Session check failed: {}", exc)
            return False
        return True

    def logout(self) -> bool:
        """Forget the local token. Returns True when a session file existed."""
        self._drop()
        return self.store.clear()

    # --- endpoints ---------------------------------------------------

    def server_status(self) -> dict[str, Any]:
        return self._parse(self._request("GET", EP_SERVER_STATUS))

    def server_info(self) -> dict[str, Any]:
        return self._post(EP_SERVER_INFO)

    def models(self) -> list[DeviceModel]:
        data = self._post(EP_MODELS)
        return [
            DeviceModel(
                brand=str(item.get("brand") or ""),
                model=str(item.get("model") or ""),
            )
            for item in data.get("data") or []
            if isinstance(item, dict)
        ]

    def operation(
        self,
        command: str,
        *,
        project: str | None = None,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Send a Transsion operation command and return the raw response."""
        payload: dict[str, Any] = {"command": command}
        if project:
            payload["project_name"] = project
        if version:
            payload["version_id"] = version
        data = self._post(EP_TRANSSION_OP, payload)
        if data.get("status") is False:
            raise OperationError(str(data.get("message") or "Operation failed"))
        return data
