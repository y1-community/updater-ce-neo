"""One-off patch: backend selection, attempt timeout, error guidance.

Source files are CRLF, so multi-line replacements are written in LF here and
converted per file. Run once, then delete.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def sub(rel, old, new, count=1):
    p = ROOT / rel
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    o = old.replace("\n", nl)
    n = new.replace("\n", nl)
    found = raw.count(o)
    if found != count:
        raise SystemExit(
            f"{rel}: expected {count} match(es), found {found} for:\n{o[:400]}"
        )
    p.write_bytes(raw.replace(o, n, count).encode("utf-8"))
    print(f"patched {rel}")


# ---------------------------------------------------------------- flash_service
FS = "src/flash_service.py"

sub(
    FS,
    '''def normalise_method(method):
    """Coerce a persisted (possibly legacy) setting into a usable method id.

    Legacy ``"auto"`` resolves to the platform default so users land on SP
    Flash Tool rather than an ambiguous automatic choice; on macOS every
    value collapses to MTKClient, the only backend that exists there.
    """
    value = str(method or "").strip().lower()
    if value not in (METHOD_SP, METHOD_MTK, METHOD_MTK_MAC, "auto"):
        return default_flash_method()
    if paths.IS_MAC:
        return METHOD_MTK
    return default_flash_method() if value == "auto" else value


def backend_method(method):
    """Map an install method onto the backend id FlashWorker dispatches on."""
    return METHOD_MTK if normalise_method(method) == METHOD_MTK_MAC else normalise_method(method)
''',
    '''def normalise_method(method):
    """Coerce a persisted (possibly legacy) setting into a usable method id.

    Legacy ``"auto"`` resolves to the platform default so users land on SP
    Flash Tool rather than an ambiguous automatic choice; on macOS every
    value collapses to MTKClient, the only backend that exists there.

    ``METHOD_MTK_MAC`` is the hidden *simulated-macOS* debug entry, not a
    backend in its own right: it runs MTKClient together with the mac flow,
    and the launch flag that turns that flow on (``--simulate-macos`` /
    ``INNIOASIS_SIMULATE_MACOS``) is scoped to one session. Outside that
    session the value means nothing, so it must not pin the app to
    MTKClient. Left sticky, it made SP Flash Tool unreachable on Windows and
    Linux after the M/D debug keys had been used once.
    """
    value = str(method or "").strip().lower()
    if value not in (METHOD_SP, METHOD_MTK, METHOD_MTK_MAC, "auto"):
        return default_flash_method()
    if paths.IS_MAC:
        return METHOD_MTK
    if value in ("auto", METHOD_MTK_MAC):
        return default_flash_method()
    return value


def backend_method(method):
    """Map an install method onto the backend id FlashWorker dispatches on.

    ``normalise_method`` has already folded the simulated-macOS entry into a
    real backend (MTKClient while the simulation is running, the platform
    default otherwise), so this is a thin alias kept for the call sites and
    tests that name the concept.
    """
    return normalise_method(method)


# --- MediaTek / SP Flash Tool error knowledge --------------------------------
# The console tool reports failures as ``S_NAME (code)`` on stdout and in its
# own QT_FLASH_TOOL.log. The raw name and code stay visible for support; the
# handful below are the ones a normal user can actually hit, and each gets an
# i18n key so the error page can say what happened and what to try.
SP_ERROR_GUIDANCE = {
    "S_TIMEOUT": "err_sp_timeout",
    "S_BROM_CMD_STARTCMD_FAIL": "err_sp_brom_startcmd",
    "S_CHIP_TYPE_NOT_MATCH": "err_sp_wrong_model",
    "S_FT_DOWNLOAD_FAIL": "err_sp_download_fail",
    "S_DA_INIT_SYNC_ERROR": "err_sp_da_init_sync",
    "S_BROM_DOWNLOAD_DA_FAIL": "err_sp_brom_da_fail",
    "S_COM_PORT_OPEN_FAIL": "err_sp_com_port",
    "S_FT_ENABLE_DRAM_FAIL": "err_sp_enable_dram",
    "S_DA_SDMMC_READ_FAILED": "err_sp_storage_read",
}

# Numeric-only fallback for the tail of a run that only carries
# ``Exception: err_code[4008]``.
SP_ERROR_CODE_NAMES = {
    "1042": "S_TIMEOUT",
    "2005": "S_BROM_CMD_STARTCMD_FAIL",
    "3184": "S_CHIP_TYPE_NOT_MATCH",
    "4008": "S_FT_DOWNLOAD_FAIL",
    "1093": "S_DA_INIT_SYNC_ERROR",
    "4032": "S_FT_ENABLE_DRAM_FAIL",
}

# Connecting the wrong player cannot be fixed by retrying or by switching
# backends, so the flow stops and says so instead of looping.
WRONG_MODEL_ERRORS = frozenset(
    {"S_CHIP_TYPE_NOT_MATCH", "DEVICE_PACKAGE_MISMATCH", "SP_WRONG_MODEL"}
)

# The app's own code for "the command ran but never showed any progress": the
# no-progress watchdog ended it and the flow may offer the other backend.
ERR_ATTEMPT_TIMEOUT = "ERR_ATTEMPT_TIMEOUT"

_SP_ERROR_NAME_RE = re.compile(r"(S_[A-Z0-9_]+)")


def sp_error_name(value) -> str:
    """``S_*`` name carried by a finished error code or tool message (or "")."""
    match = _SP_ERROR_NAME_RE.search(str(value or "").upper())
    return match.group(1) if match else ""


def sp_error_code(value) -> str:
    """Numeric MediaTek error code carried by a finished code (or "")."""
    match = re.search(r"\\((\\d{3,5})\\)", str(value or ""))
    return match.group(1) if match else ""


def is_wrong_model_error(value) -> bool:
    """True when the failure means the wrong player is connected."""
    text = str(value or "").upper()
    if any(token in text for token in WRONG_MODEL_ERRORS):
        return True
    return sp_error_name(text) in WRONG_MODEL_ERRORS


def describe_backend_error(value) -> str:
    """Translated explanation of a backend failure, or "" when unknown.

    Keeps a raw ``S_FT_DOWNLOAD_FAIL (4008)`` from being the only thing the
    user ever sees.
    """
    if not value:
        return ""
    if is_wrong_model_error(value):
        return tr("err_sp_wrong_model")
    name = sp_error_name(value) or SP_ERROR_CODE_NAMES.get(sp_error_code(value), "")
    key = SP_ERROR_GUIDANCE.get(name)
    return tr(key) if key else ""
''',
)

sub(
    FS,
    '''# One automatic retry after a DA failure (the target usually needs a power
# cycle to come back), then the retry dialog takes over.
MTK_DA_ATTEMPTS = 2
''',
    '''# One automatic retry after a DA failure (the target usually needs a power
# cycle to come back), then the retry dialog takes over.
MTK_DA_ATTEMPTS = 2
# The app's own no-progress window, counted from the moment a backend command
# starts. A command that has shown nothing by the time it elapses is ended by
# the app (the vendor tools wait far longer), the countdown is refreshed when
# the device is seen, and it is switched off as soon as the download agent or
# the images start moving. SP Flash Tool's own connection window is set to the
# same value through its console configuration file.
ATTEMPT_PROGRESS_TIMEOUT = 30.0
''',
)

sub(
    FS,
    '''    def run(self):
        while not self._stop.wait(self._interval):
            idle = time.time() - self._last
            if idle >= self._timeout and not self._warned:
                self._warned = True
                try:
                    self._on_stall(idle)
                except Exception:
                    logger.debug("MTK stall callback failed", exc_info=True)
''',
    '''    def run(self):
        while not self._stop.wait(self._interval):
            idle = time.time() - self._last
            if idle >= self._timeout and not self._warned:
                self._warned = True
                try:
                    self._on_stall(idle)
                except Exception:
                    logger.debug("MTK stall callback failed", exc_info=True)


class _AttemptWatchdog(threading.Thread):
    """Counts down the app's own no-progress window for a backend command.

    ``on_tick(remaining)`` is called once a second with the seconds left, so
    the install card can show the countdown; ``on_expire()`` is called once
    when it reaches zero. ``touch()`` refreshes the window (the device was
    seen) and ``disarm()`` ends it for good (the transfer started), which
    keeps a slow but working flash from ever being cut off.
    """

    def __init__(self, on_tick, on_expire, timeout=ATTEMPT_PROGRESS_TIMEOUT, interval=1.0):
        super().__init__(daemon=True, name="AttemptWatchdog")
        self._on_tick = on_tick
        self._on_expire = on_expire
        self._timeout = float(timeout)
        self._interval = float(interval)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._deadline = time.time() + float(timeout)
        self._armed = True

    def touch(self):
        """The device was seen: start the window over."""
        with self._lock:
            if not self._armed:
                return
            self._deadline = time.time() + self._timeout

    def disarm(self):
        """Real progress began; the window no longer applies."""
        with self._lock:
            self._armed = False
        self._stop.set()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(self._interval):
            with self._lock:
                if not self._armed:
                    return
                remaining = int(round(self._deadline - time.time()))
            if remaining <= 0:
                try:
                    self._on_expire()
                except Exception:
                    logger.debug("Attempt timeout callback failed", exc_info=True)
                return
            try:
                self._on_tick(remaining)
            except Exception:
                logger.debug("Attempt countdown tick failed", exc_info=True)
''',
)

sub(
    FS,
    '''    step_changed = Signal(str)
    progress = Signal(int)
    action_changed = Signal(str)
    log_message = Signal(str)
    finished = Signal(bool, str)  # ok, error_code
''',
    '''    step_changed = Signal(str)
    progress = Signal(int)
    action_changed = Signal(str)
    log_message = Signal(str)
    # Seconds left of the app's no-progress window while a backend command is
    # still silent; 0 once the countdown ends (progress began, or it expired).
    attempt_timeout = Signal(int)
    finished = Signal(bool, str)  # ok, error_code
''',
)

sub(
    FS,
    '''        self._tool_error = ""
        self._process = None
''',
    '''        self._tool_error = ""
        self._process = None
        # Set by the no-progress watchdog when it ends a backend command.
        self._attempt_expired = False
        self._attempt_watchdog = None
''',
)

sub(
    FS,
    '''    def _enter_usb_search(self):
        """The tool is on a search line. Show the connect sentence, still at 0%.
''',
    '''    # -- attempt no-progress watchdog ---------------------------------------
    def _start_attempt_watchdog(self, timeout=None):
        """Arm the app's own no-progress window for a backend command.

        Counted from the moment the command starts: a backend that never
        reports the player, or goes quiet right after, is ended by the app
        rather than left on the install card until the tool's own (much
        longer) timeout. ``_touch_attempt`` refreshes it when the device is
        seen and ``_note_install_progress`` switches it off once the download
        agent or the images start moving.
        """
        self._stop_attempt_watchdog()
        self._attempt_expired = False
        try:
            watchdog = _AttemptWatchdog(
                self.attempt_timeout.emit,
                self._expire_attempt,
                timeout=timeout or ATTEMPT_PROGRESS_TIMEOUT,
            )
            watchdog.start()
        except Exception:
            logger.debug("Could not start the attempt watchdog", exc_info=True)
            return None
        self._attempt_watchdog = watchdog
        return watchdog

    def _stop_attempt_watchdog(self):
        watchdog = getattr(self, "_attempt_watchdog", None)
        self._attempt_watchdog = None
        if watchdog is not None:
            watchdog.stop()
        try:
            self.attempt_timeout.emit(0)
        except Exception:
            pass

    def _touch_attempt(self):
        """The backend reported the device: start the window over."""
        watchdog = getattr(self, "_attempt_watchdog", None)
        if watchdog is not None:
            watchdog.touch()

    def _note_install_progress(self):
        """Real transfer began: the no-progress window no longer applies."""
        watchdog = getattr(self, "_attempt_watchdog", None)
        if watchdog is not None:
            watchdog.disarm()

    def _expire_attempt(self):
        """End a backend command that showed nothing inside the window."""
        if getattr(self, "_attempt_expired", False):
            return
        self._attempt_expired = True
        self._log(
            tr("err_attempt_timeout").format(seconds=int(ATTEMPT_PROGRESS_TIMEOUT))
        )
        process = getattr(self, "_process", None)
        try:
            if process is not None and process.poll() is None:
                process.terminate()
        except Exception:
            logger.debug("Could not terminate a silent backend", exc_info=True)

    def _enter_usb_search(self):
        """The tool is on a search line. Show the connect sentence, still at 0%.
''',
)

sub(
    FS,
    '''    def _leave_usb_search(self):
        """A numbered stage started. A later search line may show connect again."""
        self._usb_search_active = False
        self._past_usb_search = True
        self._device_seen = True
''',
    '''    def _leave_usb_search(self):
        """A numbered stage started. A later search line may show connect again."""
        self._usb_search_active = False
        self._past_usb_search = True
        self._device_seen = True
        # The device answered: the app's window starts over instead of ending
        # the run the moment it was making progress.
        self._touch_attempt()
''',
)

sub(
    FS,
    '''        if (
            "download da" in low
            or "of da has been sent" in low
            or "connect da" in low
        ):
            self._leave_usb_search()
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.progress.emit(12)
            return
        if "of image data has been sent" in low or "writing partition" in low:
            self._leave_usb_search()
            self.step_changed.emit(STEP_WRITE)
            return
''',
    '''        if (
            "download da" in low
            or "of da has been sent" in low
            or "connect da" in low
        ):
            self._leave_usb_search()
            self._note_install_progress()
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.progress.emit(12)
            return
        if "of image data has been sent" in low or "writing partition" in low:
            self._leave_usb_search()
            self._note_install_progress()
            self.step_changed.emit(STEP_WRITE)
            return
''',
)

for anchor in (
    '        elif "of da has been sent" in low:\n            self._leave_usb_search()\n',
    '        elif "of bootloader has been sent" in low:\n            self._leave_usb_search()\n',
    '        elif "of image data has been sent" in low:\n            self._leave_usb_search()\n',
):
    sub(FS, anchor, anchor + "            self._note_install_progress()\n")

sub(
    FS,
    '''        self._process = spawn()
        register_sp_session(getattr(self._process, "pid", 0))
''',
    '''        self._process = spawn()
        register_sp_session(getattr(self._process, "pid", 0))
        self._start_attempt_watchdog()
''',
)

sub(
    FS,
    '''        self._process.wait()
        unregister_sp_session(getattr(self._process, "pid", 0))
        return self._process.returncode
''',
    '''        self._stop_attempt_watchdog()
        self._process.wait()
        unregister_sp_session(getattr(self._process, "pid", 0))
        return self._process.returncode
''',
)

sub(
    FS,
    '''            if (
                exit_code == 0
                or self._sp_mismatch
                or self._cancelled
                or self._device_seen
''',
    '''            if (
                exit_code == 0
                or self._sp_mismatch
                or self._cancelled
                or self._attempt_expired
                or self._device_seen
''',
)

sub(
    FS,
    '''        exit_code, launch_time = self._sp_console_search(spawn, log_root)
        if exit_code is None:
            return

        if self._sp_mismatch:
''',
    '''        exit_code, launch_time = self._sp_console_search(spawn, log_root)
        if self._attempt_expired:
            # Nothing happened inside the app's own window: report it as such
            # so the flow can offer the other backend.
            self.finished.emit(False, ERR_ATTEMPT_TIMEOUT)
            return
        if exit_code is None:
            return

        if self._sp_mismatch:
''',
)

sub(
    FS,
    '''        elif self._sp_err_code:
            self.finished.emit(False, f"SP_ERR_{self._sp_err_code}")
''',
    '''        elif self._sp_err_code:
            # Name the number so the error page can explain it; the raw code
            # stays in the message for support.
            name = SP_ERROR_CODE_NAMES.get(str(self._sp_err_code), "")
            self.finished.emit(
                False,
                f"{name} ({self._sp_err_code})" if name else f"SP_ERR_{self._sp_err_code}",
            )
''',
)

sub(
    FS,
    '''            for line in iter(self._process.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("[PROGRESS] "):
''',
    '''            self._start_attempt_watchdog()
            for line in iter(self._process.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("[PROGRESS] "):
''',
)

sub(
    FS,
    '''                else:
                    self._log(line)
                    self._classify_mtk_output(line)

            self._process.wait()
            if self._cancelled:
''',
    '''                else:
                    self._log(line)
                    self._classify_mtk_output(line)

            self._stop_attempt_watchdog()
            self._process.wait()
            if self._attempt_expired:
                self.finished.emit(False, ERR_ATTEMPT_TIMEOUT)
                return
            if self._cancelled:
''',
)

sub(
    FS,
    '''def classify_tool_wait_line(line) -> str:
    """``connect``, ``forward``, or ``""`` when the line must not move the UI.

    Search / hint / dots lines are the connect phase. A later line that is
    real tool output is forward progress. Logging-error spam is ignored.
    """
    if is_logging_noise_line(line):
        return ""
''',
    '''def is_tool_trace_line(line) -> bool:
    """True for SP Flash Tool's own internal Qt trace lines.

    The tool mirrors its trace into the stream the app reads, shaped
    ``10/11/2026 17:07:43.796 FlashTool[2232][7880][D]: ...`` (the app then
    forwards it with a ``[QT] `` tag). Those lines are diagnostics, not device
    progress: reading them as forward progress pushed the install card off
    "please connect your player" and onto "please wait" while the tool was
    still searching for the device.
    """
    return "FlashTool[" in tool_line_body(line)


def classify_tool_wait_line(line) -> str:
    """``connect``, ``forward``, or ``""`` when the line must not move the UI.

    Search / hint / dots lines are the connect phase. A later line that is
    real tool output is forward progress. Logging-error spam and the tool's
    own internal trace are ignored.
    """
    if is_logging_noise_line(line):
        return ""
    if is_tool_trace_line(line):
        return ""
''',
)

sub(
    FS,
    '''    action_changed = Signal(str)
    log_message = Signal(str)
    flash_finished = Signal(bool, str)
''',
    '''    action_changed = Signal(str)
    log_message = Signal(str)
    attempt_timeout = Signal(int)
    flash_finished = Signal(bool, str)
''',
)

sub(
    FS,
    '''        worker.log_message.connect(self.log_message)
        worker.finished.connect(self.flash_finished)
''',
    '''        worker.log_message.connect(self.log_message)
        worker.attempt_timeout.connect(self.attempt_timeout)
        worker.finished.connect(self.flash_finished)
''',
)

sub(
    FS,
    '''        for sig in (worker.step_changed, worker.progress, worker.action_changed,
                    worker.log_message, worker.finished):
''',
    '''        for sig in (worker.step_changed, worker.progress, worker.action_changed,
                    worker.log_message, worker.attempt_timeout, worker.finished):
''',
)

# ------------------------------------------------------- sp_console_config.py
sub(
    "src/sp_console_config.py",
    '''CONFIG_FILE_NAME = "console_config.xml"
''',
    '''CONFIG_FILE_NAME = "console_config.xml"
# The tool's own connection window (its ``timeout-count`` attribute). The long
# default matches the payload; driven installs pass the app's no-progress
# window so tool and app give up on the same schedule.
CONNECTION_TIMEOUT_MS = 3600000
''',
)

sub(
    "src/sp_console_config.py",
    '''def build_console_config_xml(scatter, da_file, auth_file) -> str:
    """Console configuration XML for a flash that needs an auth file.

    Raises :class:`SpConfigError` when the scatter does not tell us the chip
    or storage type, since a wrong guess there is worse than no flash.
    """
''',
    '''def build_console_config_xml(
    scatter, da_file, auth_file, connection_timeout_ms: int = CONNECTION_TIMEOUT_MS
) -> str:
    """Console configuration XML for a flash that needs an auth file.

    Raises :class:`SpConfigError` when the scatter does not tell us the chip
    or storage type, since a wrong guess there is worse than no flash.

    ``connection_timeout_ms`` is the tool's connection window, the only place
    it can be set for a driven flash.
    """
''',
)

sub(
    "src/sp_console_config.py",
    '''            '        <connection type="BromUSB" high-speed="true"'
            ' without-battery="true" timeout-count="3600000" com-port="" />',
''',
    '''            '        <connection type="BromUSB" high-speed="true"'
            ' without-battery="true"'
            f' timeout-count="{int(connection_timeout_ms)}" com-port="" />',
''',
)

sub(
    "src/sp_console_config.py",
    '''def write_console_config(
    scatter, da_file, auth_file, target_dir: Optional[Path] = None
) -> Path:
    """Write the console configuration for an authenticated flash."""
    directory = Path(target_dir) if target_dir else config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILE_NAME
    path.write_text(
        build_console_config_xml(scatter, da_file, auth_file), encoding="utf-8"
    )
    return path
''',
    '''def write_console_config(
    scatter,
    da_file,
    auth_file,
    target_dir: Optional[Path] = None,
    connection_timeout_ms: int = CONNECTION_TIMEOUT_MS,
) -> Path:
    """Write the console configuration for an authenticated flash."""
    directory = Path(target_dir) if target_dir else config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILE_NAME
    path.write_text(
        build_console_config_xml(
            scatter, da_file, auth_file, connection_timeout_ms=connection_timeout_ms
        ),
        encoding="utf-8",
    )
    return path
''',
)

# ---------------------------------------------------------------- launcher.py
sub(
    "launcher.py",
    '''# Persisted simulated-macOS choice (Settings ▸ Install method ▸ "MTKClient
# (Mac)"). Re-applied here, before any ``src.*`` import, because
# paths.SIMULATE_MACOS is read at import time — long before the Settings UI
# exists. Read with the stdlib (configparser) so nothing heavier than the
# interpreter loads first; QSettings on Linux writes plain INI to this path.
if not os.environ.get("INNIOASIS_SIMULATE_MACOS") and sys.platform.startswith("linux"):
    try:
        import configparser

        config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        parser = configparser.ConfigParser()
        parser.read(config_dir / "innioasis" / "updater.conf")
        if parser.get("General", "flash_method", fallback="").strip().lower() == "mtk_mac":
            os.environ["INNIOASIS_SIMULATE_MACOS"] = "1"
    except Exception:
        pass

''',
    '''# The simulated-macOS flow is deliberately session-scoped. Choosing
# "MTKClient (Mac)" restarts the app with INNIOASIS_SIMULATE_MACOS=1 in the
# child's environment, so the flow applies for that run. It is NOT re-applied
# here from the saved install method: doing that made a value left behind by
# the M/D debug keys permanent, which turned SP Flash Tool into an unreachable
# backend on Linux and pinned every later launch to MTKClient.

''',
)

# --------------------------------------------------------------- error_page.py
sub(
    "src/ui/error_page.py",
    '''from ..i18n import tr
from .flash_page import _STEP_KEY
''',
    '''from ..flash_service import describe_backend_error
from ..i18n import tr
from .flash_page import _STEP_KEY
''',
)

sub(
    "src/ui/error_page.py",
    '''    "MISSING_IMAGES": "err_code_missing_images",
    "INTERNAL_ERROR": "err_code_internal",
''',
    '''    "MISSING_IMAGES": "err_code_missing_images",
    "INTERNAL_ERROR": "err_code_internal",
    # The tool says the chip on the cable is not the one this package targets.
    "DEVICE_PACKAGE_MISMATCH": "err_sp_wrong_model_title",
    "ERR_ATTEMPT_TIMEOUT": "err_attempt_timeout_title",
''',
)

sub(
    "src/ui/error_page.py",
    '''        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setProperty("cssClass", "hint")
        layout.addWidget(self._hint)
''',
    '''        self._cause = QLabel("")
        self._cause.setWordWrap(True)
        self._cause.setProperty("cssClass", "hint")
        self._cause.setVisible(False)
        layout.addWidget(self._cause)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setProperty("cssClass", "hint")
        layout.addWidget(self._hint)
''',
)

sub(
    "src/ui/error_page.py",
    '''    def _render(self):
        mode = self._mode
        if mode == "device":
''',
    '''    def _render(self):
        mode = self._mode
        if hasattr(self, "_cause"):
            self._cause.setVisible(False)
        if mode == "device":
''',
)

sub(
    "src/ui/error_page.py",
    '''            self._retry_row.set_value(str(self._mode_args.get("retry_count", 0)))
            ndash = '\\u2014'
''',
    '''            self._retry_row.set_value(str(self._mode_args.get("retry_count", 0)))
            guidance = describe_backend_error(self._mode_args.get("error_code"))
            self._cause.setText(guidance)
            self._cause.setVisible(bool(guidance))
            ndash = '\\u2014'
''',
)

# -------------------------------------------------------------- select_page.py
sub(
    "src/ui/select_page.py",
    '''        import sys
        from PySide6.QtCore import QSettings
        from ..paths import IS_MAC, IS_WINDOWS
        if IS_WINDOWS or IS_MAC:
            return True
        if not sys.platform.startswith("linux"):
            return True
        settings = QSettings("Innioasis", "UpdaterCE")
        method = settings.value("flash_method", "auto")
        if method == "mtk":
            return True
        return False
''',
    '''        import sys
        from PySide6.QtCore import QSettings
        from ..flash_service import METHOD_MTK, backend_method
        from ..paths import IS_MAC, IS_WINDOWS
        if IS_WINDOWS or IS_MAC:
            return True
        if not sys.platform.startswith("linux"):
            return True
        # On Linux the SP Flash Tool flow defers step 1 until the tool prints
        # its "search usb" line (the app escalates and sets up udev first).
        # MTKClient has no such line to hang it on, so its run is gated on the
        # power-off screen. NB: the same settings handle the rest of the app
        # uses — reading a differently-cased QSettings path silently returned
        # the default, so an MTKClient run on Linux never prompted at all.
        settings = QSettings("innioasis", "updater")
        return backend_method(settings.value("flash_method", "")) == METHOD_MTK
''',
)

# --------------------------------------------------------------- flash_page.py
sub(
    "src/ui/flash_page.py",
    '''        col.addWidget(self._wait_prompt_label)
''',
    '''        col.addWidget(self._wait_prompt_label)

        # The app's own no-progress countdown: shown only while a backend
        # command has produced nothing yet, and switched off the moment
        # progress begins or the install leaves this stage.
        self._attempt_lbl = QLabel("")
        self._attempt_lbl.setProperty("cssClass", "hint")
        self._attempt_lbl.setAlignment(Qt.AlignCenter)
        self._attempt_lbl.setVisible(False)
        col.addWidget(self._attempt_lbl)
''',
)

sub(
    "src/ui/flash_page.py",
    '''    def show_real_stage(self, text: str) -> None:
        """Drop the connect sentence and the throbber once the tool moves on."""
        self._prompt_only = False
''',
    '''    def set_attempt_timeout(self, seconds) -> None:
        """Show the app's own no-progress countdown for the current stage.

        ``0`` hides it: the countdown only means something while a backend
        command has produced nothing yet, and it must not outlive the stage.
        """
        label = getattr(self, "_attempt_lbl", None)
        if label is None:
            return
        try:
            remaining = int(seconds or 0)
        except (TypeError, ValueError):
            remaining = 0
        if remaining <= 0:
            label.setText("")
            label.setVisible(False)
            return
        label.setText(tr("flash_attempt_timeout").format(seconds=remaining))
        label.setVisible(True)

    def show_real_stage(self, text: str) -> None:
        """Drop the connect sentence and the throbber once the tool moves on."""
        self._prompt_only = False
        self.set_attempt_timeout(0)
''',
)

# --------------------------------------------------------------------- i18n.py
I18N_NEW = '''    "err_code_internal": {
        "zh-CN": "内部错误",
        "en": "Internal error",
        "fr": "Erreur interne",
        "es": "Error interno",
    },
    # Backend failures explained in the user's terms. The raw tool name/code
    # stays in the Error Code row above them.
    "err_sp_wrong_model_title": {
        "zh-CN": "连接了错误的型号",
        "en": "Wrong Model Connected",
        "fr": "Mauvais modèle connecté",
        "es": "Modelo incorrecto conectado",
    },
    "err_sp_wrong_model": {
        "zh-CN": "连接的播放器型号与本刷机包的目标型号不同。请断开该设备，然后为此播放器安装正确的固件。",
        "en": "The connected player is a different model than this package targets. Disconnect it, then install the firmware built for this player.",
        "fr": "Le lecteur connecté n'est pas le modèle visé par ce paquet. Débranchez-le, puis installez le firmware prévu pour ce lecteur.",
        "es": "El reproductor conectado no es el modelo al que apunta este paquete. Desconéctelo e instale el firmware de este reproductor.",
    },
    "err_sp_timeout": {
        "zh-CN": "工具没有检测到播放器。请让播放器保持关机并断开，然后在提示连接时再插入 USB。",
        "en": "The tool never saw the player. Keep it powered off and unplugged, then connect the USB cable when prompted.",
        "fr": "L'outil n'a pas vu le lecteur. Laissez-le éteint et débranché, puis branchez le câble USB à l'invite.",
        "es": "La herramienta no detectó el reproductor. Déjelo apagado y desconectado y conecte el USB cuando se le indique.",
    },
    "err_sp_brom_startcmd": {
        "zh-CN": "播放器启动过早。请完全关机后重新连接。",
        "en": "The player powered up too early. Power it off completely, then connect it again.",
        "fr": "Le lecteur s'est allumé trop tôt. Éteignez-le complètement, puis rebranchez-le.",
        "es": "El reproductor se encendió demasiado pronto. Apáguelo por completo y vuelva a conectarlo.",
    },
    "err_sp_download_fail": {
        "zh-CN": "写入被中断。请检查 USB 线与端口以及电池电量，然后重试。",
        "en": "Writing was interrupted. Check the USB cable and port and the battery charge, then try again.",
        "fr": "L'écriture a été interrompue. Vérifiez le câble et le port USB ainsi que la batterie, puis réessayez.",
        "es": "La escritura se interrumpió. Compruebe el cable y el puerto USB y la batería, y vuelva a intentarlo.",
    },
    "err_sp_da_init_sync": {
        "zh-CN": "下载代理（DA）无法启动。请尝试另一种刷机方式。",
        "en": "The download agent could not start. Try the other flash method.",
        "fr": "L'agent de téléchargement n'a pas pu démarrer. Essayez l'autre méthode de flashage.",
        "es": "El agente de descarga no pudo iniciarse. Pruebe el otro método de flasheo.",
    },
    "err_sp_brom_da_fail": {
        "zh-CN": "无法向播放器发送下载代理。请将播放器关机后重新连接。",
        "en": "The download agent could not be sent to the player. Power it off and reconnect.",
        "fr": "L'agent de téléchargement n'a pas pu être envoyé. Éteignez le lecteur et rebranchez-le.",
        "es": "No se pudo enviar el agente de descarga. Apague el reproductor y vuelva a conectarlo.",
    },
    "err_sp_com_port": {
        "zh-CN": "工具无法打开串口。请关闭其他刷机工具后重新连接。",
        "en": "The tool could not open its serial port. Close other flashing tools and reconnect.",
        "fr": "L'outil n'a pas pu ouvrir son port série. Fermez les autres outils de flashage et rebranchez.",
        "es": "La herramienta no pudo abrir su puerto serie. Cierre otras herramientas de flasheo y vuelva a conectar.",
    },
    "err_sp_enable_dram": {
        "zh-CN": "工具无法初始化播放器的内存。请使用专为此型号构建的刷机包。",
        "en": "The tool could not start the player's memory. Use the package built for this exact model.",
        "fr": "L'outil n'a pas pu initialiser la mémoire du lecteur. Utilisez le paquet prévu pour ce modèle.",
        "es": "La herramienta no pudo iniciar la memoria del reproductor. Use el paquete de este modelo exacto.",
    },
    "err_sp_storage_read": {
        "zh-CN": "工具无法读取播放器的存储。请检查连接；设备可能存在硬件故障。",
        "en": "The tool could not read the player's storage. Check the connection; the device may have a hardware fault.",
        "fr": "L'outil n'a pas pu lire le stockage du lecteur. Vérifiez la connexion ; l'appareil a peut-être un défaut matériel.",
        "es": "La herramienta no pudo leer el almacenamiento del reproductor. Compruebe la conexión; puede haber un fallo de hardware.",
    },
    "err_attempt_timeout_title": {
        "zh-CN": "设备无响应",
        "en": "The device did not respond",
        "fr": "L'appareil n'a pas répondu",
        "es": "El dispositivo no respondió",
    },
    "err_attempt_timeout": {
        "zh-CN": "刷机工具在 {seconds} 秒内没有检测到进度，已停止本次尝试。",
        "en": "The flashing tool showed no progress within {seconds} seconds, so this attempt was stopped.",
        "fr": "L'outil de flashage n'a montré aucun progrès en {seconds} secondes ; cette tentative a été arrêtée.",
        "es": "La herramienta de flasheo no mostró progreso en {seconds} segundos; se detuvo este intento.",
    },
    "flash_attempt_timeout": {
        "zh-CN": "正在等待设备… 剩余 {seconds} 秒",
        "en": "Waiting for the player… {seconds}s left",
        "fr": "En attente du lecteur… {seconds} s restantes",
        "es": "Esperando al reproductor… quedan {seconds} s",
    },
    "flash_method_confirm": {
        "zh-CN": "刷机方式已改为 {method}。请确认设备已断开并关机，然后点击“下一步”。",
        "en": "Install method changed to {method}. Confirm the player is unplugged and powered off, then click Next.",
        "fr": "Méthode changée pour {method}. Confirmez que le lecteur est débranché et éteint, puis cliquez sur Suivant.",
        "es": "Método cambiado a {method}. Confirme que el reproductor está desconectado y apagado y pulse Siguiente.",
    },
    "flash_failover_note": {
        "zh-CN": "{method} 未能开始刷机。在设备关机并断开后，将改用 {other}。",
        "en": "{method} did not get the install started. After the player is powered off and unplugged, {other} will be used instead.",
        "fr": "{method} n'a pas lancé l'installation. Une fois le lecteur éteint et débranché, {other} sera utilisé.",
        "es": "{method} no inició la instalación. Con el reproductor apagado y desconectado se usará {other}.",
    },
'''

sub(
    "src/i18n.py",
    '''    "err_code_internal": {
        "zh-CN": "内部错误",
        "en": "Internal error",
        "fr": "Erreur interne",
        "es": "Error interno",
    },
''',
    I18N_NEW,
)

print("all patches applied")
