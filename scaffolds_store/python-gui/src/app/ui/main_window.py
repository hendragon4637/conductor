from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QLabel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("project-name")
        self.setMinimumSize(400, 300)
        self.setCentralWidget(QLabel("Hello, world."))
