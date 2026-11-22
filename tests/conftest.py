"""Guards shared by every test module.

The app asks the OS to stay awake while it writes to a player. Tests exercise
those flows headlessly many times over, so they must never spawn an inhibitor
or hold a power assertion; the sleep-inhibition tests opt back in by clearing
this variable themselves.
"""

import atexit
import os
import shutil
import tempfile

import pytest

os.environ.setdefault("INNIOASIS_NO_SLEEP_INHIBIT", "1")
# Donation copy is rendered in the reader's currency. Pin the dollar so tests
# that assert "$10" describe the copy rather than the machine's locale; the
# currency tests set their own code with monkeypatch.setenv.
os.environ.setdefault("UPDATER_CURRENCY", "USD")


def _install_isolated_qsettings() -> None:
    """Route default QSettings(org, app) calls into a per-session temp folder.

    On macOS, ``QSettings("innioasis", "updater")`` writes straight to the
    user's live ``~/Library/Preferences/com.innioasis.updater.plist`` even when
    ``QSettings.setDefaultFormat(IniFormat)`` is called. If a test flips a
    setting such as ``rockbox_theme_sync/enabled`` and then fails before its
    ``finally:`` block (or while the real app is open), it silently corrupts
    the developer's real Updater preferences. Redirecting non-file QSettings
    constructors to ``IniFormat`` under a private temp directory keeps every
    test isolated from the host user's preferences.
    """
    try:
        import PySide6.QtCore as _qtcore
    except Exception:
        return

    real_qsettings = _qtcore.QSettings
    if getattr(real_qsettings, "_innioasis_pytest_isolated", False):
        return

    tmp_dir = tempfile.mkdtemp(prefix="innioasis_pytest_qsettings_")
    atexit.register(shutil.rmtree, tmp_dir, True)
    ini_fmt = real_qsettings.Format.IniFormat
    native_fmt = real_qsettings.Format.NativeFormat
    user_scope = real_qsettings.Scope.UserScope
    real_qsettings.setDefaultFormat(ini_fmt)
    real_qsettings.setPath(ini_fmt, user_scope, tmp_dir)

    class _IsolatedQSettings(real_qsettings):
        _innioasis_pytest_isolated = True

        def __init__(self, *args, **kwargs):
            if not kwargs:
                # QSettings(org), QSettings(org, app), QSettings(org, app, parent)
                if (
                    len(args) in (1, 2, 3)
                    and isinstance(args[0], str)
                    and (len(args) == 1 or isinstance(args[1], str))
                ):
                    org = args[0]
                    app = args[1] if len(args) >= 2 else ""
                    if len(args) == 3 and args[2] is not None:
                        super().__init__(ini_fmt, user_scope, org, app, args[2])
                    else:
                        super().__init__(ini_fmt, user_scope, org, app)
                    return
                # QSettings(scope, org[, app[, parent]])
                if len(args) >= 2 and isinstance(args[0], real_qsettings.Scope) and isinstance(args[1], str):
                    super().__init__(ini_fmt, *args)
                    return
                # QSettings(NativeFormat, UserScope, org[, app[, parent]])
                if (
                    len(args) >= 3
                    and args[0] == native_fmt
                    and args[1] == user_scope
                    and isinstance(args[2], str)
                ):
                    super().__init__(ini_fmt, *args[1:])
                    return
            super().__init__(*args, **kwargs)

    _qtcore.QSettings = _IsolatedQSettings


_install_isolated_qsettings()


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
