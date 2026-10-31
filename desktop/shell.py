from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote

from PyQt6.QtCore import (
    QEvent,
    QFile,
    QIODevice,
    QMimeData,
    QObject,
    QPoint,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QCursor, QDrag
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
HANDSHAKE_TIMEOUT = 20.0


class WinJob:
    """Kill-on-close job object so the backend dies with the shell."""

    def __init__(self) -> None:
        self._handle = None
        if sys.platform != "win32":
            return
        try:
            self._handle = self._create()
        except Exception:
            self._handle = None

    def _create(self):
        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        return job

    def assign(self, proc: subprocess.Popen) -> None:
        if sys.platform != "win32" or not self._handle:
            return
        k32 = ctypes.windll.kernel32
        try:
            k32.AssignProcessToJobObject(self._handle, int(proc._handle))  # type: ignore[attr-defined]
        except Exception:
            pass


def remove_temp_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


_drag_temp_roots: set[Path] = set()


def track_drag_root(path: Path | None) -> None:
    if path is not None:
        _drag_temp_roots.add(path)


def cleanup_drag_roots() -> None:
    """Drop temp trees when the popup closes or the app exits."""
    for root in list(_drag_temp_roots):
        remove_temp_path(root)
    _drag_temp_roots.clear()


class DragOutWindow(QListWidget):
    """Native Qt list whose items can be dragged into Explorer.

    A drag must originate from a real Qt widget: QtWebEngine's own drag cannot
    be handed over to the OS reliably. The window appears immediately in a
    "preparing" state while a worker thread downloads the items, so large
    transfers never freeze the UI; progress shows in the SFTP transfer list.
    """

    def __init__(self, items: list[dict], root: Path | None = None) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("bitssh drag-out · downloading…")
        self.setDragEnabled(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for entry in items:
            name = str(entry.get("name") or "")
            suffix = "/" if entry.get("dir") else ""
            row = QListWidgetItem(f"{name}{suffix}  ·  下载中…")
            self.addItem(row)
        self.resize(320, max(60, 28 * len(items) + 20))
        target = QCursor.pos()
        if sys.platform != "win32":
            screen = QApplication.primaryScreen()
            if screen is not None:
                target = screen.availableGeometry().center() - self.rect().center()
        self.move(target)
        self.show()
        self._paths: list[Path] = []
        self._root = root

    def mark_ready(self, paths: list[Path]) -> None:
        self.clear()
        self._paths = paths
        for path in paths:
            item = QListWidgetItem(path.name + ("/" if path.is_dir() else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.addItem(item)
        self.setWindowTitle("bitssh drag-out")
        self.setDragEnabled(True)
        self.resize(320, max(60, 28 * len(paths) + 20))

    def mimeData(self, items) -> QMimeData:  # noqa: ANN001
        urls = [
            QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))
            for item in items
            if item.data(Qt.ItemDataRole.UserRole)
        ]
        mime = QMimeData()
        mime.setUrls(urls)
        if urls:
            # Nautilus and friends prefer this for copy-style drops.
            payload = "copy\n" + "\n".join(url.toString() for url in urls)
            mime.setData("x-special/gnome-copied-files", payload.encode("utf-8"))
        return mime

    def startDrag(self, supported_actions) -> None:  # noqa: ANN001
        # QAbstractItemView's default drag path reads MIME data from the
        # internal model and skips our mimeData() override (so the GNOME copy
        # MIME would be lost and Nautilus would accept the drop without
        # copying). Drive the drag explicitly instead.
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        mime = self.mimeData(items)
        drag.setMimeData(mime)
        # Mutter (GNOME Wayland) asserts in its drag-feedback code when a drag
        # has no icon surface, which can leave the desktop frozen after a drop.
        row = self.visualItemRect(items[0])
        if row.isValid():
            pixmap = self.viewport().grab(row)
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(12, max(4, pixmap.height() // 2)))
        drag.exec(Qt.DropAction.CopyAction)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        remove_temp_path(self._root)
        _drag_temp_roots.discard(self._root)
        super().closeEvent(event)


def apply_titlebar_colors(hwnd: int, bg: str, fg: str, border: str) -> None:
    """Paint the native caption/border on Windows 11 via DWM.

    Qt draws a system title bar; its colour follows the OS, which looks wrong
    next to the themed web content. Attributes 34/35/36 (border/caption/text)
    need Windows 11 22000+; older systems just ignore the call.
    """
    if sys.platform != "win32":
        return

    def colorref(value: str) -> int:
        r, g, b = (int(value[i : i + 2], 16) for i in (1, 3, 5))
        return r | (g << 8) | (b << 16)

    try:
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), 20, ctypes.byref(dark), ctypes.sizeof(dark)
        )
        for attribute, value in ((35, bg), (36, fg), (34, border)):
            colour = ctypes.c_int(colorref(value))
            dwm.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), attribute, ctypes.byref(colour), ctypes.sizeof(colour)
            )
    except Exception:
        pass


class DragBridge(QObject):
    """JS -> Python channel used to start a native drag without navigating."""

    def __init__(self, main: "MainWindow", win) -> None:  # noqa: ANN001
        super().__init__(win)
        self._main = main
        self._win = win
        self._windows: list[DragOutWindow] = []

    @pyqtSlot(str)
    def setOpacity(self, value: str) -> None:
        try:
            opacity = float(value)
        except ValueError:
            return
        opacity = max(0.2, min(1.0, opacity))
        for window in [self._win, *getattr(self._main, "_windows", [])]:
            try:
                window.setWindowOpacity(opacity)
            except Exception:
                pass

    @pyqtSlot(str)
    def setClipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    @pyqtSlot(result=str)
    def getClipboard(self) -> str:
        return QApplication.clipboard().text()

    @pyqtSlot(str, str, str)
    def setTitlebar(self, bg: str, fg: str, border: str) -> None:
        if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in (bg, fg, border)):
            return
        for window in [self._win, *getattr(self._main, "_windows", [])]:
            try:
                apply_titlebar_colors(int(window.winId()), bg, fg, border)
            except Exception:
                pass

    @pyqtSlot(str)
    def setContext(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        self._main.drop_context = (
            str(data.get("session", "")),
            str(data.get("remote", "/")),
        )

    @pyqtSlot(str)
    def dragOut(self, payload: str) -> None:
        self.showDragOut(payload)

    @pyqtSlot(str)
    def showDragOut(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        files = data.get("files") or []
        session_id = str(data.get("session", ""))
        if not files or not session_id:
            return
        root = Path(tempfile.mkdtemp(prefix="bitssh-drag-"))
        track_drag_root(root)
        window = DragOutWindow(files, root)
        self._windows.append(window)
        window.destroyed.connect(
            lambda: self._windows.remove(window) if window in self._windows else None
        )
        threading.Thread(
            target=self._prepare_drag_out,
            args=(window, session_id, files, root),
            daemon=True,
        ).start()

    def _prepare_drag_out(self, window, session_id: str, files: list[dict], root: Path) -> None:
        try:
            paths = self._main.download_to_temp(session_id, files, root)
        except Exception:
            paths = []
        self._main.drag_prepared.emit((window, root, paths))


def install_webchannel(page: QWebEnginePage, main: "MainWindow", win) -> None:  # noqa: ANN001
    channel = QWebChannel(page)
    channel.registerObject("bridge", DragBridge(main, win))
    page.setWebChannel(channel)
    source = QFile(":/qtwebchannel/qwebchannel.js")
    if source.open(QIODevice.OpenModeFlag.ReadOnly):
        code = bytes(source.readAll()).decode("utf-8")
        source.close()
        script = QWebEngineScript()
        script.setName("bitssh-qwebchannel")
        script.setSourceCode(code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        page.scripts().insert(script)
    # Keep a reference so the channel object is not garbage-collected.
    page._bitssh_channel = channel  # type: ignore[attr-defined]


class FileDropFilter(QObject):
    """Intercept OS file drops before Chromium flattens them.

    QtWebEngine only exposes one item to the page for a multi-file drop, so the
    shell reads the real local paths and uploads them itself. Installed on the
    QApplication rather than the WebView's focus proxy: QtWebEngine swaps that
    proxy out during navigation, which silently dropped the filter and let the
    page upload just the first file.
    """

    def __init__(self, main: "MainWindow", target: QObject) -> None:
        super().__init__(target)
        self._main = main

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            mime = event.mimeData()
            if mime is not None and mime.hasUrls() and any(
                url.isLocalFile() for url in mime.urls()
            ):
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                return True
        elif kind == QEvent.Type.Drop:
            mime = event.mimeData()
            if mime is not None and mime.hasUrls():
                paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
                if paths:
                    event.setDropAction(Qt.DropAction.CopyAction)
                    event.accept()
                    self._main.upload_paths(paths)
                    return True
        return super().eventFilter(obj, event)


class LocalPage(QWebEnginePage):
    dialogRequested = pyqtSignal(str)

    def __init__(self, profile: QWebEngineProfile, parent, base: str) -> None:
        super().__init__(profile, parent)
        self._base = base
        self.new_window_factory = None

    def createWindow(self, _type):  # noqa: ANN001
        if self.new_window_factory is None:
            return None
        return self.new_window_factory()

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:
        target = url.toString()
        if target.startswith("about:"):
            return True
        if not target.startswith(self._base):
            return False
        # Minimal native bridge: the frontend navigates to these paths and we
        # cancel the navigation after handling it (native dialog / new window).
        if url.path() in (
            "/dialog/open",
            "/dialog/save",
            "/dialog/redock",
            "/dialog/export",
            "/dialog/pick-upload-folder",
            "/dialog/pick-upload-files",
        ):
            self.dialogRequested.emit(target)
            return False
        return True


def grant_local_permission(permission, base: str) -> None:
    try:
        if permission.isValid() and permission.origin().toString().startswith(base):
            permission.grant()
        else:
            permission.deny()
    except Exception:
        pass


def grant_local_feature(page, origin, feature, base: str) -> None:
    try:
        policy = (
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            if origin.toString().startswith(base)
            else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )
        page.setFeaturePermission(origin, feature, policy)
    except Exception:
        pass


def attach_local_permissions(page, base: str) -> None:
    """Qt 6.8+ uses permissionRequested; older QtWebEngine (e.g. 6.7 on the
    Jetson/Ubuntu 20.04 combo) only has featurePermissionRequested."""
    if hasattr(page, "permissionRequested"):
        page.permissionRequested.connect(lambda p: grant_local_permission(p, base))
    else:
        page.featurePermissionRequested.connect(
            lambda origin, feature: grant_local_feature(page, origin, feature, base)
        )


class MainWindow(QMainWindow):
    drag_prepared = pyqtSignal(object)
    sftp_refresh_requested = pyqtSignal()

    def __init__(self, base: str, token: str, proc: subprocess.Popen) -> None:
        super().__init__()
        self.base = base
        self.token = token
        self.proc = proc
        self.closing = False
        self._windows: list = []
        self._drag_helpers: list[dict] = []
        self.drop_context: tuple[str, str] | None = None
        self._drop_filters: list = []

        self.setWindowTitle("bitssh")
        self.setGeometry(40, 40, 1100, 700)

        from backend.config import cache_dir

        storage = cache_dir() / "webengine"
        storage.mkdir(parents=True, exist_ok=True)
        self.profile = QWebEngineProfile("bitssh", self)
        self.profile.setPersistentStoragePath(str(storage))
        self.profile.setCachePath(str(storage / "cache"))
        self.profile.downloadRequested.connect(self.on_download)

        page = LocalPage(self.profile, self, base)
        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        attach_local_permissions(page, base)
        page.dialogRequested.connect(lambda target: self.on_dialog(target, self))
        page.new_window_factory = self.open_attach_page
        install_webchannel(page, self, self)

        self.view = QWebEngineView(self)
        self.view.setPage(page)
        self.setCentralWidget(self.view)

        app = QApplication.instance()
        drop_filter = FileDropFilter(self, app if app is not None else self.view)
        if app is not None:
            app.installEventFilter(drop_filter)
            app.aboutToQuit.connect(cleanup_drag_roots)
            app.aboutToQuit.connect(self._cleanup_drag_helpers)
        else:
            self.view.installEventFilter(drop_filter)
        self._drop_filters.append(drop_filter)
        self.drag_prepared.connect(self._on_drag_prepared)
        self.sftp_refresh_requested.connect(self._refresh_sftp)

        if os.environ.get("BITSSH_DEBUG_VIS"):
            self._vis_timer = QTimer(self)
            self._vis_timer.timeout.connect(self._log_visibility)
            self._vis_timer.start(1000)
        if os.environ.get("BITSSH_DEBUG_EVAL"):
            self._eval_script = os.environ["BITSSH_DEBUG_EVAL"]
            self._eval_timer = QTimer(self)
            self._eval_timer.timeout.connect(self._run_debug_eval)
            self._eval_timer.start(1000)
        if os.environ.get("BITSSH_DEBUG_DOM"):
            self.view.loadFinished.connect(lambda ok: QTimer.singleShot(2500, self.dump_dom))
        if os.environ.get("BITSSH_DEBUG_UI"):
            self.view.loadFinished.connect(lambda ok: QTimer.singleShot(2500, self.debug_open))
        self.view.setUrl(QUrl(f"{base}/#token={token}"))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_proc)
        self.timer.start(500)

        auto_close = os.environ.get("BITSSH_AUTOCLOSE")
        if auto_close:
            QTimer.singleShot(int(float(auto_close) * 1000), self.close)

    def on_dialog(self, target: str, win) -> None:  # noqa: ANN001
        url = QUrl(target)
        path = url.path()
        if path == "/dialog/redock":
            self.redock(url.query(), win)
            return
        if path == "/dialog/pick-upload-folder":
            self.upload_folder_dialog(url.query(), win)
            return
        if path == "/dialog/pick-upload-files":
            self.upload_files_dialog(url.query(), win)
            return
        if path == "/dialog/export":
            self.export_dialog(url.query(), win)
            return
        if path == "/dialog/open":
            chosen, _ = QFileDialog.getOpenFileName(
                win,
                "Open workspace",
                "",
                "bitssh workspace (*.bitssh.json *.json);;All files (*)",
            )
            if chosen:
                self._post_json("/api/workspace/open", {"path": chosen})
        else:
            chosen, _ = QFileDialog.getSaveFileName(
                win,
                "Save workspace as",
                "profiles.bitssh.json",
                "bitssh workspace (*.bitssh.json);;All files (*)",
            )
            if chosen:
                self._post_json("/api/workspace/save", {"path": chosen, "force": True})
        win.view.page().runJavaScript(
            "window.__store && window.__store.getState().refresh()"
        )

    def redock(self, query: str, win) -> None:  # noqa: ANN001
        params = {key: values[0] for key, values in parse_qs(query).items()}
        session_id = params.get("session", "")
        if not session_id:
            return
        tab = {
            "sessionId": session_id,
            "profileId": params.get("profile_id", ""),
            "title": params.get("profile", "bitssh"),
            "protocol": params.get("protocol", "ssh"),
        }
        script = (
            "window.__store && window.__store.getState().addAttached("
            + json.dumps(tab)
            + ")"
        )
        self.view.page().runJavaScript(script)
        win.keep_session = True
        win.close()
        self.show()
        self.raise_()
        self.activateWindow()

    def upload_folder_dialog(self, query: str, win) -> None:  # noqa: ANN001
        params = {key: values[0] for key, values in parse_qs(query).items()}
        session_id = params.get("session", "")
        remote_dir = params.get("remote", "/")
        folder = QFileDialog.getExistingDirectory(win, "Upload folder")
        if not folder or not session_id:
            return
        # Keep the folder name on the remote side instead of flattening it.
        remote_dir = remote_dir.rstrip("/") + "/" + Path(folder).name
        payload = json.dumps({"local_path": folder, "remote_dir": remote_dir}).encode()
        req = urllib.request.Request(
            f"{self.base}/api/sftp/{session_id}/upload-local",
            data=payload,
            method="POST",
            headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as exc:
            QMessageBox.warning(win, "bitssh", f"upload failed: {exc}")
            return
        win.view.page().runJavaScript("window.__sftpRefresh && window.__sftpRefresh()")

    def upload_files_dialog(self, query: str, win) -> None:  # noqa: ANN001
        params = {key: values[0] for key, values in parse_qs(query).items()}
        session_id = params.get("session", "")
        remote = params.get("remote", "/").rstrip("/")
        if not session_id:
            return
        files, _ = QFileDialog.getOpenFileNames(win, "Upload files")
        if not files:
            return
        for raw in files:
            path = Path(raw)
            target = f"{remote}/{path.name}" if remote else f"/{path.name}"
            payload = json.dumps({"local_path": str(path), "path": target}).encode()
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload",
                data=payload,
                method="POST",
                headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as exc:
                QMessageBox.warning(win, "bitssh", f"upload failed: {exc}")
                return
        win.view.page().runJavaScript("window.__sftpRefresh && window.__sftpRefresh()")

    def export_dialog(self, query: str, win) -> None:  # noqa: ANN001
        params = {key: values[0] for key, values in parse_qs(query).items()}
        session_id = params.get("session", "")
        try:
            items = json.loads(params.get("items", "[]"))
        except json.JSONDecodeError:
            items = []
        if not session_id or not items:
            return
        folder = QFileDialog.getExistingDirectory(win, "Export to folder")
        if not folder:
            return
        base = Path(folder)

        def api(method: str, path: str, payload=None) -> dict:
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                f"{self.base}{path}",
                data=data,
                method=method,
                headers={
                    "X-Bitssh-Token": self.token,
                    "Content-Type": "application/json",
                },
            )
            body = urllib.request.urlopen(req, timeout=10).read()
            return json.loads(body) if body else {}

        targets: list[str] = []
        for item in items:
            name = Path(str(item.get("name", ""))).name or "download"
            target = base / name
            try:
                if item.get("dir"):
                    job = api("POST", f"/api/sftp/{session_id}/download-tree",
                              {"path": item["path"], "local_dir": str(target),
                               "overwrite": True})
                else:
                    job = api("POST", f"/api/sftp/{session_id}/download",
                              {"path": item["path"], "local_path": str(target),
                               "overwrite": True})
                transfer_id = job.get("id", "")
                deadline = time.time() + 180
                while transfer_id and time.time() < deadline:
                    items_now = api("GET", "/api/transfers")
                    current = next(
                        (x for x in items_now if x["id"] == transfer_id), None)
                    if current and current["status"] != "running":
                        if current["status"] != "done":
                            raise RuntimeError(current.get("error") or current["status"])
                        break
                    time.sleep(0.3)
                targets.append(str(target))
            except Exception as exc:
                QMessageBox.warning(win, "bitssh", f"export failed: {exc}")
                return
        try:
            if len(targets) == 1:
                subprocess.Popen(["explorer", "/select,", targets[0]])
            else:
                subprocess.Popen(["explorer", str(base)])
        except Exception:
            pass

    def _api_json(self, path: str, payload: dict | None = None, timeout: float = 30) -> object:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method="POST" if payload is not None else "GET",
            headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    def _wait_transfer(self, transfer_id: str, deadline_s: float) -> None:
        deadline = time.time() + deadline_s
        while transfer_id and time.time() < deadline:
            items = self._api_json("/api/transfers")
            current = next(
                (x for x in items if x.get("id") == transfer_id), None)  # type: ignore[union-attr]
            if current and current.get("status") != "running":
                if current.get("status") != "done":
                    raise RuntimeError(current.get("error") or current.get("status"))
                return
            time.sleep(0.4)

    def download_to_temp(
        self, session_id: str, files: list[dict], root: Path
    ) -> list[Path]:
        """Fetch files and folders into a temp directory for drag-out.

        Runs on a worker thread. Every item goes through the transfer manager,
        so the SFTP transfer list shows live progress while the UI stays free.
        """
        paths: list[Path] = []
        for item in files:
            remote_path = str(item.get("path", ""))
            name = Path(str(item.get("name") or remote_path.rsplit("/", 1)[-1])).name
            if not remote_path or not name or name in (".", ".."):
                continue
            local = root / name
            try:
                if item.get("dir"):
                    job = self._api_json(
                        f"/api/sftp/{session_id}/download-tree",
                        {"path": remote_path, "local_dir": str(local), "overwrite": True},
                    )
                else:
                    job = self._api_json(
                        f"/api/sftp/{session_id}/download",
                        {"path": remote_path, "local_path": str(local), "overwrite": True},
                    )
                self._wait_transfer(str(job.get("id", "")), 3600)  # type: ignore[union-attr]
                paths.append(local)
            except Exception:
                remove_temp_path(local)
        return paths

    def open_attach_page(self):
        window = AttachWindow(self, self.base, self.token)
        self._windows.append(window)
        window.destroyed.connect(
            lambda: self._windows.remove(window) if window in self._windows else None
        )
        window.show()
        return window.view.page()

    def _post_json(self, path: str, payload: dict) -> None:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:
            QMessageBox.warning(self, "bitssh", str(exc))

    def upload_paths(self, paths: list[str]) -> None:
        if not self.drop_context:
            return
        session_id, remote = self.drop_context
        remote = remote.rstrip("/")

        def worker() -> None:
            for raw in paths:
                path = Path(raw)
                if not path.exists():
                    continue
                target = f"{remote}/{path.name}" if remote else f"/{path.name}"
                try:
                    if path.is_dir():
                        self._api_json(
                            f"/api/sftp/{session_id}/upload-local",
                            {"local_path": str(path), "remote_dir": target},
                        )
                    else:
                        self._api_json(
                            f"/api/sftp/{session_id}/upload",
                            {"local_path": str(path), "path": target},
                        )
                except Exception:
                    continue
            self.sftp_refresh_requested.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_sftp(self) -> None:
        self.view.page().runJavaScript("window.__sftpRefresh && window.__sftpRefresh()")

    def _on_drag_prepared(self, payload) -> None:  # noqa: ANN001
        window, root, paths = payload
        if not paths:
            try:
                window.close()
            except RuntimeError:
                pass
            return
        if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY"):
            self._start_drag_helper(window, root, paths)
            return
        try:
            window.mark_ready(paths)
        except RuntimeError:
            remove_temp_path(root)
            _drag_temp_roots.discard(root)

    def _start_drag_helper(self, window, root: Path, paths: list[Path]) -> None:
        """Hand the drag list to a Wayland-native helper process.

        The main UI stays on xcb (QtWebEngine renders poorly under native
        Wayland on some drivers) while the drop source is Wayland, which is what
        Wayland desktops and file managers require.
        """
        entries = [
            {"path": str(path), "name": path.name, "dir": path.is_dir()}
            for path in paths
        ]
        fd, tmp_name = tempfile.mkstemp(prefix="bitssh-drag-list-", suffix=".json")
        list_file = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "wayland"
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--dragwin"]
        else:
            command = [sys.executable, str(PROJECT_ROOT / "desktop" / "shell.py"), "--dragwin"]
        command += [
            "--list",
            str(list_file),
            "--root",
            str(root),
            "--parent-pid",
            str(os.getpid()),
        ]
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            list_file.unlink(missing_ok=True)
            try:
                window.mark_ready(paths)
            except RuntimeError:
                remove_temp_path(root)
                _drag_temp_roots.discard(root)
            return
        QTimer.singleShot(
            700, lambda: self._finish_drag_helper(window, root, paths, proc, list_file)
        )

    def _finish_drag_helper(self, window, root: Path, paths: list[Path], proc, list_file: Path) -> None:
        if proc.poll() is not None:
            # The Wayland platform may be unavailable; fall back to the
            # in-process list (works for X11 targets).
            list_file.unlink(missing_ok=True)
            try:
                window.mark_ready(paths)
            except RuntimeError:
                remove_temp_path(root)
                _drag_temp_roots.discard(root)
            return
        try:
            window._root = None  # ownership moves to the helper
            window.close()
        except RuntimeError:
            pass
        _drag_temp_roots.discard(root)
        self._drag_helpers.append({"proc": proc, "root": root, "list": list_file})

    def _cleanup_drag_helpers(self) -> None:
        for item in list(self._drag_helpers):
            proc = item["proc"]
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
            remove_temp_path(item["root"])
            try:
                item["list"].unlink(missing_ok=True)
            except OSError:
                pass
        self._drag_helpers.clear()

    def on_download(self, download: QWebEngineDownloadRequest) -> None:
        suggested = download.downloadFileName()
        path, _ = QFileDialog.getSaveFileName(self, "Save as", suggested)
        if not path:
            download.cancel()
            return
        info = Path(path)
        download.setDownloadDirectory(str(info.parent))
        download.setDownloadFileName(info.name)
        download.accept()

    def _log_visibility(self) -> None:
        self.view.page().runJavaScript(
            "document.visibilityState + ':' + (document.hidden ? 'hidden' : 'shown')",
            lambda result: print("VIS", result, flush=True),
        )

    def _run_debug_eval(self) -> None:
        self.view.page().runJavaScript(
            self._eval_script,
            lambda result: print("EVAL", result, flush=True),
        )

    def dump_dom(self) -> None:
        script = """(function(){
          const t = window.__term;
          const line = t ? t.buffer.active.getLine(0).translateToString(true) : 'NO_TERM';
          const leaves = Array.from(document.querySelectorAll('.xterm *'))
            .filter(e => e.children.length === 0 && (e.textContent || '').length > 15)
            .map(e => {
              const s = getComputedStyle(e), r = e.getBoundingClientRect();
              return [e.tagName + '.' + e.className, e.textContent.slice(0, 30),
                      s.visibility, s.opacity, s.position, Math.round(r.top), Math.round(r.left)];
            });
          return JSON.stringify({line, leaves}, null, 0);
        })()"""

        def report(result) -> None:
            print("DOMDUMP", result, flush=True)

        self.view.page().runJavaScript(script, report)

    def debug_open(self) -> None:
        script = (
            "window.__store ? (window.__store.getState().profiles[0] && "
            "window.__store.getState().openSession(window.__store.getState().profiles[0]), 'opened') : 'no-store'"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_OPEN", r, flush=True))
        QTimer.singleShot(5000, self.debug_hostkey)

    def debug_hostkey(self) -> None:
        script = (
            "window.__store && window.__store.getState().hostkey ? "
            "(window.__store.getState().resolveHostkey('always'), 'trusted') : 'none'"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_HOSTKEY", r, flush=True))
        QTimer.singleShot(2500, self.debug_sftp)

    def debug_sftp(self) -> None:
        script = (
            "(function(){const b=[...document.querySelectorAll('button')]"
            ".find(x=>x.textContent.trim()==='SFTP');"
            "if(b){b.click();return 'clicked';}return 'not-found';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_SFTP", r, flush=True))
        if not os.environ.get("BITSSH_DEBUG_SFTP_ONLY"):
            QTimer.singleShot(1500, self.debug_fwd)
        if os.environ.get("BITSSH_DEBUG_NEWWIN"):
            QTimer.singleShot(2000, self.debug_newwin)
        if os.environ.get("BITSSH_DEBUG_UPLOAD"):
            QTimer.singleShot(2000, self.debug_upload)
        if os.environ.get("BITSSH_DEBUG_DOWNLOAD"):
            QTimer.singleShot(2500, self.debug_download)
        if os.environ.get("BITSSH_DEBUG_DRAGDL"):
            QTimer.singleShot(9000, self.debug_drag_download)
        if os.environ.get("BITSSH_DEBUG_FOLDERDL"):
            QTimer.singleShot(2500, self.debug_folder_download)
        if os.environ.get("BITSSH_DEBUG_TESTUPLOAD"):
            QTimer.singleShot(3000, self.debug_test_upload)
        if os.environ.get("BITSSH_DEBUG_UPLOADUI"):
            QTimer.singleShot(3000, self.debug_upload_ui)
        if os.environ.get("BITSSH_DEBUG_MULTIDL"):
            QTimer.singleShot(3000, self.debug_multi_download)
        if os.environ.get("BITSSH_DEBUG_REALSUITE"):
            QTimer.singleShot(3000, self.debug_real_suite)
        if os.environ.get("BITSSH_DEBUG_BRIDGETEST"):
            QTimer.singleShot(4000, self.debug_bridge_test)
        if os.environ.get("BITSSH_DEBUG_MULTIDROP"):
            QTimer.singleShot(3500, self.debug_multi_drop)
        if os.environ.get("BITSSH_DEBUG_DELTEST"):
            QTimer.singleShot(3500, self.debug_delete_test)
        if os.environ.get("BITSSH_DEBUG_DROPTEST"):
            QTimer.singleShot(4500, self.debug_drop_test)

    def debug_drop_test(self) -> None:
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QDragEnterEvent, QDropEvent

        local_dir = PROJECT_ROOT / ".test" / "real"
        local_dir.mkdir(parents=True, exist_ok=True)
        first = local_dir / "drop1.txt"
        second = local_dir / "drop2.txt"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        folder = local_dir / "dropdir"
        folder.mkdir(exist_ok=True)
        (folder / "inner.txt").write_bytes(b"inner")

        mime = QMimeData()
        mime.setUrls(
            [
                QUrl.fromLocalFile(str(first)),
                QUrl.fromLocalFile(str(second)),
                QUrl.fromLocalFile(str(folder)),
            ]
        )
        candidates = [self.view]
        proxy = self.view.focusProxy()
        if proxy is not None:
            candidates.append(proxy)
        from PyQt6.QtWidgets import QWidget

        for child in self.view.findChildren(QWidget):
            name = type(child).__name__
            if "RenderWidget" in name or "Focus" in name:
                candidates.append(child)
        for target in candidates:
            center = QPointF(target.width() / 2, target.height() / 2)
            # A synthetic Drop is only delivered after an accepted DragEnter.
            enter = QDragEnterEvent(
                center.toPoint(),
                Qt.DropAction.CopyAction,
                mime,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(target, enter)
            event = QDropEvent(
                center,
                Qt.DropAction.CopyAction,
                mime,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            if enter.isAccepted():
                QApplication.sendEvent(target, event)
            print(
                "DROPTEST sent to", type(target).__name__,
                "enter=", enter.isAccepted(),
                "drop=", event.isAccepted(), flush=True,
            )
            if event.isAccepted():
                break
        QTimer.singleShot(15000, self._drop_test_result)

    def _drop_test_result(self) -> None:
        script = (
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''"
        )

        def with_sid(session_id) -> None:
            if not session_id:
                print("DROPTEST no-session", flush=True)
                return
            remote = (self.drop_context or ("", "/tmp"))[1]
            try:
                req = urllib.request.Request(
                    f"{self.base}/api/sftp/{session_id}/list?path={quote(remote)}",
                    headers={"X-Bitssh-Token": self.token},
                )
                listing = json.loads(urllib.request.urlopen(req, timeout=5).read())
                names = [e["name"] for e in listing.get("entries", [])]
                print(f"DROPTEST {remote} has:",
                      [n for n in names if n.startswith("drop")], flush=True)
            except Exception as exc:
                print("DROPTEST list failed", exc, flush=True)

        self.view.page().runJavaScript(script, with_sid)

    def debug_delete_test(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._delete_test_sid,
        )

    def _delete_test_sid(self, session_id) -> None:
        if not session_id:
            print("DELTEST no-session", flush=True)
            return

        def api(path: str, payload=None, method="POST") -> bytes:
            req = urllib.request.Request(
                f"{self.base}{path}",
                data=json.dumps(payload).encode() if payload is not None else None,
                method=method,
                headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
            )
            return urllib.request.urlopen(req, timeout=10).read()

        try:
            try:
                api(f"/api/sftp/{session_id}/ops",
                    {"op": "remove", "path": "/tmp/deltest", "is_dir": True})
            except Exception:
                pass
            api(f"/api/sftp/{session_id}/ops", {"op": "mkdir", "path": "/tmp/deltest"})
            for name in ("d1.txt", "d2.txt"):
                urllib.request.urlopen(urllib.request.Request(
                    f"{self.base}/api/sftp/{session_id}/upload-stream?path=/tmp/deltest/{name}",
                    data=b"x", method="PUT",
                    headers={"X-Bitssh-Token": self.token, "X-File-Size": "1"}),
                    timeout=5).read()
        except Exception as exc:
            print("DELTEST prep failed", exc, flush=True)
            return
        script = (
            "(function(){"
            "const setVal=(sel,val)=>{const i=document.querySelector(sel);if(!i)return;"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i,val);i.dispatchEvent(new Event('input',{bubbles:true}));};"
            "setVal('.sftp-sec.remote .path-input','/tmp/deltest');"
            "setTimeout(function(){"
            "const i=document.querySelector('.sftp-sec.remote .path-input');"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
            "setTimeout(function(){"
            "const rows=[...document.querySelectorAll('.sftp-sec.remote .fs-row')];"
            "if(rows.length<2){window.__del='rows:'+rows.length;return;}"
            "rows[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "rows[1].dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true}));"
            "setTimeout(function(){"
            "document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Delete',bubbles:true}));"
            "setTimeout(function(){"
            "const m=document.querySelector('.modal');"
            "if(m){m.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
            "window.__del='enter';}else{window.__del='no-modal';}"
            "},800);},500);},1200);},150);return 'ok';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("DELTEST", r, flush=True))
        QTimer.singleShot(10000, lambda: self._delete_test_result(session_id))

    def _delete_test_result(self, session_id) -> None:
        try:
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/list?path=/tmp/deltest",
                headers={"X-Bitssh-Token": self.token},
            )
            listing = json.loads(urllib.request.urlopen(req, timeout=5).read())
            print("DELTEST remaining:",
                  [e["name"] for e in listing.get("entries", [])], flush=True)
        except Exception as exc:
            print("DELTEST list failed", exc, flush=True)

    def debug_multi_drop(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._multi_drop_sid,
        )

    def _multi_drop_sid(self, session_id) -> None:
        if not session_id:
            print("MULTIDROP no-session", flush=True)
            return
        try:
            payload = json.dumps({"op": "mkdir", "path": "/tmp/multidrop"}).encode()
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/ops",
                data=payload, method="POST",
                headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        script = (
            "(function(){"
            "const setVal=(sel,val)=>{const i=document.querySelector(sel);if(!i)return;"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i,val);i.dispatchEvent(new Event('input',{bubbles:true}));};"
            "setVal('.sftp-sec.remote .path-input','/tmp/multidrop');"
            "setTimeout(function(){"
            "const i=document.querySelector('.sftp-sec.remote .path-input');"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
            "setTimeout(function(){"
            "const dt=new DataTransfer();"
            "for (const n of ['multi1.txt','multi2.txt','multi3.txt'])"
            "  dt.items.add(new File(['data-'+n], n));"
            "document.querySelector('.sftp').dispatchEvent(new DragEvent('drop',"
            "  {bubbles:true,cancelable:true,dataTransfer:dt}));"
            "window.__md='dropped';"
            "},1200);},150);return 'ok';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("MULTIDROP", r, flush=True))
        QTimer.singleShot(12000, lambda: self._multi_drop_result(session_id))

    def _multi_drop_result(self, session_id) -> None:
        try:
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/list?path=/tmp/multidrop",
                headers={"X-Bitssh-Token": self.token},
            )
            listing = json.loads(urllib.request.urlopen(req, timeout=5).read())
            names = sorted(e["name"] for e in listing.get("entries", []))
            print("MULTIDROP remote:", names, flush=True)
        except Exception as exc:
            print("MULTIDROP list failed", exc, flush=True)

    def debug_bridge_test(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._bridge_test_sid,
        )

    def _bridge_test_sid(self, session_id) -> None:
        if not session_id:
            print("BRIDGE no-session", flush=True)
            return
        try:
            data = b"bridge-file"
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload-stream?path=/tmp/bridge.txt",
                data=data, method="PUT",
                headers={"X-Bitssh-Token": self.token, "X-File-Size": str(len(data))},
            )
            urllib.request.urlopen(req, timeout=5).read()
            try:
                self._api_json(f"/api/sftp/{session_id}/ops",
                               {"op": "mkdir", "path": "/tmp/bridgedir"})
            except Exception:
                pass  # already exists from a previous run
            inner = b"inner-file"
            urllib.request.urlopen(urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload-stream"
                "?path=/tmp/bridgedir/inner.txt",
                data=inner, method="PUT",
                headers={"X-Bitssh-Token": self.token, "X-File-Size": str(len(inner))},
            ), timeout=5).read()
            big = b"x" * (128 * 1024 * 1024)
            urllib.request.urlopen(urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload-stream"
                "?path=/tmp/bridge-big.bin",
                data=big, method="PUT",
                headers={"X-Bitssh-Token": self.token, "X-File-Size": str(len(big))},
            ), timeout=180).read()
        except Exception as exc:
            print("BRIDGE prep failed", exc, flush=True)
            return
        payload = json.dumps({
            "session": session_id,
            "files": [
                {"path": "/tmp/bridge.txt", "name": "bridge.txt"},
                {"path": "/tmp/bridgedir", "name": "bridgedir", "dir": True},
                {"path": "/tmp/bridge-big.bin", "name": "bridge-big.bin"},
            ],
        })
        script = (
            "(function(){"
            f"const p={json.dumps(payload)};"
            "if(!window.__bridge){return 'no-bridge';}"
            "window.__bridge.dragOut(p);"
            "return 'called';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("BRIDGE", r, flush=True))
        QTimer.singleShot(600, self._bridge_quick_check)
        QTimer.singleShot(45000, self._bridge_test_result)

    def _bridge_quick_check(self) -> None:
        for window in QApplication.topLevelWidgets():
            if isinstance(window, DragOutWindow):
                print(
                    "BRIDGETEST early:",
                    window.windowTitle(),
                    "draggable=", window.dragEnabled(),
                    "rows=", window.count(),
                    flush=True,
                )
        try:
            items = self._api_json("/api/transfers")
            snapshot = [
                (x["label"], x["status"], x["copied"], x["total"])
                for x in items  # type: ignore[union-attr]
            ]
            print("BRIDGETEST transfers:", snapshot, flush=True)
        except Exception as exc:
            print("BRIDGETEST transfers failed", exc, flush=True)

    def _bridge_test_result(self) -> None:
        for window in QApplication.topLevelWidgets():
            if isinstance(window, DragOutWindow):
                print(
                    "BRIDGETEST final:",
                    window.windowTitle(),
                    "draggable=", window.dragEnabled(),
                    "rows=", window.count(),
                    flush=True,
                )
        base = Path(tempfile.gettempdir())
        try:
            roots = sorted(
                base.glob("bitssh-drag-*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            roots = []
        if not roots:
            print("BRIDGETEST temp: none", flush=True)
            return
        root = roots[0]
        names = [p.name + ("/" if p.is_dir() else "") for p in root.iterdir()]
        inner = root / "bridgedir"
        print("BRIDGETEST temp:", root.name, names, flush=True)
        print(
            "BRIDGETEST inner:",
            sorted(x.name for x in inner.iterdir()) if inner.is_dir() else None,
            flush=True,
        )

    # ---------------- real mouse/keyboard suite ----------------

    def _screen_point(self, point: dict) -> tuple[int, int]:
        import ctypes as ct
        from ctypes import wintypes

        hwnd = int(self.view.winId())
        origin = wintypes.POINT(0, 0)
        ct.windll.user32.ClientToScreen(hwnd, ct.byref(origin))
        try:
            scale = ct.windll.user32.GetDpiForWindow(hwnd) / 96.0
        except Exception:
            scale = self.view.devicePixelRatioF()
        return int(origin.x + point["x"] * scale), int(origin.y + point["y"] * scale)

    def _mouse_move(self, x: int, y: int) -> None:
        import ctypes as ct

        ct.windll.user32.SetCursorPos(x, y)

    def _mouse_button(self, down: bool) -> None:
        import ctypes as ct

        ct.windll.user32.mouse_event(0x0002 if down else 0x0004, 0, 0, 0, 0)

    def _drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self._mouse_move(*start)
        time.sleep(0.25)
        self._mouse_button(True)
        time.sleep(0.25)
        for step in range(1, 13):
            x = int(start[0] + (end[0] - start[0]) * step / 12)
            y = int(start[1] + (end[1] - start[1]) * step / 12)
            self._mouse_move(x, y)
            time.sleep(0.04)
        time.sleep(0.3)
        self._mouse_button(False)

    def _click(self, point: tuple[int, int], ctrl: bool = False) -> None:
        import ctypes as ct

        vk_control = 0x11
        if ctrl:
            ct.windll.user32.keybd_event(vk_control, 0, 0, 0)
        self._mouse_move(*point)
        time.sleep(0.1)
        self._mouse_button(True)
        time.sleep(0.08)
        self._mouse_button(False)
        time.sleep(0.1)
        if ctrl:
            ct.windll.user32.keybd_event(vk_control, 0, 0x0002, 0)

    def debug_real_suite(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._real_suite_sid,
        )

    def _real_suite_sid(self, session_id) -> None:
        if not session_id:
            print("REAL no-session", flush=True)
            return
        local_dir = PROJECT_ROOT / ".test" / "real"
        import shutil

        shutil.rmtree(local_dir, ignore_errors=True)
        local_dir.mkdir(parents=True, exist_ok=True)
        desktop = Path.home() / "Desktop"
        for name in ("a.txt", "b.txt", "up.txt"):
            (desktop / name).unlink(missing_ok=True)
            (local_dir / name).unlink(missing_ok=True)
        (local_dir / "up.txt").write_bytes(b"upload-me")

        def api(method: str, path: str, payload=None, raw=None) -> bytes:
            data = raw if raw is not None else (json.dumps(payload).encode() if payload else None)
            req = urllib.request.Request(
                f"{self.base}{path}", data=data, method=method,
                headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
            )
            return urllib.request.urlopen(req, timeout=10).read()

        try:
            try:
                api("POST", f"/api/sftp/{session_id}/ops",
                    {"op": "remove", "path": "/tmp/realtest", "is_dir": True})
            except Exception:
                pass
            api("POST", f"/api/sftp/{session_id}/ops", {"op": "mkdir", "path": "/tmp/realtest"})
            for name, body in (("a.txt", b"aaaa"), ("b.txt", b"bbbb")):
                api("PUT", f"/api/sftp/{session_id}/upload-stream?path=/tmp/realtest/{name}",
                    raw=body)
        except Exception as exc:
            print("REAL prep failed", exc, flush=True)
            return

        self.setGeometry(10, 10, 860, 560)
        self.raise_()
        self.activateWindow()
        script = (
            "(function(){"
            "const setVal=(sel,val)=>{const i=document.querySelector(sel);if(!i)return;"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i,val);i.dispatchEvent(new Event('input',{bubbles:true}));};"
            "const press=(sel)=>{const i=document.querySelector(sel);if(!i)return;"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));};"
            f"setVal('.sftp-sec.local .path-input', {json.dumps(str(local_dir))});"
            "setTimeout(function(){press('.sftp-sec.local .path-input');"
            "setVal('.sftp-sec.remote .path-input','/tmp/realtest');"
            "setTimeout(function(){press('.sftp-sec.remote .path-input');"
            "setTimeout(function(){"
            "const center=(el)=>{const b=el.getBoundingClientRect();"
            "return {x:b.left+b.width/2,y:b.top+b.height/2};};"
            "const rows=[...document.querySelectorAll('.sftp-sec.remote .fs-row')];"
            "const pick=(n)=>{const r=rows.find(x=>x.textContent.includes(n));return r?center(r):null;};"
            "const panes=[...document.querySelectorAll('.sftp-sec')];"
            "const dl=[...document.querySelectorAll('.sftp-sec.remote header button')]"
            ".find(b=>b.textContent.includes('\\u4e0b\\u8f7d'));"
            "const localRows=[...document.querySelectorAll('.sftp-sec.local .fs-row')];"
            "const lp=localRows.find(x=>x.textContent.includes('up.txt'));"
            "window.__coords=JSON.stringify({a:pick('a.txt'),b:pick('b.txt'),"
            "remotePane:center(panes[0]),localPane:center(panes[1]),"
            "download:dl?center(dl):null,localUp:lp?center(lp):null,"
            "localPath:(document.querySelector('.sftp-sec.local .path-input')||{}).value,"
            "localNames:localRows.map(r=>r.textContent.trim().slice(0,20)),"
            "bridge:!!window.__bridge,"
            "aHandle:(function(){const r=rows.find(x=>x.textContent.includes('a.txt'));"
            "if(!r)return null;const h=r.querySelector('.drag-handle');return h?center(h):null;})()});"
            "},1600);},140);},140);return 'navigating';})()"
        )
        self.view.page().runJavaScript(
            script, lambda r: print("REAL nav", r, flush=True)
        )
        QTimer.singleShot(4000, lambda: self._real_coords(session_id, local_dir, desktop))

    def _real_coords(self, session_id, local_dir: Path, desktop: Path) -> None:
        self.view.page().runJavaScript(
            "window.__coords || ''", lambda raw: self._real_run(session_id, local_dir, desktop, raw)
        )

    def _real_run(self, session_id, local_dir: Path, desktop: Path, raw: str) -> None:
        try:
            coords = json.loads(raw)
        except Exception:
            print("REAL no coords", raw, flush=True)
            return
        print("REAL coords", coords, flush=True)

        import threading

        pts = {
            key: (self._screen_point(value) if isinstance(value, dict) else None)
            for key, value in coords.items()
        }
        hwnd = int(self.windowHandle().winId())
        screen = self.view.screen().availableGeometry()
        desktop_point = (
            screen.x() + screen.width() - 300,
            screen.y() + screen.height() - 250,
        )
        results = {}

        def suite() -> None:
            import ctypes as ct

            u = ct.windll.user32

            def focus() -> None:
                from ctypes import wintypes

                rect = wintypes.RECT()
                u.GetWindowRect(hwnd, ct.byref(rect))
                u.SetForegroundWindow(hwnd)
                u.BringWindowToTop(hwnd)
                time.sleep(0.3)
                self._click((rect.left + 150, rect.top + 12))
                time.sleep(0.3)

            def open_explorer_target() -> tuple[int, int]:
                drop_dir = PROJECT_ROOT / ".test" / "drop"
                drop_dir.mkdir(parents=True, exist_ok=True)
                (drop_dir / "a.txt").unlink(missing_ok=True)
                proc = subprocess.Popen(["explorer", str(drop_dir)])
                time.sleep(2.0)
                found: list[int] = []
                enum_proc = ct.WINFUNCTYPE(ct.c_bool, ct.c_void_p, ct.c_void_p)

                def callback(window, _param) -> bool:
                    if not u.IsWindowVisible(window):
                        return True
                    buffer = ct.create_unicode_buffer(64)
                    u.GetClassNameW(window, buffer, 64)
                    if buffer.value != "CabinetWClass":
                        return True
                    title = ct.create_unicode_buffer(260)
                    u.GetWindowTextW(window, title, 260)
                    if "drop" in title.value.lower():
                        found.append(window)
                        return False
                    return True

                u.EnumWindows(enum_proc(callback), 0)
                if not found:
                    return (0, 0)
                u.MoveWindow(found[0], 1300, 120, 380, 520, True)
                time.sleep(0.8)
                return (1300 + 190, 120 + 300)
            # 0) drag a remote row outside the panel -> popup appears by itself
            explorer_point = open_explorer_target()
            if pts.get("a") and explorer_point != (0, 0):
                focus()
                self._drag(pts["a"], desktop_point)
                time.sleep(2.0)
                list_hwnd = 0
                enum_proc = ct.WINFUNCTYPE(ct.c_bool, ct.c_void_p, ct.c_void_p)

                def find_list(window, _param) -> bool:
                    nonlocal list_hwnd
                    if not u.IsWindowVisible(window):
                        return True
                    title = ct.create_unicode_buffer(128)
                    u.GetWindowTextW(window, title, 128)
                    if title.value == "bitssh drag-out":
                        list_hwnd = window
                        return False
                    return True

                u.EnumWindows(enum_proc(find_list), 0)
                if list_hwnd:
                    u.MoveWindow(list_hwnd, 950, 150, 320, 220, True)
                    time.sleep(0.6)
                    focus()
                    self._drag((950 + 120, 150 + 50), explorer_point)
                    time.sleep(5)
                    drop_file = PROJECT_ROOT / ".test" / "drop" / "a.txt"
                    results["drag_out"] = drop_file.exists()
                    drop_file.unlink(missing_ok=True)
                    u.SendMessageW(list_hwnd, 0x0010, 0, 0)  # WM_CLOSE
            focus()
            # 1) drag local up.txt into the remote pane (internal upload)
            if pts.get("localUp") and pts.get("remotePane"):
                self._drag(pts["localUp"], pts["remotePane"])
                time.sleep(6)
                try:
                    listing = json.loads(urllib.request.urlopen(
                        urllib.request.Request(
                            f"{self.base}/api/sftp/{session_id}/list?path=/tmp/realtest",
                            headers={"X-Bitssh-Token": self.token}),
                        timeout=5).read())
                    results["internal_upload"] = any(
                        e["name"] == "up.txt" for e in listing.get("entries", []))
                except Exception:
                    results["internal_upload"] = False
            focus()
            # 2) drag remote b.txt into the local pane (internal download)
            if pts.get("b") and pts.get("localPane"):
                self._drag(pts["b"], pts["localPane"])
                time.sleep(6)
                results["internal_download"] = (local_dir / "b.txt").exists()
            focus()
            # 4) ctrl-click a.txt and b.txt, then download both
            if pts.get("a") and pts.get("b") and pts.get("download"):
                self._click(pts["a"])
                self._click(pts["b"], ctrl=True)
                time.sleep(0.6)
                self._click(pts["download"])
                time.sleep(8)
                results["multi_download"] = (local_dir / "a.txt").exists() and (local_dir / "b.txt").exists()
            print("REAL results", json.dumps(results), flush=True)

        threading.Thread(target=suite, daemon=True).start()

    def debug_multi_download(self) -> None:
        import shutil

        target = PROJECT_ROOT / ".test" / "dl-multi"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        script = (
            "(function(){"
            "const diag=[];"
            "const setInput=(sel,val)=>{const i=document.querySelector(sel);"
            "diag.push(sel+'='+(i?i.tagName:'null'));if(!i)return;"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i,val);i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));};"
            f"setInput('.sftp-sec.local .path-input', {json.dumps(str(target))});"
            "setInput('.sftp-sec.remote .path-input','/home/123');"
            "setTimeout(function(){"
            "const rows=[...document.querySelectorAll('.sftp-sec.remote .fs-row')]"
            ".filter(r=>!r.textContent.includes('\\u{1F4C1}'));"
            "window.__mdrows=rows.length;"
            "if(rows.length<2){window.__md='rows:'+rows.length;return;}"
            "rows[0].dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "rows[1].dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true}));"
            "setTimeout(function(){"
            "const btn=[...document.querySelectorAll('.sftp-sec.remote header button')]"
            ".find(b=>b.textContent.includes('\\u4e0b\\u8f7d'));"
            "if(!btn){window.__md='no-btn';return;}"
            "window.__md='btnDisabled='+btn.disabled;"
            "btn.click();window.__md+=';clicked';"
            "},600);"
            "},1500);return diag.join(',');})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_MULTIDL", r, flush=True))
        QTimer.singleShot(35000, lambda: self._debug_multi_dl_result(target))

    def _debug_multi_dl_result(self, target) -> None:
        files = [p.name for p in target.glob("*")] if target.exists() else []
        print("DEBUG_MULTIDL files", files, flush=True)
        try:
            req = urllib.request.Request(
                f"{self.base}/api/transfers", headers={"X-Bitssh-Token": self.token}
            )
            items = json.loads(urllib.request.urlopen(req, timeout=5).read())
            print("DEBUG_MULTIDL transfers",
                  [(i["label"], i["status"], i["copied"], i["total"]) for i in items[-4:]],
                  flush=True)
        except Exception as exc:
            print("DEBUG_MULTIDL transfers failed", exc, flush=True)
        self.view.page().runJavaScript(
            "JSON.stringify({md: window.__md||null, rows: window.__mdrows||0, "
            "toast: (document.querySelector('.toast')||{}).textContent||null})",
            lambda r: print("DEBUG_MULTIDL state", r, flush=True),
        )

    def debug_upload_ui(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._debug_upload_ui_with_sid,
        )

    def _debug_upload_ui_with_sid(self, session_id) -> None:
        if not session_id:
            print("DEBUG_UPLOADUI no-session", flush=True)
            return
        try:
            payload = json.dumps({"op": "mkdir", "path": "/tmp/ui-upload"}).encode()
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/ops",
                data=payload,
                method="POST",
                headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
        script = (
            "(function(){"
            "const setInput=(sel,val)=>{const i=document.querySelector(sel);"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i,val);i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));};"
            f"setInput('.sftp-sec.local .path-input', {json.dumps(str(PROJECT_ROOT / '.test'))});"
            "setTimeout(function(){"
            "setInput('.sftp-sec.remote .path-input','/tmp/ui-upload');"
            "setTimeout(function(){"
            "const row=[...document.querySelectorAll('.sftp-sec.local .fs-row')]"
            ".find(r=>r.textContent.includes('me'));"
            "if(!row){window.__upui='no-row';return;}"
            "const btn=[...row.querySelectorAll('button')].find(b=>b.title==='\\u4e0a\\u4f20');"
            "if(!btn){window.__upui='no-btn';return;}"
            "btn.click();window.__upui='clicked';"
            "},1200);},800);"
            "return 'navigating';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_UPLOADUI", r, flush=True))
        QTimer.singleShot(30000, lambda: self._debug_upload_ui_result(session_id))

    def _debug_upload_ui_result(self, session_id) -> None:
        try:
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/list?path=/tmp/ui-upload",
                headers={"X-Bitssh-Token": self.token},
            )
            listing = json.loads(urllib.request.urlopen(req, timeout=5).read())
            names = [e["name"] for e in listing.get("entries", [])]
            print("DEBUG_UPLOADUI top:", names[:10], flush=True)
        except Exception as exc:
            print("DEBUG_UPLOADUI list failed", exc, flush=True)

    def debug_test_upload(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._debug_test_upload_with_sid,
        )

    def _debug_test_upload_with_sid(self, session_id) -> None:
        if not session_id:
            print("DEBUG_TESTUPLOAD no-session", flush=True)
            return
        local = PROJECT_ROOT / ".test" / "me"
        if not local.is_dir():
            print("DEBUG_TESTUPLOAD missing", local, flush=True)
            return
        try:
            payload = json.dumps({"local_path": str(local), "remote_dir": "/tmp/me-upload"}).encode()
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload-local",
                data=payload,
                method="POST",
                headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
            )
            result = json.loads(urllib.request.urlopen(req, timeout=10).read())
        except Exception as exc:
            print("DEBUG_TESTUPLOAD start failed", exc, flush=True)
            return
        print("DEBUG_TESTUPLOAD started", result, flush=True)
        QTimer.singleShot(30000, lambda: self._debug_test_upload_result(session_id, result["id"]))

    def _debug_test_upload_result(self, session_id, transfer_id) -> None:
        item = None
        try:
            req = urllib.request.Request(
                f"{self.base}/api/transfers", headers={"X-Bitssh-Token": self.token}
            )
            items = json.loads(urllib.request.urlopen(req, timeout=5).read())
            item = next((i for i in items if i["id"] == transfer_id), None)
        except Exception as exc:
            print("DEBUG_TESTUPLOAD transfers failed", exc, flush=True)
        print("DEBUG_TESTUPLOAD item", item, flush=True)
        try:
            req2 = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/list?path=/tmp/me-upload",
                headers={"X-Bitssh-Token": self.token},
            )
            listing = json.loads(urllib.request.urlopen(req2, timeout=5).read())
            names = [e["name"] for e in listing.get("entries", [])]
            print("DEBUG_TESTUPLOAD top:", names[:15], flush=True)
        except Exception as exc:
            print("DEBUG_TESTUPLOAD list failed", exc, flush=True)

    def debug_folder_download(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._debug_folder_with_sid,
        )

    def _debug_folder_with_sid(self, session_id) -> None:
        if not session_id:
            print("DEBUG_FOLDERDL no-session", flush=True)
            return
        import shutil

        try:
            call = lambda method, path, payload=None, raw=None: urllib.request.urlopen(
                urllib.request.Request(
                    f"{self.base}{path}",
                    data=raw if raw is not None else (json.dumps(payload).encode() if payload else None),
                    method=method,
                    headers={"X-Bitssh-Token": self.token, "Content-Type": "application/json"},
                ),
                timeout=5,
            ).read()
            try:
                call("POST", f"/api/sftp/{session_id}/ops",
                     {"op": "remove", "path": "/tmp/dl-folder", "is_dir": True})
            except Exception:
                pass
            call("POST", f"/api/sftp/{session_id}/ops",
                 {"op": "mkdir", "path": "/tmp/dl-folder"})
            call("PUT", f"/api/sftp/{session_id}/upload-stream?path=/tmp/dl-folder/f.txt",
                 raw=b"folder-file-content")
        except Exception as exc:
            print("DEBUG_FOLDERDL prep failed", exc, flush=True)
            return

        target = PROJECT_ROOT / ".test" / "dl-folder"
        shutil.rmtree(target, ignore_errors=True)
        script = (
            "(function(){"
            "const setInput=(sel,val)=>{const i=document.querySelector(sel);"
            "const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "s.call(i,val);i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));};"
            f"setInput('.sftp-sec.local .path-input', {json.dumps(str(PROJECT_ROOT / '.test'))});"
            "setTimeout(function(){"
            "setInput('.sftp-sec.remote .path-input','/tmp');"
            "setTimeout(function(){"
            "const row=[...document.querySelectorAll('.sftp-sec.remote .fs-row')]"
            ".find(r=>r.textContent.includes('dl-folder'));"
            "if(!row){window.__fd='no-row';return;}"
            "const btn=[...row.querySelectorAll('button')].find(b=>b.title==='\\u4e0b\\u8f7d');"
            "if(!btn){window.__fd='no-btn';return;}"
            "btn.click();window.__fd='clicked';"
            "},1000);},700);"
            "return 'navigating';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_FOLDERDL_NAV", r, flush=True))
        QTimer.singleShot(7000, self.debug_folder_result)

    def debug_folder_result(self) -> None:
        target = PROJECT_ROOT / ".test" / "dl-folder" / "f.txt"
        print("DEBUG_FOLDERDL_LOCAL", target.exists(),
              target.read_bytes() if target.exists() else b"", flush=True)

    def debug_drag_download(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._debug_drag_with_sid,
        )

    def _debug_drag_with_sid(self, session_id) -> None:
        if not session_id:
            print("DEBUG_DRAGDL no-session", flush=True)
            return
        try:
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload-stream?path=/tmp/dl-drag.txt",
                data=b"drag-download",
                method="PUT",
                headers={"X-Bitssh-Token": self.token, "X-File-Size": "13"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:
            print("DEBUG_DRAGDL prep failed", exc, flush=True)
            return
        (PROJECT_ROOT / ".test" / "dl-drag.txt").unlink(missing_ok=True)
        script = """(function(){
          const input=document.querySelector('.sftp-sec.remote .path-input');
          const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          setter.call(input,'/tmp');
          input.dispatchEvent(new Event('input',{bubbles:true}));
          input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
          setTimeout(function(){
            const row=[...document.querySelectorAll('.sftp-sec.remote .fs-row')]
              .find(r=>r.textContent.includes('dl-drag.txt'));
            if(!row){window.__drag='no-row';return;}
            const dt=new DataTransfer();
            row.dispatchEvent(new DragEvent('dragstart',
              {bubbles:true,cancelable:true,dataTransfer:dt}));
            const payload=dt.getData('application/x-bitssh-remote');
            if(!payload){window.__drag='no-payload';return;}
            const aside=document.querySelector('.sftp');
            aside.dispatchEvent(new DragEvent('drop',
              {bubbles:true,cancelable:true,dataTransfer:dt}));
            window.__drag='dropped:'+payload;
          }, 1200);
          return 'navigating';
        })()"""
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_DRAGDL_NAV", r, flush=True))
        QTimer.singleShot(6000, self.debug_drag_download_result)

    def debug_drag_download_result(self) -> None:
        script = """(function(){
          const toast=document.querySelector('.toast');
          return JSON.stringify({state: window.__drag||null,
            toast: toast?toast.textContent:null});
        })()"""
        self.view.page().runJavaScript(
            script, lambda r: print("DEBUG_DRAGDL_RESULT", r, flush=True)
        )
        local = PROJECT_ROOT / ".test" / "dl-drag.txt"
        print("DEBUG_DRAGDL_LOCAL", local.exists(), local.stat().st_size if local.exists() else -1, flush=True)

    def debug_download(self) -> None:
        self.view.page().runJavaScript(
            "window.__store && window.__store.getState().tabs[0]"
            " ? window.__store.getState().tabs[0].sessionId : ''",
            self._debug_download_with_sid,
        )

    def _debug_download_with_sid(self, session_id) -> None:
        if not session_id:
            print("DEBUG_DOWNLOAD no-session", flush=True)
            return
        try:
            req = urllib.request.Request(
                f"{self.base}/api/sftp/{session_id}/upload-stream?path=/tmp/dl-test.txt",
                data=b"hello-download",
                method="PUT",
                headers={"X-Bitssh-Token": self.token, "X-File-Size": "14"},
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:
            print("DEBUG_DOWNLOAD prep failed", exc, flush=True)
            return
        (PROJECT_ROOT / ".test" / "dl-test.txt").unlink(missing_ok=True)
        script = """(function(){
          const input=document.querySelector('.sftp-sec.remote .path-input');
          const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          setter.call(input,'/tmp');
          input.dispatchEvent(new Event('input',{bubbles:true}));
          input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
          setTimeout(function(){
            const row=[...document.querySelectorAll('.sftp-sec.remote .fs-row')]
              .find(r=>r.textContent.includes('dl-test.txt'));
            if(!row){ window.__dl='no-row'; return; }
            const btn=[...row.querySelectorAll('button')]
              .find(b=>b.title==='\\u4e0b\\u8f7d');
            if(!btn){ window.__dl='no-btn'; return; }
            btn.click();
            window.__dl='clicked';
          }, 1200);
          return 'navigating';
        })()"""
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_DL_NAV", r, flush=True))
        QTimer.singleShot(5000, self.debug_download_result)

    def debug_download_result(self) -> None:
        script = """(function(){
          const toast=document.querySelector('.toast');
          const rows=[...document.querySelectorAll('.sftp-sec.transfers .transfer-row')]
            .map(r=>r.textContent.replace(/\\s+/g,' ').trim());
          const modal=document.querySelector('.modal p, .modal h2');
          return JSON.stringify({state: window.__dl||null,
            toast: toast?toast.textContent:null, rows, modal: modal?modal.textContent:null});
        })()"""
        self.view.page().runJavaScript(
            script, lambda r: print("DEBUG_DL_RESULT", r, flush=True)
        )
        local = PROJECT_ROOT / ".test" / "dl-test.txt"
        print("DEBUG_DL_LOCAL", local.exists(), local.stat().st_size if local.exists() else -1, flush=True)

    def debug_upload(self) -> None:
        script = """(function(){
          const secs=[...document.querySelectorAll('.sftp-sec')];
          const local=secs[1]; if(!local) return 'no-local';
          const row=[...local.querySelectorAll('.fs-row')].find(r=>r.textContent.includes('\\u{1F4C4}'));
          if(!row) return 'no-file';
          const btn=[...row.querySelectorAll('button')].find(b=>b.title==='\\u4e0a\\u4f20');
          if(!btn) return 'no-btn';
          btn.click();
          return 'clicked:'+row.textContent.trim().slice(0,40);
        })()"""
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_UPLOAD", r, flush=True))
        QTimer.singleShot(4000, self.debug_upload_result)

    def debug_upload_result(self) -> None:
        script = """(function(){
          const toast=document.querySelector('.toast');
          const rows=[...document.querySelectorAll('.sftp-sec.transfers .transfer-row')]
            .map(r=>r.textContent.replace(/\\s+/g,' ').trim());
          return JSON.stringify({toast: toast?toast.textContent:null, rows});
        })()"""
        self.view.page().runJavaScript(
            script, lambda r: print("DEBUG_UPLOAD_RESULT", r, flush=True)
        )

    def debug_newwin(self) -> None:
        script = (
            "(function(){const b=document.querySelector('.tab button[title=\"Open in new window\"]');"
            "if(b){b.click();return 'clicked';}return 'not-found';})()"
        )
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_NEWWIN", r, flush=True))
        QTimer.singleShot(3500, self.debug_redock)

    def debug_redock(self) -> None:
        if not self._windows:
            print("DEBUG_REDOCK no-window", flush=True)
            return
        page = self._windows[0].view.page()
        script = (
            "(function(){const b=[...document.querySelectorAll('button')]"
            ".find(x=>x.textContent.trim()==='移回主窗口');"
            "if(b){b.click();return 'clicked';}return 'not-found';})()"
        )
        page.runJavaScript(script, lambda r: print("DEBUG_REDOCK", r, flush=True))

    def debug_fwd(self) -> None:
        script = """(function(){
          const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='转发');
          if(b) b.click();
          const tab=window.__store.getState().tabs[0];
          if(!tab) return 'no-session';
          const token=location.hash.startsWith('#token=')?location.hash.slice(7):'';
          const x=new XMLHttpRequest();
          x.open('POST','/api/forwards',false);
          x.setRequestHeader('X-Bitssh-Token',token);
          x.setRequestHeader('Content-Type','application/json');
          x.send(JSON.stringify({session_id:tab.sessionId,type:'local',bind_host:'127.0.0.1',bind_port:0,dest_host:'127.0.0.1',dest_port:2222}));
          return 'fwd:'+x.status;
        })()"""
        self.view.page().runJavaScript(script, lambda r: print("DEBUG_FWD", r, flush=True))

    def check_proc(self) -> None:
        if self.closing:
            return
        if self.proc.poll() is not None:
            self.timer.stop()
            QMessageBox.critical(self, "bitssh", f"backend exited with code {self.proc.returncode}")
            self.closing = True
            self.close()

    def shutdown_backend(self) -> None:
        if self.proc.poll() is not None:
            return
        req = urllib.request.Request(
            self.base + "/internal/shutdown",
            method="POST",
            headers={"X-Bitssh-Token": self.token},
        )
        try:
            urllib.request.urlopen(req, timeout=2).read()
        except (urllib.error.URLError, OSError):
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def closeEvent(self, event) -> None:
        self.closing = True
        self.timer.stop()
        for window in list(self._windows):
            window.close()
        self._windows.clear()
        self.shutdown_backend()
        super().closeEvent(event)


class AttachWindow(QMainWindow):
    """A secondary window showing one existing session (single-pane mode)."""

    def __init__(self, main: MainWindow, base: str, token: str) -> None:
        super().__init__()
        self.main = main
        self.base = base
        self.token = token
        self.session_id = ""
        self.keep_session = False
        self.setWindowTitle("bitssh")
        self.setGeometry(140, 140, 1100, 720)

        page = LocalPage(main.profile, self, base)
        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanPaste, True)
        attach_local_permissions(page, base)
        page.dialogRequested.connect(lambda target: main.on_dialog(target, self))
        page.new_window_factory = main.open_attach_page
        install_webchannel(page, main, self)
        page.urlChanged.connect(self._on_url)

        self.view = QWebEngineView(self)
        self.view.setPage(page)
        self.setCentralWidget(self.view)

    def _on_url(self, url: QUrl) -> None:
        fragment = url.fragment()
        if not fragment:
            return
        params = {key: values[0] for key, values in parse_qs(fragment).items()}
        if params.get("attach"):
            self.session_id = params["attach"]
        if params.get("profile"):
            self.setWindowTitle(params["profile"])

    def closeEvent(self, event) -> None:
        if self.session_id and not self.keep_session:
            try:
                req = urllib.request.Request(
                    f"{self.base}/api/sessions/{self.session_id}",
                    method="DELETE",
                    headers={"X-Bitssh-Token": self.token},
                )
                urllib.request.urlopen(req, timeout=2).read()
            except Exception:
                pass
        super().closeEvent(event)


def backend_command(handshake: Path) -> list[str]:
    args = ["--server", "--handshake", str(handshake), "--parent-pid", str(os.getpid())]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "backend", *args]


def wait_handshake(path: Path, proc: subprocess.Popen) -> dict:
    deadline = time.monotonic() + HANDSHAKE_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"backend exited early with code {proc.returncode}")
        if path.exists():
            for _ in range(10):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    path.unlink(missing_ok=True)
                    return data
                except (json.JSONDecodeError, OSError):
                    time.sleep(0.05)
        time.sleep(0.05)
    raise TimeoutError("backend did not write a handshake file")


def wait_healthy(base: str, token: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(base + "/api/ping", headers={"X-Bitssh-Token": token})
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise TimeoutError("backend health check failed")


def _configure_webengine() -> None:
    """Keep the terminal rendering when the window is unfocused or covered.

    Chromium throttles timers and rendering for hidden/occluded windows, which
    makes background output look frozen until the window is touched. bitssh
    only loads its own loopback page, so disabling those policies is safe.
    """
    feature_switch = "--disable-features="
    disable = {"CalculateNativeWinOcclusion", "IntensiveWakeUpThrottling"}
    kept: list[str] = []
    for token in os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split():
        if token.startswith(feature_switch):
            disable.update(name for name in token[len(feature_switch):].split(",") if name)
        elif token not in kept:
            kept.append(token)
    kept.append(feature_switch + ",".join(sorted(disable)))
    for token in (
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ):
        if token not in kept:
            kept.append(token)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(kept)


def _run_shell(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bitssh")
    parser.add_argument("workspace", nargs="?", help="workspace file to open")
    args = parser.parse_args(argv)

    _configure_webengine()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("bitssh")

    handshake = Path(tempfile.gettempdir()) / f"bitssh-hs-{os.getpid()}-{int(time.time()*1000)}.json"
    job = WinJob()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        backend_command(handshake),
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    job.assign(proc)

    try:
        hs = wait_handshake(handshake, proc)
        base = f"http://127.0.0.1:{hs['port']}"
        wait_healthy(base, hs["token"])
    except Exception as exc:
        proc.kill()
        QMessageBox.critical(None, "bitssh", f"cannot start backend: {exc}")
        return 2

    win = MainWindow(base, hs["token"], proc)
    win.show()
    code = app.exec()
    if proc.poll() is None:
        proc.kill()
    return code


def main(argv: list[str] | None = None) -> int:
    """Single entry point: ``--server`` runs the backend, otherwise the GUI shell."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--dragwin" in args:
        desktop_dir = str(Path(__file__).resolve().parent)
        if desktop_dir not in sys.path:
            sys.path.insert(0, desktop_dir)
        from dragwin import main as dragwin_main

        return dragwin_main(args[args.index("--dragwin") + 1:])
    if "--server" in args:
        from backend.__main__ import main as backend_main

        return backend_main(args)

    try:
        return _run_shell(args)
    except Exception:
        import traceback

        crash = Path(tempfile.gettempdir()) / "bitssh-shell-crash.log"
        crash.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            from PyQt6.QtWidgets import QApplication as _App, QMessageBox as _Box

            _app = _App.instance() or _App([])
            _Box.critical(None, "bitssh", f"startup failed:\n{crash}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
