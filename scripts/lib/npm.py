"""npm registry 的最小客户端（仅标准库）。"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

from . import util

REGISTRY = "https://registry.npmjs.org"


def parse_spec(spec: str) -> tuple[str, str]:
    """解析扩展声明：name / name@version / @scope/name@version。"""
    if spec.startswith("@"):
        at = spec.find("@", 1)
        return (spec, "latest") if at == -1 else (spec[:at], spec[at + 1 :])
    if "@" in spec:
        name, version = spec.split("@", 1)
        return name, version or "latest"
    return spec, "latest"


def metadata(name: str) -> dict:
    url = f"{REGISTRY}/{urllib.parse.quote(name, safe='@')}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": util.USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def resolve_version(meta: dict, wanted: str) -> str:
    if wanted == "latest":
        return meta["dist-tags"]["latest"]
    if wanted in meta["versions"]:
        return wanted
    raise LookupError(f"npm 上没有版本 {wanted}")


def verify_sri(path: Path, integrity: str) -> None:
    """校验 npm 的 sha512 SRI。"""
    algo, _, expected = integrity.partition("-")
    if algo != "sha512":
        raise ValueError(f"不支持的 integrity 算法：{algo}")
    digest = hashlib.sha512()
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            digest.update(block)
    actual = base64.b64encode(digest.digest()).decode()
    if actual != expected:
        raise ValueError(f"{path.name} 的 sha512 与 npm integrity 不一致")


def extract_package(tgz: Path, dest: Path) -> None:
    """解压 npm tarball 中的 package/ 到 dest。"""
    tmp = dest.parent / (dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz, "r:gz") as tf:
        tf.extractall(tmp, filter="data")
    shutil.move(str(tmp / "package"), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)


def install_dependencies(pkg_dir: Path) -> None:
    """补齐扩展的运行依赖；peer 由 pi 宿主提供，不安装。"""
    util.run(
        [
            "npm",
            "install",
            "--omit=dev",
            "--omit=peer",
            "--ignore-scripts",
            "--no-save",
            "--no-package-lock",
            "--no-audit",
            "--no-fund",
        ],
        cwd=pkg_dir,
    )


def missing_dependencies(pkg_dir: Path, dependencies: dict) -> list[str]:
    return [name for name in dependencies if not (pkg_dir / "node_modules" / name / "package.json").exists()]


def tree_versions(root: Path) -> list[str]:
    """收集 node_modules 下所有包的 name@version，用于计算内容指纹。"""
    rows: set[str] = set()
    for path in root.rglob("node_modules/**/package.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("name") and data.get("version"):
            rows.add(f"{data['name']}@{data['version']}")
    return sorted(rows)
