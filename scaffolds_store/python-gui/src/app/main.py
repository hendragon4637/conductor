from __future__ import annotations

import sys
from typing import NoReturn

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    if "--smoke" in sys.argv:
        sys.exit(0)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
