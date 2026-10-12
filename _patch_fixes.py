"""One-off fixes found by the new tests."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
FS = "src/flash_service.py"


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


sub(FS, "import logging\nimport os\nimport re\n", "import logging\nimport math\nimport os\nimport re\n")

# --- _StallWatcher: threading.Thread._stop is a method; an Event attribute
# named _stop shadows it and breaks the thread's own bookkeeping (join()).
sub(
    FS,
    """    def __init__(self, on_stall, timeout=MTK_STALL_WARN_SECONDS, interval=2.0):
        super().__init__(daemon=True, name="MtkStallWatcher")
        self._on_stall = on_stall
        self._timeout = timeout
        self._interval = interval
        self._stop = threading.Event()
""",
    """    def __init__(self, on_stall, timeout=MTK_STALL_WARN_SECONDS, interval=2.0):
        super().__init__(daemon=True, name="MtkStallWatcher")
        self._on_stall = on_stall
        self._timeout = timeout
        self._interval = interval
        # NB: not ``_stop`` — that name is a method on threading.Thread and
        # shadowing it breaks join()/thread bookkeeping.
        self._stopped = threading.Event()
""",
)

sub(
    FS,
    """    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(self._interval):
            idle = time.time() - self._last
""",
    """    def stop(self):
        self._stopped.set()

    def run(self):
        while not self._stopped.wait(self._interval):
            idle = time.time() - self._last
""",
)

# --- _AttemptWatchdog: same shadowing, plus the countdown must not round a
# partly-elapsed window down to zero.
sub(
    FS,
    """        self._on_tick = on_tick
        self._on_expire = on_expire
        self._timeout = float(timeout)
        self._interval = float(interval)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._deadline = time.time() + float(timeout)
        self._armed = True
""",
    """        self._on_tick = on_tick
        self._on_expire = on_expire
        self._timeout = float(timeout)
        self._interval = float(interval)
        # Not ``_stop``: that is a threading.Thread method.
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._deadline = time.time() + float(timeout)
        self._armed = True
""",
)

sub(
    FS,
    """    def disarm(self):
        \"\"\"Real progress began; the window no longer applies.\"\"\"
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
""",
    """    def disarm(self):
        \"\"\"Real progress began; the window no longer applies.\"\"\"
        with self._lock:
            self._armed = False
        self._stopped.set()

    def stop(self):
        self._stopped.set()

    def run(self):
        while not self._stopped.wait(self._interval):
            with self._lock:
                if not self._armed:
                    return
                remaining = self._deadline - time.time()
            if remaining <= 0:
""",
)

sub(
    FS,
    """            try:
                self._on_tick(remaining)
            except Exception:
                logger.debug("Attempt countdown tick failed", exc_info=True)
""",
    """            try:
                # Ceil: a partly-elapsed second still has time left in it.
                self._on_tick(int(math.ceil(remaining)))
            except Exception:
                logger.debug("Attempt countdown tick failed", exc_info=True)
""",
)

# --- A search line inside SP Flash Tool's own Qt trace is still a search.
sub(
    FS,
    """def is_sp_usb_search_line(line) -> bool:
    \"\"\"True when flash_tool is searching USB.

    The connect sentence follows this prefix only: case-insensitive, with
    leading whitespace (and a log timestamp) ignored. A later mention inside
    a write log is not a search.
    \"\"\"
    text = _TIMESTAMP_PREFIX_RE.sub("", tool_line_body(line)).lstrip()
    return text.lower().startswith("search usb")
""",
    """def _strip_qt_trace_prefix(text: str) -> str:
    \"\"\"The message part of an SP Flash Tool internal Qt trace line.

    ``10/11/2026 17:07:43.796 FlashTool[2232][7880][D]: APCore::ConnMedia::
    Search(): Search usb, timeout set as 120000 ms(..\\\\..\\\\Conn.cpp,21)``
    becomes ``Search usb, timeout set as 120000 ms``. Text that is not a trace
    line is returned untouched, so the console's own lines keep working.
    \"\"\"
    if "FlashTool[" not in text:
        return text
    _head, _sep, tail = text.partition("]: ")
    tail = tail.strip()
    if tail.endswith(")"):
        open_paren = tail.rfind("(")
        if open_paren != -1:
            tail = tail[:open_paren].rstrip()
    if ": " in tail:
        tail = tail.rsplit(": ", 1)[1].strip()
    return tail


def is_sp_usb_search_line(line) -> bool:
    \"\"\"True when flash_tool is searching USB.

    The connect sentence follows this prefix only: case-insensitive, with
    leading whitespace (and a log timestamp) ignored, and recognised inside
    the tool's own internal Qt trace as well as on its console stream. A later
    mention inside a write log is not a search.
    \"\"\"
    text = _TIMESTAMP_PREFIX_RE.sub("", tool_line_body(line)).lstrip()
    return _strip_qt_trace_prefix(text).lower().startswith("search usb")
""",
)

# --- Wrong model: the error row names it in the user's terms.
sub(
    "src/ui/error_page.py",
    """from ..flash_service import describe_backend_error
""",
    """from ..flash_service import describe_backend_error, is_wrong_model_error
""",
)

sub(
    "src/ui/error_page.py",
    """    "INTERNAL_ERROR": "err_code_internal",
    # The tool says the chip on the cable is not the one this package targets.
    "DEVICE_PACKAGE_MISMATCH": "err_sp_wrong_model_title",
    "ERR_ATTEMPT_TIMEOUT": "err_attempt_timeout_title",
""",
    """    "INTERNAL_ERROR": "err_code_internal",
    "ERR_ATTEMPT_TIMEOUT": "err_attempt_timeout_title",
""",
)

sub(
    "src/ui/error_page.py",
    """def _format_error_code(code) -> str:
    \"\"\"Human-readable error code for the error page (keeps the raw code).\"\"\"
    code = str(code or "\\u2014")
    key = _ERROR_CODE_KEYS.get(code)
    if key:
        return f"{tr(key)} ({code})"
    return code
""",
    """def _format_error_code(code) -> str:
    \"\"\"Human-readable error code for the error page (keeps the raw code).\"\"\"
    code = str(code or "\\u2014")
    # A chip mismatch is the one failure whose name the user must read: it is
    # the wrong player, not a bad cable, and no retry can fix it.
    if is_wrong_model_error(code):
        return f"{tr('err_sp_wrong_model_title')} ({code})"
    key = _ERROR_CODE_KEYS.get(code)
    if key:
        return f"{tr(key)} ({code})"
    return code
""",
)

print("fixes applied")
