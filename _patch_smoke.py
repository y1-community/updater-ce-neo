"""One-off patch: bring the two method-switch smoke tests to the new contract."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
ST = "tests/smoke_test.py"


def sub(rel, old, new, count=1):
    p = ROOT / rel
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    o = old.replace("\n", nl)
    n = new.replace("\n", nl)
    found = raw.count(o)
    if found != count:
        raise SystemExit(
            f"{rel}: expected {count} match(es), found {found} for:\n{o[:400]}"
        )
    p.write_bytes(raw.replace(o, n, count).encode("utf-8"))
    print(f"patched {rel}")


sub(
    ST,
    """def test_flash_method_switch():
    \"\"\"Switching the install method in Settings while waiting restarts the run
    with the new backend, and the choice is persisted. Legacy \"auto\" resolves
    to the platform default (SP Flash Tool) rather than its own mode.\"\"\"
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    from src import paths

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    calls = []
    w.service.start_flash = lambda pkg, pre=\"\", method=\"auto\", **kw: calls.append((pkg, method))
    w.service.start_device_monitor = lambda: None
    w.settings.setValue(\"flash_method\", \"auto\")
    w._flash_method = \"auto\"

    w._on_package_selected(\"C:/fake/rom.zip\", \"Rockbox (Y1)\", \"Y1\")
    exp_method = \"mtk\" if paths.IS_MAC else \"sp\"
    assert w._flash_page.current_method() == exp_method, f\"legacy auto -> {exp_method}\"

    if not paths.IS_MAC:
        # User switches to MTKClient while the backend searches (Windows/Linux).
        w._settings_page.reveal_advanced_methods()
        combo = w._settings_page._method_combo
        combo.setCurrentIndex(combo.findData(\"mtk\"))
        assert w._settings_page.current_method() == \"mtk\"
        assert calls[-1] == (\"C:/fake/rom.zip\", \"mtk\"), \"backend restarted with new method\"
        assert w._flash_page.current_method() == \"mtk\", \"flash page reflects the new method\"
        assert w.settings.value(\"flash_method\") == \"mtk\"
    else:
        # On macOS, MTKClient is the only engine; verify it is the active method.
        assert w._settings_page.current_method() == \"mtk\"
        assert w._flash_page.current_method() == \"mtk\"

    w.settings.setValue(\"flash_method\", \"auto\")  # leave machine state clean
    w.close()
    app.processEvents()
""",
    """def test_flash_method_switch():
    \"\"\"Switching the install method in Settings while waiting re-arms the
    power-off step: the new backend's command must not start until the user
    confirms the player is unplugged and powered off, and the choice is
    persisted. Legacy \"auto\" resolves to the platform default (SP Flash
    Tool) rather than its own mode.\"\"\"
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    from src import paths

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    calls = []
    w.service.start_flash = lambda pkg, pre=\"\", method=\"auto\", **kw: calls.append((pkg, method))
    w.service.start_device_monitor = lambda: None
    w.service.cancel_flash = lambda: None
    w.service.stop_device_monitor = lambda: None
    w._ensure_elevated_for_install = lambda: True
    w.settings.setValue(\"flash_method\", \"auto\")
    w._flash_method = \"auto\"

    w._on_package_selected(\"C:/fake/rom.zip\", \"Rockbox (Y1)\", \"Y1\")
    exp_method = \"mtk\" if paths.IS_MAC else \"sp\"
    assert w._flash_page.current_method() == exp_method, f\"legacy auto -> {exp_method}\"

    if not paths.IS_MAC:
        # User switches to MTKClient while the backend searches (Windows/Linux).
        w._settings_page.reveal_advanced_methods()
        combo = w._settings_page._method_combo
        combo.setCurrentIndex(combo.findData(\"mtk\"))
        assert w._settings_page.current_method() == \"mtk\"
        assert w._flash_page.current_method() == \"mtk\", \"flash page reflects the new method\"
        assert w.settings.value(\"flash_method\") == \"mtk\"
        # The switch itself starts nothing: the power-off step comes first, so
        # a tool is never left searching before the user is ready.
        assert \"mtk\" not in [call[1] for call in calls], (
            \"the new backend must wait for the power-off confirmation\"
        )
        assert w._power_off_prompt is True
        w._on_power_off_continue()
        assert calls[-1] == (\"C:/fake/rom.zip\", \"mtk\"), \"Continue starts the new backend\"
    else:
        # On macOS, MTKClient is the only engine; verify it is the active method.
        assert w._settings_page.current_method() == \"mtk\"
        assert w._flash_page.current_method() == \"mtk\"

    w.settings.setValue(\"flash_method\", \"auto\")  # leave machine state clean
    w.close()
    app.processEvents()
""",
)

sub(
    ST,
    """        page.set_method(METHOD_MTK_MAC)
        assert page._method_note.text() == tr(\"flash_method_note_mtk_mac\")
        assert not page._method_note.isHidden()
        page.set_method(METHOD_SP)
""",
    """        # \"MTKClient (Mac)\" is the simulated-macOS debug entry, not a sticky
        # backend: outside the macOS flow it resolves to the platform default,
        # so the card shows the SP note for it.
        page.set_method(METHOD_MTK_MAC)
        assert page._method_note.text() == tr(\"flash_method_note_sp\")
        assert not page._method_note.isHidden()
        assert tr(\"flash_method_note_mtk_mac\"), \"the mac note string must exist\"
        page.set_method(METHOD_SP)
""",
)

print("smoke tests patched")
