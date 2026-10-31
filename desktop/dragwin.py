from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt, QUrl, QMimeData
from PyQt6.QtGui import QDrag, QGuiApplication
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QListWidget, QListWidgetItem


def remove_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


class DragList(QListWidget):
    """Wayland-native drag list, launched by the xcb main window.

    QtWebEngine renders badly under native Wayland on some drivers, while the
    Mutter X11->Wayland DnD bridge acknowledges drops without copying files.
    Keeping the main UI on xcb and this popup on Wayland gives both stable
    rendering and working drops onto Wayland desktops/file managers.
    """

    def __init__(self, entries: list[dict], root: Path | None, list_file: Path) -> None:
        super().__init__()
        self.setWindowTitle("bitssh drag-out")
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for entry in entries:
            path = Path(str(entry.get("path", "")))
            item = QListWidgetItem(path.name + ("/" if path.is_dir() else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.addItem(item)
        self.resize(320, max(60, 28 * len(entries) + 20))
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())
        self.show()
        self._root = root
        self._list_file = list_file

    def mimeData(self, items) -> QMimeData:  # noqa: ANN001
        urls = [
            QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))
            for item in items
            if item.data(Qt.ItemDataRole.UserRole)
        ]
        mime = QMimeData()
        mime.setUrls(urls)
        if urls:
            payload = "copy\n" + "\n".join(url.toString() for url in urls)
            mime.setData("x-special/gnome-copied-files", payload.encode("utf-8"))
        return mime

    def startDrag(self, supported_actions) -> None:  # noqa: ANN001
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        row = self.visualItemRect(items[0])
        if row.isValid():
            pixmap = self.viewport().grab(row)
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(12, max(4, pixmap.height() // 2)))
        drag.exec(Qt.DropAction.CopyAction)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        remove_path(self._root)
        remove_path(self._list_file)
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bitssh-dragwin")
    parser.add_argument("--list", required=True)
    parser.add_argument("--root")
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args(argv)

    list_file = Path(args.list)
    entries = json.loads(list_file.read_text(encoding="utf-8"))
    app = QApplication(sys.argv)
    window = DragList(entries, Path(args.root) if args.root else None, list_file)

    if args.parent_pid:
        def watch() -> None:
            while True:
                time.sleep(2.0)
                if os.getppid() != args.parent_pid:
                    app.quit()
                    return

        threading.Thread(target=watch, daemon=True).start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
