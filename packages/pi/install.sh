#!/bin/sh
# pi agent 离线安装（无需 root、无需联网、无需 Node.js）
#
# 安装位置：
#   $PI_INSTALL_PREFIX（默认 ~/.local/opt/pi-agent）  pi 本体
#   ~/.local/bin/pi                                   启动器（已设置 PI_OFFLINE 等）
#   ~/.pi/agent/packages/<name>                       扩展
#   ~/.pi/agent/settings.json                         追加扩展声明
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${PI_INSTALL_PREFIX:-$HOME/.local/opt/pi-agent}
BIN_DIR=${PI_BIN_DIR:-$HOME/.local/bin}
AGENT_DIR=${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}

if [ ! -x "$HERE/pi/pi" ]; then
  echo "错误：找不到 $HERE/pi/pi" >&2
  exit 1
fi

echo "==> 安装 pi 到 $PREFIX/pi"
mkdir -p "$PREFIX"
rm -rf "$PREFIX/pi"
cp -a "$HERE/pi" "$PREFIX/pi"
chmod +x "$PREFIX/pi/pi"
if [ -d "$PREFIX/pi/native" ]; then
  find "$PREFIX/pi/native" -type f -exec chmod +x {} + 2>/dev/null || true
fi

echo "==> 创建启动器 $BIN_DIR/pi"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/pi" <<EOF
#!/bin/sh
exec env PI_OFFLINE=1 PI_SKIP_VERSION_CHECK=1 PI_TELEMETRY=0 "$PREFIX/pi/pi" "\$@"
EOF
chmod +x "$BIN_DIR/pi"

echo "==> 安装扩展到 $AGENT_DIR/packages/"
mkdir -p "$AGENT_DIR/packages"
refs=
for src in "$HERE"/packages/*; do
  [ -d "$src" ] || continue
  name=$(basename "$src")
  rm -rf "$AGENT_DIR/packages/$name"
  cp -a "$src" "$AGENT_DIR/packages/$name"
  refs="$refs ./packages/$name"
  echo "    $name"
done

SETTINGS="$AGENT_DIR/settings.json"
echo "==> 写入扩展声明 $SETTINGS"
if command -v python3 >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  python3 - "$SETTINGS" $refs <<'PY'
import json
import os
import sys

path, refs = sys.argv[1], sys.argv[2:]
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        data = {}
packages = data.get("packages")
if not isinstance(packages, list):
    packages = []
for ref in refs:
    if ref not in packages:
        packages.append(ref)
data["packages"] = packages
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY
elif [ ! -s "$SETTINGS" ]; then
  {
    printf '{\n  "packages": ['
    sep=
    for ref in $refs; do
      printf '%s\n    "%s"' "$sep" "$ref"
      sep=,
    done
    printf '\n  ]\n}\n'
  } > "$SETTINGS"
else
  echo "警告：未找到 python3，请手工把扩展路径加入 $SETTINGS 的 packages 数组" >&2
fi

echo "==> 冒烟测试"
if ! PI_OFFLINE=1 "$PREFIX/pi/pi" --version; then
  cat >&2 <<EOF
错误：pi 无法运行。请检查：
  file $PREFIX/pi/pi            # 应为 ELF 64-bit x86-64
  ldd --version | head -n 1     # 需要 glibc >= 2.17
  df -h "$PREFIX"               # 分区不能挂载为 noexec
EOF
  exit 1
fi

echo
echo "安装完成。"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "请把 $BIN_DIR 加入 PATH，例如：export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
echo "验证：pi --version && pi list"
