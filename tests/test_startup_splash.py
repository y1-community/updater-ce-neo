"""The Windows launch card stays compact, legible and correctly named."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src import config
from src.ui import splash


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_startup_splash_is_compact_and_credits_the_maintainer(qapp):
    assert splash._CARD_W <= 400
    assert splash._CARD_H <= 170
    assert splash._CREDIT == "Made by Ryan Specter"

    card = splash._StartupSplash("Getting ready…", title="Innioasis Updater")
    try:
        assert card.windowType() == Qt.WindowType.SplashScreen
        flags = card.windowFlags()
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.FramelessWindowHint
        # Supporting copy has to stay readable next to the larger title.
        assert card._status_font.pixelSize() >= 14
        assert card._credit_font.pixelSize() >= 12
    finally:
        card.deleteLater()


def test_launch_card_names_the_product(monkeypatch):
    monkeypatch.setattr(config, "is_mediatek_installer", lambda: False)
    assert splash._DEFAULT_TITLE == "Innioasis Updater"
    assert splash._splash_title(portable_fw=False) == "Innioasis Updater"


def test_portable_launch_names_the_firmware(monkeypatch):
    monkeypatch.setattr(config, "is_mediatek_installer", lambda: False)
    assert splash._splash_title(portable_fw=True, firmware_name="Rockbox") == "Rockbox"
    # Updater CE says software; only the MediaTek Installer says firmware.
    assert splash._splash_title(portable_fw=True, firmware_name="  ") == "Install Software"
    assert splash._splash_title(portable_fw=True) == "Install Software"
    assert splash._splash_title(portable_fw=True) != splash._DEFAULT_TITLE


def test_mediatek_installer_keeps_its_own_name(monkeypatch):
    monkeypatch.setattr(config, "is_mediatek_installer", lambda: True)
    title = splash._splash_title(portable_fw=False)
    assert title == config.get_app_name()
    assert title != splash._DEFAULT_TITLE


def test_subtitle_repeats_only_the_version(qapp):
    card = splash._StartupSplash("Getting ready…", title="Innioasis Updater")
    try:
        assert card._title == "Innioasis Updater"
        version = splash._version()
        assert card._subtitle == (f"v{version}" if version else "")
    finally:
        card.deleteLater()


def test_portable_subtitle_is_the_package_version(qapp):
    assert splash._splash_subtitle(True, "3.15") == "v3.15"
    assert splash._splash_subtitle(True, "v3.15") == "v3.15"
    # No package version: no app version pretending to be one.
    assert splash._splash_subtitle(True, "") == ""

    card = splash._StartupSplash(
        "Getting ready to install your firmware…",
        title="Rockbox",
        subtitle=splash._splash_subtitle(True, "3.15"),
    )
    try:
        assert card._title == "Rockbox"
        assert card._subtitle == "v3.15"
    finally:
        card.deleteLater()


def test_portable_status_asks_to_install_the_software():
    from src import i18n

    previous = i18n.current_language()
    i18n.translator().set_language("en")
    try:
        assert i18n.tr("startup_splash_firmware") == (
            "Getting ready to install your software…"
        )
        assert i18n.tr("startup_splash_install_firmware") == "Install Software"
    finally:
        i18n.translator().set_language(previous)
