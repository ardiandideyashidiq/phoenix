"""Stream firmware files to disk."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath

import requests
from loguru import logger

from .errors import DownloadError
from .models import RemoteFile

ProgressCallback = Callable[[int, int | None], None]
CHUNK_SIZE = 1 << 20


def safe_filename(name: str, fallback: str = "firmware.bin") -> str:
    """Strip directories and traversal segments from a server-provided name."""
    candidate = PurePosixPath((name or "").replace("\\", "/")).name.strip()
    if candidate in {"", ".", ".."}:
        return fallback
    return candidate


class Downloader:
    def __init__(
        self,
        destination: Path | str = ".",
        *,
        http: requests.Session | None = None,
        overwrite: bool = False,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self.destination = Path(destination)
        self.http = http or requests.Session()
        self.overwrite = overwrite
        self.chunk_size = chunk_size

    def save(self, file: RemoteFile, *, on_progress: ProgressCallback | None = None) -> Path:
        """Download ``file`` atomically and return the written path."""
        if not file.download_url:
            raise DownloadError(f"No download URL for {file.name}")

        self.destination.mkdir(parents=True, exist_ok=True)
        target = self._target_path(safe_filename(file.name))
        partial = target.with_name(target.name + ".part")
        logger.info("Downloading {} -> {}", file.name, target)

        downloaded = 0
        response = None
        try:
            response = self.http.get(
                file.download_url,
                stream=True,
                timeout=600,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if response.status_code != 200:
                raise DownloadError(f"HTTP {response.status_code} for {file.name}")

            total = int(response.headers.get("content-length") or file.size or 0) or None
            with partial.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if not chunk:
                        continue
                    stream.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)
        except requests.exceptions.RequestException as exc:
            partial.unlink(missing_ok=True)
            raise DownloadError(f"Download failed for {file.name}: {exc}") from exc
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        finally:
            if response is not None:
                response.close()

        logger.debug("Writing {} bytes to {}", downloaded, target)
        partial.replace(target)
        return target

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
