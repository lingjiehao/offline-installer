"""配置、包声明与纯函数（便于单元测试）。"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from .util import human_size


@dataclass(frozen=True)
class Platform:
    os: str = "linux"
    arch: str = "x64"
    libc: str = "gnu"

    @property
    def label(self) -> str:
        return f"{self.os}-{self.arch}"


@dataclass(frozen=True)
class Package:
    id: str
    type: str = "github"
    repo: str = ""
    asset: str = ""
    bins: tuple[str, ...] = ()
    description: str = ""
    note: str = ""
    install: str = ""
    version_regex: str = ""
    requirements: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    dir: Path | None = None


@dataclass(frozen=True)
class Config:
    release_tag: str
    release_name: str
    retention: int
    platform: Platform


def load_config(root: Path) -> Config:
    raw = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    return Config(
        release_tag=raw.get("release_tag", "bundles"),
        release_name=raw.get("release_name", "Offline bundles"),
        retention=int(raw.get("retention", 3)),
        platform=Platform(**raw.get("platform", {})),
    )


def load_packages(root: Path, only: list[str] | None = None) -> list[Package]:
    """读取 packages/*/package.toml；only 非空时只返回其中列出的包。"""
    known = {f.name for f in fields(Package)} - {"dir"}
    packages: list[Package] = []
    for path in sorted((root / "packages").glob("*/package.toml")):
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("id", path.parent.name)
        raw.setdefault("type", "github")
        unknown = set(raw) - known
        if unknown:
            raise SystemExit(f"{path}: 未知字段 {', '.join(sorted(unknown))}")
        pkg = Package(dir=path.parent, **raw)
        if pkg.type not in ("github", "wheelhouse", "pi"):
            raise SystemExit(f'{path}: 不支持 type = "{pkg.type}"（可选 github / wheelhouse / pi）')
        if pkg.type == "github" and not (pkg.repo and pkg.asset):
            raise SystemExit(f'{path}: type = "github" 需要 repo 与 asset')
        if pkg.type == "wheelhouse" and not pkg.requirements:
            raise SystemExit(f'{path}: type = "wheelhouse" 需要 requirements')
        if pkg.type == "pi" and not (pkg.repo and pkg.asset and pkg.extensions):
            raise SystemExit(f'{path}: type = "pi" 需要 repo、asset 与 extensions')
        if only and pkg.id not in only:
            continue
        packages.append(pkg)

    ids = [p.id for p in packages]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"包 id 重复：{', '.join(sorted(dup))}")
    missing = sorted(set(only or []) - set(ids))
    if missing:
        raise SystemExit(f"找不到包：{', '.join(missing)}")
    return packages


def version_from_tag(tag: str, regex: str = "") -> str:
    """tag -> 版本号。默认去掉开头的 v；可用 version_regex 自定义。"""
    if regex:
        match = re.search(regex, tag)
        if not match:
            raise ValueError(f"tag {tag!r} 不匹配 version_regex {regex!r}")
        return match.group(1) if match.groups() else match.group(0)
    return re.sub(r"^[vV]", "", tag.strip())


def asset_name(pkg: Package, version: str, tag: str) -> str:
    return pkg.asset.replace("{version}", version).replace("{tag}", tag)


def pick_asset(assets: list[dict], name: str) -> dict:
    for asset in assets:
        if asset.get("name") == name:
            return asset
    available = "\n  ".join(a.get("name", "?") for a in assets)
    raise LookupError(f"上游没有名为 {name!r} 的资产，可用资产：\n  {available}")


def install_command(pkg: Package, name: str, version: str, dirname: str = "") -> str:
    return (
        pkg.install.replace("{asset}", name)
        .replace("{version}", version)
        .replace("{dir}", dirname)
    )


def is_pure_wheel(filename: str) -> bool:
    """纯 Python wheel（py3-none-any / py2.py3-none-any）。"""
    return filename.endswith("-none-any.whl")


def version_from_freeze(lines: list[str], name: str) -> str:
    """从 pip freeze 输出中取指定包的版本；包名按 PEP 503 规范化比较。"""
    def norm(value: str) -> str:
        return re.sub(r"[-_.]+", "-", value).lower()

    target = norm(name)
    for line in lines:
        if "==" not in line:
            continue
        pkg_name, version = line.split("==", 1)
        if norm(pkg_name) == target:
            return version.strip()
    raise ValueError(f"pip freeze 中找不到 {name}")


def wheelhouse_asset_name(pkg_id: str, version: str, digest: str) -> str:
    return f"{pkg_id}-{version}+{digest}-py3-none-any.tar.gz"


def split_retained(entries: list[dict], keep: int) -> tuple[list[dict], list[dict]]:
    """按 built_at 倒序，前 keep 个保留，其余待删除。"""
    ordered = sorted(entries, key=lambda e: (e.get("built_at") or "", e.get("name") or ""), reverse=True)
    return ordered[:keep], ordered[keep:]


def make_index(cfg: Config, state: dict, repo: str, now: str) -> dict:
    """每个包只列最新版本；历史版本留在 state.json 里。"""
    packages = []
    for pkg_id, entry in sorted(state.get("packages", {}).items()):
        assets = entry.get("assets") or []
        if not assets:
            continue
        latest = sorted(assets, key=lambda a: a.get("built_at") or "")[-1]
        packages.append(
            {
                "id": pkg_id,
                "version": entry.get("version", ""),
                "description": entry.get("description", ""),
                "note": entry.get("note", ""),
                "bins": entry.get("bins", []),
                "asset": latest["name"],
                "size": latest.get("size", 0),
                "sha256": latest.get("sha256", ""),
                "url": f"https://github.com/{repo}/releases/download/{cfg.release_tag}/{latest['name']}",
                "upstream": entry.get("upstream", ""),
                "install": latest.get("install", "")
                if entry.get("type") in ("wheelhouse", "pi")
                else "",
                "extensions": entry.get("extensions", []),
            }
        )
    return {
        "generated_at": now,
        "release": {"tag": cfg.release_tag, "url": f"https://github.com/{repo}/releases/tag/{cfg.release_tag}"},
        "platform": cfg.platform.label,
        "packages": packages,
    }


def release_body(cfg: Config, state: dict, repo: str) -> str:
    lines = [
        f"## 离线包（{cfg.platform.label}）",
        "",
        "二进制文件均来自上游项目，未经修改。",
        "",
        "| 包 | 版本 | 文件 | 大小 | sha256 |",
        "|---|---|---|---|---|",
    ]
    for pkg_id, entry in sorted(state.get("packages", {}).items()):
        assets = entry.get("assets") or []
        if not assets:
            continue
        latest = sorted(assets, key=lambda a: a.get("built_at") or "")[-1]
        size = human_size(latest.get("size", 0))
        digest = (latest.get("sha256") or "")[:16]
        lines.append(f"| `{pkg_id}` | {entry.get('version', '')} | `{latest['name']}` | {size} | `{digest}…` |")
    lines += [
        "",
        "`index.json` 包含每个包的完整信息；`SHA256SUMS` 用于校验。",
    ]
    return "\n".join(lines)
