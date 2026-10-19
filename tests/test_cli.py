from __future__ import annotations

import base64
import json
from typing import ClassVar

import click
import pytest
from click.testing import CliRunner

from phoenix import cli as cli_module
from phoenix.models import DeviceModel, Session


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.session = Session(token="tok", name="tester", credit=1)
        self.logins: list[tuple[str, str]] = []
        type(self).instances.append(self)

    def restore(self):
        return self.session

    def login(self, email, password, *, remember=True):
        self.logins.append((email, password))
        self.session = Session(token="tok", name="tester", credit=1, email=email)
        return self.session

    def logout(self):
        return True

    def server_status(self):
        return {"status": "OK"}

    def server_info(self):
        return {"status": True, "message": "hi"}

    def models(self):
        return [DeviceModel(brand="TECNO", model="X6871")]

    def operation(self, command, *, project=None, version=None):
        assert command == "firmware_list"
        payload = [{"market": "EU", "version": "X6871-H1-EU", "date": "2025-01-01"}]
        return {"status": True, "data": b64(json.dumps(payload))}


@pytest.fixture
def fake_client(monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(cli_module, "PhoenixClient", FakeClient)
    return FakeClient


def test_firmware_lists_versions(fake_client, tmp_path):
    result = CliRunner().invoke(
        cli_module.cli, ["--session-file", str(tmp_path / "s.json"), "firmware", "x6871"]
    )

    assert result.exit_code == 0, result.output
    assert "X6871-H1-EU" in result.output
    assert "1 firmware version(s)" in result.output


def test_help_lists_subcommands():
    result = CliRunner().invoke(cli_module.cli, ["--help"])

    assert result.exit_code == 0
    commands = set(cli_module.cli.list_commands(click.Context(cli_module.cli)))
    assert commands == {"login", "firmware", "download"}


def test_login_prompts_for_missing_credentials(fake_client, monkeypatch, tmp_path):
    monkeypatch.delenv("PHOENIX_EMAIL", raising=False)
    monkeypatch.delenv("PHOENIX_PASSWORD", raising=False)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--session-file", str(tmp_path / "s.json"), "login"],
        input="user@example.com\nsecret\n",
    )

    assert result.exit_code == 0, result.output
    assert fake_client.instances[-1].logins == [("user@example.com", "secret")]
    assert "Logged in" in result.output


def test_login_reads_environment(fake_client, monkeypatch, tmp_path):
    monkeypatch.setenv("PHOENIX_EMAIL", "env@example.com")
    monkeypatch.setenv("PHOENIX_PASSWORD", "envsecret")

    result = CliRunner().invoke(
        cli_module.cli, ["--session-file", str(tmp_path / "s.json"), "login"]
    )

    assert result.exit_code == 0, result.output
    assert fake_client.instances[-1].logins == [("env@example.com", "envsecret")]


def test_legacy_firmware_flag(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli_module, "run", lambda argv: calls.append(argv) or 0)

    assert cli_module.legacy_main(["--firmware", "x6871"]) == 0
    assert calls == [["firmware", "x6871"]]


def test_legacy_download_maps_to_subcommand(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli_module, "run", lambda argv: calls.append(argv) or 0)

    code = cli_module.legacy_main(
        ["--download", "--project", "X6871", "--version-id", "V1", "--output-dir", "./fw"]
    )

    assert code == 0
    assert calls == [["download", "X6871", "V1", "--yes", "-o", "./fw"]]


def test_legacy_bad_download_exits(monkeypatch):
    monkeypatch.setattr(cli_module, "run", lambda argv: 0)
    with pytest.raises(SystemExit, match="--project"):
        cli_module.legacy_main(["--download"])
