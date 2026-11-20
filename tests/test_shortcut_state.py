"""The Settings shortcut switches describe the real shortcuts on this machine.

Setup writes the desktop icon to the *all-users* desktop and the menu entries to
the current user's Start Menu, so reading only the user's own desktop reported
the icon as missing and enabling the switch added a second copy beside it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import shortcut_manager as sm


def _sandbox(tmp_path, monkeypatch):
    """Point the manager at four empty folders: user/common menu and desktop."""
    roots = {"menu": [], "desktop": []}
    for kind in ("menu", "desktop"):
        for scope in ("user", "common"):
            folder = tmp_path / f"{scope}_{kind}"
            folder.mkdir()
            roots[kind].append(folder)
    monkeypatch.setattr(sm, "_roots", lambda kind: list(roots[kind]))
    monkeypatch.setattr(sm, "supported", lambda: True)
    monkeypatch.setattr(sm.paths, "IS_WINDOWS", True)
    # Shortcut targets are read through PowerShell on Windows; tests that care
    # about them supply their own map.
    monkeypatch.setattr(sm, "_lnk_targets", lambda files: {})
    return roots


def _stub_writer(monkeypatch):
    """Write a placeholder file instead of starting PowerShell."""

    def write(target, shortcut_id):
        Path(target).write_bytes(b"lnk")

    monkeypatch.setattr(sm, "_write_windows_lnk", write)
    monkeypatch.setattr(sm, "_write_linux_desktop", write)


def test_detects_what_the_installer_wrote(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    (roots["desktop"][1] / f"{sm.DESKTOP_APP_NAME}.lnk").write_bytes(b"x")
    legacy = roots["menu"][0] / sm.LEGACY_COMMUNITY_FOLDER
    legacy.mkdir()
    (legacy / f"{sm.TOOL_NAME}.lnk").write_bytes(b"x")

    state = sm.get_state()
    assert state[sm.APP_ID]["desktop"] is True
    assert state[sm.TOOL_ID]["menu"] is True
    assert state[sm.APP_ID]["menu"] is False


def test_does_not_claim_the_other_brand(tmp_path, monkeypatch):
    if sm._is_mediatek_brand():
        pytest.skip("checked against the Updater CE brand")
    roots = _sandbox(tmp_path, monkeypatch)
    (roots["desktop"][0] / f"{sm.DESKTOP_APP_NAME_MEDIATEK}.lnk").write_bytes(b"x")
    assert sm.get_state()[sm.APP_ID]["desktop"] is False


def test_shortcut_renamed_inside_our_folder_is_still_ours(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    folder = roots["menu"][0] / sm.DESKTOP_APP_NAME
    folder.mkdir()
    (folder / "My Updater.lnk").write_bytes(b"x")
    assert sm.get_state()[sm.APP_ID]["menu"] is True


def test_tool_folder_does_not_imply_the_app_shortcut(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    folder = roots["menu"][0] / sm.TOOL_NAME
    folder.mkdir()
    (folder / "flash_tool.lnk").write_bytes(b"x")
    assert sm.get_state()[sm.APP_ID]["menu"] is False


def test_enabling_an_existing_shortcut_does_not_duplicate_it(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    _stub_writer(monkeypatch)
    installed = roots["desktop"][1] / f"{sm.DESKTOP_APP_NAME}.lnk"
    installed.write_bytes(b"x")

    assert sm.set_shortcut(sm.APP_ID, "desktop", True) is True
    assert installed.is_file()
    assert list(roots["desktop"][0].iterdir()) == []


def test_enabling_a_missing_shortcut_creates_it(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    _stub_writer(monkeypatch)

    assert sm.set_shortcut(sm.APP_ID, "menu", True) is True
    folder = roots["menu"][0] / sm._menu_folder_name()
    created = list(folder.iterdir())
    assert len(created) == 1
    assert created[0].stem == sm._creation_name(sm.APP_ID, "menu")
    assert not list(roots["menu"][0].glob("*.lnk"))
    assert sm.get_state()[sm.APP_ID]["menu"] is True


def test_legacy_community_folder_keeps_only_the_app_in_one_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "current_language", lambda: "en")
    monkeypatch.setattr(sm, "_is_mediatek_brand", lambda: False)
    roots = _sandbox(tmp_path, monkeypatch)
    legacy = roots["menu"][0] / sm.LEGACY_COMMUNITY_FOLDER
    legacy.mkdir()
    app = legacy / f"{sm.APP_NAME}.lnk"
    tool = legacy / f"{sm.TOOL_NAME}.lnk"
    app.write_bytes(b"app")
    tool.write_bytes(b"tool")

    assert sm.set_shortcut(sm.APP_ID, "menu", True) is True

    folder = roots["menu"][0] / sm._menu_folder_name()
    assert (folder / app.name).read_bytes() == b"app"
    assert not tool.exists()
    assert not legacy.exists()
    assert not list(roots["menu"][0].glob("*.lnk"))


def test_another_apps_shortcut_is_not_ours(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    other = roots["desktop"][0] / "Notepad.lnk"
    other.write_bytes(b"x")
    monkeypatch.setattr(
        sm, "_lnk_targets", lambda files: {sm._path_key(other): r"C:\Windows\notepad.exe"}
    )

    assert sm.get_state()[sm.APP_ID]["desktop"] is False
    assert sm.get_state()[sm.TOOL_ID]["desktop"] is False


def test_renamed_shortcut_pointing_at_the_tool_is_detected(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    renamed = roots["desktop"][0] / "My MTK Tool.lnk"
    renamed.write_bytes(b"x")
    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {sm._path_key(renamed): r"C:\Program Files\UpdaterCE\SP_Flash_Tool\flash_tool.exe"},
    )

    assert sm.get_state()[sm.TOOL_ID]["desktop"] is True
    assert sm.get_state()[sm.APP_ID]["desktop"] is False


def test_renamed_shortcut_pointing_at_the_app_is_detected(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    renamed = roots["menu"][0] / "Firmware Thing.lnk"
    renamed.write_bytes(b"x")
    monkeypatch.setattr(
        sm,
        "_lnk_targets",
        lambda files: {sm._path_key(renamed): r"C:\Program Files\UpdaterCE\InnioasisUpdater.exe"},
    )

    assert sm.get_state()[sm.APP_ID]["menu"] is True
    assert sm.get_state()[sm.TOOL_ID]["menu"] is False


def test_disabling_removes_every_copy(tmp_path, monkeypatch):
    roots = _sandbox(tmp_path, monkeypatch)
    _stub_writer(monkeypatch)
    for folder in roots["desktop"]:
        (folder / f"{sm.DESKTOP_APP_NAME}.lnk").write_bytes(b"x")

    assert sm.set_shortcut(sm.APP_ID, "desktop", False) is True
    assert sm.get_state()[sm.APP_ID]["desktop"] is False
