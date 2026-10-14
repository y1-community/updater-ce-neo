"""Launch resilience: a failing host-integration step must not cost the window.

Window chrome (glass, traffic lights, dark title bar) and the host theme are the
parts of startup that reach into the OS, and they are exactly the parts we
cannot test on every host the users run. Each was already a no-op where the
feature is missing, but a step that *raises* -- an OS build that changed, a
half-installed pyobjc bridge -- aborted ``main()`` before the event loop, so the
user got nothing at all instead of a plain window.

Also pinned here: the start-up path no longer drags the ``requests`` stack in
(measured ~40 ms of the window's import), and the PEP 810 ``__lazy_modules__``
declaration behaves the way pre-3.15 interpreters expect (i.e. does nothing).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import app as app_module  # noqa: E402
from src import network  # noqa: E402
from src.ui import main_window  # noqa: E402


def test_startup_step_swallows_a_failing_host_call():
    def boom(*_args, **_kwargs):
        raise RuntimeError("no DWM on this build")

    assert app_module._startup_step("boom", boom) is None


def test_startup_step_still_returns_what_the_step_returns():
    assert app_module._startup_step("fine", lambda value: value + 1, 41) == 42


def test_native_chrome_applies_every_step_and_survives_failures(monkeypatch):
    """One broken piece of chrome must not take the other two with it."""
    from src.ui import glass

    applied = []

    def failing_glass(_window, dark=None):
        raise OSError("NSGlassEffectView unavailable")

    def traffic_lights(window, x_offset=0):
        applied.append(("traffic_lights", x_offset, window))

    def dark_titlebar(window, dark):
        applied.append(("dark_titlebar", dark, window))

    monkeypatch.setattr(glass, "apply_glass", failing_glass)
    monkeypatch.setattr(glass, "configure_traffic_lights", traffic_lights)
    monkeypatch.setattr(glass, "apply_windows_dark_titlebar", dark_titlebar)

    sentinel = object()
    app_module._apply_native_chrome(sentinel, dark=True)

    assert ("traffic_lights", 18, sentinel) in applied
    assert ("dark_titlebar", True, sentinel) in applied


def test_startup_does_not_import_the_requests_stack():
    """The update check runs on a worker thread seconds in; its HTTP stack must
    not be on the first-window path."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import src.ui.main_window; "
        "print('requests' in sys.modules, 'urllib3' in sys.modules)"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)], capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False False"


def test_requests_module_is_a_lazy_accessor():
    """Callers get the real module; importing it is what is deferred."""
    assert network.requests_module().__name__ == "requests"


def test_lazy_modules_marker_holds_real_module_names():
    listed = getattr(main_window, "__lazy_modules__", None)
    assert listed, "main_window must declare its PEP 810 lazy modules"
    assert all(isinstance(name, str) and "." in name for name in listed)


@pytest.mark.skipif(sys.version_info < (3, 15), reason="PEP 810 lazy imports need Python 3.15+")
def test_lazy_modules_are_deferred_on_315():
    """On 3.15 the interpreter resolves these on first use, so importing the
    window must not pull them in."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import src.ui.main_window as mw; "
        "missing = [n for n in mw.__lazy_modules__ if n in sys.modules]; "
        "print('deferred' if not missing else 'eager:' + ','.join(missing))"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)], capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "deferred"
