# bitssh

A Bitvise-style SSH and serial client for Windows and Linux. The UI is a local web app (React + xterm.js) hosted by a PyQt6 desktop shell; SSH, SFTP, port forwarding and serial I/O all live in a separate Python backend process.

> Version **0.1.0** (2026-10-31): the core features and the Windows build are complete and verified on real hardware. Linux has PyInstaller onedir plus x86_64 and arm64 deb builds; AppImage is not available yet. IME, real serial hardware and keyboard-interactive (OTP) authentication still need manual acceptance on target machines.

## Download

- **Installers (GitHub Releases only)**: `https://github.com/YangYangyou-Zhang/bitssh-dist/releases`
- **Baidu Netdisk (mirror)**: link `<TBD>`　code `<TBD>`
- **Source code**:
  - GitHub: `https://github.com/YangYangyou-Zhang/bitssh`
  - Gitee: `https://gitee.com/zhangyangyangyou/bitssh`

> GitHub/Gitee source and GitHub Releases links are final; the Baidu Netdisk link/code is still pending.

## Features

### Sessions and workspace

- SSH / serial profiles: create, edit, duplicate, delete.
- The workspace is a single JSON file (default: `<config dir>/profiles.bitssh.json`):
  - autosaves 0.8 s after changes using a temporary file + atomic replace, keeping one `.bak`;
  - content SHA-256 is checked before every write, so external edits pause autosave and offer reload / overwrite / save-as;
  - restores the last opened workspace on startup and silently falls back to the default file if it is missing or broken;
  - open, save-as, import/export profiles; exports never contain passwords or private keys.

### SSH

- Authentication: password, private key (PEM / OpenSSH / PuTTY `.ppk`), SSH agent, keyboard-interactive (with an OTP form).
- Host-key TOFU: the fingerprint is shown on first connect (trust once / trust always / reject); a changed key blocks the connection instead of being silently overwritten.
- Legacy device support: `diffie-hellman-group14-sha1`, `ssh-rsa` and similar algorithms can be enabled explicitly.
- Jump hosts: multi-hop `tunnel=` chains with per-hop authentication and host-key verification.
- Each session keeps the last 256 KB of terminal output and replays it when a new window attaches.

### Terminal

- xterm.js 6 with the DOM renderer by default (safer for CJK, wide characters and software rendering); WebGL is opt-in.
- Flow control via `onWriteParsed` with 256/64 KiB watermarks; output is paused while the window is hidden to avoid memory growth and deadlocks.
- Multiple tabs, background tabs keep rendering, and a session can be detached into its own window or moved back.
- Clipboard: `Ctrl+Shift+C` (or `Ctrl+Insert`) copies the selection, `Ctrl+Shift+V` pastes.
- Chromium background/occlusion throttling is disabled: output keeps refreshing while the window is unfocused or covered by another window. Reading pauses only when minimized, to bound memory.
- The terminal adds no refresh throttle of its own and displays output as it arrives (0.2 s-cadence output shows no lag in testing). If `top` seems slow, that is top's own default 3-second delay — use `top -d 1` or `htop`.

### SFTP

- Dual pane (local / remote) with four resizable sections.
- Upload and download single files or whole folders recursively; multi-select, rename, delete (remote folders are deleted recursively).
- Double-click a remote file for edit sync: download to a cache directory, open it with the default application, upload automatically when it changes, and prompt “overwrite remote / reload remote” if the remote copy changed meanwhile.
- Drag and drop:
  - drag files or folders from Explorer into the SFTP panel to upload them (multi-file supported);
  - drag remote rows to the local pane to download into the current local directory;
  - use the inline `⇱` handle or drag a remote row out of the panel to open a native drag-out list, from which files and folders can be dragged into Explorer together.
- All drag-in/drag-out transfers run in the background and never freeze the UI; progress is shown in the Transfers section below. The drag-out window appears immediately with a “downloading…” state and becomes draggable once the items are ready.
- Downloads ask before overwriting; one-shot download tickets allow header-free streaming.

### Port forwarding

- Local forwarding, remote forwarding and dynamic SOCKS5 (CONNECT only, no UDP ASSOCIATE).
- The forward panel shows state and lets you start/stop each rule; listeners bind to `127.0.0.1` by default.

### Serial

- Port enumeration and hot-plug, configurable baud rate / data bits / parity / stop bits.
- DTR / RTS / BREAK controls, hex view, timestamps, CRLF toggle and a send bar.

### UI

- Chinese and English, following the system locale or switched manually with immediate effect.
- Dark theme whose default palette is sampled from a VS Code green dark scheme; editable live from the Appearance page or by editing `<config dir>/theme.json`.
- Adjustable window opacity; on Windows 11 the native title bar follows the theme through DWM, while Linux leaves it to the window manager.
- Terminal tabs can be detached into separate windows and moved back.

### Security design

- The backend binds `127.0.0.1` on a random port only; the token arrives via the URL fragment, REST requests use `X-Bitssh-Token`, and WebSockets carry it in a subprotocol, so it never appears in query strings or logs.
- `Origin`/`Host` are validated, `/docs`, CORS and devtools are disabled, a CSP restricts resource origins, and external navigation is blocked.
- Credentials live in an AES-256-GCM `vault.bin` whose 32-byte master key is stored in the OS keyring; private keys are never sent to the renderer.
- Host-key changes are always blocked and require manual action.

## Architecture

```
┌─ Desktop shell (PyQt6 + QtWebEngine) ─┐        ┌─ Backend (Python 3.12) ───────────┐
│ windows / native dialogs / downloads  │ HTTP   │ FastAPI + uvicorn (loopback only) │
│ no asyncio in this process            │◄─────►│ asyncssh pool / SFTP / forwards   │
│                                       │  WS    │ pyserial serial thread            │
└───────────────────────────────────────┘        └───────────────────────────────────┘
```

- Two-process startup: the shell launches `bitssh --server`; the backend binds a random loopback port and atomically writes `{port, token}` to a temporary handshake file. The shell reads it, deletes it immediately and loads the page.
- Orphan prevention: `PR_SET_PDEATHSIG` on Linux, a kill-on-close Job Object on Windows, plus a parent-PID watchdog as a fallback. A normal shutdown goes through the internal shutdown endpoint.
- Frontend: React + zustand + react-i18next; terminals use xterm.js.
- Configuration directories (`platformdirs`):
  - Windows: `%LOCALAPPDATA%\bitssh`
  - Linux: `~/.config/bitssh` (logs and cache use their platform directories)
  - Main files: `profiles.bitssh.json`, `settings.json`, `theme.json`, `vault.bin`.

## Build and run (Windows)

### Requirements

- Windows 10/11 x64 (title-bar theming requires Windows 11)
- Python 3.12 x64 (enable “Add python.exe to PATH” during installation)
- Node.js 20 LTS
- Git (optional, for fetching the source)

```powershell
python --version
node --version
npm --version
```

### Install dependencies

From the repository root:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install asyncssh==2.24.1 "cryptography>=48.0.1,<51" fastapi "uvicorn[standard]>=0.30" "pydantic>=2" pyserial==3.5 "keyring>=25" "platformdirs>=4" "PyQt6==6.11.0" "PyQt6-WebEngine==6.11.0" "pyinstaller>=6.7"

cd frontend
npm install
```

> Do not rely on `pip install -e .` alone: the project does not configure setuptools package discovery, so editable installs can fail. Install the dependency list above and run the app from the source tree. If PyPI is slow, append `-i https://pypi.tuna.tsinghua.edu.cn/simple`.

### Run from source

```powershell
.venv\Scripts\python.exe desktop\shell.py
```

The first launch creates the default workspace and config under `%LOCALAPPDATA%\bitssh\`.

### Package

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

The script:

1. stops any running bitssh process first (releasing the file locks under `dist_win`);
2. builds the frontend by calling `node node_modules\typescript\bin\tsc` and `node node_modules\vite\bin\vite.js build` directly (if the project path contains `&`, `npm run build` breaks because the `node_modules\.bin` PATH entry is truncated);
3. runs PyInstaller into the onedir output **outside the source tree**: `..\bitssh-dist\win\bitssh\bitssh.exe`;
4. creates the distribution archive `..\bitssh-dist\win\bitssh-win-x64.zip` and `sha256sums.txt`;
5. repoints the desktop shortcut `bitssh.lnk` to the new build.

`..\bitssh-dist\win\bitssh\` is the extracted run directory (used locally, not tracked by git); the zip is the distributable artifact. Override the output root with `BITSSH_DIST_ROOT`.

The package does not include user configuration; first run still creates `profiles.bitssh.json`, `settings.json`, `theme.json` and `vault.bin` under `%LOCALAPPDATA%\bitssh`.

## Build and run (Linux)

### Requirements

- Ubuntu 22.04+ / Debian 12+ (PyQt6 6.11 wheels require glibc ≥ 2.34); for arm64 prefer Ubuntu 24.04+.
- Python 3.12 (Ubuntu 22.04 ships 3.10; install 3.12 with uv).
- Node.js 20+ (the version in Ubuntu 22.04's apt repository is too old).
- Runtime libraries and CJK fonts:

```bash
sudo apt update
sudo apt install -y python3 python3-venv libxcb-cursor0 libnss3 libasound2 libxkbcommon-x11-0 fonts-noto-cjk curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
```

### Install dependencies

```bash
cd <repo>
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python \
  asyncssh==2.24.1 "cryptography>=48.0.1,<51" fastapi "uvicorn[standard]>=0.30" \
  "pydantic>=2" pyserial==3.5 "keyring>=25" "platformdirs>=4" \
  "PyQt6==6.11.0" "PyQt6-WebEngine==6.11.0" "pyinstaller>=6.7"
(cd frontend && npm install)
```

> If PyPI is slow, add `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`.

### Run from source

Use X11/XWayland for the main UI (QtWebEngine can render incorrectly in native Wayland mode on some drivers):

```bash
export QT_QPA_PLATFORM=xcb
# On Wayland desktops this is also required for the native-Wayland drag helper:
export WAYLAND_DISPLAY=wayland-0
.venv/bin/python desktop/shell.py
```

A pure X11 session does not need `WAYLAND_DISPLAY`. On Wayland sessions the drag-out popup is handed to a separate native-Wayland helper process (`desktop/dragwin.py`), so drops into the desktop (DING) and Nautilus work without affecting main-window rendering.

### Package

```bash
bash packaging/build_linux.sh
```

The script builds the frontend when Node and `frontend/node_modules` are available (otherwise it reuses the existing `frontend/dist`), then runs PyInstaller into the fixed onedir output and calls `build_deb.sh`:

```text
../bitssh-dist/linux/<arch>/bitssh/bitssh             # extracted run directory (intermediate, not tracked)
../bitssh-dist/linux/<arch>/bitssh_<version>_<arch>.deb   # e.g. bitssh_0.1.0_amd64.deb
../bitssh-dist/linux/<arch>/bitssh-linux-<arch>.tar.gz    # portable bundle, symlinks preserved
../bitssh-dist/linux/<arch>/sha256sums.txt
```

`<arch>` is `x86_64` (x86) or `aarch64` (ARM). The version is **0.1.0** everywhere.

The output root defaults to `bitssh-dist/` next to the repo and can be overridden with `BITSSH_DIST_ROOT`. Run it directly (on Wayland desktops also use xcb and export `WAYLAND_DISPLAY` so the drag helper works):

```bash
QT_QPA_PLATFORM=xcb WAYLAND_DISPLAY=wayland-0 ../bitssh-dist/linux/x86_64/bitssh/bitssh
```

Or install the deb (the command becomes `bitssh`, and a desktop-menu entry is installed):

```bash
sudo apt install ./bitssh-dist/linux/x86_64/bitssh_0.1.0_amd64.deb
bitssh
```

- The deb architecture comes from the build machine's `dpkg --print-architecture`: x86_64 produces `amd64`, Jetson/arm64 produces `arm64`.
- **Ubuntu 20.04 / arm64 (Jetson, verified)**: artifacts live under `bitssh-dist/linux/aarch64/`. The system Python 3.9 works with:
  - `PyQt6==6.7.1` + `PyQt6-WebEngine==6.7.0` (manylinux_2_28, compatible with glibc 2.31);
  - `asyncssh==2.21.1` (2.24 requires Python >=3.10);
  - `eval_type_backport` (Pydantic on Python 3.9 needs it for `str | None`);
  - the system package `libpython3.9` for PyInstaller;
  - the source now includes a `featurePermissionRequested` compatibility branch (QtWebEngine 6.7 has no `permissionRequested`).
  Build with `bash packaging/build_linux.sh`; it produces `bitssh_<version>_arm64.deb` and `bitssh-linux-aarch64.tar.gz`.
- Verified on Ubuntu 22.04.5 (GNOME Wayland + XWayland) with the source tree, the onedir build and the x86_64 deb: startup, SSH, SFTP, terminal copy/paste, drops to the desktop and Nautilus, and the frozen `--dragwin` helper all work.
- AppImage is not available yet.
- Ubuntu 23.10+ restricts unprivileged user namespaces by default, which may stop QtWebEngine from starting. Since only local content is loaded, `QTWEBENGINE_DISABLE_SANDBOX=1` is an acceptable workaround there.
- Whether a same-name drop prompts to replace is decided by the target: Nautilus asks, while the GNOME desktop-icons extension (DING) may overwrite or skip silently.

## Testing

```bash
.venv\Scripts\python -m pytest tests
```

The `tests/` directory contains integration tests and spikes for backend lifecycle, workspace, terminal, SFTP, forwarding, jump hosts, edit sync and serial (prepare local SSH/serial fixtures as described in the scripts).

The desktop shell exposes development-only self-test hooks (environment variables):

| Variable | Effect |
| --- | --- |
| `BITSSH_DEBUG_UI=1` | Open the first profile, trust its host key and open the SFTP panel |
| `BITSSH_AUTOCLOSE=<seconds>` | Close the window after a delay for automation |
| `BITSSH_DEBUG_SFTP_ONLY=1` | Stop after opening SFTP |
| `BITSSH_DEBUG_DROPTEST=1` | Simulate an Explorer drop of two files plus one folder |
| `BITSSH_DEBUG_BRIDGETEST=1` | Simulate dragging a file + folder out into the drag-out list |
| `BITSSH_DEBUG_VIS=1` | Print page visibility every second (verify occlusion throttling is off) |
| `BITSSH_DEBUG_EVAL=<js>` | Evaluate a script in the page every second and print the result |

All test artifacts must stay inside the repository's `.test/` directory and be removed afterwards.

## Project layout

```
backend/            FastAPI backend, SSH/SFTP/forwarding/serial, workspace and vault services
desktop/shell.py    PyQt6 desktop shell (windows, bridge, drag and drop, native dialogs)
desktop/dragwin.py  Native-Wayland drag-out popup (Linux helper)
frontend/           React + xterm.js frontend (Vite)
packaging/          PyInstaller spec, Windows/Linux build scripts, deb packaging and icon
tests/              Backend and integration tests, debug scripts
.test/              Local test artifacts (not committed)

../bitssh-dist/     Build output (outside the repo by default: win/, linux/)
```

## Repository and artifacts

- **The source repository contains source only.** Build output defaults to `bitssh-dist/` next to the repository (`win/`, `linux/`) and can be redirected with `BITSSH_DIST_ROOT`; no dist directory lives inside the source tree.
- The build scripts produce the distributables automatically:
  - `bitssh-dist/win/bitssh-win-x64.zip` + `sha256sums.txt`
  - `bitssh-dist/linux/x86_64/bitssh_0.1.0_amd64.deb`, `bitssh-linux-x86_64.tar.gz` + `sha256sums.txt`
  - `bitssh-dist/linux/aarch64/bitssh_0.1.0_arm64.deb`, `bitssh-linux-aarch64.tar.gz` + `sha256sums.txt`
- Intermediates are ignored via `.gitignore`: `.venv/`, `node_modules/`, `build/`, `frontend/dist/`, logs and `.test/`; extracted run directories stay local as well.
- The distributables are large because QtWebEngine is bundled. For remote hosting use one of: GitHub/Gitee **Releases** assets (recommended), **Git LFS**, or a separate `bitssh-dist` release repository, keeping the source repo clean.

## Known limitations

- PyInstaller onedir packages are available for Windows and Linux (`build_windows.ps1` / `build_linux.sh`); Linux x86_64 and arm64 debs are available, while AppImage is not. Older ARM/Linux systems use the PyQt6 6.7 / asyncssh 2.21 compatibility set with the same features.
- Dynamic forwarding supports SOCKS5 CONNECT only, not UDP ASSOCIATE.
- Every jump host must currently be reachable directly from the client (jump hosts reachable only through the previous hop are not supported).
- Chinese IME, real serial hardware and keyboard-interactive/OTP authentication still need manual acceptance.
- Workspaces on network shares / UNC paths trigger a warning: mtime precision and locking semantics are unreliable, and concurrent editing is not supported.
- Native title-bar coloring is Windows 11 only; Linux follows the desktop environment.
- Test coverage is still strongest on Windows; on Linux, startup, SSH, SFTP, clipboard and drag-out have been verified from source and with the onedir build, while the rest still needs acceptance testing.

## Third-party components

asyncssh, cryptography, FastAPI/uvicorn/pydantic, pyserial, keyring, platformdirs, PyQt6/QtWebEngine, React, zustand, i18next, xterm.js. Each component is governed by its own license.
