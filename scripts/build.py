#!/usr/bin/env python3
"""构建入口。

用法：
  python scripts/build.py plan  [--packages "fd ripgrep"] [--force] [--github-output FILE]
  python scripts/build.py build --pkg fd [--out dist] [--work-dir .work] [--keep]

plan  解析上游最新版本，判断哪些包需要构建（已发布过的跳过）。
build 构建单个包：
        github    下载上游资产到 dist/
        wheelhouse 下载全部 wheel、离线安装自检、打包到 dist/
      并写 dist/meta/<id>.json 供 publish 使用。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import model, npm, util  # noqa: E402
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
    published: dict[str, set[str]] = {}
    for pkg_id, entry in (state or {}).get("packages", {}).items():
        published[pkg_id] = {asset.get("name") for asset in entry.get("assets", [])}

    todo: list[dict] = []
    for pkg in packages:
        if pkg.type in ("wheelhouse", "pi"):
            # 依赖/扩展版本无法预先得知，总是重建；内容哈希相同则发布阶段跳过上传
            todo.append({"pkg": pkg.id})
            util.log(f"待构建 {pkg.id}（{pkg.type}，总是重建）")
            continue

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
    (pkg,) = model.load_packages(ROOT, [args.pkg])
    if pkg.type == "github":
        return build_github(pkg, args)
    if pkg.type == "wheelhouse":
        return build_wheelhouse(pkg, args)
    if pkg.type == "pi":
        return build_pi(pkg, args)
    util.die(f"{pkg.id}: 不支持的类型 {pkg.type}")
    return 1


def build_github(pkg: model.Package, args: argparse.Namespace) -> int:
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
        "type": pkg.type,
        "version": version,
        "tag": tag,
        "asset": name,
        "sha256": sha256,
        "size": size,
        "upstream": f"https://github.com/{pkg.repo}/releases/tag/{tag}",
        "description": pkg.description,
        "note": pkg.note,
        "bins": list(pkg.bins),
        "install": "",
        "built_at": util.utc_now_iso(),
    }
    util.write_json(out / "meta" / f"{pkg.id}.json", meta)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    util.log(f"完成：{target}")
    return 0


def build_wheelhouse(pkg: model.Package, args: argparse.Namespace) -> int:
    out = Path(args.out)
    work = Path(args.work_dir) / pkg.id
    wheels_dir = work / "wheels"
    shutil.rmtree(work, ignore_errors=True)
    wheels_dir.mkdir(parents=True)

    requested = work / "requested.txt"
    requested.write_text("\n".join(pkg.requirements) + "\n", encoding="utf-8")

    python = args.python or sys.executable
    util.log(f"{pkg.id}（wheelhouse）")
    util.run(
        [
            python,
            "-m",
            "pip",
            "download",
            "--only-binary=:all:",
            "--no-cache-dir",
            "--dest",
            wheels_dir,
            "-r",
            requested,
        ]
    )

    wheels = sorted(wheels_dir.glob("*.whl"))
    if not wheels:
        util.die("没有下载到任何 wheel")
    impure = [w.name for w in wheels if not model.is_pure_wheel(w.name)]
    if impure:
        util.die(
            "发现非纯 Python wheel，当前版本只支持纯 Python 包：\n  " + "\n  ".join(impure)
        )
    util.log(f"    共 {len(wheels)} 个 wheel，全部为纯 Python")

    # 在临时 venv 里做一次完全离线的安装自检，并生成精确锁定
    venv = work / "venv"
    util.run([python, "-m", "venv", venv])
    venv_python = venv / "bin" / "python"
    util.run(
        [
            venv_python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            wheels_dir,
            "--disable-pip-version-check",
            "--quiet",
            "-r",
            requested,
        ]
    )
    frozen = util.run([venv_python, "-m", "pip", "freeze"], capture=True).strip().splitlines()
    version = model.version_from_freeze(frozen, pkg.id)
    util.log(f"    离线安装自检通过，{pkg.id} {version}")

    digest = util.sha256_text("\n".join(w.name for w in wheels))[:6]
    asset = model.wheelhouse_asset_name(pkg.id, version, digest)
    dirname = asset[: -len(".tar.gz")]
    stage = work / "stage" / dirname
    stage.mkdir(parents=True)

    shutil.copytree(wheels_dir, stage / "wheels")
    (stage / "requirements.txt").write_text("\n".join(frozen) + "\n", encoding="utf-8")
    shutil.copy2(requested, stage / "requested.txt")

    installer = pkg.dir / "install.sh"
    if not installer.exists():
        util.die(f"缺少 {installer}")
    shutil.copy2(installer, stage / "install.sh")
    (stage / "install.sh").chmod(0o755)

    out.mkdir(parents=True, exist_ok=True)
    archive = out / asset
    if archive.exists():
        util.die(f"{archive} 已存在，请先清理 dist/")
    util.make_tar_gz(stage, archive)

    size = archive.stat().st_size
    meta = {
        "id": pkg.id,
        "type": pkg.type,
        "version": version,
        "tag": version,
        "asset": asset,
        "sha256": util.sha256_file(archive),
        "size": size,
        "upstream": f"https://pypi.org/project/{pkg.id}/{version}/",
        "description": pkg.description,
        "note": pkg.note,
        "bins": list(pkg.bins),
        "install": model.install_command(pkg, asset, version, dirname),
        "built_at": util.utc_now_iso(),
    }
    util.write_json(out / "meta" / f"{pkg.id}.json", meta)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    util.log(f"完成：{archive}（{util.human_size(size)}）")
    return 0


def build_pi(pkg: model.Package, args: argparse.Namespace) -> int:
    """pi 官方自包含二进制 + 指定扩展（含运行依赖）。"""
    out = Path(args.out)
    work = Path(args.work_dir) / pkg.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    gh = GitHub(args.token)
    release = gh.latest_release(pkg.repo)
    tag = release["tag_name"]
    version = model.version_from_tag(tag, pkg.version_regex)
    name = model.asset_name(pkg, version, tag)
    asset = model.pick_asset(release["assets"], name)
    util.log(f"{pkg.id} {version}")

    archive = work / name
    sha256, size = util.download(asset["browser_download_url"], archive, args.token)
    expected = (asset.get("digest") or "").removeprefix("sha256:")
    if expected and expected != sha256:
        util.die(f"sha256 校验失败：上游为 {expected}，实际为 {sha256}")
    util.log(f"    下载完成（{util.human_size(size)}）")

    dirname = f"pi-offline-{version}"
    stage = work / "stage" / dirname
    stage.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(stage, filter="data")
    if not (stage / "pi" / "pi").exists():
        util.die("pi 归档结构异常：找不到 pi/pi")

    extensions: list[dict] = []
    for spec in pkg.extensions:
        ext_name, wanted = npm.parse_spec(spec)
        meta = npm.metadata(ext_name)
        try:
            ext_version = npm.resolve_version(meta, wanted)
        except LookupError as exc:
            util.die(f"{ext_name}: {exc}")
        info = meta["versions"][ext_version]
        dist = info["dist"]
        flat = ext_name.replace("/", "__")
        tgz = work / "ext" / f"{flat}-{ext_version}.tgz"
        util.download(dist["tarball"], tgz)
        try:
            npm.verify_sri(tgz, dist["integrity"])
        except ValueError as exc:
            util.die(str(exc))

        dest_pkg = stage / "packages" / flat
        npm.extract_package(tgz, dest_pkg)
        dependencies = info.get("dependencies") or {}
        if dependencies:
            util.log(f"    {ext_name}@{ext_version}：安装 {len(dependencies)} 个运行依赖")
            npm.install_dependencies(dest_pkg)
            missing = npm.missing_dependencies(dest_pkg, dependencies)
            if missing:
                util.die(f"{ext_name} 依赖不完整：{', '.join(missing)}")
        else:
            util.log(f"    {ext_name}@{ext_version}：无运行依赖")
        extensions.append({"name": ext_name, "version": ext_version})

    for script in ("install.sh", "uninstall.sh"):
        src = pkg.dir / script
        if not src.exists():
            util.die(f"缺少 {src}")
        shutil.copy2(src, stage / script)
        (stage / script).chmod(0o755)

    fingerprint = [f"pi@{version}"] + [f"{e['name']}@{e['version']}" for e in extensions]
    fingerprint += npm.tree_versions(stage / "packages")
    digest = util.sha256_text("\n".join(sorted(fingerprint)))[:6]
    asset_name = f"{pkg.id}-{version}-linux-x64-{digest}.tar.gz"

    out.mkdir(parents=True, exist_ok=True)
    target = out / asset_name
    if target.exists():
        util.die(f"{target} 已存在，请先清理 dist/")
    util.make_tar_gz(stage, target)

    final_size = target.stat().st_size
    meta = {
        "id": pkg.id,
        "type": pkg.type,
        "version": version,
        "tag": tag,
        "asset": asset_name,
        "sha256": util.sha256_file(target),
        "size": final_size,
        "upstream": f"https://github.com/{pkg.repo}/releases/tag/{tag}",
        "description": pkg.description,
        "note": pkg.note,
        "bins": list(pkg.bins),
        "install": model.install_command(pkg, asset_name, version, dirname),
        "extensions": extensions,
        "built_at": util.utc_now_iso(),
    }
    util.write_json(out / "meta" / f"{pkg.id}.json", meta)

    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    util.log(f"完成：{target}（{util.human_size(final_size)}）")
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
    p_build.add_argument("--python", default="", help="wheelhouse 使用的 Python，默认当前解释器")
    p_build.add_argument("--keep", action="store_true", help="保留下载的临时文件")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
