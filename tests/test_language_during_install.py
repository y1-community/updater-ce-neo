"""Language stays changeable during install / prep / themes stages.

A reader who starts the app in one language and then finds their own is
supported must be able to switch while an install is guiding them — the
selector used to disappear with the rest of the sidebar exactly when the
prompts they need to follow and answer were on screen. The switch drives the
same live retranslation as everywhere else, so the flow copy follows at once.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from src.i18n import translator, tr
from src.ui.main_window import MainWindow


def _window():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    app.processEvents()
    return app, win


def test_language_selector_stays_visible_during_install_stages():
    app, win = _window()
    try:
        win._apply_install_ui_state(True)
        app.processEvents()
        # The rest of the sidebar collapses…
        assert not win._settings_btn.isVisible()
        # …but the language row does not: the prompts on screen need it.
        assert win._lang_combo.isVisible()
        assert win._lang_label.isVisible()
    finally:
        win._apply_install_ui_state(False)
        win.close()
        app.processEvents()


def test_switching_language_during_install_retranslates_the_flow():
    app, win = _window()
    start = translator().lang
    try:
        win._apply_install_ui_state(True)
        win._flash_page.update_step("step_write")
        app.processEvents()
        english_title = win._flash_page._guide_title.text()

        # Switch to another shipped language through the real handler, with
        # the install UI on screen.
        idx = win._lang_combo.findData("ja")
        assert idx >= 0, "Japanese must be one of the offered languages"
        win._on_language_changed(idx)
        app.processEvents()

        assert translator().lang == "ja"
        assert win._flash_page._guide_title.text() == tr("flash_guide_title")
        if tr("flash_guide_title") != english_title:
            assert win._flash_page._guide_title.text() != english_title
        # The step line follows too (install instructions in the new language).
        assert win._flash_page._step_label.text() == tr("step_write")
    finally:
        idx = win._lang_combo.findData(start)
        if idx >= 0:
            win._on_language_changed(idx)
        app.processEvents()
        win.close()
        app.processEvents()
