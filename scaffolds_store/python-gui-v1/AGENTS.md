# Python GUI Conventions

## Framework & Packaging
- PySide6 (Qt6) for GUI; PyInstaller for packaging (onedir + windowed)
- No cross-compile — build per target OS
- Resources bundled via `--add-data`; accessed with `sys._MEIPASS`-aware helper

## Project Structure
- `src/__PKG__/main.py` — entry: `main()` → QApplication + MainWindow; `--smoke` flag for CI
- `src/__PKG__/ui/` — windows, dialogs, widgets (one class per file)
- `src/__PKG__/core/` — business logic: NO PySide6 imports (testable headless)
- `src/__PKG__/resources/` — icons, assets loaded via importlib.resources
- `tests/` — pytest on core/ logic (headless)
- `<app>.spec` — committed PyInstaller spec (reproducible builds)

## Style Rules
- core/ never imports PySide6 → unit-testable without a display
- Long work off UI thread: QThreadPool/QRunnable
- Errors shown as QMessageBox dialogs, never printed tracebacks
- App state in OS user dir via QStandardPaths
- Full type hints; ruff clean

## Packaging Rules
- `pyinstaller --noconfirm app.spec` — spec committed and versioned
- Packaged app must launch with ZERO env setup (no venv, no pip)

## Completion Check
Before marking a node complete, run `bash gates.sh` from the workspace root.
The script must exit 0 and print "ALL GATES GREEN".
