# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

import PyQt6
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

datas = []
binaries = []
hiddenimports = []

for pkg in ("asyncssh", "uvicorn", "backend"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("uvicorn.protocols")
hiddenimports.append("dragwin")

frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    datas.append((str(frontend_dist), "frontend_dist"))

qt6 = Path(PyQt6.__file__).resolve().parent / "Qt6"
for sub, dest in (
    ("resources", "PyQt6/Qt6/resources"),
    ("translations/qtwebengine_locales", "PyQt6/Qt6/translations/qtwebengine_locales"),
):
    path = qt6 / sub
    if path.is_dir():
        datas.append((str(path), dest))

for process_name in ("QtWebEngineProcess.exe", "QtWebEngineProcess"):
    process = qt6 / "bin" / process_name
    if process.is_file():
        binaries.append((str(process), "PyQt6/Qt6/bin"))
        break

a = Analysis(
    [str(ROOT / "desktop" / "shell.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bitssh",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="bitssh",
)
