"""Guards shared by every test module.

The app asks the OS to stay awake while it writes to a player. Tests exercise
those flows headlessly many times over, so they must never spawn an inhibitor
or hold a power assertion; the sleep-inhibition tests opt back in by clearing
this variable themselves.
"""

import os

import pytest

os.environ.setdefault("INNIOASIS_NO_SLEEP_INHIBIT", "1")
# Donation copy is rendered in the reader's currency. Pin the dollar so tests
# that assert "$10" describe the copy rather than the machine's locale; the
# currency tests set their own code with monkeypatch.setenv.
os.environ.setdefault("UPDATER_CURRENCY", "USD")


@pytest.fixture(autouse=True)
def _isolated_busy_marker(tmp_path_factory, monkeypatch):
    """Never touch the per-user marker a flashing copy protects its tool with.

    ``process_guard.begin_install_session`` publishes to that shared file, and
    ``instance_marker.clear`` deletes it at exit. A test run that did so would
    overwrite — and then remove — the marker of an Updater that is flashing on
    this machine, after which another copy's sweep is free to terminate its
    ``mtk.py`` / ``flash_tool`` mid-write (the app then reports the tool's
    ``-15`` SIGTERM exit). Tests get their own file instead.
    """
    from src import instance_marker

    path = tmp_path_factory.mktemp("busy-marker") / "busy.json"
    monkeypatch.setattr(instance_marker, "marker_path", lambda: path)
    yield


@pytest.fixture(autouse=True)
def _forget_dismissed_install_hints():
    """A dismissed card hint is a user preference, not test state.

    The paperclip, microSD and keep-data tips keep their "Got it." in
    QSettings, so a developer who dismissed one while looking at the app would
    otherwise hide the tip from every test that expects to see it. The saved
    values go back afterwards: running the suite must never rewrite which
    hints the developer has already read.
    """
    settings = None
    saved = {}
    try:
        from PySide6.QtCore import QSettings

        settings = QSettings("innioasis", "updater")
        for name in ("paperclip", "microsd", "keep_data_warning"):
            # Both the reader's "Got it." and the one automatic showing are
            # preferences of whoever runs the app, never test state.
            for group in ("dismissed_install_hints", "seen_install_hints"):
                key = f"{group}/{name}"
                if settings.contains(key):
                    saved[key] = bool(settings.value(key, False, type=bool))
                settings.remove(key)
        settings.sync()
    except Exception:
        settings = None
    yield
    if settings is not None:
        try:
            for key, value in saved.items():
                settings.setValue(key, value)
            settings.sync()
        except Exception:
            pass
