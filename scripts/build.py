#!/usr/bin/env python3
"""构建入口。

用法：
  python scripts/build.py plan  [--packages "fd ripgrep"] [--force] [--github-output FILE]
  python scripts/build.py build --pkg fd [--out dist] [--work-dir .work] [--keep]

plan  解析上游最新版本，判断哪些包需要构建（已发布过的跳过）。
build 下载单个包的资产到 dist/，并写 dist/meta/<id>.json 供 publish 使用。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import model, util  # noqa: E402
from lib.gh import GitHub  # noqa: E402

STATE_ASSET = "state.json"


def fetch_state(gh: GitHub, repo: str, tag: str) -> dict | None:
    release = gh.release_by_tag(repo, tag)
    if not release:
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == STATE_ASSET:
            return json.loads(gh.get(asset["browser_download_url"]))
    return None


def parse_package_filter(value: str) -> list[str]:
    return [item for item in value.replace(",", " ").split() if item]


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = model.load_config(ROOT)
    only = parse_package_filter(args.packages)
    packages = model.load_packages(ROOT, only)
    gh = GitHub(args.token)

    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    state = fetch_state(gh, repo, cfg.release_tag) if repo else None
    published = {}
    for pkg_id, entry in (state or {}).get("packages", {}).items():
        published[pkg_id] = {asset.get("name") for asset in entry.get("assets", [])}

    todo: list[dict] = []
    for pkg in packages:
        try:
            release = gh.latest_release(pkg.repo)
            version = model.version_from_tag(release["tag_name"], pkg.version_regex)
            name = model.asset_name(pkg, version, release["tag_name"])
            model.pick_asset(release["assets"], name)
        except Exception as exc:  # 上游异常时清楚报出来，而不是静默跳过
            util.die(f"{pkg.id}: {exc}")

        if not args.force and name in published.get(pkg.id, set()):
            util.log(f"跳过 {pkg.id}：{name} 已发布")
            continue
        todo.append({"pkg": pkg.id})
        util.log(f"待构建 {pkg.id}：{name}")

    matrix = {"include": todo}
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"matrix={json.dumps(matrix, ensure_ascii=False)}\n")
            fh.write(f"has_work={'true' if todo else 'false'}\n")
    if args.json:
        print(json.dumps({"matrix": matrix, "has_work": bool(todo)}, ensure_ascii=False))
    util.log(f"共 {len(todo)} 个包待构建")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    config = model.load_config(ROOT)
    (pkg,) = model.load_packages(ROOT, [args.pkg])
    gh = GitHub(args.token)

    release = gh.latest_release(pkg.repo)
    tag = release["tag_name"]
    version = model.version_from_tag(tag, pkg.version_regex)
    name = model.asset_name(pkg, version, tag)
    asset = model.pick_asset(release["assets"], name)

    out = Path(args.out)
    work = Path(args.work_dir) / pkg.id
    dest = work / name
    work.mkdir(parents=True, exist_ok=True)

    util.log(f"{pkg.id} {version}")
    util.log(f"    来源 {asset['browser_download_url']}")
    sha256, size = util.download(asset["browser_download_url"], dest, args.token)
    util.log(f"    下载完成（{util.human_size(size)}）")

    expected = (asset.get("digest") or "").removeprefix("sha256:")
    if expected and expected != sha256:
        util.die(f"sha256 校验失败：上游为 {expected}，实际为 {sha256}")
    if expected:
        util.log("    sha256 与上游 digest 一致")

    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    if target.exists():
        util.die(f"{target} 已存在，请先清理 dist/ 或换一个 --out")
    shutil.copy2(dest, target)

    meta = {
        "id": pkg.id,
        "version": version,
        "tag": tag,
        "asset": name,
        "sha256": sha256,
        "size": size,
        "upstream": f"https://github.com/{pkg.repo}/releases/tag/{tag}",
        "description": pkg.description,
        "note": pkg.note,
        "bins": list(pkg.bins),
        "install": model.install_command(pkg, name, version),
        "built_at": util.utc_now_iso(),
    }
    util.write_json(out / "meta" / f"{pkg.id}.json", meta)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    util.log(f"完成：{target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线包构建入口")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"), help="GitHub token（可选）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="列出需要构建的包")
    p_plan.add_argument("--packages", default="", help="空格或逗号分隔；留空表示全部")
    p_plan.add_argument("--repo", default="", help="owner/name，用于读取已发布状态")
    p_plan.add_argument("--force", action="store_true", help="忽略已发布状态")
    p_plan.add_argument("--github-output", default="", help="写入 GITHUB_OUTPUT")
    p_plan.add_argument("--json", action="store_true", help="向 stdout 输出 JSON")
    p_plan.set_defaults(func=cmd_plan)

    p_build = sub.add_parser("build", help="构建单个包")
    p_build.add_argument("--pkg", required=True)
    p_build.add_argument("--out", default="dist")
    p_build.add_argument("--work-dir", default=os.environ.get("OFFLINE_WORK_DIR", ".work"))
    p_build.add_argument("--keep", action="store_true", help="保留下载的临时文件")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
