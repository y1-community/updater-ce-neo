#!/usr/bin/env python3
"""Exercise the real AppImage replacement and relaunch path in a Linux container."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_NO_PREFETCH", "1")
sys.path.insert(0, "/workspace")

from PySide6.QtWidgets import QApplication  # noqa: E402
from src import paths  # noqa: E402
from src.ui.dialogs import UpdateAvailableDialog  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_swap() -> None:
    app = QApplication(["--platform", "offscreen"])
    target = paths.get_appimage_path()
    assert target is not None, "APPIMAGE did not resolve"
    target = Path(target)
    source = Path(os.environ["DOWNLOADED_APPIMAGE"])
    assert target == Path(os.environ["APPIMAGE"]).resolve()
    assert target.is_file() and source.is_file()
    before = sha256(target)
    downloaded = sha256(source)
    assert before != downloaded
    log_path = Path("/tmp/selfupdate/relaunch.log")
    log = log_path.open("w")
    real_popen = subprocess.Popen
    launched = []

    def recording_popen(*args, **kwargs):
        kwargs.setdefault("stdout", log)
        kwargs.setdefault("stderr", subprocess.STDOUT)
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        process = real_popen(*args, **kwargs)
        launched.append((args, kwargs, process))
        return process

    class Stub:
        _dest_path = source
        _progress_label = None

        def _replace_running_appimage(self, source_path):
            return UpdateAvailableDialog._replace_running_appimage(self, source_path)

    stub = Stub()
    original_quit = QApplication.quit
    QApplication.quit = lambda: print("QUIT_REQUESTED", flush=True)
    subprocess.Popen = recording_popen
    try:
        UpdateAvailableDialog._launch_installer(stub)
    finally:
        subprocess.Popen = real_popen
        QApplication.quit = original_quit
        log.flush()

    after = sha256(target)
    assert target.read_bytes() == source.read_bytes(), "installed bytes are not the download"
    assert after == downloaded, "installed hash did not become the downloaded hash"
    assert os.access(target, os.X_OK), "installed AppImage is not executable"
    print(f"BEFORE_HASH={before}")
    print(f"DOWNLOADED_HASH={downloaded}")
    print(f"INSTALLED_HASH={after}")
    print(f"EXECUTABLE={bool(target.stat().st_mode & 0o111)}")
    print(f"REPLACED_PATH={target}")

    assert launched, "the dialog did not launch the replacement"
    _, _, process = launched[0]
    print(f"RELAUNCH_PID={process.pid}")
    deadline = time.time() + 20
    text = ""
    while time.time() < deadline:
        text = log_path.read_text(errors="replace")
        if "Updater CE" in text:
            break
        if process.poll() is not None:
            break
        time.sleep(0.2)
    text = log_path.read_text(errors="replace")
    print("===== RELAUNCH LOG =====")
    print(text[:4000])
    print("===== END LOG =====")
    assert "Updater CE" in text, "replacement process produced no startup evidence"
    rc = process.poll()
    terminated = False
    if rc is None:
        # The headless window keeps running after printing its startup line;
        # ending it from the harness is the expected outcome, not a crash.
        terminated = True
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        rc = process.returncode
    print(f"RELAUNCH_EXIT={rc}")
    print(f"TERMINATED_BY_HARNESS={terminated}")
    assert rc == 0 or terminated, f"replacement process exited with {rc}"
    app.processEvents()
    print("SWAP_OK")


def run_portable() -> None:
    target = Path(os.environ["APPIMAGE"]).resolve()
    source = Path(os.environ["PORTABLE_ARCHIVE"])
    before = sha256(target)
    result = UpdateAvailableDialog._replace_running_appimage(object(), source)
    assert result is None, "portable archive was treated as an AppImage"
    assert sha256(target) == before, "portable archive changed the installed AppImage"
    print(f"PORTABLE_GUARD_OK target={target} source={source.name}")


def run_not_appimage() -> None:
    target = Path(os.environ["TARGET_COPY"]).resolve()
    source = Path(os.environ["DOWNLOADED_APPIMAGE"])
    os.environ.pop("APPIMAGE", None)
    assert paths.get_appimage_path() is None, "paths resolved an AppImage without APPIMAGE"
    before = sha256(target)
    result = UpdateAvailableDialog._replace_running_appimage(object(), source)
    assert result is None, "non-AppImage run replaced a file"
    assert sha256(target) == before
    print(f"NOT_APPIMAGE_FALLBACK_OK target={target}")


def run_readonly() -> None:
    target = Path(os.environ["APPIMAGE"]).resolve()
    source = Path(os.environ["DOWNLOADED_APPIMAGE"])
    before = sha256(target)
    result = UpdateAvailableDialog._replace_running_appimage(object(), source)
    assert result is None, "read-only install directory was modified"
    assert sha256(target) == before
    print(f"READONLY_FALLBACK_OK target={target}")


def main() -> None:
    mode = sys.argv[1]
    if mode == "swap":
        run_swap()
    elif mode == "portable":
        run_portable()
    elif mode == "not-appimage":
        run_not_appimage()
    elif mode == "readonly":
        run_readonly()
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
