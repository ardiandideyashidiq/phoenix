from __future__ import annotations

import pytest

from phoenix.downloader import Downloader, safe_filename
from phoenix.errors import DownloadError
from phoenix.models import RemoteFile

from .conftest import FakeHTTP, FakeStream


def test_safe_filename_strips_directories():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename(r"..\..\windows\evil.exe") == "evil.exe"
    assert safe_filename("..") == "firmware.bin"
    assert safe_filename("firmware.zip") == "firmware.zip"


def test_save_streams_to_disk(tmp_path):
    stream = FakeStream([b"abc", b"def"], headers={"content-length": "6"})
    downloader = Downloader(tmp_path, http=FakeHTTP(lambda *_: stream))
    progress: list[tuple[int, int | None]] = []

    path = downloader.save(
        RemoteFile(name="fw.bin", file_id="f", size=6, download_url="https://cdn/fw.bin"),
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert path == tmp_path / "fw.bin"
    assert path.read_bytes() == b"abcdef"
    assert progress == [(3, 6), (6, 6)]
    assert stream.closed


def test_save_does_not_overwrite_by_default(tmp_path):
    (tmp_path / "fw.bin").write_text("old")
    downloader = Downloader(tmp_path, http=FakeHTTP(lambda *_: FakeStream([b"new"])))

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path == tmp_path / "fw (1).bin"
    assert (tmp_path / "fw.bin").read_text() == "old"


def test_save_overwrites_when_asked(tmp_path):
    (tmp_path / "fw.bin").write_text("old")
    downloader = Downloader(
        tmp_path,
        http=FakeHTTP(lambda *_: FakeStream([b"new"])),
        overwrite=True,
    )

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path == tmp_path / "fw.bin"
    assert path.read_bytes() == b"new"


def test_save_requires_url(tmp_path):
    with pytest.raises(DownloadError, match="No download URL"):
        Downloader(tmp_path, http=FakeHTTP(lambda *_: FakeStream([]))).save(
            RemoteFile(name="fw.bin", file_id="f")
        )


def test_save_rejects_http_error(tmp_path):
    stream = FakeStream([], status=404)
    with pytest.raises(DownloadError, match="HTTP 404"):
        Downloader(tmp_path, http=FakeHTTP(lambda *_: stream)).save(
            RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin")
        )
    assert not (tmp_path / "fw.bin").exists()
    assert not (tmp_path / "fw.bin.part").exists()
