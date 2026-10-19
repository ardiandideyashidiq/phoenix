"""Stream firmware files to disk with ranged parallel download and resume."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath

import requests
from loguru import logger

from .errors import DownloadError
from .models import RemoteFile

ProgressCallback = Callable[[int, int | None], None]
UrlRefresher = Callable[[], str]

CHUNK_SIZE = 1 << 20
PART_SIZE = 32 << 20
WORKERS = 4
RETRIES = 3
DOWNLOAD_TIMEOUT = 600
CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")
USER_AGENT = "Mozilla/5.0"


def safe_filename(name: str, fallback: str = "firmware.bin") -> str:
    """Strip directories and traversal segments from a server-provided name."""
    candidate = PurePosixPath((name or "").replace("\\", "/")).name.strip()
    if candidate in {"", ".", ".."}:
        return fallback
    return candidate


def _content_range_total(value: str | None) -> int | None:
    match = CONTENT_RANGE.match(value or "")
    if not match or match.group(3) == "*":
        return None
    return int(match.group(3))


class Downloader:
    def __init__(
        self,
        destination: Path | str = ".",
        *,
        http: requests.Session | None = None,
        overwrite: bool = False,
        chunk_size: int = CHUNK_SIZE,
        workers: int = WORKERS,
        part_size: int = PART_SIZE,
        resume: bool = True,
    ) -> None:
        self.destination = Path(destination)
        self.http = http or requests.Session()
        self.overwrite = overwrite
        self.chunk_size = chunk_size
        self.workers = max(1, workers)
        self.part_size = max(1, part_size)
        self.resume = resume

        self._lock = threading.Lock()
        self._abort = threading.Event()
        self._downloaded = 0
        self._total: int | None = None
        self._on_progress: ProgressCallback | None = None

    def save(
        self,
        file: RemoteFile,
        *,
        on_progress: ProgressCallback | None = None,
        refresh_url: UrlRefresher | None = None,
    ) -> Path:
        """Download ``file``, resuming a previous ``.part`` when possible."""
        if not file.download_url:
            raise DownloadError(f"No download URL for {file.name}")

        self.destination.mkdir(parents=True, exist_ok=True)
        self._abort.clear()
        target = self._target_path(safe_filename(file.name))
        partial = target.with_name(target.name + ".part")
        state_path = target.with_name(target.name + ".part.json")

        total, supports_range = self._probe(file, refresh_url)
        total = total or file.size or None
        logger.info(
            "Downloading {} -> {} ({} bytes, {})",
            file.name,
            target,
            total or "unknown",
            f"{self.workers} ranged connections" if supports_range else "single stream",
        )

        if supports_range and total:
            self._ranged_download(file, partial, state_path, total, on_progress, refresh_url)
        else:
            self._sequential_download(file, partial, state_path, total, on_progress, refresh_url)

        partial.replace(target)
        state_path.unlink(missing_ok=True)
        return target

    # --- probing -----------------------------------------------------

    def _probe(self, file: RemoteFile, refresh_url: UrlRefresher | None) -> tuple[int | None, bool]:
        """Return ``(total_size, supports_ranges)`` for the download URL."""
        url = file.download_url
        for attempt in range(RETRIES):
            response = None
            try:
                response = self.http.get(
                    url,
                    headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT},
                    stream=True,
                    timeout=30,
                )
                if response.status_code == 206:
                    total = _content_range_total(response.headers.get("Content-Range"))
                    return total or file.size or None, True
                if response.status_code == 200:
                    total = int(response.headers.get("content-length") or file.size or 0) or None
                    return total, False
                if response.status_code in {401, 403, 404} and refresh_url and attempt < RETRIES - 1:
                    logger.info("Download URL rejected (HTTP {}), refreshing", response.status_code)
                    url = refresh_url()
                    continue
                logger.warning("Range probe returned HTTP {}", response.status_code)
                return file.size or None, False
            except requests.exceptions.RequestException as exc:
                if attempt == RETRIES - 1:
                    logger.warning("Range probe failed: {}", exc)
                    return file.size or None, False
                time.sleep(2**attempt)
            finally:
                if response is not None:
                    response.close()
        return file.size or None, False

    # --- ranged download ---------------------------------------------

    def _ranged_download(
        self,
        file: RemoteFile,
        partial: Path,
        state_path: Path,
        total: int,
        on_progress: ProgressCallback | None,
        refresh_url: UrlRefresher | None,
    ) -> None:
        ranges = [(start, min(start + self.part_size, total) - 1) for start in range(0, total, self.part_size)]
        completed = self._load_state(file, partial, state_path, total)
        completed = {index for index in completed if 0 <= index < len(ranges)}

        partial.touch(exist_ok=True)
        with partial.open("r+b") as stream:
            stream.truncate(total)

        self._total = total
        self._on_progress = on_progress
        self._downloaded = sum(end - start + 1 for index, (start, end) in enumerate(ranges) if index in completed)
        if on_progress:
            on_progress(self._downloaded, total)

        pending = [
            (index, start, end)
            for index, (start, end) in enumerate(ranges)
            if index not in completed
        ]
        if not pending:
            logger.info("All {} part(s) already present", len(ranges))
            return

        logger.info("Fetching {} of {} part(s)", len(pending), len(ranges))
        pool = ThreadPoolExecutor(max_workers=self.workers)
        futures = [
            pool.submit(
                self._fetch_part,
                file,
                index,
                start,
                end,
                partial,
                state_path,
                completed,
                refresh_url,
            )
            for index, start, end in pending
        ]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            self._abort.set()
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            pool.shutdown(wait=True)

        if len(completed) != len(ranges):
            raise DownloadError(f"Incomplete download for {file.name}")

    def _fetch_part(
        self,
        file: RemoteFile,
        index: int,
        start: int,
        end: int,
        partial: Path,
        state_path: Path,
        completed: set[int],
        refresh_url: UrlRefresher | None,
    ) -> None:
        url = file.download_url
        for attempt in range(RETRIES):
            response = None
            try:
                response = self.http.get(
                    url,
                    headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT},
                    stream=True,
                    timeout=DOWNLOAD_TIMEOUT,
                )
                if response.status_code in {401, 403, 404} and refresh_url and attempt < RETRIES - 1:
                    logger.info("Part {} rejected (HTTP {}), refreshing URL", index, response.status_code)
                    url = refresh_url()
                    continue
                if response.status_code >= 500 and attempt < RETRIES - 1:
                    logger.warning("Part {} got HTTP {}, retrying", index, response.status_code)
                    time.sleep(2**attempt)
                    continue
                if response.status_code != 206:
                    raise DownloadError(
                        f"HTTP {response.status_code} for bytes {start}-{end} of {file.name}"
                    )
                match = CONTENT_RANGE.match(response.headers.get("Content-Range") or "")
                if not match or int(match.group(1)) != start:
                    raise DownloadError(
                        f"Server returned the wrong range for {file.name}: "
                        f"{response.headers.get('Content-Range')!r}"
                    )
                with partial.open("r+b") as stream:
                    stream.seek(start)
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if self._abort.is_set():
                            raise DownloadError(f"Download aborted for {file.name}")
                        if not chunk:
                            continue
                        stream.write(chunk)
                        self._advance(len(chunk))
                with self._lock:
                    completed.add(index)
                    self._save_state(state_path, file, completed)
                return
            except requests.exceptions.RequestException as exc:
                if attempt == RETRIES - 1:
                    raise DownloadError(f"Download failed for {file.name}: {exc}") from exc
                time.sleep(2**attempt)
            finally:
                if response is not None:
                    response.close()
        raise DownloadError(f"Download failed for {file.name} (bytes {start}-{end})")

    def _advance(self, count: int) -> None:
        with self._lock:
            self._downloaded += count
            if self._on_progress:
                self._on_progress(self._downloaded, self._total)

    # --- resume state ------------------------------------------------

    def _load_state(self, file: RemoteFile, partial: Path, state_path: Path, total: int) -> set[int]:
        if not self.resume or not partial.exists() or not state_path.exists():
            return set()
        try:
            state = json.loads(state_path.read_text())
            if (
                state.get("file_id") != file.file_id
                or int(state.get("total", 0)) != total
                or int(state.get("part_size", 0)) != self.part_size
            ):
                logger.info("Discarding stale resume state for {}", file.name)
                return set()
            completed = {int(value) for value in state.get("completed", [])}
        except (OSError, ValueError, TypeError):
            logger.info("Discarding unreadable resume state for {}", file.name)
            return set()

        if partial.stat().st_size > total:
            logger.info("Discarding oversized partial file for {}", file.name)
            return set()
        logger.info("Resuming {} with {} part(s) already done", file.name, len(completed))
        return completed

    def _save_state(self, state_path: Path, file: RemoteFile, completed: set[int]) -> None:
        payload = {
            "file_id": file.file_id,
            "total": self._total,
            "part_size": self.part_size,
            "completed": sorted(completed),
        }
        temp = state_path.with_name(state_path.name + ".tmp")
        temp.write_text(json.dumps(payload))
        temp.replace(state_path)

    # --- sequential fallback -----------------------------------------

    def _sequential_download(
        self,
        file: RemoteFile,
        partial: Path,
        state_path: Path,
        total: int | None,
        on_progress: ProgressCallback | None,
        refresh_url: UrlRefresher | None,
    ) -> None:
        """Single stream. Used when the server ignores Range requests."""
        partial.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)

        url = file.download_url
        response = None
        downloaded = 0
        try:
            response = self.http.get(
                url,
                stream=True,
                timeout=DOWNLOAD_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
            )
            if response.status_code != 200:
                raise DownloadError(f"HTTP {response.status_code} for {file.name}")
            total = int(response.headers.get("content-length") or total or 0) or None
            with partial.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if self._abort.is_set():
                        raise DownloadError(f"Download aborted for {file.name}")
                    if not chunk:
                        continue
                    stream.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)
        except requests.exceptions.RequestException as exc:
            raise DownloadError(f"Download failed for {file.name}: {exc}") from exc
        finally:
            if response is not None:
                response.close()

        logger.debug("Writing {} bytes to {}", downloaded, partial)

    # --- target path -------------------------------------------------

    def _target_path(self, name: str) -> Path:
        target = self.destination / name
        if self.overwrite or not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        for index in range(1, 1000):
            candidate = target.with_name(f"{stem} ({index}){suffix}")
            if not candidate.exists():
                return candidate
        raise DownloadError(f"Too many files named {name}")
