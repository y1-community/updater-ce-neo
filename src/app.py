#!/usr/bin/env python3
"""Neo updater entry point — InniUpdaterChin interface + online catalogue +
donations modal (see ASSESSMENT.md for the port map)."""

import logging
import sys

if __package__ is None or __package__ == "":
    import os
    from pathlib import Path

    _repo_root = str(Path(__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    import src
    __package__ = "src"

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import paths


def _configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Also write to a per-user log file so frozen GUI builds (no console) keep
    # a diagnosable trail (e.g. the real exception behind an INTERNAL_ERROR).
    try:
        from .downloads import downloads_dir

        log_path = downloads_dir().parent / "updater.log"
        fh = logging.FileHandler(str(log_path), encoding="utf-8", delay=True)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass
    # Mirror logging records (backend traces, mtkclient's [LIB] lines, errors)
    # into the diagnostics buffers so the in-app log viewer holds them too.
    try:
        from .diagnostics import install_log_capture

        install_log_capture()
    except Exception:
        pass
    try:
        from .diagnostics import guard_broken_stream_handlers

        guard_broken_stream_handlers()
    except Exception:
        pass


def main():
    _configure_logging()

    # Say which front end this is, once: a frozen build carries its brand
    # inside, so this is the one line that tells support which app a log came
    # from (and confirms the packaging baked the right one).
    from . import config

    logging.getLogger("innioasis.startup").info(
        "Starting %s v%s (brand: %s)",
        config.get_app_name(),
        config.APP_VERSION,
        config.build_brand() or "updater_ce",
    )

    if len(sys.argv) >= 4 and sys.argv[1] == "--flash-cli":
        import os
        import signal
        import time
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication
        from .flash_service import FlashWorker

        cli_app = QCoreApplication(sys.argv)
        extract_dir = sys.argv[2]
        scatter_file = sys.argv[3]
        scatter_platform = sys.argv[4] if len(sys.argv) >= 5 else ""
        package_path = sys.argv[5] if len(sys.argv) >= 6 else ""
        model = sys.argv[6] if len(sys.argv) >= 7 else ""

        worker = FlashWorker(
            package_path=package_path or "dummy.zip",
            method="mtk",
            pre_extracted_dir=extract_dir,
            model=model,
        )
        worker.progress.connect(lambda x: print(f"[PROGRESS] {x}", flush=True))
        worker.step_changed.connect(lambda x: print(f"[STEP] {x}", flush=True))
        worker.log_message.connect(lambda x: print(f"[LOG] {x}", flush=True))
        worker.action_changed.connect(lambda x: print(f"[ACTION] {x}", flush=True))

        def on_finished(ok, msg):
            print(f"[RESULT] {int(ok)} {msg}", flush=True)
            sys.exit(0 if ok else 1)

        worker.finished.connect(on_finished)

        def handle_sigterm(signum=None, frame=None):
            worker.cancel()
            time.sleep(0.5)
            os._exit(1)

        if os.name != "nt":
            signal.signal(signal.SIGTERM, handle_sigterm)
            signal.signal(signal.SIGINT, handle_sigterm)

        try:
            worker._flash_via_mtkclient_core(Path(extract_dir), Path(scatter_file), scatter_platform)
        except BaseException as e:
            print(f"[RESULT] 0 {e}", flush=True)
            sys.exit(1)
        return

    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)
    # Names the app to the desktop (menu entry matching, WM_CLASS, macOS Dock):
    # the packaged front end tells the environment which one it is. Read before
    # the language is restored, so the name is the stable English one.
    app.setApplicationName(config.get_app_name())
    app.setOrganizationName("innioasis")

    import sys as _sys
    if _sys.platform != "darwin":
        # On Windows and Linux, set the window/taskbar icon explicitly.
        # On macOS, omit app.setWindowIcon: setting an application icon through
        # Python/Qt overrides NSApplication's bundle icon and breaks macOS's
        # native dock icon decoration (automatic coloration, dark mode tinting,
        # and squircle dynamic lighting).
        icon = paths.RESOURCES_DIR / "icon.ico"
        if not icon.exists():
            icon = paths.RESOURCES_DIR / "icon.png"
        if icon.exists():
            app.setWindowIcon(QIcon(str(icon)))

    from .i18n import translator
    from .ui.dark import ThemeWatcher, apply_theme, is_dark
    from .ui.glass import (
        apply_glass,
        apply_windows_dark_titlebar,
        configure_traffic_lights,
        prepare_window_for_glass,
    )
    from .ui.main_window import MainWindow

    # Follow the host appearance. Older macOS uses vibrant light or vibrant
    # dark to match; Tahoe and Golden Gate stay on the Liquid Glass path.
    apply_theme(app, force_dark=None)

    # Default language: follow the system, fall back to English.
    import locale

    try:
        lang = locale.getlocale()[0] or ""
        if lang.lower().startswith("zh"):
            translator().set_language("zh-CN")
        else:
            translator().set_language("en")
    except Exception:
        translator().set_language("en")

    window = MainWindow()
    prepare_window_for_glass(window)
    window.show()
    apply_glass(window, dark=is_dark())
    configure_traffic_lights(window, x_offset=18)
    apply_windows_dark_titlebar(window, is_dark())

    # Match host appearance live: light/dark switches, desktop accent colours,
    # system font changes — the same way native apps follow the OS. The window
    # normally brings the watcher with it (and registers it on the app); one is
    # installed here only when it did not, so the host is never watched twice.
    theme_watcher = getattr(app, "_theme_watcher", None)
    if theme_watcher is None:
        def _on_theme_applied():
            apply_windows_dark_titlebar(window, is_dark())
            apply_glass(window)
            configure_traffic_lights(window)

        theme_watcher = ThemeWatcher(app, on_apply=_on_theme_applied)
        theme_watcher.install()
        # Keep a reference for the lifetime of the app; the watcher owns timers
        # and an event filter on the QApplication itself.
        app._theme_watcher = theme_watcher

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
