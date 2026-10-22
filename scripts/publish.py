#!/usr/bin/env python3
"""发布入口。

用法：
  python scripts/publish.py [--dist dist] [--repo owner/name] [--dry-run] [--clobber]

步骤：
  1. 确保滚动 Release 存在
  2. 上传 dist/ 中尚未发布的资产
  3. 每个包只保留最近 N 个版本（config.toml 的 retention）
  4. 生成并上传 index.json / SHA256SUMS / state.json
  5. 刷新 Release 说明
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import model, util  # noqa: E402
from lib.gh import GitHub  # noqa: E402

STATE_ASSET = "state.json"
INDEX_ASSET = "index.json"
SUMS_ASSET = "SHA256SUMS"


def content_type(name: str) -> str:
    if name.endswith(".zip"):
        return "application/zip"
    if name.endswith((".tar.gz", ".tgz")):
        return "application/gzip"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".txt"):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def load_metas(dist: Path) -> list[dict]:
    metas = []
    for path in sorted((dist / "meta").glob("*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        asset = dist / meta["asset"]
        if not asset.exists():
            util.die(f"缺少资产文件 {asset}（meta: {path}）")
        actual = util.sha256_file(asset)
        if actual != meta["sha256"]:
            util.die(f"{asset} 的 sha256 与 meta 不一致")
        meta["path"] = asset
        metas.append(meta)
    return metas


def merge_state(state: dict, metas: list[dict], uploaded: set[str] | None = None) -> dict:
    packages = state.setdefault("packages", {})
    for meta in metas:
        entry = packages.setdefault(meta["id"], {})
        entry.update(
            {
                "type": meta.get("type", "github"),
                "version": meta["version"],
                "tag": meta["tag"],
                "upstream": meta["upstream"],
                "description": meta["description"],
                "note": meta["note"],
                "bins": meta["bins"],
                "extensions": meta.get("extensions", []),
            }
        )
        existing = {a["name"]: a for a in entry.get("assets", [])}
        # 同名资产已经存在且本次未重新上传时，保留原有记录（sha256/size/时间）
        if uploaded is None or meta["asset"] in uploaded or meta["asset"] not in existing:
            existing[meta["asset"]] = {
                "name": meta["asset"],
                "sha256": meta["sha256"],
                "size": meta["size"],
                "built_at": meta["built_at"],
                "install": meta["install"],
            }
        entry["assets"] = list(existing.values())
    return state


def collect_sums(state: dict) -> str:
    rows = []
    for entry in state.get("packages", {}).values():
        for asset in entry.get("assets", []):
            rows.append(f"{asset['sha256']}  {asset['name']}")
    return "\n".join(sorted(rows)) + ("\n" if rows else "")


def ensure_release(gh: GitHub, cfg: model.Config, repo: str) -> dict:
    release = gh.release_by_tag(repo, cfg.release_tag)
    if release:
        return release
    util.log(f"创建 Release {cfg.release_tag}")
    return gh.create_release(repo, cfg.release_tag, cfg.release_name)


def remote_state(gh: GitHub, release: dict) -> dict:
    for asset in release.get("assets", []):
        if asset.get("name") == STATE_ASSET:
            return json.loads(gh.get(asset["browser_download_url"]))
    return {"schema": 1, "packages": {}}


def upload_replace(gh: GitHub, repo: str, release: dict, name: str, path: Path) -> None:
    for asset in release.get("assets", []):
        if asset.get("name") == name:
            gh.delete_asset(repo, asset["id"])
            break
    gh.upload_asset(repo, release["id"], name, path, content_type(name))


def cmd_publish(args: argparse.Namespace) -> int:
    cfg = model.load_config(ROOT)
    dist = Path(args.dist)
    metas = load_metas(dist)
    if not metas:
        util.die(f"{dist} 下没有可发布的资产（缺少 meta/*.json）")

    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo and not args.dry_run:
        util.die("缺少仓库名：设置 GITHUB_REPOSITORY 或传 --repo owner/name")

    local_state = merge_state({"schema": 1, "packages": {}}, metas)
    report = [
        f"待上传 {len(metas)} 个资产（{util.human_size(sum(m['size'] for m in metas))}）：",
        *[f"  - {m['asset']}  {util.human_size(m['size'])}  sha256={m['sha256'][:12]}…" for m in metas],
    ]
    for line in report:
        util.log(line)

    if args.dry_run:
        preview = model.make_index(cfg, local_state, repo or "OWNER/REPO", util.utc_now_iso())
        util.log("dry-run：不联网、不上传。index.json 预览：")
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        util.die("缺少 GITHUB_TOKEN")
    gh = GitHub(token)

    release = ensure_release(gh, cfg, repo)
    state = remote_state(gh, release)

    existing = {a["name"]: a for a in release.get("assets", [])}
    uploaded: set[str] = set()
    for meta in metas:
        if meta["asset"] in existing and not args.clobber:
            util.log(f"已存在，跳过：{meta['asset']}")
            continue
        if meta["asset"] in existing:
            gh.delete_asset(repo, existing[meta["asset"]]["id"])
        util.log(f"上传 {meta['asset']}")
        gh.upload_asset(repo, release["id"], meta["asset"], meta["path"], content_type(meta["asset"]))
        uploaded.add(meta["asset"])

    state = merge_state(state, metas, uploaded)

    # 清理超出保留数量的旧版本
    release = gh.release_by_tag(repo, cfg.release_tag) or release
    asset_ids = {a["name"]: a["id"] for a in release.get("assets", [])}
    for pkg_id, entry in state.get("packages", {}).items():
        keep, drop = model.split_retained(entry.get("assets", []), cfg.retention)
        entry["assets"] = keep
        for asset in drop:
            util.log(f"清理 {pkg_id} 旧版本 {asset['name']}")
            if asset["name"] in asset_ids:
                gh.delete_asset(repo, asset_ids[asset["name"]])

    now = util.utc_now_iso()
    index = model.make_index(cfg, state, repo, now)
    sums = collect_sums(state)
    for name, text in ((INDEX_ASSET, json.dumps(index, ensure_ascii=False, indent=2) + "\n"), (SUMS_ASSET, sums)):
        path = dist / name
        path.write_text(text, encoding="utf-8")
    util.write_json(dist / STATE_ASSET, state)

    release = gh.release_by_tag(repo, cfg.release_tag) or release
    for name in (INDEX_ASSET, SUMS_ASSET, STATE_ASSET):
        upload_replace(gh, repo, release, name, dist / name)

    gh.update_release(repo, release["id"], body=model.release_body(cfg, state, repo))

    util.log("发布完成：")
    util.log(f"  https://github.com/{repo}/releases/tag/{cfg.release_tag}")
    util.log(f"  https://github.com/{repo}/releases/latest/download/{INDEX_ASSET}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布离线包到 GitHub Release")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--repo", default="", help="owner/name，默认取 GITHUB_REPOSITORY")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不联网")
    parser.add_argument("--clobber", action="store_true", help="同名资产覆盖上传")
    args = parser.parse_args(argv)
    return cmd_publish(args)


if __name__ == "__main__":
    raise SystemExit(main())
