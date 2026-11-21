"""The themes install must carry the theme pack, not just the bundled fonts."""

import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

from src import theme_pack as tp


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _raw_release_pack(path: Path) -> Path:
    """The upstream release layout: everything under a ``.rockbox`` root."""
    return _make_zip(
        path,
        {
            ".rockbox/themes/Test.cfg": (
                b"wps: /sdcard/.rockbox/wps/Test.wps\n"
                b"font: /sdcard/.rockbox/fonts/f.fnt\n"
            ),
            ".rockbox/wps/Test.wps": b"wps",
            ".rockbox/fonts/f.fnt": b"font",
        },
    )


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(tp, "downloads_dir", lambda: tmp_path)
    monkeypatch.setattr(tp, "bundled_fonts_pack_zip_path", lambda: tmp_path / "missing-fonts.zip")
    monkeypatch.setattr(tp, "bundled_themepack_zip_path", lambda: tmp_path / "missing-themes.zip")


def test_y2_builds_themes_from_raw_release_and_patches_every_path(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    source = _raw_release_pack(tmp_path / tp.THEME_PACK_ZIP_NAME)
    # A fonts-only cached Y2 payload must never be reused.
    _make_zip(
        tmp_path / tp.THEME_PACK_ZIP_NAME_Y2,
        {"fonts/stale.fnt": b"old"},
    )

    out = tp.ensure_normalized_theme_pack_zip(apply_path_fix=True)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert {"themes/Test.cfg", "wps/Test.wps", "fonts/f.fnt"} <= names
        cfg = zf.read("themes/Test.cfg")
    assert b"/sdcard" not in cfg
    assert b"/.rockbox/wps/Test.wps" in cfg
    assert b"/.rockbox/fonts/f.fnt" in cfg
    assert source.is_file()


def test_y1_source_and_target_do_not_overwrite_themes_with_fonts(tmp_path, monkeypatch):
    """Y1 uses the download's own filename as its target; reading it matters."""
    _isolate(monkeypatch, tmp_path)
    source = _raw_release_pack(tmp_path / tp.THEME_PACK_ZIP_NAME)

    out = tp.ensure_normalized_theme_pack_zip(apply_path_fix=False, force_rebuild=True)

    assert out == source
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        cfg = zf.read("themes/Test.cfg")
    assert {"themes/Test.cfg", "wps/Test.wps", "fonts/f.fnt"} <= names
    # Y1 keeps the storage-qualified paths its firmware expects.
    assert b"/sdcard/.rockbox/wps/Test.wps" in cfg


def test_ensure_ready_downloads_a_missing_pack(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("src.config.is_offline_mode", lambda: False)

    def fake_download(target, timeout=0, progress_cb=None):
        _raw_release_pack(Path(target))
        if progress_cb:
            progress_cb(1, 1)
        return True, "merged", 3

    monkeypatch.setattr(tp, "download_and_merge_theme_pack", fake_download)

    ready, reason = tp.ensure_ready_theme_pack_zip(apply_path_fix=True)

    assert ready is not None, reason
    assert tp.theme_zip_has_themes(ready)
    with zipfile.ZipFile(ready) as zf:
        assert b"/sdcard" not in zf.read("themes/Test.cfg")


def test_fonts_only_payload_is_not_reported_as_themes(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    fonts = _make_zip(tmp_path / "fonts.zip", {".rockbox/fonts/f.fnt": b"font"})
    monkeypatch.setattr(tp, "bundled_fonts_pack_zip_path", lambda: fonts)
    monkeypatch.setattr("src.config.is_offline_mode", lambda: True)

    ready, reason = tp.ensure_ready_theme_pack_zip(apply_path_fix=True)

    assert ready is None
    assert "offline" in reason


def test_path_patch_handles_paths_without_a_trailing_slash():
    assert tp.patch_theme_content_for_y2("dir: /sdcard/.rockbox") == "dir: /.rockbox"
    assert (
        tp.patch_theme_content_for_y2("/storage/sdcard1/.rockbox/wps/Test.wps")
        == "/.rockbox/wps/Test.wps"
    )
    # Already-correct root paths are left alone.
    assert tp.patch_theme_content_for_y2("/.rockbox/wps/Test.wps") == "/.rockbox/wps/Test.wps"


def test_adb_worker_fails_when_the_push_is_not_verified(tmp_path):
    payload = _raw_release_pack(tmp_path / "payload.zip")
    worker = tp.AdbPushWorker("/fake/adb", "SERIAL", parent=None, zip_path=payload)
    results = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))

    with patch("src.adb.adb_find_all_rockbox_dirs", return_value=["/sdcard/.rockbox"]), \
         patch("src.adb.adb_push_and_unpack_zip", return_value=(False, "no unzip")), \
         patch("src.adb.adb_push_merge", return_value=(True, "pushed")), \
         patch("src.adb.adb_verify_theme_payload", return_value=(False, "2 of 3 theme files are missing")):
        worker.run()

    assert results and results[0][0] is False
    assert "missing" in results[0][1]


def test_verification_treats_case_variants_as_one_fat_file(tmp_path):
    """FAT cannot hold both case spellings; the push is still complete."""
    from src import adb

    payload = _make_zip(
        tmp_path / "case.zip",
        {
            "themes/Test.cfg": b"cfg",
            "wps/Test.wps": b"wps",
            "fonts/F.fnt": b"font",
        },
    )
    listing = (
        "1\t/sdcard/.rockbox/themes/Test.cfg\n"
        "1\t/sdcard/.rockbox/wps/Test.wps\n"
        "1\t/sdcard/.rockbox/fonts/f.FNT\n"
    )
    proc = subprocess.CompletedProcess([], 0, stdout=listing, stderr="")
    with patch.object(adb, "_run_adb", return_value=proc):
        ok, msg = adb.adb_verify_theme_payload(
            "/fake/adb", "SERIAL", "/sdcard/.rockbox", payload
        )

    assert ok, msg


def test_cleanup_renames_when_rm_fails_on_a_live_card():
    """A live Rockbox can make rm return ENOTEMPTY; the folder still goes."""
    from src import adb

    rm_failed = subprocess.CompletedProcess(
        [], 1, stdout="", stderr="rm failed for /.rockbox, Directory not empty"
    )
    moved = subprocess.CompletedProcess([], 0, stdout="__ADB_MV_OK__\n", stderr="")
    calls = []

    def fake_run(_adb, *args, **kwargs):
        cmd = str(args[-1]) if args else ""
        calls.append(cmd)
        return moved if "mv " in cmd else rm_failed

    with patch.object(adb, "_run_adb", side_effect=fake_run), patch.object(
        adb, "adb_shell_ls", return_value=False
    ):
        ok, msg = adb.adb_delete_rockbox_dirs(
            "/fake/adb", "SERIAL", ["/storage/sdcard0/.rockbox"]
        )

    assert ok is True, msg
    assert "Removed 1" in msg
    assert any("mv " in c for c in calls)


def test_bind_mount_aliases_are_not_double_targets():
    """/mnt/media_rw/sdcard0 and /storage/sdcard0 are the same card."""
    from src import adb

    proc = subprocess.CompletedProcess(
        [], 0, stdout="/mnt/media_rw/sdcard0/.rockbox\n/storage/sdcard0/.rockbox\n", stderr=""
    )
    with patch.object(adb, "_run_adb", return_value=proc):
        dirs = adb.adb_find_all_rockbox_dirs("/fake/adb", "SERIAL")

    assert dirs == ["/storage/sdcard0/.rockbox"]


def test_adb_worker_refuses_to_push_fonts_when_the_pack_is_unavailable(tmp_path):
    worker = tp.AdbPushWorker("/fake/adb", "SERIAL", parent=None, zip_path=None)
    results = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))

    with patch.object(
        tp, "ensure_ready_theme_pack_zip", return_value=(None, "offline mode is on")
    ), patch("src.adb.adb_find_all_rockbox_dirs", return_value=["/sdcard/.rockbox"]):
        worker.run()

    assert results and results[0][0] is False
    assert "offline mode is on" in results[0][1]
