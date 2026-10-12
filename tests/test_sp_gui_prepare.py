"""The SP Flash Tool GUI route prepares firmware instead of failing.

A run that was interrupted leaves an extract with its scatter and some of its
images, which is exactly what "images referenced in scatter file were not
found" is reported from. That used to end in a dialog; these tests cover what
happens instead: the cache is treated as absent, the release is downloaded (or
the archive extracted again) with the usual progress screen, every session this
app started is stopped, and only then is the tool's own window opened.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

SCATTER_BODY = "file_name: fat_sparse.img\n"


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


@pytest.fixture()
def window():
    """A real window, on the offscreen platform.

    The paths under test reach no dialog of their own: the point of every test
    here is that the firmware is prepared *instead of* being reported, so a
    dialog would leave the state the assertions look for unset.
    """
    from src.ui import main_window as mw

    _app()  # Qt aborts the process if a widget is built without one
    w = mw.MainWindow()
    try:
        yield w
    finally:
        w.close()
        _app().processEvents()


def _incomplete_extract() -> tempfile.TemporaryDirectory:
    """A temp extract whose scatter names an image that is not beside it."""
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "MT6582_Android_scatter.txt").write_text(SCATTER_BODY, encoding="utf-8")
    return td


def test_a_half_written_cache_is_not_offered(window, monkeypatch):
    """The incomplete extract resolves as "no cache", so a fresh one is used."""
    from src import device_tracking

    with _incomplete_extract() as td:
        root = Path(td)
        scatter = root / "MT6582_Android_scatter.txt"
        monkeypatch.setattr(
            device_tracking, "get_latest_package", lambda settings: {"model": "Y2"}
        )
        monkeypatch.setattr(
            "src.sp_flash_gui.cached_install_firmware",
            lambda latest: (scatter, root),
        )
        assert window._cached_gui_package() is None
        assert "fat_sparse.img" in window._gui_cache_reason

        # With the image present the same extract is a cache again.
        (root / "fat_sparse.img").write_bytes(b"img")
        cached = window._cached_gui_package()
        assert cached is not None and cached["kind"] == "cache"
        assert Path(cached["scatter"]).is_file()


def test_an_incomplete_package_is_prepared_instead_of_refused(window, monkeypatch, tmp_path):
    """The SP GUI install method re-prepares the selected package, no dialog."""
    from src import sp_flash_gui

    package = tmp_path / "rom_y2.zip"
    package.write_bytes(b"PK\x03\x04")
    prepared = []

    monkeypatch.setattr(sp_flash_gui, "is_sp_flash_gui_supported", lambda: True)
    monkeypatch.setattr(
        sp_flash_gui,
        "check_cached_firmware_readiness",
        lambda **kwargs: (
            False,
            None,
            None,
            "Images referenced in scatter file were not found: fat_sparse.img",
        ),
    )
    monkeypatch.setattr(
        window._select_page,
        "begin_external_prepare",
        lambda target, callback: prepared.append(target) or True,
    )
    window._package_path = str(package)
    window._package_model = "Y2"
    window._package_name = "Original Software 3.2.1 for Y2"

    window._begin_sp_gui_install()

    assert prepared and prepared[0]["kind"] == "local"
    assert prepared[0]["path"] == str(package)
    assert window._gui_after_prepare == "launch"
    assert window._gui_launch_when_ready is True
    # The progress screen is up: the download replaced the old error dialog.
    assert window._download_active is True
    assert window._gui_handoff_prepare is True


def test_the_desktop_tool_is_opened_only_after_the_sessions_stop(window, monkeypatch, tmp_path):
    """Console/MTKClient sessions stop before history.ini is staged and the GUI opens."""
    from src import sp_flash_gui
    from src.ui.main_window import _PAGE_SELECT

    scatter = tmp_path / "scatter.txt"
    scatter.write_text("scatter\n", encoding="utf-8")
    order = []
    monkeypatch.setattr(
        window.service,
        "terminate_install_sessions",
        lambda *_a, **_k: order.append("sessions") or "stopped",
    )
    monkeypatch.setattr(
        sp_flash_gui,
        "launch_sp_flash_tool_gui",
        lambda **kwargs: order.append("launch") or (True, "ok"),
    )
    window._gui_ready = (scatter, tmp_path, "Y2")
    window._gui_handoff_ready = True
    window._gui_handoff_prepare = True

    window._complete_gui_handoff()

    assert order == ["sessions", "launch"], order
    assert window._stack.currentIndex() == _PAGE_SELECT


def test_sessions_are_reported_to_the_log(window, monkeypatch):
    """terminate_install_sessions()' report lands in the run log."""
    logged = []
    monkeypatch.setattr(
        window.service, "terminate_install_sessions", lambda *_a, **_k: "Stopped 2 console runs."
    )
    monkeypatch.setattr(window, "_append_log", logged.append)
    window._terminate_install_sessions()
    assert logged == ["Stopped 2 console runs."]
