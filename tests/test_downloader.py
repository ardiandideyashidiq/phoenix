from __future__ import annotations

import json

import pytest

from phoenix.downloader import Downloader, safe_filename
from phoenix.errors import DownloadError
from phoenix.models import RemoteFile

from .conftest import FakeHTTP, FakeStream


def stream_handler(chunks: list[bytes], *, status: int = 200):
    def handler(method, url, kwargs):
        return FakeStream(chunks, status=status, headers={"content-length": str(sum(map(len, chunks)))})

    return handler


def test_safe_filename_strips_directories():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename(r"..\..\windows\evil.exe") == "evil.exe"
    assert safe_filename("..") == "firmware.bin"
    assert safe_filename("firmware.zip") == "firmware.zip"


def test_save_streams_to_disk(tmp_path):
    downloader = Downloader(tmp_path, http=FakeHTTP(stream_handler([b"abc", b"def"])))
    progress: list[tuple[int, int | None]] = []

    path = downloader.save(
        RemoteFile(name="fw.bin", file_id="f", size=6, download_url="https://cdn/fw.bin"),
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert path == tmp_path / "fw.bin"
    assert path.read_bytes() == b"abcdef"
    assert progress == [(3, 6), (6, 6)]


def test_save_does_not_overwrite_by_default(tmp_path):
    (tmp_path / "fw.bin").write_text("old")
    downloader = Downloader(tmp_path, http=FakeHTTP(stream_handler([b"new"])))

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path == tmp_path / "fw (1).bin"
    assert (tmp_path / "fw.bin").read_text() == "old"


def test_save_overwrites_when_asked(tmp_path):
    (tmp_path / "fw.bin").write_text("old")
    downloader = Downloader(
        tmp_path,
        http=FakeHTTP(stream_handler([b"new"])),
        overwrite=True,
    )

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path == tmp_path / "fw.bin"
    assert path.read_bytes() == b"new"


def test_save_requires_url(tmp_path):
    with pytest.raises(DownloadError, match="No download URL"):
        Downloader(tmp_path, http=FakeHTTP(stream_handler([]))).save(
            RemoteFile(name="fw.bin", file_id="f")
        )


def test_save_rejects_http_error(tmp_path):
    downloader = Downloader(tmp_path, http=FakeHTTP(stream_handler([], status=404)))
    with pytest.raises(DownloadError, match="HTTP 404"):
        downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))
    assert not (tmp_path / "fw.bin").exists()


def ranged_handler(data: bytes, *, deny_first_part: bool = False):
    calls: list[tuple[str, str]] = []
    denied = {"done": False}

    def handler(method, url, kwargs):
        requested = kwargs["headers"]["Range"]
        calls.append((url, requested))
        if requested == "bytes=0-0":
            return FakeStream(
                [b""], status=206, headers={"Content-Range": f"bytes 0-0/{len(data)}"}
            )
        start, end = (int(part) for part in requested.removeprefix("bytes=").split("-"))
        if deny_first_part and start == 0 and not denied["done"]:
            denied["done"] = True
            return FakeStream([b""], status=403)
        return FakeStream(
            [data[start : end + 1]],
            status=206,
            headers={"Content-Range": f"bytes {start}-{end}/{len(data)}"},
        )

    return handler, calls


def test_ranged_download_assembles_parts(tmp_path):
    data = b"0123456789"
    handler, calls = ranged_handler(data)
    downloader = Downloader(tmp_path, http=FakeHTTP(handler), workers=2, part_size=4)

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path.read_bytes() == data
    assert not (tmp_path / "fw.bin.part").exists()
    assert not (tmp_path / "fw.bin.part.json").exists()
    ranges = [name for _, name in calls if name != "bytes=0-0"]
    assert sorted(ranges) == ["bytes=0-3", "bytes=4-7", "bytes=8-9"]


def test_ranged_download_resumes_completed_parts(tmp_path):
    data = b"0123456789"
    handler, calls = ranged_handler(data)
    partial = tmp_path / "fw.bin.part"
    partial.write_bytes(data[:4] + b"\0" * 6)
    (tmp_path / "fw.bin.part.json").write_text(
        json.dumps({"file_id": "f", "total": 10, "part_size": 4, "completed": [0]})
    )
    downloader = Downloader(tmp_path, http=FakeHTTP(handler), workers=2, part_size=4)

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path.read_bytes() == data
    ranges = [name for _, name in calls if name != "bytes=0-0"]
    assert sorted(ranges) == ["bytes=4-7", "bytes=8-9"]


def test_ranged_download_refreshes_expired_url(tmp_path):
    data = b"0123456789"
    handler, calls = ranged_handler(data, deny_first_part=True)
    refreshed: list[str] = []
    downloader = Downloader(tmp_path, http=FakeHTTP(handler), workers=1, part_size=4)

    path = downloader.save(
        RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"),
        refresh_url=lambda: refreshed.append("fresh") or "https://cdn/fresh",
    )

    assert path.read_bytes() == data
    assert refreshed == ["fresh"]
    assert any(url == "https://cdn/fresh" for url, _ in calls)


def test_ranged_download_rejects_wrong_content_range(tmp_path):
    data = b"0123456789"

    def handler(method, url, kwargs):
        requested = kwargs["headers"]["Range"]
        if requested == "bytes=0-0":
            return FakeStream([b""], status=206, headers={"Content-Range": "bytes 0-0/10"})
        return FakeStream([data], status=206, headers={"Content-Range": "bytes 5-9/10"})

    downloader = Downloader(tmp_path, http=FakeHTTP(handler), workers=1, part_size=4)
    with pytest.raises(DownloadError, match="wrong range"):
        downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))


def test_ignored_range_falls_back_to_stream(tmp_path):
    """A server that answers 200 to Range gets the sequential path."""
    data = b"abcdef"
    downloader = Downloader(tmp_path, http=FakeHTTP(stream_handler([data])), workers=4)

    path = downloader.save(RemoteFile(name="fw.bin", file_id="f", download_url="https://cdn/fw.bin"))

    assert path.read_bytes() == data
