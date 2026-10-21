"""Ponto de entrada para execucao da interface do MARK via python -m mark.ui."""

import sys
from PyQt6.QtWidgets import QApplication
from mark.ui.main_window import MainWindow

MarkWindow = MainWindow


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


__all__ = ["MainWindow", "MarkWindow", "QApplication", "main"]

if __name__ == "__main__":
    sys.exit(main())
