"""Ponto de entrada para execucao da interface do MARK via python -m mark.ui."""

import sys
from pathlib import Path
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from mark.ui.main_window import MainWindow

MarkWindow = MainWindow


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MARK")
    app.setApplicationDisplayName("MARK")
    app.setDesktopFileName("mark.desktop")

    root = Path(__file__).resolve().parents[3]
    icon_candidates = [
        root / "assets/mark-256.png",
        root / "assets/mark.svg",
        root / "assets/mark-desktop.svg",
    ]
    app_icon = None
    for cand in icon_candidates:
        if cand.is_file():
            app_icon = QIcon(str(cand))
            break
    if not app_icon or app_icon.isNull():
        app_icon = QIcon.fromTheme("mark")
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = MainWindow()
    window.show()
    return app.exec()


__all__ = ["MainWindow", "MarkWindow", "QApplication", "main"]

if __name__ == "__main__":
    sys.exit(main())
