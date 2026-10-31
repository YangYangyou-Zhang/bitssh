#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_dir="${BITSSH_DIST_DIR:-${root}/dist_linux}"
app_dir="${out_dir}/bitssh"
svg="${root}/packaging/bitssh.svg"
mkdir -p "${out_dir}"

if [ ! -x "${app_dir}/bitssh" ]; then
    echo "missing onedir build: ${app_dir}/bitssh (run build_linux.sh first)" >&2
    exit 1
fi

version="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "${root}/pyproject.toml" | head -1)"
version="${version:-0.1.0}"
arch="$(dpkg --print-architecture)"
pkg_name="bitssh_${version}_${arch}.deb"

stage="$(mktemp -d)"
trap 'rm -rf "${stage}"' EXIT

install -d "${stage}/DEBIAN" "${stage}/opt/bitssh" "${stage}/usr/bin" \
    "${stage}/usr/share/applications" "${stage}/usr/share/icons/hicolor/scalable/apps"
cp -a "${app_dir}/." "${stage}/opt/bitssh/"
install -m 644 "${svg}" "${stage}/usr/share/icons/hicolor/scalable/apps/bitssh.svg"

cat > "${stage}/DEBIAN/control" <<EOF
Package: bitssh
Version: ${version}
Section: net
Priority: optional
Architecture: ${arch}
Depends: libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-sync1, libxcb-xfixes0, libxcb-xinerama0, libxcb-xkb1, libxkbcommon-x11-0, libnss3, libasound2 | libasound2t64, libgl1, libgbm1, libxcomposite1, libxdamage1, libxrandr2, libxtst6, libxi6, libsm6, libice6
Recommends: fonts-noto-cjk
Maintainer: bitssh maintainers <bitssh@localhost>
Description: SSH and serial client with terminal, SFTP and port forwarding
 bitssh bundles a session tree, xterm.js terminal, dual-pane SFTP,
 local/remote/SOCKS port forwarding, jump hosts and serial debugging
 in a desktop application.
EOF

cat > "${stage}/usr/bin/bitssh" <<'SH'
#!/bin/sh
# Run the main UI through X11/XWayland for stable QtWebEngine rendering; the
# drag-out helper switches itself to native Wayland when available.
if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${QT_QPA_PLATFORM:-}" ]; then
    export QT_QPA_PLATFORM=xcb
fi
exec /opt/bitssh/bitssh "$@"
SH
chmod 755 "${stage}/usr/bin/bitssh"

cat > "${stage}/usr/share/applications/bitssh.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=bitssh
Comment=SSH and serial client with terminal, SFTP and port forwarding
Exec=bitssh
Icon=bitssh
Terminal=false
Categories=Network;RemoteAccess;
StartupWMClass=bitssh
DESK

dpkg-deb --build --root-owner-group "${stage}" "${out_dir}/${pkg_name}" >/dev/null
echo "== deb =="
dpkg-deb --info "${out_dir}/${pkg_name}" | sed -n '1,14p'
echo "package: ${out_dir}/${pkg_name}"
