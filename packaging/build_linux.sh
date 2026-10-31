#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
py="${root}/.venv/bin/python"
dist_root="${BITSSH_DIST_ROOT:-$(cd "${root}/.." && pwd)/bitssh-dist}"
dist="${dist_root}/linux/$(uname -m)"
mkdir -p "${dist}"

echo "== frontend build =="
if command -v node >/dev/null 2>&1 && [ -d "${root}/frontend/node_modules" ]; then
    cd "${root}/frontend"
    node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json
    node node_modules/vite/bin/vite.js build
    cd "${root}"
else
    echo "node/npm or frontend/node_modules missing; using existing frontend/dist" >&2
    test -f "${root}/frontend/dist/index.html" || {
        echo "frontend/dist is missing; install Node.js 20+ and run npm install in frontend/" >&2
        exit 1
    }
fi

if [ ! -x "${py}" ]; then
    echo "missing venv: ${py} (create it with uv, see README)" >&2
    exit 1
fi

if [ -e "${dist}" ]; then
    if ! rm -rf "${dist}" 2>/dev/null; then
        echo "cannot remove ${dist}; stopping running bitssh processes" >&2
        pkill -f "${dist}/bitssh" 2>/dev/null || true
        sleep 1
        rm -rf "${dist}"
    fi
fi

echo "== pyinstaller -> ${dist} =="
"${py}" -m PyInstaller --noconfirm --clean \
    --distpath "${dist}" \
    --workpath "${root}/build" \
    "${root}/packaging/bitssh.spec"

exe="${dist}/bitssh/bitssh"
if [ ! -x "${exe}" ]; then
    echo "missing output: ${exe}" >&2
    exit 1
fi

echo "== deb package =="
BITSSH_DIST_DIR="${dist}" bash "${root}/packaging/build_deb.sh"

echo "== distribution tarball =="
tar_name="bitssh-linux-$(uname -m).tar.gz"
tar -C "${dist}" -czf "${dist}/${tar_name}" bitssh
(
    cd "${dist}"
    sha256sum ./*.deb ./*.tar.gz > sha256sums.txt
)

echo "== done =="
echo "binary : ${exe}"
echo "deb    : ${dist}/$(ls "${dist}" | grep -E '\.deb$' | head -1)"
echo "tarball: ${dist}/${tar_name}"
echo "run    : QT_QPA_PLATFORM=xcb ${exe}"
