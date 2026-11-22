"""Regression tests for Linux browser and file-manager launches from packaged builds."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import browser, diagnostics

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_clean_linux_env_restores_host_vars_from_appimage_launcher():
    """AppImage/portable linux-run.sh saves host vars; browser children must get them back."""
    poisoned = {
        "INNIOASIS_HOST_ENV_SAVED": "1",
        "INNIOASIS_APP_BUNDLE_ROOT": "/tmp/.mount_UpdateXYZ",
        "INNIOASIS_ORIG_LD_LIBRARY_PATH": "/opt/custom/lib",
        "INNIOASIS_ORIG_PATH": "/usr/local/bin:/usr/bin:/bin:/snap/bin",
        "INNIOASIS_ORIG_XDG_DATA_DIRS": "/var/lib/flatpak/exports/share:/usr/local/share:/usr/share",
        "INNIOASIS_ORIG_QT_PLUGIN_PATH": "",
        "INNIOASIS_ORIG_QT_QPA_PLATFORM_PLUGIN_PATH": "",
        "INNIOASIS_ORIG_QT_QPA_PLATFORM": "",
        "INNIOASIS_ORIG_QT_QPA_PLATFORMTHEME": "kde",
        "INNIOASIS_ORIG_QT_OPENGL": "",
        "INNIOASIS_ORIG_LIBGL_ALWAYS_SOFTWARE": "",
        "INNIOASIS_ORIG_QT_IM_MODULE": "",
        "LD_LIBRARY_PATH": "/tmp/.mount_UpdateXYZ/usr/bin/_internal:/tmp/.mount_UpdateXYZ/usr/lib:/opt/custom/lib",
        "LD_LIBRARY_PATH_ORIG": "/tmp/.mount_UpdateXYZ/usr/bin/_internal:/opt/custom/lib",
        "PATH": "/tmp/.mount_UpdateXYZ/usr/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin",
        "XDG_DATA_DIRS": "/tmp/.mount_UpdateXYZ/usr/share:/var/lib/flatpak/exports/share:/usr/local/share:/usr/share",
        "QT_PLUGIN_PATH": "/tmp/.mount_UpdateXYZ/usr/bin/_internal/PySide6/Qt/plugins",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/tmp/.mount_UpdateXYZ/usr/bin/_internal/PySide6/Qt/plugins/platforms",
        "QT_QPA_PLATFORM": "wayland;xcb",
        "QT_OPENGL": "software",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "APPDIR": "/tmp/.mount_UpdateXYZ",
        "APPIMAGE": "/home/user/.local/share/innioasis-updater/UpdaterCE.appimage",
        "PYTHONHOME": "/tmp/.mount_UpdateXYZ/usr/bin/_internal",
        "PYTHONPATH": "/tmp/.mount_UpdateXYZ/usr/bin/_internal",
        "SSL_CERT_FILE": "/tmp/.mount_UpdateXYZ/usr/bin/_internal/certifi/cacert.pem",
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "INNIOASIS_SAFE_MODE": "1",
    }

    clean = browser.clean_linux_subprocess_env(poisoned)

    assert clean["LD_LIBRARY_PATH"] == "/opt/custom/lib"
    assert clean["PATH"] == "/usr/local/bin:/usr/bin:/bin:/snap/bin"
    assert clean["XDG_DATA_DIRS"] == "/var/lib/flatpak/exports/share:/usr/local/share:/usr/share"
    assert clean["QT_QPA_PLATFORMTHEME"] == "kde"
    assert clean["DISPLAY"] == ":0"
    assert clean["WAYLAND_DISPLAY"] == "wayland-0"
    assert clean["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"

    for stripped in (
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_QPA_PLATFORM",
        "QT_OPENGL",
        "LIBGL_ALWAYS_SOFTWARE",
        "APPDIR",
        "APPIMAGE",
        "PYTHONHOME",
        "PYTHONPATH",
        "SSL_CERT_FILE",
        "LD_LIBRARY_PATH_ORIG",
        "INNIOASIS_HOST_ENV_SAVED",
        "INNIOASIS_ORIG_LD_LIBRARY_PATH",
        "INNIOASIS_SAFE_MODE",
    ):
        assert stripped not in clean, f"{stripped} should be removed from child env"


def test_clean_linux_env_scrubs_pyinstaller_paths_without_wrapper():
    """Direct PyInstaller runs (tarball launcher) still get scrubbed cleanly."""
    poisoned = {
        "LD_LIBRARY_PATH": "/tmp/_MEI123456:/home/user/.local/share/innioasis-updater/app/_internal",
        "LD_LIBRARY_PATH_ORIG": "",
        "PATH": "/home/user/.local/share/innioasis-updater/app/_internal:/usr/bin:/bin",
        "QT_PLUGIN_PATH": "/home/user/.local/share/innioasis-updater/app/_internal/PySide6/Qt/plugins",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/home/user/.local/share/innioasis-updater/app/_internal/PySide6/Qt/plugins/platforms",
        "QT_QPA_PLATFORM": "wayland;xcb",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
        "REQUESTS_CA_BUNDLE": "/tmp/_MEI123456/certifi/cacert.pem",
        "INNIOASIS_SAFE_MODE": "1",
        "QT_OPENGL": "software",
        "LIBGL_ALWAYS_SOFTWARE": "1",
        "QT_IM_MODULE": "compose",
    }

    clean = browser.clean_linux_subprocess_env(poisoned)

    assert "LD_LIBRARY_PATH" not in clean
    assert "QT_PLUGIN_PATH" not in clean
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in clean
    assert "QT_QPA_PLATFORM" not in clean
    assert "REQUESTS_CA_BUNDLE" not in clean
    assert "QT_OPENGL" not in clean
    assert "LIBGL_ALWAYS_SOFTWARE" not in clean
    assert "QT_IM_MODULE" not in clean
    assert clean["SSL_CERT_FILE"] == "/etc/ssl/certs/ca-certificates.crt"
    assert clean["PATH"] == "/usr/bin:/bin"


def test_open_browser_empty_or_none_returns_false():
    assert browser.open_browser("") is False
    assert browser.open_browser("   ") is False
    assert browser.open_browser(None) is False  # type: ignore[arg-type]


def test_open_browser_linux_passes_clean_env_and_preserves_query_vars():
    """Linux open_browser strips AppImage env and keeps innioasis.app URL params intact."""
    from urllib.parse import parse_qs, urlsplit

    spawned: list[tuple[list[str], dict[str, str]]] = []

    def fake_popen(cmd, *args, **kwargs):
        spawned.append((list(cmd), dict(kwargs.get("env") or {})))
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd=cmd, timeout=0.25)
        return proc

    poisoned_env = {
        "INNIOASIS_HOST_ENV_SAVED": "1",
        "INNIOASIS_ORIG_LD_LIBRARY_PATH": "",
        "INNIOASIS_ORIG_PATH": "/usr/bin:/bin",
        "INNIOASIS_ORIG_XDG_DATA_DIRS": "/usr/share",
        "LD_LIBRARY_PATH": "/tmp/.mount_Updater/usr/bin/_internal",
        "QT_PLUGIN_PATH": "/tmp/.mount_Updater/usr/bin/_internal/PySide6/Qt/plugins",
        "PATH": "/tmp/.mount_Updater/usr/bin:/usr/bin:/bin",
        "GIO_LAUNCHED_DESKTOP_FILE": "/home/user/.local/share/applications/innioasis-updater.desktop",
        "GIO_LAUNCHED_DESKTOP_FILE_PID": "9999",
    }

    fake_tr = SimpleNamespace(lang="fr")
    with patch.dict("os.environ", poisoned_env, clear=True):
        with patch("platform.system", return_value="Linux"):
            with patch.object(browser, "_find_linux_browser", return_value="/usr/bin/firefox"):
                with patch.object(browser, "_native_accent_hex", return_value="#e95420"):
                    with patch("src.i18n.translator", return_value=fake_tr):
                        with patch("src.player_update_check.is_donation_ui_disabled", return_value=False):
                            with patch("src.ui.dark.is_dark", return_value=True):
                                with patch("subprocess.Popen", side_effect=fake_popen):
                                    ok = browser.open_browser(
                                        "https://innioasis.app/post_install.html?model=y1&fw=rockbox"
                                    )

    assert ok is True
    assert len(spawned) == 1
    cmd, child_env = spawned[0]
    assert cmd[:2] == ["/usr/bin/firefox", "--new-window"]
    parsed = urlsplit(cmd[2])
    params = parse_qs(parsed.query)
    assert parsed.hostname == "innioasis.app"
    assert params["model"] == ["y1"]
    assert params["fw"] == ["rockbox"]
    assert params["lang"] == ["fr"]
    assert params["accent"] == ["#e95420"]
    assert params["donations"] == ["true"]
    assert params["theme"] == ["dark"]
    assert params["ce"] == ["true"]
    assert "LD_LIBRARY_PATH" not in child_env
    assert "QT_PLUGIN_PATH" not in child_env
    assert "GIO_LAUNCHED_DESKTOP_FILE" not in child_env
    assert "GIO_LAUNCHED_DESKTOP_FILE_PID" not in child_env


def test_linux_gsettings_accent_and_dark_mode_use_clean_env(tmp_path):
    """gsettings calls in dark.py must drop AppImage LD_LIBRARY_PATH so GNOME accent/theme work."""
    from src.ui import dark

    seen_envs: list[dict[str, str]] = []

    def fake_run(cmd, *args, **kwargs):
        seen_envs.append(dict(kwargs.get("env") or {}))
        key = cmd[-1]
        if key == "accent-color":
            return SimpleNamespace(returncode=0, stdout="'purple'\n")
        if key == "color-scheme":
            return SimpleNamespace(returncode=0, stdout="'prefer-dark'\n")
        return SimpleNamespace(returncode=1, stdout="")

    poisoned = {
        "INNIOASIS_HOST_ENV_SAVED": "1",
        "INNIOASIS_ORIG_LD_LIBRARY_PATH": "",
        "INNIOASIS_ORIG_PATH": "/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/tmp/.mount_Updater/usr/bin/_internal",
        "PATH": "/tmp/.mount_Updater/usr/bin:/usr/bin:/bin",
        "XDG_CONFIG_HOME": str(tmp_path / "empty_config"),
    }

    with patch.dict("os.environ", poisoned, clear=True):
        with patch("subprocess.run", side_effect=fake_run):
            assert dark._linux_declared_accent() == "#9141ac"
            with patch("sys.platform", "linux"):
                assert dark.is_system_dark_mode() is True

    assert len(seen_envs) == 2
    for env in seen_envs:
        assert "LD_LIBRARY_PATH" not in env


def test_linux_yaru_theme_accent_fallback(tmp_path):
    """Ubuntu 22.04/24.04 Yaru gtk-theme accent is parsed when GNOME accent-color is absent."""
    from src.ui import dark

    def fake_run(cmd, *args, **kwargs):
        key = cmd[-1]
        if key == "accent-color":
            return SimpleNamespace(returncode=1, stdout="")
        if key == "gtk-theme":
            return SimpleNamespace(returncode=0, stdout="'Yaru-magenta-dark'\n")
        return SimpleNamespace(returncode=1, stdout="")

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / "empty")}, clear=False):
        with patch("subprocess.run", side_effect=fake_run):
            assert dark._linux_declared_accent() == "#b34cb3"
    assert dark._accent_from_yaru_theme("'Adwaita-dark'") is None


def test_open_browser_linux_falls_back_when_primary_launcher_exits_nonzero():
    """If the primary browser binary fails immediately, fall back to xdg-open / gio."""
    attempts: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        attempts.append(list(cmd))
        proc = MagicMock()
        if cmd[0] == "/usr/bin/firefox":
            proc.wait.return_value = 127
        else:
            proc.wait.return_value = 0
        return proc

    def fake_which(name, path=None):
        return {
            "xdg-open": "/usr/bin/xdg-open",
            "gio": "/usr/bin/gio",
        }.get(name)

    with patch("platform.system", return_value="Linux"):
        with patch.object(browser, "_find_linux_browser", return_value="/usr/bin/firefox"):
            with patch("shutil.which", side_effect=fake_which):
                with patch("subprocess.Popen", side_effect=fake_popen):
                    ok = browser.open_browser("https://innioasis.app/guide.html")

    assert ok is True
    assert len(attempts) == 2
    assert attempts[0][:2] == ["/usr/bin/firefox", "--new-window"]
    assert attempts[1][0] == "/usr/bin/xdg-open"
    assert attempts[1][1].startswith("https://innioasis.app/guide.html")


def test_find_linux_browser_routes_flatpak_default_to_desktop_opener():
    """Reverse-DNS Flatpak desktop defaults must use xdg-open rather than a native binary."""
    def fake_which(name, path=None):
        return {
            "xdg-settings": "/usr/bin/xdg-settings",
            "xdg-open": "/usr/bin/xdg-open",
            "firefox": "/usr/bin/firefox",
        }.get(name)

    def fake_run(cmd, *args, **kwargs):
        assert "LD_LIBRARY_PATH" not in (kwargs.get("env") or {})
        return SimpleNamespace(returncode=0, stdout="org.mozilla.firefox.desktop\n")

    env = {
        "INNIOASIS_HOST_ENV_SAVED": "1",
        "INNIOASIS_ORIG_LD_LIBRARY_PATH": "",
        "INNIOASIS_ORIG_PATH": "/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/tmp/.mount_Updater/usr/bin/_internal",
        "PATH": "/tmp/.mount_Updater/usr/bin:/usr/bin:/bin",
    }

    with patch("shutil.which", side_effect=fake_which):
        with patch("subprocess.run", side_effect=fake_run):
            chosen = browser._find_linux_browser(env)

    assert chosen == "/usr/bin/xdg-open"


def test_find_linux_browser_prefers_most_specific_match():
    """firefox-esr.desktop chooses firefox-esr over firefox when both exist."""
    def fake_which(name, path=None):
        return {
            "xdg-settings": "/usr/bin/xdg-settings",
            "firefox": "/usr/bin/firefox",
            "firefox-esr": "/usr/bin/firefox-esr",
        }.get(name)

    def fake_run(cmd, *args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="firefox-esr.desktop\n")

    with patch("shutil.which", side_effect=fake_which):
        with patch("subprocess.run", side_effect=fake_run):
            chosen = browser._find_linux_browser({"PATH": "/usr/bin:/bin"})

    assert chosen == "/usr/bin/firefox-esr"


def test_linux_cmd_for_desktop_openers():
    assert browser._linux_cmd_for("/usr/bin/gio", "https://innioasis.app", True) == [
        "/usr/bin/gio",
        "open",
        "https://innioasis.app",
    ]
    assert browser._linux_cmd_for("/usr/bin/kioclient6", "https://innioasis.app", True) == [
        "/usr/bin/kioclient6",
        "exec",
        "https://innioasis.app",
    ]
    assert browser._linux_cmd_for("/usr/bin/xdg-open", "https://innioasis.app", True) == [
        "/usr/bin/xdg-open",
        "https://innioasis.app",
    ]
    assert browser._linux_cmd_for("/usr/bin/firefox", "https://innioasis.app", False) == [
        "/usr/bin/firefox",
        "https://innioasis.app",
    ]


def test_reveal_in_file_manager_linux_uses_clean_env(tmp_path):
    spawned: list[tuple[list[str], dict[str, str]]] = []

    def fake_popen(cmd, *args, **kwargs):
        spawned.append((list(cmd), dict(kwargs.get("env") or {})))
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    target_file = tmp_path / "app.log"
    target_file.write_text("ok", encoding="utf-8")

    poisoned = {
        "INNIOASIS_HOST_ENV_SAVED": "1",
        "INNIOASIS_ORIG_LD_LIBRARY_PATH": "",
        "INNIOASIS_ORIG_PATH": "/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/tmp/.mount_Updater/usr/bin/_internal",
        "PATH": "/tmp/.mount_Updater/usr/bin:/usr/bin:/bin",
    }

    with patch.dict("os.environ", poisoned, clear=True):
        with patch("sys.platform", "linux"):
            with patch("os.name", "posix"):
                with patch("shutil.which", side_effect=lambda n, path=None: "/usr/bin/xdg-open" if n == "xdg-open" else None):
                    with patch("subprocess.Popen", side_effect=fake_popen):
                        ok = diagnostics.reveal_in_file_manager(target_file)

    assert ok is True
    assert spawned[0][0] == ["/usr/bin/xdg-open", str(tmp_path.resolve())]
    assert "LD_LIBRARY_PATH" not in spawned[0][1]


def test_linux_run_script_captures_host_env_before_overrides():
    """assets/linux-run.sh must save INNIOASIS_ORIG_* before prepending bundle paths."""
    script_path = REPO_ROOT / "assets" / "linux-run.sh"
    res = subprocess.run(["sh", "-n", str(script_path)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr

    text = script_path.read_text(encoding="utf-8")
    save_pos = text.index("export INNIOASIS_ORIG_LD_LIBRARY_PATH=")
    override_ld_pos = text.index('export LD_LIBRARY_PATH="$LIB_DIRS')
    override_qt_pos = text.index('export QT_PLUGIN_PATH="$QT_PLUGIN_DIR"')
    assert save_pos < override_ld_pos < override_qt_pos
