"""A notice can name an operating system and a CPU architecture.

The narrowest case the feed needs is one platform build: a Type B Y1 running a
Rockbox 0.1 tag, seen from a macOS install on Apple silicon. These tests pin
that down, along with the rule that an unreadable architecture never hides a
notice from anyone.
"""

import threading

import pytest
from PySide6.QtWidgets import QApplication

from src import notice_service
from src.notice_service import NoticeContext, normalise_arch, parse_notice_xml


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _notice(**attrs):
    extra = " ".join(f'{name}="{value}"' for name, value in attrs.items())
    xml = (
        f'<notices><notice id="n" {extra}>'
        "<title>Quirk</title><content>Only on this build</content></notice></notices>"
    )
    return parse_notice_xml(xml)[0]


def _quirk_notice():
    return _notice(
        models="Y1",
        device_type="Type B",
        firmware_repo="rockbox-y1/rockbox",
        firmware_version="0.1",
        os="macos",
        arch="arm64",
    )


def _context(**overrides):
    values = dict(
        model="Y1",
        device_type="B",
        firmware_repo="rockbox-y1/rockbox",
        firmware_version="v0.1",
    )
    values.update(overrides)
    return NoticeContext(**values)


def test_parse_reads_arch_and_os():
    notice = _notice(os="macos", arch="arm64, x86_64")

    assert notice.platforms == ("mac",)
    assert notice.arches == ("arm64", "x86_64")
    assert notice.is_contextual is True


def test_os_and_platform_are_one_axis_and_aliases_resolve():
    notice = _notice(platform="win", os="darwin")

    assert notice.platforms == ("windows", "mac")


def test_architecture_aliases_are_canonical():
    assert normalise_arch("aarch64") == "arm64"
    assert normalise_arch("AMD64") == "x86_64"
    assert normalise_arch("x86-64") == "x86_64"
    assert normalise_arch("i686") == "x86"
    assert normalise_arch("armv7l") == "armv7"
    assert normalise_arch("universal2") == "universal"
    assert normalise_arch("*") == "universal"


def test_the_narrowest_case_matches_macos_arm64(monkeypatch):
    monkeypatch.setattr(notice_service, "current_arch", lambda: "arm64")
    notice = _quirk_notice()

    assert notice.matches(_context(), platform="mac") is True


def test_the_same_notice_is_hidden_on_other_platforms(monkeypatch):
    monkeypatch.setattr(notice_service, "current_arch", lambda: "arm64")
    notice = _quirk_notice()

    assert notice.matches(_context(), platform="windows") is False
    assert notice.matches(_context(), platform="linux") is False


def test_the_same_notice_is_hidden_on_other_architectures(monkeypatch):
    monkeypatch.setattr(notice_service, "current_arch", lambda: "x86_64")
    notice = _quirk_notice()

    assert notice.matches(_context(), platform="mac") is False


def test_the_context_axes_still_narrow(monkeypatch):
    monkeypatch.setattr(notice_service, "current_arch", lambda: "arm64")
    notice = _quirk_notice()

    assert notice.matches(_context(device_type="A"), platform="mac") is False
    assert notice.matches(_context(firmware_version="0.2"), platform="mac") is False
    assert notice.matches(_context(firmware_repo="other/player"), platform="mac") is False
    assert notice.matches(_context(model="Y2"), platform="mac") is False


def test_an_unknown_architecture_never_hides_a_notice(monkeypatch):
    """A machine that cannot report its architecture still sees the notice."""
    monkeypatch.setattr(notice_service, "current_arch", lambda: "")
    notice = _quirk_notice()

    assert notice.matches(_context(), platform="mac") is True


def test_universal_matches_every_machine(monkeypatch):
    notice = _notice(arch="universal", os="macos")

    for machine in ("arm64", "x86_64", "armv7"):
        monkeypatch.setattr(notice_service, "current_arch", lambda m=machine: m)
        assert notice.matches(_context(), platform="mac") is True


def test_the_notice_list_applies_platform_and_arch(monkeypatch):
    """The list, pop-out and tray are filtered by platform and machine too."""
    notice = _quirk_notice()

    monkeypatch.setattr(notice_service, "current_arch", lambda: "arm64")
    assert notice.is_live(platform="mac") is True
    assert notice.is_live(platform="linux") is False

    monkeypatch.setattr(notice_service, "current_arch", lambda: "x86_64")
    assert notice.is_live(platform="mac") is False


def test_a_notice_with_no_arch_or_os_is_still_for_everyone(monkeypatch):
    notice = _notice(models="Y1")

    monkeypatch.setattr(notice_service, "current_arch", lambda: "armv7")
    for platform in ("mac", "windows", "linux"):
        assert notice.is_live(platform=platform) is True


def test_matching_the_machine_outranks_a_general_announcement(monkeypatch):
    monkeypatch.setattr(notice_service, "current_arch", lambda: "arm64")
    general = _notice(models="Y1")
    specific = _notice(models="Y1", arch="arm64")

    assert specific.context_score(_context(), platform="mac") > general.context_score(
        _context(), platform="mac"
    )


def test_a_background_refresh_queues_the_rotation_timer(qapp, monkeypatch):
    """A worker thread cannot start the card's timer; the sync is queued."""
    import types

    from PySide6 import QtCore

    service = notice_service.NoticeService()
    service._rotation_timer = None
    main_thread = threading.get_ident()
    made = []

    class _FakeTimer:
        def __init__(self, *args, **kwargs):
            made.append(threading.get_ident())
            self.timeout = types.SimpleNamespace(connect=lambda fn: None)

        def setInterval(self, *args):
            pass

        def isActive(self):
            return False

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(QtCore, "QTimer", _FakeTimer)

    worker = threading.Thread(target=lambda: service._sync_rotation_timer(3))
    worker.start()
    worker.join()
    assert made == [], "a worker thread must not create the rotation timer"

    for _ in range(3):
        qapp.processEvents()
    assert made == [main_thread]


def test_the_windows_launch_notice_is_windows_only():
    """The Smart App Control notice is for Windows installs of Updater CE.

    Mac and Linux still receive the other notices. The text session uses the
    same platform attribute with its own names (darwin / win32 / linux).
    """
    from pathlib import Path

    from src.console.session import _normalise_platform
    from src.paths import REPO_ROOT

    feed = (REPO_ROOT / "assets" / "online" / "notice.xml").read_text(encoding="utf-8")
    notices = parse_notice_xml(feed)
    launch = next(notice for notice in notices if notice.id == "2026-11-07-codesigning")
    others = [notice for notice in notices if notice.id != launch.id]

    assert launch.platforms == ("windows",)
    assert launch.is_live(platform="windows") is True
    assert launch.is_live(platform="mac") is False
    assert launch.is_live(platform="linux") is False
    assert others and all(notice.is_live(platform="mac") for notice in others)
    assert all(notice.is_live(platform="windows") for notice in others)

    site = Path(REPO_ROOT).parent / "Innioasis-Updater" / "notice.xml"
    if site.is_file():
        published = next(
            notice
            for notice in parse_notice_xml(site.read_text(encoding="utf-8"))
            if notice.id == "2026-11-07-codesigning"
        )
        assert published.platforms == ("windows",)
        assert published.is_live(platform="windows") is True
        assert published.is_live(platform="mac") is False
        assert published.is_live(platform="linux") is False

    import xml.etree.ElementTree as ET

    root = ET.fromstring(feed)
    element = next(node for node in root.findall("notice") if node.get("id") == launch.id)
    platforms = {
        _normalise_platform(part)
        for part in f"{element.get('platform') or ''},{element.get('os') or ''}".split(",")
        if part.strip()
    }
    assert _normalise_platform("win32") in platforms
    assert _normalise_platform("darwin") not in platforms
    assert _normalise_platform("linux") not in platforms
    for node in root.findall("notice"):
        if node.get("id") == launch.id:
            continue
        untouched = {
            part.strip()
            for part in f"{node.get('platform') or ''},{node.get('os') or ''}".split(",")
            if part.strip()
        }
        assert untouched == set()
