import sys

from PySide6.QtWidgets import QApplication

from __PKG__.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    if "--smoke" in sys.argv:          # CI launch check: construct, then exit clean
        return 0
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
