# bitssh

类 Bitvise 的 SSH + 串口客户端，面向 Windows 与 Linux。主界面是一个本地 Web 应用（React + xterm.js），由 PyQt6 桌面壳托管；SSH、SFTP、端口转发与串口全部由独立的 Python 后端进程处理。

> 当前版本 **0.1.0**（2026-10-31）：M0–M5 的功能与 Windows 打包已完成并实机验证；Linux 已提供 PyInstaller onedir、x86_64 与 arm64 deb 包，AppImage 尚未提供；IME、真实串口与键盘交互认证（OTP）仍需在目标机器上人工验收。

## 下载

- **安装包（仅 GitHub Releases）**：`https://github.com/<GitHub用户名>/bitssh-dist/releases`
- **百度网盘（备用）**：链接：`<待补充>`　提取码：`<待补充>`
- **源码**：
  - GitHub：`https://github.com/<GitHub用户名>/bitssh`
  - Gitee：`https://gitee.com/<Gitee用户名>/bitssh`

> 上方 `<...>` 为占位符，上传仓库/网盘后替换为实际地址。

## 功能

### 会话与工作区

- 会话配置（SSH / 串口）列表，新建、编辑、复制、删除。
- 工作区就是一个 JSON 文件（默认 `<配置目录>/profiles.bitssh.json`）：
  - 修改后 0.8 秒自动保存，临时文件 + 原子替换，并保留 `.bak`；
  - 写盘前用内容 SHA-256 检测外部修改，冲突时暂停自动保存并让用户选择重载/覆盖/另存；
  - 启动时恢复上次打开的工作区，文件缺失或损坏时静默回落到默认文件；
  - 打开、另存为、导入/导出会话；导出默认不包含任何密码或私钥。

### SSH

- 认证方式：密码、私钥（PEM / OpenSSH / PuTTY `.ppk`）、SSH Agent、键盘交互（支持 OTP 表单）。
- 主机密钥 TOFU：首次连接展示指纹，可选仅本次/永久信任/拒绝；已记录密钥发生变化时直接阻止连接，不会自动覆盖。
- 兼容老设备：可显式启用 `diffie-hellman-group14-sha1`、`ssh-rsa` 等算法。
- 跳板机：多跳 `tunnel=` 链，每跳独立认证与主机密钥校验。
- 每个会话保留最近 256 KB 终端输出，切换/新开窗口时先补发再续传。

### 终端

- xterm.js 6，默认 DOM 渲染器（CJK、宽字符与软件渲染更稳），可选 WebGL 加速。
- `onWriteParsed` + 256/64 KiB 水位线做流控，后台/不可见时暂停读取，避免内存堆积与死锁。
- 多标签、标签常驻渲染（切回不空白）、会话可分离到独立窗口并移回主窗口。
- 终端剪贴板：`Ctrl+Shift+C`（或 `Ctrl+Insert`）复制选中文本，`Ctrl+Shift+V` 粘贴；右键/鼠标中键不额外绑定。
- 关闭了 Chromium 的后台/遮挡节流：窗口失焦或被其他窗口盖住时输出仍持续刷新；最小化时暂停读取以控制内存。
- 终端自身没有额外的刷新节流，输出实时上屏（实测 0.2 秒级输出无滞后）。如果觉得 `top` 刷新慢，那是 `top` 本身默认 3 秒间隔，可用 `top -d 1` 或 `htop`。

### SFTP

- 双栏：本机文件 / 远程文件，四个区块高度可拖动调整。
- 上传/下载单文件与整个文件夹（递归），多选、重命名、删除（远程目录递归删除）。
- 双击远程文件进入编辑同步：下载到缓存目录 → 系统默认程序打开 → 检测到保存后自动回传；远端同时被改时提示“覆盖远端 / 重新下载”。
- 拖拽：
  - 从资源管理器拖入文件或文件夹 → 上传到当前远程目录（支持多文件）；
  - 远程行拖到本机栏 → 下载到当前本地目录；
  - 通过行内 `⇱` 或把远程行拖出面板 → 弹出原生拖出列表，文件与文件夹可一起拖进资源管理器。
- 拖入/拖出传输全部在后台执行，不阻塞界面；进度显示在下方“传输”区。拖出窗口会立即出现并显示“下载中…”，下载完成后才允许拖拽。
- 下载冲突默认询问是否覆盖；下载票据用于免鉴权的一次性直链。

### 端口转发

- 本地转发、远程转发、动态 SOCKS5（仅 CONNECT，不支持 UDP ASSOCIATE）。
- 转发面板显示运行状态，可随时启停；监听地址默认 `127.0.0.1`。

### 串口

- 端口枚举与热插拔、波特率/数据位/校验/停止位配置。
- DTR / RTS / BREAK 控制，十六进制显示、时间戳、CRLF 开关与发送栏。

### 界面

- 中英双语，跟随系统或手动切换，即时生效。
- 深色主题：默认配色取自 VS Code 绿系深色方案，可在“外观”页面实时调整，也可直接编辑 `<配置目录>/theme.json`。
- 可调窗口透明度；Windows 11 下原生标题栏颜色跟随主题（DWM），Linux 由窗口管理器绘制。
- 终端标签可分离为独立窗口，也能移回主窗口。

### 安全设计

- 后端只监听 `127.0.0.1`，随机端口；token 通过 URL fragment 下发，REST 用 `X-Bitssh-Token` 头，WebSocket 用子协议传递，不出现在查询串或日志里。
- 校验 `Origin`/`Host`，关闭 `/docs`、CORS 与开发者工具，CSP 限制资源来源，禁止页面导航到外部地址。
- 凭据保存在 AES-256-GCM 加密的 `vault.bin`，主密钥（32 字节）存系统 keyring；私钥不会发送给渲染进程。
- 主机密钥变更一律阻断，需人工处理。

## 架构

```
┌─ 桌面壳 (PyQt6 + QtWebEngine) ─┐        ┌─ 后端 (Python 3.12) ──────────────┐
│ 窗口 / 原生对话框 / 下载 / 拖出  │ HTTP   │ FastAPI + uvicorn（仅回环）        │
│ 不运行 asyncio                  │◄─────►│ asyncssh 会话池 / SFTP / 转发      │
│                                 │  WS    │ pyserial 串口线程                  │
└─────────────────────────────────┘        └───────────────────────────────────┘
```

- 双进程启动：壳拉起 `bitssh --server`，后端绑定随机回环端口后把 `{port, token}` 原子写入临时握手文件，壳读取后立即删除并加载页面。
- 防孤儿：Linux 用 `PR_SET_PDEATHSIG`，Windows 用 kill-on-close 的 Job Object，另有父进程 PID 看门狗兜底；正常退出走内部 shutdown 接口。
- 前端：React + zustand + react-i18next；终端用 xterm.js。
- 配置目录（`platformdirs`）：
  - Windows：`%LOCALAPPDATA%\bitssh`
  - Linux：`~/.config/bitssh`（日志与缓存分别在对应目录）
  - 主要文件：`profiles.bitssh.json`、`settings.json`、`theme.json`、`vault.bin`。

## 构建与运行（Windows）

### 环境要求

- Windows 10/11 x64（标题栏跟随主题需要 Windows 11）
- Python 3.12 x64（安装时勾选 “Add python.exe to PATH”）
- Node.js 20 LTS
- Git（可选，用于获取源码）

```powershell
python --version
node --version
npm --version
```

### 安装依赖

在仓库根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install asyncssh==2.24.1 "cryptography>=48.0.1,<51" fastapi "uvicorn[standard]>=0.30" "pydantic>=2" pyserial==3.5 "keyring>=25" "platformdirs>=4" "PyQt6==6.11.0" "PyQt6-WebEngine==6.11.0" "pyinstaller>=6.7"

cd frontend
npm install
```

> 不要只依赖 `pip install -e .`：项目没有配置 setuptools 的包发现，editable 安装可能失败；按上面的依赖列表安装即可，程序从源码目录直接运行。PyPI 慢时可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

### 开发运行

```powershell
.venv\Scripts\python.exe desktop\shell.py
```

首次启动会在 `%LOCALAPPDATA%\bitssh\` 创建默认工作区与配置。

### 打包

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

脚本会：

1. 先结束正在运行的 bitssh（释放 `dist_win` 下 exe 的文件锁）；
2. 直接调用 `node node_modules\typescript\bin\tsc` 和 `node node_modules\vite\bin\vite.js build` 构建前端（项目路径含 `&` 时不能直接用 `npm run build`，PATH 会被截断）；
3. 用 PyInstaller 生成 onedir 产物到**仓库外**的 `..\bitssh-dist\win\bitssh\bitssh.exe`；
4. 生成分发压缩包 `..\bitssh-dist\win\bitssh-win-x64.zip` 与 `sha256sums.txt`；
5. 把桌面快捷方式 `bitssh.lnk` 指向新产物。

`..\bitssh-dist\win\bitssh\` 是解压后的运行目录（本地使用，不进 git），zip 才是分发/归档产物。可用环境变量 `BITSSH_DIST_ROOT` 覆盖输出根目录。

安装包不包含用户配置；首次运行仍在 `%LOCALAPPDATA%\bitssh` 下创建 `profiles.bitssh.json`、`settings.json`、`theme.json`、`vault.bin`。

## 构建与运行（Linux）

### 环境要求

- Ubuntu 22.04+ / Debian 12+（PyQt6 6.11 的 wheel 要求 glibc ≥ 2.34）；arm64 建议 Ubuntu 24.04+。
- Python 3.12（Ubuntu 22.04 默认是 3.10，用 uv 安装 3.12）。
- Node.js 20+（Ubuntu 22.04 apt 源里的版本过旧）。
- 运行库与中文字体：

```bash
sudo apt update
sudo apt install -y python3 python3-venv libxcb-cursor0 libnss3 libasound2 libxkbcommon-x11-0 fonts-noto-cjk curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12
```

### 安装依赖

```bash
cd <仓库目录>
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python \
  asyncssh==2.24.1 "cryptography>=48.0.1,<51" fastapi "uvicorn[standard]>=0.30" \
  "pydantic>=2" pyserial==3.5 "keyring>=25" "platformdirs>=4" \
  "PyQt6==6.11.0" "PyQt6-WebEngine==6.11.0" "pyinstaller>=6.7"
(cd frontend && npm install)
```

> PyPI 访问慢时可加 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`。

### 开发运行

主界面请使用 X11/XWayland（部分显卡/驱动下 QtWebEngine 在原生 Wayland 下会渲染异常）：

```bash
export QT_QPA_PLATFORM=xcb
# Wayland 桌面还需要这个，供拖出窗口的原生 Wayland helper 使用：
export WAYLAND_DISPLAY=wayland-0
.venv/bin/python desktop/shell.py
```

纯 X11 会话不需要 `WAYLAND_DISPLAY`。拖出小窗口在 Wayland 会话下会自动交给独立的原生 Wayland 子进程（`desktop/dragwin.py`）显示，因此可以拖入桌面（DING）和 Nautilus，同时不影响主界面渲染。

### 打包

```bash
bash packaging/build_linux.sh
```

脚本会：若存在 Node 与 `frontend/node_modules` 则先构建前端，否则复用现有 `frontend/dist`；然后用 PyInstaller 生成固定目录的 onedir 产物，并调用 `build_deb.sh` 生成 deb：

```text
../bitssh-dist/linux/<arch>/bitssh/bitssh             # 解压后的运行目录（中间产物，不进 git）
../bitssh-dist/linux/<arch>/bitssh_<版本>_<架构>.deb   # 例如 bitssh_0.1.0_amd64.deb
../bitssh-dist/linux/<arch>/bitssh-linux-<arch>.tar.gz # 免安装包，保留软链接
../bitssh-dist/linux/<arch>/sha256sums.txt
```

其中 `<arch>` 为 `x86_64`（x86）或 `aarch64`（ARM）。当前版本统一为 **0.1.0**。

输出根目录默认是仓库同级的 `bitssh-dist/`，可用 `BITSSH_DIST_ROOT` 覆盖。直接运行（Wayland 桌面同样建议 xcb，并导出 `WAYLAND_DISPLAY` 以启用拖出 helper）：

```bash
QT_QPA_PLATFORM=xcb WAYLAND_DISPLAY=wayland-0 ../bitssh-dist/linux/x86_64/bitssh/bitssh
```

或安装 deb（安装后命令为 `bitssh`，桌面菜单中也会出现入口）：

```bash
sudo apt install ./bitssh-dist/linux/x86_64/bitssh_0.1.0_amd64.deb
bitssh
```

- deb 的架构由构建机的 `dpkg --print-architecture` 决定：x86_64 生成 `amd64`，Jetson/arm64 生成 `arm64`。
- **Ubuntu 20.04 / arm64（Jetson，已实测）**：产物位于 `bitssh-dist/linux/aarch64/`。系统自带的 Python 3.9 即可，需要：
  - `PyQt6==6.7.1` + `PyQt6-WebEngine==6.7.0`（manylinux_2_28，glibc 2.31 可用）；
  - `asyncssh==2.21.1`（2.24 起要求 Python ≥3.10）；
  - `eval_type_backport`（Python 3.9 下 Pydantic 解析 `str | None` 需要）；
  - 安装系统包 `libpython3.9` 供 PyInstaller 打包；
  - 源码已自带 `featurePermissionRequested` 兼容分支（QtWebEngine 6.7 没有 `permissionRequested`）。
  构建仍用 `bash packaging/build_linux.sh`，产出 `bitssh_<版本>_arm64.deb` 和 `bitssh-linux-aarch64.tar.gz`。
- 已在 Ubuntu 22.04.5（GNOME Wayland + XWayland）上验证源码、onedir 产物与 x86_64 deb：启动、SSH、SFTP、终端复制/粘贴、拖出到桌面与 Nautilus、frozen 下的 `--dragwin` helper 均正常。
- AppImage 尚未提供。
- Ubuntu 23.10+ 若限制非特权 user namespace，可设置 `QTWEBENGINE_DISABLE_SANDBOX=1`（只加载本机页面）。
- 拖放同名文件时是否提示“替换”由目标决定：Nautilus 会询问，GNOME 桌面图标扩展（DING）可能静默覆盖或跳过。

## 测试

```bash
.venv\Scripts\python -m pytest tests
```

仓库内的 `tests/` 包含后端生命周期、工作区、终端、SFTP、转发、跳板、编辑同步与串口等集成测试（需要本地 SSH/串口测试环境时按脚本内说明准备）。

桌面壳提供若干开发用自测钩子（环境变量）：

| 变量 | 作用 |
| --- | --- |
| `BITSSH_DEBUG_UI=1` | 自动打开第一个会话、信任主机密钥并点开 SFTP 面板 |
| `BITSSH_AUTOCLOSE=<秒>` | 定时关闭窗口，便于自动化 |
| `BITSSH_DEBUG_SFTP_ONLY=1` | 只打开 SFTP，不继续后续步骤 |
| `BITSSH_DEBUG_DROPTEST=1` | 模拟资源管理器一次拖入两个文件加一个文件夹 |
| `BITSSH_DEBUG_BRIDGETEST=1` | 模拟拖出文件 + 文件夹并弹出拖出列表 |
| `BITSSH_DEBUG_VIS=1` | 每秒打印页面可见性（验证遮挡/后台不被节流） |
| `BITSSH_DEBUG_EVAL=<js>` | 每秒在页面执行一段脚本并打印结果（通用调试） |

测试产生的一切文件必须放在仓库内的 `.test/` 目录，测试结束后删除。

## 目录结构

```
backend/            FastAPI 后端、SSH/SFTP/转发/串口、工作区与凭据服务
desktop/shell.py    PyQt6 桌面壳（窗口、桥、拖放、原生对话框）
desktop/dragwin.py  Wayland 原生拖出小窗口（Linux helper）
frontend/           React + xterm.js 前端（Vite 构建）
packaging/          PyInstaller spec、Windows/Linux 构建脚本、deb 打包与图标
tests/              后端与集成测试、调试脚本
.test/              本地测试产物（不提交）

../bitssh-dist/     构建产物（默认在仓库外：win/、linux/，不提交）
```

## 仓库与产物

- **源码仓库只放源码**。构建产物默认输出到仓库同级的 `bitssh-dist/`（`win/`、`linux/`），可用 `BITSSH_DIST_ROOT` 改到任意位置；源码目录不再包含 dist 目录。
- 构建脚本会自动生成可分发文件：
  - `bitssh-dist/win/bitssh-win-x64.zip` + `sha256sums.txt`
  - `bitssh-dist/linux/x86_64/bitssh_0.1.0_amd64.deb`、`bitssh-linux-x86_64.tar.gz` + `sha256sums.txt`
  - `bitssh-dist/linux/aarch64/bitssh_0.1.0_arm64.deb`、`bitssh-linux-aarch64.tar.gz` + `sha256sums.txt`
- `.venv/`、`node_modules/`、`build/`、`frontend/dist/`、日志与 `.test/` 等中间产物都在 `.gitignore` 中；解压后的运行目录也只保留在本地。
- 分发产物体积较大（内置 QtWebEngine）。远端托管建议三选一：GitHub/Gitee **Releases** 附件（推荐）、**Git LFS**、或把 `bitssh-dist` 单独做成一个发布仓库，源码仓库保持干净。

## 已知限制

- Windows 与 Linux 均提供 PyInstaller onedir 包（`build_windows.ps1` / `build_linux.sh`）；Linux x86_64 与 arm64 deb 均已提供，AppImage 尚未提供。ARM/Linux 老系统使用 PyQt6 6.7/asyncssh 2.21 兼容组合，功能一致。
- 动态转发仅支持 SOCKS5 CONNECT，不支持 UDP ASSOCIATE。
- 跳板机目前要求每一跳都能由客户端直接访问（不支持仅能经上一跳到达的跳板）。
- 中文输入法、真实串口硬件、键盘交互认证/OTP 需要人工验收。
- 工作区放在网络盘/UNC 路径时会提示：mtime 精度与锁语义不可靠，不建议多人同时编辑。
- 原生标题栏着色仅在 Windows 11 可用；Linux 跟随桌面环境。
- 测试覆盖仍以 Windows 为主；Linux 侧已用源码与 onedir 产物验证启动、SSH、SFTP、剪贴板和拖出流程，其余功能仍需继续验收。

## 第三方组件

asyncssh、cryptography、FastAPI/uvicorn/pydantic、pyserial、keyring、platformdirs、PyQt6/QtWebEngine、React、zustand、i18next、xterm.js。各组件的许可证以各自项目为准。
