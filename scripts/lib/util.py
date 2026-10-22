"""通用工具：日志、哈希、下载、JSON 读写。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "offline-installer/0.1"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"错误：{msg}")
    raise SystemExit(code)


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class DownloadError(RuntimeError):
    pass


def download(url: str, dest: Path, token: str | None = None, retries: int = 3) -> tuple[str, int]:
    """下载到 dest，返回 (sha256, size)。断点续传，失败重试。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return _download_once(url, dest, token)
        except (urllib.error.URLError, TimeoutError, ConnectionError, DownloadError) as exc:
            last = exc
            log(f"    下载失败（第 {attempt}/{retries} 次）：{exc}")
            if attempt < retries:
                time.sleep(2 * attempt)
    raise DownloadError(f"{url}: {last}")


def _download_once(url: str, dest: Path, token: str | None) -> tuple[str, int]:
    offset = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if offset:
        headers["Range"] = f"bytes={offset}-"

    req = urllib.request.Request(url, headers=headers)
    h = hashlib.sha256()
    if offset:
        with dest.open("rb") as fh:
            while block := fh.read(1 << 20):
                h.update(block)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if offset and resp.status != 206:
                offset = 0
                h = hashlib.sha256()
            mode = "ab" if offset else "wb"
            with dest.open(mode) as fh:
                while block := resp.read(1 << 20):
                    fh.write(block)
                    h.update(block)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:
            return sha256_file(dest), dest.stat().st_size
        raise
    return h.hexdigest(), dest.stat().st_size
