"""Shared test doubles (no network access)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from phoenix.config import Settings


class FakeResponse:
    def __init__(
        self,
        payload: Any = None,
        *,
        status: int = 200,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)
        self.headers = headers or {}
        self.url = "https://test.invalid"
        self.request = None

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def close(self) -> None:
        pass


class FakeStream(FakeResponse):
    def __init__(
        self,
        chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__({}, status=status, headers=headers)
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size: int = 0):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


@dataclass
class Call:
    method: str
    url: str
    kwargs: dict[str, Any]


class FakeHTTP:
    """Minimal requests.Session stand-in."""

    def __init__(self, handler: Callable[[str, str, dict[str, Any]], FakeResponse]) -> None:
        self.handler = handler
        self.calls: list[Call] = []
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(Call(method, url, kwargs))
        return self.handler(method, url, kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(session_file=tmp_path / "session.json")
