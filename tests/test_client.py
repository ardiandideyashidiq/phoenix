from __future__ import annotations

import pytest

from phoenix.client import PhoenixClient
from phoenix.errors import AuthError, OperationError
from phoenix.models import Session

from .conftest import FakeHTTP, FakeResponse


def make_client(settings, handler) -> PhoenixClient:
    return PhoenixClient(settings, http=FakeHTTP(handler))


def test_login_saves_session(settings):
    def handler(method, url, kwargs):
        assert url.endswith("/api/auth/login")
        assert kwargs["json"] == {"email": "a@b.c", "password": "pw"}
        return FakeResponse({"status": "OK", "token": "tok", "name": "N", "credit": 7})

    client = make_client(settings, handler)
    session = client.login("a@b.c", "pw")

    assert session == Session(token="tok", name="N", credit=7, email="a@b.c")
    assert client.http.headers["Authorization"] == "Bearer tok"
    assert settings.session_file.exists()
    assert '"tok"' in settings.session_file.read_text()


def test_login_failure_raises(settings):
    client = make_client(settings, lambda *_: FakeResponse({"status": "ERROR", "message": "bad"}))
    with pytest.raises(AuthError, match="bad"):
        client.login("a@b.c", "wrong")


def test_restore_rejects_expired_token(settings):
    client = make_client(settings, lambda *_: FakeResponse({"message": "Unauthenticated."}, status=401))
    client.store.save(Session(token="expired", email="a@b.c"))

    assert client.restore() is None
    assert "Authorization" not in client.http.headers


def test_restore_accepts_valid_token(settings):
    client = make_client(settings, lambda *_: FakeResponse({"status": True, "data": []}))
    client.store.save(Session(token="good", email="a@b.c"))

    assert client.restore() is not None
    assert client.http.headers["Authorization"] == "Bearer good"


def test_models_are_parsed(settings):
    payload = {"status": True, "data": [{"brand": "TECNO", "model": "X6871"}, {"brand": "ITEL"}]}
    client = make_client(settings, lambda *_: FakeResponse(payload))
    client.session = Session(token="tok")

    models = client.models()
    assert [(m.brand, m.model) for m in models] == [("TECNO", "X6871"), ("ITEL", "")]


def test_operation_failure_raises(settings):
    client = make_client(settings, lambda *_: FakeResponse({"status": False, "message": "CREDIT NOT ENOUGH"}))
    client.session = Session(token="tok")

    with pytest.raises(OperationError, match="CREDIT NOT ENOUGH"):
        client.operation("firmware_link", project="X6871", version="V1")


def test_non_json_response_raises(settings):
    client = make_client(settings, lambda *_: FakeResponse(ValueError("no json"), text="<html>oops</html>"))
    client.session = Session(token="tok")

    with pytest.raises(OperationError, match="Non-JSON"):
        client.server_info()
