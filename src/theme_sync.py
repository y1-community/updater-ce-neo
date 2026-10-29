"""Passive Rockbox 360p theme sync: keep device themes fresh without being asked.

Two halves:

1. **Gating + memory** — pure helpers over the tracked-install records in
   :mod:`player_update_check`. Passive transfers only run while *some* model
   tracks Rockbox with a 360p build installed. A 240p build, any other
   software, or nothing tracked means the sync stays idle. Successful merges
   (wizard or passive) remember their targets and stamp a last-sync time.

2. **Passive worker** — :class:`PassiveThemeSyncWorker` (a QThread) builds
   the combined payload (bundled base + latest remote overlay, cached) and
   silently merges it into every reachable ``.rockbox`` location: ADB
   devices (all serials, all probed device dirs), remembered USB folders,
   and ``.rockbox`` dirs found on removable drives. Every failure is silent
   (debug log only) so background passes can never interrupt the user.

When a :class:`theme_pack.Rockbox360pSetupWizard` is open for the same
model, :class:`ThemeSyncService` forwards live progress into it — and if the
wizard is still watching for ADB when a passive push starts, the wizard
adopts that push instead of making the user wait out the watch window.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Signal

logger = logging.getLogger(__name__)

_GROUP_THEME_SYNC = "rockbox_theme_sync"
_KEY_TARGETS = "targets"
_KEY_LAST_SYNC = "last_sync"
_KEY_ENABLED = "enabled"

#: Rockbox package slugs that carry the 360p family builds.
ROCKBOX_360P_SLUGS = ("rockbox-y1", "rockbox-y2", "rockbox-a5")

#: Re-download the remote overlay when the cached zip is older than this.
PAYLOAD_REFRESH = timedelta(hours=24)

#: First passive pass after startup, then the steady interval.
STARTUP_DELAY_MS = 60_000
SYNC_INTERVAL_MS = 10 * 60_000

_MAX_REMEMBERED_TARGETS = 8


def _settings(settings: Optional[QSettings] = None) -> QSettings:
    return settings if settings is not None else QSettings("innioasis", "updater")


def is_rockbox_360p_record(rec: Optional[dict]) -> bool:
    """True when a tracked-install record is Rockbox with a 360p build.

    Rockbox is recognised by software name or package slug; 240p builds
    carry a ``240p`` marker in the release label or tag and are excluded.
    """
    if not rec:
        return False
    software = str(rec.get("software_name") or "").casefold()
    slug = str(rec.get("package_slug") or "").casefold()
    if "rockbox" not in software and not slug.startswith("rockbox"):
        return False
    marker = f"{rec.get('release_label') or ''} {rec.get('tag_name') or ''}".casefold()
    return "240p" not in marker


def tracked_360p_rockbox_models(settings: Optional[QSettings] = None) -> list[str]:
    """Models whose tracked install is a 360p Rockbox build (possibly empty)."""
    from . import player_update_check as _tracking

    models: list[str] = []
    try:
        installs = _tracking.get_all_device_installs(settings)
    except Exception:
        return []
    for model, rec in (installs or {}).items():
        try:
            if model == "Y1" and is_rockbox_360p_record(rec):
                models.append(model)
        except Exception:
            continue
    return models


def remember_theme_target(model: str, target: str | Path, settings: Optional[QSettings] = None) -> None:
    """Remember a folder a themes merge was sent to (USB/storage picks)."""
    target = str(target or "").strip()
    if not model or not target:
        return
    s = _settings(settings)
    key = f"{_GROUP_THEME_SYNC}/{model}/{_KEY_TARGETS}"
    try:
        current = [t for t in (s.value(key, [], type=list) or []) if str(t).strip()]
    except Exception:
        current = []
    current = [t for t in current if t != target]
    current.insert(0, target)
    try:
        s.setValue(key, current[:_MAX_REMEMBERED_TARGETS])
    except Exception:
        pass


def remembered_theme_targets(model: str, settings: Optional[QSettings] = None) -> list[str]:
    """Previously used merge targets for ``model`` (newest first)."""
    if not model:
        return []
    s = _settings(settings)
    try:
        values = s.value(f"{_GROUP_THEME_SYNC}/{model}/{_KEY_TARGETS}", [], type=list) or []
    except Exception:
        return []
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def record_theme_sync(model: str, settings: Optional[QSettings] = None) -> None:
    """Stamp a successful themes merge time (powers the Updated-on status)."""
    if not model:
        return
    try:
        _settings(settings).setValue(
            f"{_GROUP_THEME_SYNC}/{model}/{_KEY_LAST_SYNC}",
            datetime.now(timezone.utc).isoformat(),
        )
    except Exception:
        pass


def last_theme_sync(model: str, settings: Optional[QSettings] = None) -> str:
    """ISO timestamp of the last successful merge, or ``""``."""
    if not model:
        return ""
    try:
        return str(_settings(settings).value(f"{_GROUP_THEME_SYNC}/{model}/{_KEY_LAST_SYNC}", "", type=str) or "")
    except Exception:
        return ""


def themes_auto_install_enabled(settings: Optional[QSettings] = None) -> bool:
    """The in-beta Settings toggle: off unless the user has turned it on."""
    try:
        return bool(_settings(settings).value(f"{_GROUP_THEME_SYNC}/{_KEY_ENABLED}", False, type=bool))
    except Exception:
        return False


def set_themes_auto_install_enabled(enabled: bool, settings: Optional[QSettings] = None) -> None:
    """Persist the in-beta auto-install toggle (Settings → Install Rockbox 360p Themes)."""
    try:
        _settings(settings).setValue(f"{_GROUP_THEME_SYNC}/{_KEY_ENABLED}", bool(enabled))
    except Exception:
        logger.debug("Could not persist themes auto-install toggle", exc_info=True)


def theme_zip_path() -> Path:
    from .downloads import downloads_dir
    from .theme_pack import THEME_PACK_ZIP_NAME

    return downloads_dir() / THEME_PACK_ZIP_NAME


def _zip_is_fresh(path: Path) -> bool:
    try:
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return False
    return age < PAYLOAD_REFRESH


def _download_zip(url: str, dest: Path, timeout: float = 60.0) -> bool:
    try:
        import urllib.request

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp, "wb") as handle:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        os.replace(tmp, dest)
        return dest.stat().st_size > 1024
    except Exception as e:
        logger.debug("Passive themepack download failed: %s", e)
        return False


def ensure_combined_payload() -> Optional[Path]:
    """Build (or reuse) the combined themes dir in a temp dir. Silent None on failure."""
    from . import theme_pack as _pack

    work = Path(tempfile.mkdtemp(prefix="rockbox_360p_passive_"))
    dest = work / ".rockbox"
    try:
        zip_path = theme_zip_path()
        if not zip_path.is_file() or not _zip_is_fresh(zip_path):
            if not _download_zip(_pack.THEME_PACK_URL, zip_path):
                if not zip_path.is_file():
                    # Offline with no cache: bundled base alone still helps.
                    base = _pack.bundled_rockbox_dir()
                    if base.is_dir():
                        _pack.merge_dirs(base, dest)
                        return dest
                    return None
        try:
            _pack.build_combined_rockbox_dir(zip_path, dest)
        except Exception:
            logger.debug("Passive combined build failed", exc_info=True)
            base = _pack.bundled_rockbox_dir()
            if base.is_dir():
                _pack.merge_dirs(base, dest)
            else:
                return None
        return dest if dest.is_dir() else None
    except Exception:
        logger.debug("Passive payload prep failed", exc_info=True)
        return None


def find_removable_rockbox_dirs() -> list[Path]:
    """Existing ``.rockbox`` dirs on non-internal drives (no creation, no internal)."""
    roots: list[Path] = []
    try:
        if sys.platform == "darwin":
            volumes = Path("/Volumes")
            if volumes.is_dir():
                roots.extend(p for p in volumes.iterdir())
        elif os.name == "nt":
            system = os.environ.get("SystemDrive", "C:").upper()
            for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
                drive = Path(f"{letter}:\\")
                try:
                    if drive.exists() and f"{letter}:" != system.rstrip(":"):
                        roots.append(drive)
                except OSError:
                    continue
        else:
            user = os.environ.get("USER", "")
            for base in (Path(f"/run/media/{user}") if user else None, Path("/media"), Path("/mnt")):
                if base is not None and base.is_dir():
                    try:
                        roots.extend(p for p in base.iterdir() if p.is_dir())
                    except OSError:
                        continue
    except Exception:
        return []
    found: list[Path] = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            direct = root / ".rockbox"
            if direct.is_dir():
                found.append(direct)
                continue
            # Players often expose storage with the data one level down.
            try:
                children = [c for c in root.iterdir() if c.is_dir()]
            except OSError:
                continue
            if len(children) > 32:
                continue
            for child in children:
                candidate = child / ".rockbox"
                try:
                    if candidate.is_dir():
                        found.append(candidate)
                except OSError:
                    continue
        except OSError:
            continue
    return found


class PassiveThemeSyncWorker(QThread):
    """One silent pass: push/merge the payload into every reachable target."""

    progress = Signal(str, int, int, str)  # model, done, total, label
    device_push_started = Signal(str, str)  # model, serial
    done = Signal(str, bool)  # model, ok

    def __init__(self, models: list[str], settings: Optional[QSettings] = None, parent=None):
        super().__init__(parent)
        self._models = list(models or [])
        self._settings = settings
        self._payload_parent: Optional[Path] = None

    def run(self):
        from . import adb as _adb
        from . import theme_pack as _pack

        for model in self._models:
            if self.isInterruptionRequested():
                return
            try:
                ok = self._sync_model(model, _adb, _pack)
            except Exception:
                logger.debug("Passive theme sync failed for %s", model, exc_info=True)
                ok = False
            finally:
                self._cleanup_payload()
            self.done.emit(model, ok)

    def _cleanup_payload(self) -> None:
        parent, self._payload_parent = self._payload_parent, None
        if parent is not None:
            try:
                import shutil

                shutil.rmtree(parent, ignore_errors=True)
            except Exception:
                pass

    def _sync_model(self, model: str, _adb, _pack) -> bool:
        payload = ensure_combined_payload()
        if payload is None:
            return False
        self._payload_parent = payload.parent
        merged_any = False

        # 1. ADB: every attached player (by transport — the shared serial
        # cannot tell two connected players apart), every probed .rockbox dir.
        try:
            adb_binary = _adb.find_adb_binary()
        except Exception:
            adb_binary = None
        if adb_binary:
            try:
                devices = _adb.adb_device_list(adb_binary)
            except Exception:
                devices = []
            for device in devices or []:
                if self.isInterruptionRequested():
                    return merged_any
                try:
                    dirs = _adb.adb_find_all_rockbox_dirs(adb_binary, device)
                except Exception:
                    dirs = []
                for device_dir in dirs or []:
                    self.device_push_started.emit(model, str(device))
                    try:
                        ok, _msg = _adb.adb_push_merge(
                            adb_binary, device, payload, device_dir,
                            progress_cb=lambda d, t, m=model: self.progress.emit(m, d, t, f"adb:{device}"),
                        )
                    except Exception:
                        ok = False
                    merged_any = merged_any or ok

        # 2. Remembered USB/storage folders.
        try:
            targets = remembered_theme_targets(model, self._settings)
        except Exception:
            targets = []
        total_files: Optional[int] = None
        for target in targets or []:
            if self.isInterruptionRequested():
                return merged_any
            try:
                dest = Path(target)
                if not dest.is_dir():
                    continue
                count = _pack.merge_dirs(
                    payload, dest,
                    progress_cb=lambda d, t, m=model: self.progress.emit(m, d, t, "usb"),
                )
                if total_files is None:
                    total_files = count
                merged_any = True
            except Exception:
                continue

        # 3. Removable drives carrying a .rockbox dir right now.
        try:
            removable = find_removable_rockbox_dirs()
        except Exception:
            removable = []
        for dest in removable or []:
            if self.isInterruptionRequested():
                return merged_any
            try:
                _pack.merge_dirs(payload, dest)
                merged_any = True
                try:
                    remember_theme_target(model, str(dest), self._settings)
                except Exception:
                    pass
            except Exception:
                continue

        if merged_any:
            try:
                record_theme_sync(model, self._settings)
            except Exception:
                pass
        return merged_any


class ThemeSyncService(QObject):
    """Owns the passive worker; forwards live progress into open wizards."""

    def __init__(self, settings: Optional[QSettings] = None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._worker: Optional[PassiveThemeSyncWorker] = None
        self._timer = QTimer(self)
        self._timer.setInterval(SYNC_INTERVAL_MS)
        self._timer.timeout.connect(self.run_once)

    def start(self) -> None:
        """Begin: one delayed pass, then the steady interval. Idle-safe."""
        QTimer.singleShot(STARTUP_DELAY_MS, self.run_once)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.requestInterruption()
                worker.wait(3000)
            except Exception:
                pass

    def run_once(self) -> None:
        """Start a silent pass if the in-beta toggle is on and any model tracks a 360p build."""
        if not themes_auto_install_enabled(self._settings):
            return
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            models = tracked_360p_rockbox_models(self._settings)
        except Exception:
            return
        if not models:
            return
        worker = PassiveThemeSyncWorker(models, self._settings, parent=self)
        worker.progress.connect(self._on_progress)
        worker.device_push_started.connect(self._on_device_push_started)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_worker_finished(self) -> None:
        self._worker = None

    def _open_wizards(self, model: str) -> list:
        try:
            from . import theme_pack as _pack

            return [w for w in _pack.open_wizards() if getattr(w, "model", None) == model]
        except Exception:
            return []

    def _on_progress(self, model: str, done: int, total: int, label: str) -> None:
        for wiz in self._open_wizards(model):
            try:
                wiz.note_passive_progress(done, total, label)
            except Exception:
                pass

    def _on_device_push_started(self, model: str, serial: str) -> None:
        # A passive push starting while the user watches for ADB on the same
        # model: hand the wizard the live serial instead of the timeout wait.
        for wiz in self._open_wizards(model):
            try:
                if wiz.maybe_adopt_passive_push(serial):
                    break
            except Exception:
                pass
