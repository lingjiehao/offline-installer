"""GitHub REST API 的最小封装（仅标准库）。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str | None = None):
        self.token = token or None

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "offline-installer/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict | None = None,
        retries: int = 3,
    ) -> tuple[int, bytes]:
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            req = urllib.request.Request(url, data=data, headers=self._headers(headers), method=method)
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                    last = GitHubError(f"HTTP {exc.code}")
                    time.sleep(2 * attempt)
                    continue
                raise GitHubError(f"{method} {url} -> HTTP {exc.code}: {body[:500]}") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
                if attempt < retries:
                    time.sleep(2 * attempt)
                    continue
                raise GitHubError(f"{method} {url}: {exc}") from None
        raise GitHubError(str(last))

    def get(self, url: str) -> bytes:
        return self._request("GET", url)[1]

    def get_json(self, path: str):
        url = path if path.startswith("http") else API + path
        return json.loads(self.get(url))

    def get_optional_json(self, path: str):
        try:
            return self.get_json(path)
        except GitHubError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def latest_release(self, repo: str) -> dict:
        release = self.get_optional_json(f"/repos/{repo}/releases/latest")
        if release:
            return release
        # 上游全部标记为 prerelease 时，退回最新的正式 release
        for release in self.get_json(f"/repos/{repo}/releases?per_page=20"):
            if not release.get("prerelease") and not release.get("draft"):
                return release
        raise GitHubError(f"{repo}: 没有可用的正式 release")

    def release_by_tag(self, repo: str, tag: str) -> dict | None:
        return self.get_optional_json(f"/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}")

    def create_release(self, repo: str, tag: str, name: str, body: str = "") -> dict:
        payload = json.dumps(
            {"tag_name": tag, "name": name, "body": body, "make_latest": "true"}
        ).encode()
        _, raw = self._request("POST", f"{API}/repos/{repo}/releases", data=payload)
        return json.loads(raw)

    def update_release(self, repo: str, release_id: int, **fields) -> dict:
        payload = json.dumps(fields, ensure_ascii=False).encode()
        _, raw = self._request("PATCH", f"{API}/repos/{repo}/releases/{release_id}", data=payload)
        return json.loads(raw)

    def delete_asset(self, repo: str, asset_id: int) -> None:
        self._request("DELETE", f"{API}/repos/{repo}/releases/assets/{asset_id}")

    def upload_asset(
        self,
        repo: str,
        release_id: int,
        name: str,
        path: Path,
        content_type: str = "application/octet-stream",
    ) -> dict:
        data = path.read_bytes()
        url = f"{UPLOAD}/repos/{repo}/releases/{release_id}/assets?name={urllib.parse.quote(name)}"
        headers = {"Content-Type": content_type, "Content-Length": str(len(data))}
        _, raw = self._request("POST", url, data=data, headers=headers, retries=2)
        return json.loads(raw)
