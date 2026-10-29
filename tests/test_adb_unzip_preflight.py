"""The on-device unpack is only a success when the payload's files are there.

``adb shell`` does not pass a compound command's exit status through, so a
player with no unzip used to report "Unpacked into …" while nothing was
written. The same shape hid a card that filled up mid-extract: the folders
exist, so the old folder-existence check passed. Both are now judged against
the archive's own file list, and a shortfall falls through to the per-file
push instead of claiming success.
"""

import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src import adb  # noqa: E402

PAYLOAD = {
    "fonts/35-Nimbus.fnt": b"FONT",
    "fonts/05-Tiny.fnt": b"TINY",
    "themes/cabbie.cfg": b"CFG",
    "wps/cabbie.wps": b"WPS",
    # Padding: the transfer refuses archives under 1 KiB as truncated.
    "fonts/pad.bin": b"x" * 2048,
}
DEST = "/storage/sdcard0/.rockbox"


def _proc(stdout="", returncode=0):
    class _P:
        pass

    p = _P()
    p.stdout = stdout
    p.stderr = ""
    p.returncode = returncode
    return p


def _payload(tmp_path):
    path = tmp_path / "payload.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in PAYLOAD.items():
            z.writestr(name, data)
    return path


def _du(dest, names):
    return _proc("".join(f"64\t{dest}/{name}\r\n" for name in names))


def _fake_adb(du_stdout):
    """Serve probe/mkdir/unpack/du/cleanup the way a player would."""

    def fake_run(_adb, *args, **_kwargs):
        command = str(args[-1])
        if command.startswith("if command -v unzip >/dev/null 2>&1 ||"):
            return _proc("__ADB_UNZIP_OK__\n")
        if " -d " in command and "unzip" in command:
            return _proc("Archive: payload.zip\n")
        if command.startswith("du -a "):
            return du_stdout
        return _proc("__ADB_MKDIR_OK__ __ADB_CLEANUP_OK__\n")

    return fake_run


def test_a_player_without_unzip_refuses_the_archive_before_pushing_it(tmp_path):
    calls = []

    def fake_run(_adb, *args, **_kwargs):
        calls.append(args)
        if str(args[-1]).startswith("if command -v unzip"):
            return _proc("__ADB_NO_UNZIP__\n")
        return _proc("")

    with patch.object(adb, "_run_adb", side_effect=fake_run):
        ok, msg = adb.adb_push_and_unpack_zip("/fake/adb", "SERIAL", _payload(tmp_path), [DEST])

    assert ok is False
    assert "no unzip" in msg
    # Only the probe ran: no push, no unpack, no cleanup round trip.
    assert all("push" not in " ".join(map(str, args)) for args in calls)


def test_an_unpack_that_cannot_run_is_not_reported_as_unpacked(tmp_path):
    def fake_run(_adb, *args, **_kwargs):
        command = str(args[-1])
        if command.startswith("if command -v unzip >/dev/null 2>&1 ||"):
            return _proc("__ADB_UNZIP_OK__\n")
        if " -d " in command and "unzip" in command:
            # The tool vanished between the probe and the unpack.
            return _proc("__ADB_NO_UNZIP__\n")
        return _proc("__ADB_MKDIR_OK__ __ADB_CLEANUP_OK__\n")

    with patch.object(adb, "_run_adb", side_effect=fake_run), patch.object(
        adb, "_run_adb_push_with_progress", return_value=_proc("")
    ):
        ok, msg = adb.adb_push_and_unpack_zip("/fake/adb", "SERIAL", _payload(tmp_path), [DEST])

    assert ok is False
    assert "no unzip" in msg


def test_a_short_extract_fails_against_the_archives_own_manifest(tmp_path):
    """The folders exist and unzip exited 0 — the missing files still fail it."""
    partial = list(PAYLOAD)[:2]
    with patch.object(adb, "_run_adb", side_effect=_fake_adb(_du(DEST, partial))), patch.object(
        adb, "_run_adb_push_with_progress", return_value=_proc("")
    ):
        ok, msg = adb.adb_push_and_unpack_zip("/fake/adb", "SERIAL", _payload(tmp_path), [DEST])

    assert ok is False
    assert "3 of 5 files are missing" in msg
    assert "themes/cabbie.cfg" in msg


def test_a_complete_extract_is_a_success_and_a_failed_one_falls_back(tmp_path):
    """Every manifest entry present: success; otherwise the worker pushes files."""
    from src.theme_pack import AdbPushWorker

    zip_path = _payload(tmp_path)

    with patch.object(adb, "_run_adb", side_effect=_fake_adb(_du(DEST, PAYLOAD))), patch.object(
        adb, "_run_adb_push_with_progress", return_value=_proc("")
    ):
        ok, msg = adb.adb_push_and_unpack_zip("/fake/adb", "SERIAL", zip_path, [DEST])
    assert ok is True
    assert DEST in msg

    # The worker's own decision: an unpack that cannot be verified must not end
    # the stage as a success — it takes the per-file push instead.
    worker = AdbPushWorker(
        "/fake/adb", "SERIAL", parent=None, zip_path=zip_path
    )
    results = []
    worker.finished.connect(lambda ok_, msg_: results.append((ok_, msg_)))
    pushes = []

    def no_unpack(*_args, **_kwargs):
        return False, "1 of 4 files are missing after unpacking"

    def fake_push(*args, **kwargs):
        pushes.append(kwargs.get("device_dir") or args[3])
        return True, "pushed"

    with patch.object(adb, "adb_find_all_rockbox_dirs", return_value=[DEST]), patch.object(
        adb, "adb_push_and_unpack_zip", side_effect=no_unpack
    ), patch.object(adb, "adb_push_merge", side_effect=fake_push):
        worker.run()

    assert pushes == [DEST]
    assert results and results[0][0] is True
