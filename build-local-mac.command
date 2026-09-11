#!/bin/bash
set -Eeuo pipefail

APP_VERSION="1.3.5"
DMG_NAME="KAYTRADE-${APP_VERSION}-Intel-test.dmg"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${ROOT_DIR}/.build-venv-v135"
TMP_DIR=""

pause_and_exit() {
  local code="${1:-0}"
  echo
  if [[ "$code" -eq 0 ]]; then
    echo "按回车键关闭窗口。"
  else
    echo "构建未完成。请保留上面的错误信息，按回车键关闭窗口。"
  fi
  read -r _ || true
  exit "$code"
}

cleanup() {
  if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
    rm -rf "$TMP_DIR"
  fi
}

on_error() {
  local code=$?
  cleanup
  echo
  echo "[失败] 第 ${BASH_LINENO[0]} 行执行失败（退出码 ${code}）。"
  pause_and_exit "$code"
}

trap on_error ERR

echo "============================================================"
echo " KAYTRADE V${APP_VERSION} · Intel Mac 本地安全构建"
echo "============================================================"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[错误] 本脚本只能在 macOS 上运行。"
  pause_and_exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "[错误] 当前不是 Intel Mac（x86_64），停止构建以避免生成错误架构。"
  pause_and_exit 1
fi

cd "$ROOT_DIR"
for required in OKXLocal.spec build_icon.py verify_bundle.py desktop.py v135_patch.py v135_execution_patch.py; do
  if [[ ! -f "$required" ]]; then
    echo "[错误] 缺少 ${required}。请先下载完整的 V1.3.5 修复分支源码。"
    pause_and_exit 1
  fi
done

PYTHON_BIN=""
for candidate in python3.13 /usr/local/bin/python3.13 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "$candidate")"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "[缺少组件] 没有找到 Python 3.13。"
  echo "请先从 https://www.python.org/downloads/macos/ 安装 Python 3.13（macOS 64-bit universal2），然后重新运行本文件。"
  open "https://www.python.org/downloads/macos/" || true
  pause_and_exit 1
fi

PY_ARCH="$($PYTHON_BIN -c 'import platform; print(platform.machine())')"
if [[ "$PY_ARCH" != "x86_64" ]]; then
  echo "[错误] Python 架构为 ${PY_ARCH}，需要 Intel x86_64 Python 3.13。"
  pause_and_exit 1
fi

echo "[1/7] 创建独立构建环境"
"$PYTHON_BIN" -m venv "$VENV_DIR"
PYTHON="${VENV_DIR}/bin/python"

echo "[2/7] 安装固定版本的打包依赖"
"$PYTHON" -m pip install --disable-pip-version-check --upgrade pip
"$PYTHON" -m pip install --disable-pip-version-check \
  "pyinstaller==6.16.0" "certifi==2025.8.3" "Pillow==11.3.0"

echo "[3/7] 编译检查"
"$PYTHON" -m py_compile \
  strategy.py engine.py exchange.py candles.py core.py app.py visual.py desktop.py \
  v135_patch.py v135_execution_patch.py test_v135_execution.py \
  test_v135_safety2.py test_v135_order_state.py test_v135_ack.py \
  test_v135_fills.py test_v135_runtime_stability.py

echo "[4/7] 运行 V1.3.5 回归测试"
"$PYTHON" -m unittest -q \
  test_engine test_candles test_v134 test_v135 test_v135_execution test_v135_safety2
"$PYTHON" -m unittest -q \
  test_v135_order_state test_v135_ack test_v135_fills test_v135_runtime_stability

echo "[5/7] 生成 Intel Mac 应用"
rm -rf "${ROOT_DIR}/build" "${ROOT_DIR}/dist" "${ROOT_DIR}/installer"
rm -f "${ROOT_DIR}/${DMG_NAME}" "${ROOT_DIR}/SHA256.txt"
"$PYTHON" build_icon.py
MACOSX_DEPLOYMENT_TARGET=14.0 "$PYTHON" -m PyInstaller --noconfirm OKXLocal.spec
"$PYTHON" verify_bundle.py

echo "[6/7] 执行应用冒烟测试和签名验证"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kaytrade-v135.XXXXXX")"
"${ROOT_DIR}/dist/KAYTRADE.app/Contents/MacOS/KAYTRADE" \
  --smoke-test "${TMP_DIR}/kaytrade-smoke.txt"
test -s "${TMP_DIR}/kaytrade-smoke.txt"
codesign --verify --deep --strict "${ROOT_DIR}/dist/KAYTRADE.app"

echo "[7/7] 制作 DMG 安装包"
mkdir -p "${ROOT_DIR}/installer"
ditto "${ROOT_DIR}/dist/KAYTRADE.app" "${ROOT_DIR}/installer/KAYTRADE.app"
cp "${ROOT_DIR}/RELEASE-1.3.5.md" "${ROOT_DIR}/installer/RELEASE-1.3.5.md"
cp "${TMP_DIR}/kaytrade-smoke.txt" "${ROOT_DIR}/installer/BUILD-TEST.txt"
ln -s /Applications "${ROOT_DIR}/installer/Applications"
hdiutil create -volname "KAYTRADE-${APP_VERSION}" \
  -srcfolder "${ROOT_DIR}/installer" -ov -format UDZO "${ROOT_DIR}/${DMG_NAME}"
shasum -a 256 "${ROOT_DIR}/${DMG_NAME}" > "${ROOT_DIR}/SHA256.txt"

cleanup
trap - ERR

echo
echo "[完成] 已生成并验证："
echo "${ROOT_DIR}/${DMG_NAME}"
echo "${ROOT_DIR}/SHA256.txt"
echo
cat "${ROOT_DIR}/SHA256.txt"
open -R "${ROOT_DIR}/${DMG_NAME}"
osascript -e 'display notification "Intel Mac 安装包已生成" with title "KAYTRADE V1.3.5"' || true
pause_and_exit 0
