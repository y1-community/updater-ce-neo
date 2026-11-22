"""Regression tests for download and install cancellation prompt visibility & flow."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette


@pytest.fixture
def window_no_net(qapp, monkeypatch):
    """MainWindow with catalogue network fetches stubbed out for deterministic UI tests."""
    from src import catalog, manifest
    from src.ui.main_window import MainWindow
    from src.ui.select_page import SelectPackagePage

    catalog.LIVE_CATALOG = manifest.load_snapshot_manifest()
    monkeypatch.setattr(SelectPackagePage, "_refresh_releases", lambda self, *a, **kw: None)
    monkeypatch.setattr(SelectPackagePage, "_refresh_manifest", lambda self, *a, **kw: None)

    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        for worker in list(getattr(win._select_page, "_active_workers", ())):
            try:
                worker.requestInterruption()
                worker.wait(500)
            except Exception:
                pass
        win.close()
        qapp.processEvents()


def _assert_stop_prompt_unclipped(fp, head):
    """Icon, question text, and both action buttons must sit strictly within head bounds."""
    assert fp._stop_open is True
    assert fp._stop_question.isVisible()
    assert fp._stop_actions.isVisible()
    assert fp._stop_back.isVisible()
    assert fp._stop_continue.isVisible()

    head_h = head.height()
    assert head_h >= 40
    for name, widget in (
        ("icon", head.icon),
        ("question", fp._stop_question),
        ("actions", fp._stop_actions),
    ):
        g = widget.geometry()
        assert g.top() >= 0, f"{name} clipped at top: {g} in head_h={head_h}"
        assert g.bottom() < head_h, f"{name} clipped at bottom: {g} in head_h={head_h}"


def test_download_cancel_prompt_visible_unclipped_and_returns_to_select(window_no_net, qapp):
    win = window_no_net
    fp = win._flash_page

    win._on_download_started("Rockbox 0.6.1 for Y1", "Y1")
    win._on_download_progress(35, "Downloading 35%")
    qapp.processEvents()

    assert fp._download_cancel_btn.isVisible() and fp._download_cancel_btn.isEnabled()

    # Click Cancel -> stop prompt must be visible and completely unclipped
    fp._download_cancel_btn.click()
    qapp.processEvents()
    _assert_stop_prompt_unclipped(fp, fp._download_head)

    # Progress tick while prompt is open must keep it visible and unclipped
    win._on_download_progress(45, "Downloading 45%")
    qapp.processEvents()
    _assert_stop_prompt_unclipped(fp, fp._download_head)

    # Back arrow dismisses prompt and resumes download
    fp._stop_back.click()
    qapp.processEvents()
    assert fp._stop_open is False
    assert win._stack.currentIndex() == 1
    assert fp._download_progress.isVisible()
    assert fp._download_cancel_btn.isVisible()

    # Click Cancel again -> Confirm X cancels download and returns to Select Software
    fp._download_cancel_btn.click()
    qapp.processEvents()
    _assert_stop_prompt_unclipped(fp, fp._download_head)

    fp._stop_continue.click()
    qapp.processEvents()
    assert fp._stop_open is False
    assert win._stack.currentIndex() == 0
    assert not win._install_run_active()

    # A late queued download_finished signal from the cancelled worker must not hijack Select Software
    win._on_download_finished(True, "/tmp/late_cancelled.zip")
    qapp.processEvents()
    assert win._stack.currentIndex() == 0
    assert not win._install_run_active()


def test_flashing_cancel_button_and_prompt_visible_and_returns_to_select(window_no_net, qapp):
    win = window_no_net
    fp = win._flash_page

    fp.clear_install_failure()
    fp.begin_run()
    fp.set_package_name("Rockbox 0.6.1 for Y1")
    fp.show_flashing()
    fp.update_progress(42)
    win._nav_to_page(1)
    qapp.processEvents()

    # show_flashing() and reset_for_new_run() must keep the cancel button visible
    assert fp._cancel_btn.isVisible() and fp._cancel_btn.isEnabled()

    fp._cancel_btn.click()
    qapp.processEvents()
    _assert_stop_prompt_unclipped(fp, fp._flash_head)

    # Back dismisses and restores progress bar + cancel button
    fp._stop_back.click()
    qapp.processEvents()
    assert fp._stop_open is False
    assert fp._progress_bar.isVisible()
    assert fp._cancel_btn.isVisible()

    # Confirm cancel returns cleanly to Select Software
    fp._cancel_btn.click()
    qapp.processEvents()
    fp._stop_continue.click()
    qapp.processEvents()
    assert fp._stop_open is False
    assert win._stack.currentIndex() == 0
    assert not win._install_run_active()


def test_preparing_and_connect_cancel_prompts_visible_and_return_to_select(window_no_net, qapp):
    win = window_no_net
    fp = win._flash_page

    # 1. Preparing stage
    win._on_prep_started("Rockbox 0.6.1 for Y1", "Y1")
    qapp.processEvents()
    assert fp._prep_cancel_btn.isVisible() and fp._prep_cancel_btn.isEnabled()
    fp._prep_cancel_btn.click()
    qapp.processEvents()
    _assert_stop_prompt_unclipped(fp, fp._prep_head)
    fp._stop_continue.click()
    qapp.processEvents()
    assert win._stack.currentIndex() == 0
    assert not win._install_run_active()

    # 2. Waiting (connect search) stage
    fp.begin_run()
    fp.set_package_name("Rockbox 0.6.1 for Y1")
    fp.show_connect_search()
    win._nav_to_page(1)
    qapp.processEvents()
    assert fp._wait_cancel_btn.isVisible() and fp._wait_cancel_btn.isEnabled()
    fp._wait_cancel_btn.click()
    qapp.processEvents()
    _assert_stop_prompt_unclipped(fp, fp._wait_head)
    fp._stop_continue.click()
    qapp.processEvents()
    assert win._stack.currentIndex() == 0
    assert not win._install_run_active()


def test_deferred_view_switch_applies_when_stop_prompt_is_dismissed(window_no_net, qapp):
    """If a download finishes while the stop prompt is open, clicking Back advances to preparing."""
    win = window_no_net
    fp = win._flash_page

    win._on_download_started("Rockbox 0.6.1 for Y1", "Y1")
    qapp.processEvents()
    fp._download_cancel_btn.click()
    qapp.processEvents()
    assert fp._stop_open is True

    # Download finishes and transitions to preparing while the user is looking at the stop prompt
    win._on_download_finished(True, "/tmp/fake.zip")
    qapp.processEvents()
    assert fp._stop_open is True
    assert fp._stack.currentWidget() is fp._downloading_view

    # User decides to continue (clicks Back) -> must advance to preparing, not stay stuck on download
    fp._stop_back.click()
    qapp.processEvents()
    assert fp._stop_open is False
    assert fp._stack.currentWidget() is fp._preparing_view


def test_long_wrapped_stop_prompt_grows_head_and_uses_live_palette(window_no_net, qapp):
    """Long/translated stop prompts grow _CardHead vertically and adopt non-default host palette text."""
    win = window_no_net
    fp = win._flash_page

    # Apply a non-default KDE-style accent windowText palette
    pal = qapp.palette()
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#12ab89"))
    qapp.setPalette(pal)

    win._on_download_started("Rockbox 0.6.1 for Y1", "Y1")
    qapp.processEvents()
    long_msg = "Are you sure you want to stop the download and close Innioasis Updater right now?"
    fp.prompt_stop(long_msg, on_continue=lambda: None)
    qapp.processEvents()

    _assert_stop_prompt_unclipped(fp, fp._download_head)
    assert "#12ab89" in fp._stop_question.styleSheet().lower()
    fp.dismiss_prompt_stop()
    qapp.processEvents()


def test_reset_after_run_clears_gui_handoff_prepare_flag(window_no_net, qapp):
    """Cancelling while _gui_handoff_prepare is set must not block returning to Select Software."""
    win = window_no_net
    win._gui_handoff_prepare = True
    win._download_active = True
    win._nav_to_page(1)
    qapp.processEvents()

    win._return_to_select_software()
    qapp.processEvents()

    assert win._gui_handoff_prepare is False
    assert not win._install_run_active()
    assert win._stack.currentIndex() == 0
