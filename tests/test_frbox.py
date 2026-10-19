from __future__ import annotations

import pytest

from phoenix.errors import ShareError
from phoenix.frbox import FrboxClient
from phoenix.models import ShareLink

from .conftest import FakeHTTP, FakeResponse

SHARE_URL = "https://frbox.transsion.com/disk/s/SID?pwd=secret"


def make_client(settings, handler) -> FrboxClient:
    return FrboxClient(settings, http=FakeHTTP(handler))


def test_share_link_parsing():
    share = ShareLink.from_url(SHARE_URL)
    assert share.share_id == "SID"
    assert share.password == "secret"


def test_resolve_walks_folders_and_pages(settings):
    calls: list[tuple[str, dict, dict]] = []

    def handler(method, url, kwargs):
        path = url.split("aliyunpds.com", 1)[-1]
        payload, headers = kwargs["json"], kwargs["headers"]
        calls.append((path, payload, headers))

        if path == "/v2/share_link/get_share_token":
            assert payload["share_pwd"] == "secret"
            return FakeResponse({"share_token": "st", "expire_time": "later"})
        if path == "/v2/file/list":
            assert headers["x-share-token"] == "st"
            if payload["parent_file_id"] == "root":
                if not payload.get("marker"):
                    return FakeResponse(
                        {
                            "items": [
                                {"type": "folder", "file_id": "dir1", "name": "folder"},
                                {"type": "file", "file_id": "f0", "name": "a.bin", "size": 10, "download_url": "old0"},
                            ],
                            "next_marker": "page2",
                        }
                    )
                return FakeResponse({"items": [], "next_marker": ""})
            if payload["parent_file_id"] == "dir1":
                return FakeResponse(
                    {
                        "items": [
                            {"type": "file", "file_id": "f1", "name": "b.bin", "size": 20, "download_url": "old1"},
                            {"type": "file", "file_id": "f1", "name": "dup.bin", "size": 20},
                        ]
                    }
                )
        if path == "/v2/file/get_download_url":
            return FakeResponse({"url": f"fresh-{payload['file_id']}"})
        raise AssertionError(f"unexpected call {path}")

    files = make_client(settings, handler).resolve(ShareLink.from_url(SHARE_URL))

    assert {f.name: f.download_url for f in files} == {
        "a.bin": "fresh-f0",
        "b.bin": "fresh-f1",
    }

    root_pages = [c for c in calls if c[0] == "/v2/file/list" and c[1]["parent_file_id"] == "root"]
    assert [c[1].get("marker") for c in root_pages] == [None, "page2"]


def test_resolve_rejects_missing_share_id(settings):
    with pytest.raises(ShareError, match="share id"):
        make_client(settings, lambda *_: FakeResponse({})).resolve(ShareLink(url="https://x/"))


def test_resolve_raises_when_token_missing(settings):
    client = make_client(settings, lambda *_: FakeResponse({"code": "InvalidResource.SharePwd"}))
    with pytest.raises(ShareError, match="No share token"):
        client.resolve(ShareLink.from_url(SHARE_URL))


def test_resolve_raises_when_empty(settings):
    def handler(method, url, kwargs):
        if url.endswith("get_share_token"):
            return FakeResponse({"share_token": "st"})
        return FakeResponse({"items": []})

    with pytest.raises(ShareError, match="no files"):
        make_client(settings, handler).resolve(ShareLink.from_url(SHARE_URL))


def test_http_error_becomes_share_error(settings):
    client = make_client(settings, lambda *_: FakeResponse({}, status=500, text="boom"))
    with pytest.raises(ShareError, match="HTTP 500"):
        client.resolve(ShareLink.from_url(SHARE_URL))
