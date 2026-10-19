from __future__ import annotations

import base64
import json

import pytest

from phoenix.errors import OperationError
from phoenix.firmware import (
    FirmwareService,
    candidate_projects,
    decode_payload,
    normalize_project,
)
from phoenix.models import ShareLink


class StubClient:
    """Return a payload per ``(command, project)``, else ``default``."""

    def __init__(self, default=None, *, responses=None):
        self.default = default
        self.responses = responses or {}
        self.calls: list[tuple[str, str | None, str | None]] = []

    def operation(self, command, *, project=None, version=None):
        self.calls.append((command, project, version))
        return self.responses.get((command, project), self.default)


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def list_payload(items):
    return {"status": True, "data": b64(json.dumps(items))}


def test_decode_payload_accepts_base64_and_plain_text():
    assert decode_payload(b64("hello")) == "hello"
    assert decode_payload("https://example.com/a?pwd=x") == "https://example.com/a?pwd=x"
    assert decode_payload("") == ""


def test_normalize_project():
    assert normalize_project(" x6871 ") == "X6871"
    with pytest.raises(ValueError):
        normalize_project("  ")


def test_candidate_projects_probes_variants():
    candidates = candidate_projects("x6871")
    assert candidates[0] == "X6871"
    assert "X6871(ANDROID 15)" in candidates
    assert "X6871(ANDROID 16)" in candidates
    assert "X6871-T" in candidates
    assert candidate_projects("X6871(Android 16)") == ["X6871(ANDROID 16)"]
    assert candidate_projects("x670-t") == ["X670-T"]


def test_list_decodes_and_maps_versions():
    payload = list_payload(
        [
            {"market": "EU", "version": "X6871-V1", "date": "2025-01-01", "project": "X6871"},
            {"market": "RU", "version": "X6871-V2", "date": "2025-02-02"},
            {"market": "XX"},
        ]
    )
    client = StubClient(payload)
    versions = FirmwareService(client).list("x6871")

    assert client.calls[0] == ("firmware_list", "X6871", None)
    assert [(v.version, v.market, v.project) for v in versions] == [
        ("X6871-V2", "RU", "X6871"),
        ("X6871-V1", "EU", "X6871"),
    ]


def test_list_merges_android_variants_newest_first():
    client = StubClient(
        list_payload([]),
        responses={
            ("firmware_list", "X6871"): list_payload(
                [{"market": "OP", "version": "X6871-OLD", "date": "2025-03-20", "project": "X6871"}]
            ),
            ("firmware_list", "X6871(ANDROID 15)"): list_payload(
                [
                    {
                        "market": "OP",
                        "version": "X6871-15.1.2.180SP05(OP001PF001AZ)",
                        "date": "2026-06-18",
                        "project": "X6871(ANDROID 15)",
                    }
                ]
            ),
            ("firmware_list", "X6871(ANDROID 16)"): list_payload(
                [
                    {
                        "market": "OP",
                        "version": "X6871-16.3.0.155SP20(OP008PF001AZ)",
                        "date": "2026-10-14",
                        "project": "X6871(ANDROID 16)",
                    }
                ]
            ),
        },
    )

    versions = FirmwareService(client).list("X6871")

    assert [v.version for v in versions] == [
        "X6871-16.3.0.155SP20(OP008PF001AZ)",
        "X6871-15.1.2.180SP05(OP001PF001AZ)",
        "X6871-OLD",
    ]
    assert versions[0].project == "X6871(ANDROID 16)"


def test_list_explicit_variant_skips_discovery():
    payload = list_payload(
        [{"market": "OP", "version": "X6871-A16", "date": "2026-10-14", "project": "X6871(ANDROID 16)"}]
    )
    client = StubClient(payload)

    versions = FirmwareService(client).list("X6871(Android 16)")

    assert client.calls == [("firmware_list", "X6871(ANDROID 16)", None)]
    assert [v.version for v in versions] == ["X6871-A16"]


def test_list_merges_suffix_variant():
    client = StubClient(
        list_payload([]),
        responses={
            ("firmware_list", "X670"): list_payload(
                [{"market": "EU", "version": "X670-H814GJ-S-EU-231114V213", "date": "2023-11-14", "project": "X670"}]
            ),
            ("firmware_list", "X670-T"): list_payload(
                [
                    {
                        "market": "GL",
                        "version": "X670-H814DGHJKL-T-GL-240224V556",
                        "date": "2024-02-24",
                        "project": "X670-T",
                    }
                ]
            ),
        },
    )

    versions = FirmwareService(client).list("X670")

    assert [v.version for v in versions] == [
        "X670-H814DGHJKL-T-GL-240224V556",
        "X670-H814GJ-S-EU-231114V213",
    ]
    assert versions[0].project == "X670-T"


def test_link_decodes_url_and_password():
    url = "https://frbox.transsion.com/disk/s/SID?pwd=secret"
    client = StubClient(
        list_payload([]),
        responses={
            ("firmware_link", "X6871"): {"status": True, "data": b64(url), "pwd": "secret"}
        },
    )

    share = FirmwareService(client).link("X6871", "X6871-V1")

    assert client.calls[-1] == ("firmware_link", "X6871", "X6871-V1")
    assert share.url == url
    assert share.share_id == "SID"
    assert share.password == "secret"
    assert share.public_url == url


def test_link_resolves_android_variant():
    url = "https://frbox.transsion.com/disk/s/SID"
    client = StubClient(
        list_payload([]),
        responses={
            ("firmware_list", "X6871(ANDROID 16)"): list_payload(
                [
                    {
                        "market": "OP",
                        "version": "X6871-16.3.0.155SP20(OP008PF001AZ)",
                        "date": "2026-10-14",
                        "project": "X6871(ANDROID 16)",
                    }
                ]
            ),
            ("firmware_link", "X6871(ANDROID 16)"): {
                "status": True,
                "data": b64(url),
                "pwd": "p",
            },
        },
    )

    share = FirmwareService(client).link("X6871", "X6871-16.3.0.155SP20(OP008PF001AZ)")

    assert client.calls[-1] == (
        "firmware_link",
        "X6871(ANDROID 16)",
        "X6871-16.3.0.155SP20(OP008PF001AZ)",
    )
    assert share.password == "p"


def test_link_resolves_suffix_variant():
    url = "https://frbox.transsion.com/disk/s/SID"
    client = StubClient(
        list_payload([]),
        responses={
            ("firmware_list", "X670-T"): list_payload(
                [
                    {
                        "market": "GL",
                        "version": "X670-H814DGHJKL-T-GL-240224V556",
                        "date": "2024-02-24",
                        "project": "X670-T",
                    }
                ]
            ),
            ("firmware_link", "X670-T"): {"status": True, "data": b64(url), "pwd": "p"},
        },
    )

    share = FirmwareService(client).link("X670", "X670-H814DGHJKL-T-GL-240224V556")

    assert client.calls[-1] == (
        "firmware_link",
        "X670-T",
        "X670-H814DGHJKL-T-GL-240224V556",
    )
    assert share.password == "p"


def test_link_with_explicit_variant_skips_discovery():
    url = "https://frbox.transsion.com/disk/s/SID"
    client = StubClient(
        responses={
            ("firmware_link", "X6871(ANDROID 16)"): {"status": True, "data": b64(url)}
        }
    )

    FirmwareService(client).link("X6871(Android 16)", "V1")

    assert client.calls == [("firmware_link", "X6871(ANDROID 16)", "V1")]


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
    client = StubClient(
        list_payload([]),
        responses={
            ("firmware_link", "X6871"): {
                "status": True,
                "data": "https://frbox.transsion.com/disk/s/ABC?pwd=zzz",
            }
        },
    )

    share = FirmwareService(client).link("X6871", "V1")

    assert share.share_id == "ABC"
    assert share.password == "zzz"


def test_link_without_data_raises():
    client = StubClient(
        list_payload([]),
        responses={("firmware_link", "X6871"): {"status": True}},
    )
    with pytest.raises(OperationError, match="no share link"):
        FirmwareService(client).link("X6871", "V1")


def test_list_with_bad_payload_raises():
    client = StubClient({"status": True, "data": b64("not-json")})
    with pytest.raises(OperationError, match="Cannot parse"):
        FirmwareService(client).list("X6871")
