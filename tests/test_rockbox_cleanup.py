"""The old-``.rockbox`` removal step keeps data only when it is asked to.

Unticking Keep my settings is the reader asking for a fresh start, and on a
Rockbox install the old themes, fonts and presets live in ``.rockbox`` on the
storage partition — so the install removes that folder. Ticking it means the
setup comes across untouched. These tests pin that rule and the deletion
helpers, since the step runs unattended on someone's player.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from src import rockbox_cleanup as rc


def test_a_rockbox_install_without_keep_data_cleans_up():
    assert rc.rockbox_install_needs_cleanup(is_rockbox=True, keep_data=False)


def test_keep_data_keeps_the_old_setup():
    assert not rc.rockbox_install_needs_cleanup(is_rockbox=True, keep_data=True)


def test_other_software_has_no_rockbox_folder_to_remove():
    assert not rc.rockbox_install_needs_cleanup(is_rockbox=False, keep_data=False)


def test_only_a_rockbox_folder_is_a_target():
    assert rc.is_rockbox_dir("/media/player/.rockbox")
    assert rc.is_rockbox_dir("/media/player/.ROCKBOX")
    # A parent, a lookalike and an empty value are never deleted.
    assert not rc.is_rockbox_dir("/media/player")
    assert not rc.is_rockbox_dir("/media/player/rockbox")
    assert not rc.is_rockbox_dir("")


def test_the_drive_root_finds_its_rockbox_folder(tmp_path):
    (tmp_path / ".rockbox").mkdir()
    assert rc.find_rockbox_dirs(tmp_path) == [tmp_path / ".rockbox"]


def test_picking_a_rockbox_folder_uses_it_directly(tmp_path):
    target = tmp_path / ".rockbox"
    target.mkdir()
    assert rc.find_rockbox_dirs(target) == [target]


def test_a_folder_inside_the_player_still_finds_the_folder(tmp_path):
    (tmp_path / ".rockbox").mkdir()
    inside = tmp_path / "Music"
    inside.mkdir()
    assert tmp_path / ".rockbox" in rc.find_rockbox_dirs(inside)


def test_sdcard_layout_finds_the_folder_below_the_pick(tmp_path):
    # The Y2 mounts the card as a folder on the drive, not at its root.
    sdcard = tmp_path / "sdcard"
    (sdcard / ".rockbox").mkdir(parents=True)
    assert rc.find_rockbox_dirs(tmp_path) == [sdcard / ".rockbox"]


def test_deletion_removes_the_folder_and_counts_it(tmp_path):
    target = tmp_path / ".rockbox"
    target.mkdir()
    (target / "config.cfg").write_text("x", encoding="utf-8")
    ok, message, count = rc.delete_local_rockbox_dirs([target])
    assert ok, message
    assert count == 1
    assert not target.exists()


def test_deletion_refuses_anything_but_a_rockbox_folder(tmp_path):
    keep = tmp_path / "Music"
    keep.mkdir()
    ok, message, count = rc.delete_local_rockbox_dirs([keep])
    assert not ok
    assert count == 0
    assert keep.exists()
    assert "not a .rockbox folder" in message


def test_cancelling_stops_before_the_first_deletion(tmp_path):
    target = tmp_path / ".rockbox"
    target.mkdir()
    ok, message, count = rc.delete_local_rockbox_dirs(
        [target], cancel_cb=lambda: True
    )
    assert not ok
    assert count == 0
    assert target.exists()
    assert "cancelled" in message


def test_a_missing_folder_counts_as_removed(tmp_path):
    # The player may already have been wiped by the flash step itself.
    ok, message, count = rc.delete_local_rockbox_dirs([tmp_path / ".rockbox"])
    assert ok, message
    assert count == 1


def test_a_lookalike_folder_is_never_a_target(tmp_path):
    # A project's own "rockbox" folder next to the player's data must survive;
    # only the dotted Rockbox payload folder is ever removed.
    (tmp_path / "rockbox").mkdir()
    (tmp_path / "Rockbox Themes").mkdir()
    assert rc.find_rockbox_dirs(tmp_path) == []
