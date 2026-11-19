import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

from src.i18n import tr
from src.ui.main_window import MainWindow, _PAGE_SELECT, _PAGE_SETTINGS, _PAGE_SUPPORT


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


class _NoReleaseWorker(QObject):
    """Stands in for the version lookup so the form opens without a network."""

    loaded = Signal(object, object)
    finished = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__()

    def start(self):
        pass

    def requestInterruption(self):
        pass

    def wait(self, *_args):
        return True


def _settings():
    return QSettings("innioasis", "updater")


def _restore(settings, saved):
    if saved is None:
        settings.remove("onboarding_completed")
    else:
        settings.setValue("onboarding_completed", saved)
    settings.sync()


def test_first_launch_opens_the_journey_and_done_returns_to_the_catalogue(
    qapp, monkeypatch
):
    """A player installed before this app existed is recorded here."""
    from src import device_tracking
    from src.ui import settings_page as settings_ui

    monkeypatch.setattr(settings_ui, "_AddDeviceReleasesWorker", _NoReleaseWorker)
    settings = _settings()
    saved = settings.value("onboarding_completed")
    settings.remove("onboarding_completed")
    monkeypatch.setattr(device_tracking, "get_all_device_installs", lambda *a, **k: {})

    win = MainWindow()
    page = win._settings_page
    try:
        assert win.start_onboarding_if_needed() is True
        assert win._onboarding_active is True
        assert page._onboarding is True
        assert win._stack.currentIndex() == _PAGE_SETTINGS

        # The journey asks the question and shows where to answer it: the card
        # is alone and its form is already open.
        assert not page._reminders_card.isHidden()
        assert not page._add_device_panel.isHidden()
        assert page._add_device_model.count() > 0

        # One heading, not two: the card title is the call to action, and the
        # ordinary coverage line stands down. There is no master switch to
        # hide: reminders are per player.
        assert page._reminders_card.title() == tr("onboarding_title")
        assert not hasattr(page, "_cb_reminders")
        assert page._reminders_desc.isHidden()
        # Done is for a player that was logged; until then the way out is the
        # de-emphasised skip, and the rail waits on the journey.
        assert page._onboarding_done.text() == tr("onboarding_done")
        assert not page._onboarding_done.isEnabled()
        assert page._onboarding_skip.text() == tr("onboarding_skip")
        assert page._onboarding_skip.isFlat()
        # Done and "Add another player" wait for the first player to be logged,
        # so the form is the only thing in front of someone adding one device.
        assert page._onboarding_actions.isHidden()
        assert page._onboarding_more.isHidden()
        assert win._settings_btn.text() == tr("nav_welcome")
        assert not win._nav_buttons["nav_select_package"][0].isEnabled()
        # A row that is still clickable opens nothing while the journey runs.
        win._nav_to_page(_PAGE_SUPPORT)
        assert win._stack.currentIndex() == _PAGE_SETTINGS

        # A player on record turns the skip into Done and offers another round.
        monkeypatch.setattr(
            device_tracking,
            "get_all_device_installs",
            lambda *a, **k: {"Y2": {"model": "Y2", "software_name": "Reborn"}},
        )
        page.refresh_settings()
        qapp.processEvents()
        assert page._onboarding_done.isEnabled()
        assert not page._onboarding_actions.isHidden()
        assert not page._onboarding_more.isHidden()

        page._onboarding_done.click()
        qapp.processEvents()

        assert win._onboarding_active is False
        assert page._onboarding is False
        assert win._stack.currentIndex() == _PAGE_SELECT
        # The card goes back to being the ordinary reminders card, and the
        # rail gets its own names and rows back.
        assert page._reminders_card.title() == tr("settings_reminders_group")
        assert win._settings_btn.text() == tr("nav_settings")
        assert win._nav_buttons["nav_select_package"][0].isEnabled()
        assert settings.value("onboarding_completed", False, type=bool) is True
        # The journey is a one-time thing: the next launch is the catalogue.
        assert win.start_onboarding_if_needed() is False
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        _restore(settings, saved)


def test_a_tracked_player_skips_the_welcome(qapp, monkeypatch):
    """Nothing to add by hand once an install is on record."""
    from src import device_tracking

    settings = _settings()
    saved = settings.value("onboarding_completed")
    settings.remove("onboarding_completed")
    monkeypatch.setattr(
        device_tracking,
        "get_all_device_installs",
        lambda *a, **k: {"Y2": {"model": "Y2", "software_name": "Reborn"}},
    )

    win = MainWindow()
    try:
        assert win.start_onboarding_if_needed() is False
        assert win._onboarding_active is False
        assert win._settings_page._onboarding is False
        assert win._stack.currentIndex() == _PAGE_SELECT
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        _restore(settings, saved)


def test_skipping_the_welcome_unlocks_the_window(qapp, monkeypatch):
    """Skip is enough: the journey ends and the catalogue opens."""
    from src import device_tracking
    from src.ui import settings_page as settings_ui

    monkeypatch.setattr(settings_ui, "_AddDeviceReleasesWorker", _NoReleaseWorker)
    settings = _settings()
    saved = settings.value("onboarding_completed")
    settings.remove("onboarding_completed")
    monkeypatch.setattr(device_tracking, "get_all_device_installs", lambda *a, **k: {})

    win = MainWindow()
    page = win._settings_page
    try:
        assert win.start_onboarding_if_needed() is True
        # The row renamed Welcome keeps the journey up; it does not hand over
        # the full Settings column.
        win._settings_btn.click()
        qapp.processEvents()
        assert page._onboarding is True
        assert win._stack.currentIndex() == _PAGE_SETTINGS
        # Done is not a way out before a player is on record.
        assert not page._onboarding_done.isEnabled()

        page._onboarding_skip.click()
        qapp.processEvents()

        assert win._onboarding_active is False
        assert page._onboarding is False
        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win._settings_btn.text() == tr("nav_settings")
        assert win._nav_buttons["nav_select_package"][0].isEnabled()
        assert settings.value("onboarding_completed", False, type=bool) is True
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        _restore(settings, saved)


def test_reset_triggers_onboarding(qapp, monkeypatch, tmp_path):
    """R is a fresh launch: Welcome, the completed flag cleared, nothing else."""
    from src import catalog, downloads, manifest

    # The reset clears every QSettings group and empties the real caches, so
    # the host's answers are saved first and redirected to a scratch folder.
    groups = (
        QSettings("innioasis", "updater"),
        QSettings("Innioasis", "UpdaterCE"),
        QSettings("Innioasis", "Innioasis Updater"),
    )
    saved = [{key: store.value(key) for key in store.allKeys()} for store in groups]
    monkeypatch.setattr(downloads, "clear_firmware_cache", lambda: None)
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path / "downloads")
    monkeypatch.setattr(catalog, "_app_cache_dir", lambda: tmp_path / "app_cache")
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path / "manifest_cache")

    s = groups[0]
    s.setValue("onboarding_completed", True)

    win = MainWindow()
    try:
        # Onboarding should NOT be active initially
        assert win._onboarding_active is False

        # Reset the app
        win.reset_all_configuration_and_caches()
        qapp.processEvents()

        # Onboarding SHOULD be active now
        assert win._onboarding_active is True
        assert win._settings_page._onboarding is True
        # And it is what the user is looking at: a fresh launch opens Welcome,
        # so the reset must not finish by navigating on to the catalogue.
        assert win._stack.currentIndex() == _PAGE_SETTINGS
        # The next launch reads this flag: the journey is on screen, not done.
        assert win.settings.value("onboarding_completed", True, type=bool) is False
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
        for store, values in zip(groups, saved):
            store.clear()
            for key, value in values.items():
                store.setValue(key, value)
