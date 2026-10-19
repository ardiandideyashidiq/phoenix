"""Resolve frbox.transsion.com shares through the Aliyun PDS API."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import requests
from loguru import logger

from .config import FRBOX_ORIGIN, Settings
from .errors import ShareError
from .models import RemoteFile, ShareLink

MAX_DEPTH = 8
PAGE_LIMIT = 100


class FrboxClient:
    """Anonymous Aliyun PDS client for Transsion firmware shares."""

    def __init__(
        self,
        settings: Settings | None = None,
        http: requests.Session | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.http = http or requests.Session()
        self.http.headers.update({"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        self._tokens: dict[str, str] = {}

    def resolve(self, share: ShareLink) -> list[RemoteFile]:
        """Return every file in the share, with a fresh download URL."""
        if not share.share_id:
            raise ShareError(f"Cannot find a share id in {share.url}")
        token = self._share_token(share)
        files = self._walk(share, token)
        if not files:
            raise ShareError("Share contains no files")
        resolved = [self._with_fresh_url(share, token, item) for item in files]
        logger.info("Resolved {} file(s) from share {}", len(resolved), share.share_id)
        return resolved

    # --- transport ---------------------------------------------------

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        referer: str,
        share_token: str | None = None,
    ) -> dict[str, Any]:
        url = self.settings.frbox_api.rstrip("/") + path
        headers = {
            "Origin": FRBOX_ORIGIN,
            "Referer": referer,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        if share_token:
            headers["x-share-token"] = share_token
        logger.debug("POST {} payload={}", url, payload)
        try:
            response = self.http.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.timeout,
                verify=self.settings.verify_ssl,
            )
        except requests.exceptions.SSLError as exc:
            if not self.settings.verify_ssl:
                raise ShareError(f"TLS error contacting {url}: {exc}") from exc
            logger.warning("TLS verification failed, retrying without verification: {}", exc)
            response = self.http.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.timeout,
                verify=False,
            )
        except requests.exceptions.RequestException as exc:
            raise ShareError(f"Cannot reach {url}: {exc}") from exc

        if response.status_code != 200:
            raise ShareError(f"{path} failed (HTTP {response.status_code}): {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ShareError(f"{path} returned non-JSON: {response.text[:300]}") from exc
        if not isinstance(data, dict):
            raise ShareError(f"{path} returned an unexpected payload")
        return data

    # --- share flow --------------------------------------------------

    def _share_token(self, share: ShareLink) -> str:
        cached = self._tokens.get(share.share_id)
        if cached:
            return cached
        payload: dict[str, Any] = {"share_id": share.share_id}
        if share.password:
            # Aliyun PDS expects "share_pwd", not "password".
            payload["share_pwd"] = share.password
        data = self._post("/v2/share_link/get_share_token", payload, referer=share.url)
        token = data.get("share_token")
        if not token:
            raise ShareError(f"No share token returned: {str(data)[:300]}")
        logger.debug("Got share token (expires {})", data.get("expire_time", "?"))
        self._tokens[share.share_id] = str(token)
        return str(token)

    def download_url(self, share: ShareLink, file: RemoteFile) -> str:
        """Fetch a fresh (short-lived) download URL for one file."""
        token = self._share_token(share)
        try:
            data = self._post(
                "/v2/file/get_download_url",
                {"share_id": share.share_id, "file_id": file.file_id},
                referer=share.url,
                share_token=token,
            )
        except ShareError:
            # The share token may have expired; force a new one on retry.
            self._tokens.pop(share.share_id, None)
            raise
        url = data.get("url") or data.get("download_url")
        if not url:
            raise ShareError(f"No download URL returned for {file.name}")
        return str(url)

    def _walk(self, share: ShareLink, token: str) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        seen: set[str] = set()

        def visit(folder_id: str, depth: int) -> None:
            if depth > MAX_DEPTH:
                logger.warning("Skipping {}: share nesting is too deep", folder_id)
                return
            marker: str | None = None
            while True:
                payload: dict[str, Any] = {
                    "share_id": share.share_id,
                    "parent_file_id": folder_id,
                    "limit": PAGE_LIMIT,
                }
                if marker:
                    payload["marker"] = marker
                data = self._post("/v2/file/list", payload, referer=share.url, share_token=token)

                for item in data.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    file_id = str(item.get("file_id") or "")
                    if not file_id or file_id in seen:
                        continue
                    item_type = item.get("type")
                    if item_type == "folder":
                        seen.add(file_id)
                        visit(file_id, depth + 1)
                    elif item_type == "file":
                        seen.add(file_id)
                        files.append(
                            RemoteFile(
                                name=str(item.get("name") or file_id),
                                file_id=file_id,
                                size=int(item.get("size") or 0),
                                download_url=str(item.get("download_url") or item.get("url") or ""),
                            )
                        )

                marker = data.get("next_marker")
                if not marker:
                    break

        visit("root", 0)
        return files

    def _with_fresh_url(self, share: ShareLink, token: str, item: RemoteFile) -> RemoteFile:
        try:
            url = self.download_url(share, item)
        except ShareError as exc:
            logger.warning("Keeping listing URL for {}: {}", item.name, exc)
            return item
        return replace(item, download_url=url)
