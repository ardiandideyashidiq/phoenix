from __future__ import annotations

import os
import stat

from phoenix.models import Session
from phoenix.session import SessionStore


def test_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "nested" / "session.json")
    session = Session(token="tok", name="N", credit=3, email="a@b.c")

    store.save(session)

    assert store.load() == session
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600


def test_missing_file_returns_none(tmp_path):
    assert SessionStore(tmp_path / "none.json").load() is None


def test_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{not json")
    assert SessionStore(path).load() is None


def test_empty_token_returns_none(tmp_path):
    path = tmp_path / "session.json"
    path.write_text('{"token": ""}')
    assert SessionStore(path).load() is None


def test_clear(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    store.save(Session(token="tok"))

    assert store.clear() is True
    assert store.clear() is False
