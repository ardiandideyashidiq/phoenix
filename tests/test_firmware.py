from __future__ import annotations

import base64
import json

import pytest

from phoenix.errors import OperationError
from phoenix.firmware import FirmwareService, decode_payload, normalize_project
from phoenix.models import ShareLink


class StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, str | None, str | None]] = []

    def operation(self, command, *, project=None, version=None):
        self.calls.append((command, project, version))
        return self.payload


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def test_decode_payload_accepts_base64_and_plain_text():
    assert decode_payload(b64("hello")) == "hello"
    assert decode_payload("https://example.com/a?pwd=x") == "https://example.com/a?pwd=x"
    assert decode_payload("") == ""


def test_normalize_project():
    assert normalize_project(" x6871 ") == "X6871"
    with pytest.raises(ValueError):
        normalize_project("  ")


def test_list_decodes_and_maps_versions():
    payload = {
        "status": True,
        "data": b64(
            json.dumps(
                [
                    {"market": "EU", "version": "X6871-V1", "date": "2025-01-01", "project": "X6871"},
                    {"market": "RU", "version": "X6871-V2", "date": "2025-02-02"},
                    {"market": "XX"},
                ]
            )
        ),
    }
    client = StubClient(payload)
    versions = FirmwareService(client).list("x6871")

    assert client.calls == [("firmware_list", "X6871", None)]
    assert [(v.version, v.market, v.project) for v in versions] == [
        ("X6871-V1", "EU", "X6871"),
        ("X6871-V2", "RU", "X6871"),
    ]


def test_link_decodes_url_and_password():
    url = "https://frbox.transsion.com/disk/s/SID?pwd=secret"
    client = StubClient({"status": True, "data": b64(url), "pwd": "secret"})

    share = FirmwareService(client).link("X6871", "X6871-V1")

    assert client.calls == [("firmware_link", "X6871", "X6871-V1")]
    assert share.url == url
    assert share.share_id == "SID"
    assert share.password == "secret"
    assert share.public_url == url


def test_public_url_appends_password():
    share = ShareLink.from_url("https://frbox.transsion.com/disk/s/SID", password="p w&")
    assert share.public_url == "https://frbox.transsion.com/disk/s/SID?pwd=p+w%26"


def test_public_url_does_not_duplicate_password():
    url = "https://frbox.transsion.com/disk/s/SID?pwd=already"
    assert ShareLink.from_url(url, "other").public_url == url


def test_public_url_without_password():
    url = "https://frbox.transsion.com/disk/s/SID"
    assert ShareLink.from_url(url).public_url == url


def test_link_accepts_plain_url():
    client = StubClient({"status": True, "data": "https://frbox.transsion.com/disk/s/ABC?pwd=zzz"})

    share = FirmwareService(client).link("X6871", "V1")

    assert share.share_id == "ABC"
    assert share.password == "zzz"


def test_link_without_data_raises():
    client = StubClient({"status": True})
    with pytest.raises(OperationError, match="no share link"):
        FirmwareService(client).link("X6871", "V1")


def test_list_with_bad_payload_raises():
    client = StubClient({"status": True, "data": b64("not-json")})
    with pytest.raises(OperationError, match="Cannot parse"):
        FirmwareService(client).list("X6871")
