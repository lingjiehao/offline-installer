#!/bin/sh
# compiledb 离线安装脚本（无需联网）
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${PYTHON:-python3}
MODE=auto
VENV_DIR=

usage() {
  cat <<'EOF'
用法：
  ./install.sh                 # 已在 venv/conda 中则装进当前环境，否则 pip install --user
  ./install.sh --user          # 强制 pip install --user
  ./install.sh --system        # 装进当前 Python（可能需要权限，不推荐）
  ./install.sh --venv DIR      # 新建 venv 并安装到 DIR（推荐）
  ./install.sh --python PY     # 指定 Python 解释器
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --user) MODE=user ;;
    --system) MODE=system ;;
    --venv) MODE=venv; VENV_DIR=${2:?--venv 需要一个目录参数}; shift ;;
    --python) PY=${2:?--python 需要一个解释器路径}; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[ -d "$HERE/wheels" ] || { echo "错误：缺少 $HERE/wheels" >&2; exit 1; }
[ -f "$HERE/requirements.txt" ] || { echo "错误：缺少 $HERE/requirements.txt" >&2; exit 1; }

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "错误：找不到 $PY，可用 --python 指定" >&2
  exit 1
fi
"$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 6) else 1)' || {
  echo "错误：需要 Python 3.6 或更高版本" >&2
  exit 1
}
"$PY" -m pip --version >/dev/null 2>&1 || {
  echo "错误：$PY 没有可用的 pip" >&2
  exit 1
}

if [ "$MODE" = venv ]; then
  "$PY" -m venv "$VENV_DIR"
  PY="$VENV_DIR/bin/python"
elif [ "$MODE" = auto ]; then
  if [ -n "${VIRTUAL_ENV:-}" ] || [ -n "${CONDA_PREFIX:-}" ]; then
    MODE=system
  else
    MODE=user
  fi
fi

EXTRA=
[ "$MODE" = user ] && EXTRA=--user

status=0
# shellcheck disable=SC2086
"$PY" -m pip install --no-index --find-links "$HERE/wheels" \
  --disable-pip-version-check -r "$HERE/requirements.txt" $EXTRA || status=$?

if [ "$status" -ne 0 ]; then
  cat >&2 <<'EOF'

安装失败。常见原因：
  - 当前 Python 受 PEP 668 管理（externally-managed-environment）：
      请改用  ./install.sh --venv .venv
  - 目标机器的 Python 版本低于包要求
EOF
  exit "$status"
fi

echo "安装完成：$("$PY" -m pip show compiledb 2>/dev/null | sed -n '2p')"
