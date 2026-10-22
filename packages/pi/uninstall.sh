#!/bin/sh
# 卸载 pi agent（由 install.sh 安装的版本）
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${PI_INSTALL_PREFIX:-$HOME/.local/opt/pi-agent}
BIN_DIR=${PI_BIN_DIR:-$HOME/.local/bin}
AGENT_DIR=${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}

names=
for src in "$HERE"/packages/*; do
  [ -d "$src" ] || continue
  names="$names $(basename "$src")"
done

echo "==> 移除扩展声明"
if [ -f "$AGENT_DIR/settings.json" ] && command -v python3 >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  python3 - "$AGENT_DIR/settings.json" $names <<'PY'
import json
import sys

path, names = sys.argv[1], set(sys.argv[2:])
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}
refs = {f"./packages/{name}" for name in names}
packages = data.get("packages")
if isinstance(packages, list):
    data["packages"] = [p for p in packages if not (isinstance(p, str) and p in refs)]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
PY
fi

echo "==> 删除扩展目录"
for name in $names; do
  rm -rf "$AGENT_DIR/packages/$name"
done

echo "==> 删除 pi 与启动器"
rm -f "$BIN_DIR/pi"
rm -rf "$PREFIX"

echo "已卸载。"
