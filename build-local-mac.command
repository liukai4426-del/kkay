#!/bin/bash
set -Eeuo pipefail

APP_VERSION="1.6.3"
BUILD_NUMBER="1630"
DMG_NAME="KAYTRADE-${APP_VERSION}-Intel-build${BUILD_NUMBER}.dmg"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${ROOT_DIR}/.build-venv-v163"
TMP_DIR=""

pause_and_exit() {
  local code="${1:-0}"
  echo
  if [[ "$code" -eq 0 ]]; then echo "按回车键关闭窗口。"; else echo "构建未完成。请保留上面的错误信息，按回车键关闭窗口。"; fi
  read -r _ || true
  exit "$code"
}
cleanup() { [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR" || true; }
on_error() { local code=$?; cleanup; echo; echo "[失败] 第 ${BASH_LINENO[0]} 行执行失败（退出码 ${code}）。"; pause_and_exit "$code"; }
trap on_error ERR

echo "============================================================"
echo " KAYTRADE V${APP_VERSION} Build${BUILD_NUMBER} · Intel Mac 本地安全构建"
echo "============================================================"

[[ "$(uname -s)" == "Darwin" ]] || { echo "[错误] 本脚本只能在 macOS 上运行。"; pause_and_exit 1; }
[[ "$(uname -m)" == "x86_64" ]] || { echo "[错误] 当前不是 Intel Mac（x86_64）。"; pause_and_exit 1; }

cd "$ROOT_DIR"
for required in OKXLocal.spec build_icon.py verify_bundle.py desktop.py v163_model.py v163_update_patch.py v163_ui_patch.py test_v163_update.py test_v163_ui.py RELEASE-1.6.3.md; do
  [[ -f "$required" ]] || { echo "[错误] 缺少 ${required}。请下载完整 V1.6.3 分支源码。"; pause_and_exit 1; }
done

PYTHON_BIN=""
for candidate in python3.13 /usr/local/bin/python3.13 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13; do
  if command -v "$candidate" >/dev/null 2>&1; then PYTHON_BIN="$(command -v "$candidate")"; break; fi
done
[[ -n "$PYTHON_BIN" ]] || { echo "[缺少组件] 没有找到 Python 3.13。"; open "https://www.python.org/downloads/macos/" || true; pause_and_exit 1; }
[[ "$($PYTHON_BIN -c 'import platform; print(platform.machine())')" == "x86_64" ]] || { echo "[错误] Python 必须是 Intel x86_64。"; pause_and_exit 1; }

echo "[1/7] 创建独立构建环境"
"$PYTHON_BIN" -m venv "$VENV_DIR"
PYTHON="${VENV_DIR}/bin/python"

echo "[2/7] 安装固定打包依赖"
"$PYTHON" -m pip install --disable-pip-version-check --upgrade pip
"$PYTHON" -m pip install --disable-pip-version-check "pyinstaller==6.16.0" "certifi==2025.8.3" "Pillow==11.3.0"

echo "[3/7] 编译检查"
"$PYTHON" -m py_compile v160_model.py v160_update_patch.py v161_model.py v161_update_patch.py v162_model.py v162_update_patch.py v163_model.py v163_update_patch.py v163_ui_patch.py test_v163_update.py test_v163_ui.py desktop.py

echo "[4/7] 运行回归测试"
"$PYTHON" -m unittest -v test_engine
"$PYTHON" -m unittest -v test_candles
"$PYTHON" -m unittest -v test_v160_update
"$PYTHON" -m unittest -v test_v161_update
"$PYTHON" -m unittest -v test_v162_update
"$PYTHON" -m unittest -v test_v163_update
"$PYTHON" -m unittest -v test_v163_ui

echo "[5/7] 生成 Intel Mac 应用"
rm -rf "${ROOT_DIR}/build" "${ROOT_DIR}/dist" "${ROOT_DIR}/installer"
rm -f "${ROOT_DIR}/${DMG_NAME}" "${ROOT_DIR}/SHA256.txt"
"$PYTHON" build_icon.py
MACOSX_DEPLOYMENT_TARGET=14.0 "$PYTHON" -m PyInstaller --noconfirm OKXLocal.spec
"$PYTHON" verify_bundle.py

echo "[6/7] 应用冒烟测试和签名验证"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kaytrade-v163.XXXXXX")"
"${ROOT_DIR}/dist/KAYTRADE.app/Contents/MacOS/KAYTRADE" --smoke-test "${TMP_DIR}/kaytrade-smoke.txt"
test -s "${TMP_DIR}/kaytrade-smoke.txt"
codesign --verify --deep --strict "${ROOT_DIR}/dist/KAYTRADE.app"

echo "[7/7] 制作 DMG"
mkdir -p "${ROOT_DIR}/installer"
ditto "${ROOT_DIR}/dist/KAYTRADE.app" "${ROOT_DIR}/installer/KAYTRADE.app"
cp "${ROOT_DIR}/RELEASE-1.6.3.md" "${ROOT_DIR}/installer/RELEASE-1.6.3.md"
cp "${TMP_DIR}/kaytrade-smoke.txt" "${ROOT_DIR}/installer/BUILD-TEST.txt"
ln -s /Applications "${ROOT_DIR}/installer/Applications"
hdiutil create -volname "KAYTRADE-${APP_VERSION}" -srcfolder "${ROOT_DIR}/installer" -ov -format UDZO "${ROOT_DIR}/${DMG_NAME}"
shasum -a 256 "${ROOT_DIR}/${DMG_NAME}" > "${ROOT_DIR}/SHA256.txt"

cleanup
trap - ERR
echo; echo "[完成] 已生成并验证："; echo "${ROOT_DIR}/${DMG_NAME}"; cat "${ROOT_DIR}/SHA256.txt"
open -R "${ROOT_DIR}/${DMG_NAME}"
osascript -e 'display notification "Intel Mac 安装包已生成" with title "KAYTRADE V1.6.3"' || true
pause_and_exit 0
