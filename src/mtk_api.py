"""Thin wrapper over the bundled mtkclient (the Chinese build's fork).

``vendor/mtkclient`` is the mtkclient **as shipped inside InnioasisUpdater 1.1.7**
(its legacy DA write path, DRAM bring-up, USB read loop and error strings), so a
guided install writes the flash the same way the Chinese app does. The wrappers
here stay additive: they add the cross-platform libusb backend, the config
fields mtkclient's own argparse would have created, and one USB-stall guard the
fork is missing, and they never replace fork behaviour we can use as-is.

Originally a port of InniUpdaterChin's ``mtk_api``.

``handshake``/``attach`` split the session bring-up so the flash service can
inspect the mode the target answered in before the download agent is
uploaded; ``connect`` keeps the original one-shot behaviour for the debug
demo and external callers.

IMPORTANT: All mtkclient imports are deferred to function call time. This
prevents the ``sys.stdout.detach()`` crash in frozen PyInstaller builds
(console=False) where sys.stdout starts as None. The stdout/stderr fix
must execute before any mtkclient code runs.
"""

import io
import logging
import os
import sys
import time

from . import paths

# Ensure mtkclient is importable (adds vendor dir to sys.path).
paths.ensure_mtkclient_importable()

from pathlib import Path

# Module-level references populated on first use.
_DaHandler = None
_Mtk = None
_MtkConfig = None
_imports_done = False
logger = logging.getLogger(__name__)


def find_cross_platform_libusb() -> str | None:
    """Find libusb-1.0 binary path across Windows, macOS, and Linux."""
    if sys.platform.startswith("darwin"):
        return paths.find_libusb_dylib() or "libusb-1.0.dylib"
    elif sys.platform.startswith("win") or os.name == "nt":
        candidates = [
            Path("libusb-1.0.dll"),
            Path(paths.MTKCLIENT_DIR) / "mtkclient" / "Windows" / "libusb-1.0.dll",
            Path(paths.BUNDLE_DIR) / "libusb-1.0.dll",
            Path(paths.BUNDLE_DIR) / "mtkclient" / "Windows" / "libusb-1.0.dll",
        ]
        for c in candidates:
            if c.is_file():
                return str(c.resolve())
        return "libusb-1.0.dll"
    else:
        # Linux: check AppImage bundled libraries, multiarch paths, and sonames
        appdir = os.environ.get("APPDIR")
        candidates = []
        if appdir:
            candidates.extend([
                os.path.join(appdir, "usr", "lib", "libusb-1.0.so.0"),
                os.path.join(appdir, "usr", "lib", "x86_64-linux-gnu", "libusb-1.0.so.0"),
                os.path.join(appdir, "usr", "lib", "libusb-1.0.so"),
            ])
        candidates.extend([
            "/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0",
            "/usr/lib/aarch64-linux-gnu/libusb-1.0.so.0",
            "/usr/lib64/libusb-1.0.so.0",
            "/lib64/libusb-1.0.so.0",
            "/usr/lib/libusb-1.0.so.0",
            "libusb-1.0.so.0",
            "libusb-1.0.so",
        ])
        for c in candidates:
            if os.path.exists(c):
                return c
        try:
            import ctypes.util

            found = ctypes.util.find_library("usb-1.0")
            if found:
                return found
        except Exception:
            pass
        return "libusb-1.0.so.0"


def get_cross_platform_libusb_backend():
    """Create a pyusb libusb-1 backend with robust cross-platform path resolution."""
    try:
        import usb.backend.libusb1

        lib_path = find_cross_platform_libusb()
        if lib_path:
            try:
                backend = usb.backend.libusb1.get_backend(find_library=lambda x: lib_path)
                if backend is not None:
                    return backend
            except Exception as e:
                logger.debug("Failed loading libusb with %s: %s", lib_path, e)
        return usb.backend.libusb1.get_backend()
    except Exception as e:
        logger.warning("Could not instantiate libusb backend: %s", e)
        return None


def _apply_runtime_patches():
    """Apply runtime USB and platform adaptations without modifying upstream mtkclient."""
    try:
        from mtkclient.config import usb_ids

        # The bundled Chinese fork already lists these preloader PIDs; setdefault
        # keeps them if a future sync ever drops one.
        if 0x0E8D in usb_ids.default_ids:
            for pid in (0x2002, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x200A):
                usb_ids.default_ids[0x0E8D].setdefault(pid, -1)
    except Exception as e:
        logger.debug("Could not extend usb_ids: %s", e)

    try:
        from mtkclient.Library.Connection import usblib

        orig_init = usblib.UsbClass.__init__

        def patched_init(self, *args, **kwargs):
            orig_init(self, *args, **kwargs)
            backend = get_cross_platform_libusb_backend()
            if backend is not None:
                self.backend = backend

        usblib.UsbClass.__init__ = patched_init

        def patched_usbread(self, resplen=None, maxtimeout=100, w_max_packet_size=None):
            endearly = False
            if resplen is None:
                endearly = True
                resplen = self.maxsize
            if resplen <= 0:
                self.info("Warning !")
            res = bytearray()
            timeout = 0
            loglevel = self.loglevel
            epr = self.EP_IN.read
            q = self.queue
            b = self.buffer
            if w_max_packet_size is None:
                w_max_packet_size = getattr(self.EP_IN, "wMaxPacketSize", 64)
            extend = res.extend
            fast = self.fast
            buffer = None
            buflen = min(resplen, w_max_packet_size)
            if self.fast:
                buffer = b[:buflen]
            bytestoread = resplen
            import usb.core  # type: ignore

            while bytestoread > 0:
                bytestoread = resplen - len(res) if len(res) < resplen else 0
                if not q.empty():
                    # The queue holds whole USB reads. A chunk bigger than this
                    # request leaves the remainder queued, exactly like the
                    # bundled (Chinese-fork) usbread does — dropping it would
                    # lose bytes the caller is waiting for.
                    chunk = q.get(bytestoread)
                    if len(chunk) > bytestoread:
                        q.put(chunk[bytestoread:])
                        extend(chunk[:bytestoread])
                    else:
                        extend(chunk)
                    bytestoread = resplen - len(res) if len(res) < resplen else 0
                if bytestoread <= 0:
                    break
                sz = min(buflen, bytestoread)
                try:
                    if fast:
                        rlen = epr(buffer, timeout)
                        if rlen > sz:
                            self.warning("Buffer overflow")
                            q.put(buffer[rlen:])
                            if self.loglevel == logging.DEBUG:
                                import traceback
                                self.warning(traceback.format_exc())
                                self.warning(f"{rlen} vs {sz}")
                                self.warning(buffer[sz:].hex())
                                sys.stdout.flush()
                        extend(buffer[:rlen])
                        if rlen < sz and maxtimeout == -1:
                            break
                        if rlen == 0 and maxtimeout != -1:
                            timeout += 1
                            if timeout >= maxtimeout:
                                return b""
                    else:
                        # On macOS, AppleUSBHost requires reading at least wMaxPacketSize
                        # to avoid USB Overflow errors when reading small chunk sizes.
                        if sys.platform.startswith("darwin"):
                            read_sz = max(sz, w_max_packet_size)
                        else:
                            read_sz = sz
                        dt = epr(read_sz)
                        rlen = len(dt)
                        if rlen > sz:
                            extend(dt[:sz])
                            q.put(dt[sz:])
                        else:
                            extend(dt)
                        if endearly and rlen != 0:
                            break
                        if rlen < sz and maxtimeout == -1:
                            break
                        if rlen == 0 and maxtimeout != -1:
                            # A zero-byte read with no USBError is a stalled endpoint.
                            # Upstream re-reads immediately, spinning indefinitely.
                            timeout += 1
                            if timeout >= maxtimeout:
                                return b""
                except usb.core.USBError as e:
                    error = str(getattr(e, "strerror", "") or str(e))
                    if "timed out" in error.lower() or getattr(e, "backend_error_code", None) == -7:
                        self.debug("Timed out")
                        if timeout >= maxtimeout:
                            return b""
                        timeout += 1
                    elif "overflow" in error.lower():
                        self.error("USB Overflow")
                        return b""
                    elif "no such device" in error.lower():
                        self.error("Device disconnected")
                        sys.exit(1)
                    elif "pipe" in error.lower():
                        try:
                            if hasattr(self, "device"):
                                self.device.clear_halt(self.EP_IN)
                        except Exception:
                            pass
                        if timeout >= maxtimeout:
                            return b""
                        timeout += 1
                    else:
                        self.info(repr(e))
                        return b""

            if loglevel == logging.DEBUG:
                import inspect
                self.debug("USB " + inspect.currentframe().f_back.f_code.co_name + ": length(" + hex(resplen) + ")")
                self.verify_data(res[:resplen], "RX:")
            return bytes(res[:resplen])

        usblib.UsbClass.usbread = patched_usbread
    except Exception as e:
        logger.debug("Could not patch usblib: %s", e)

    try:
        from mtkclient.Library import Port

        orig_run_handshake = Port.Port.run_handshake

        def patched_run_handshake(self, retries=5):
            ep_out = self.cdc.EP_OUT.write
            ep_in = self.cdc.EP_IN.read
            maxinsize = getattr(self.cdc.EP_IN, "wMaxPacketSize", 64)

            self.cdc.set_line_coding(921600, 0, 8, 1)
            self.cdc.setcontrollinestate(rts=True)

            startcmd = b"\xa0\x0a\x50\x05"
            expected_echo = bytes(~b & 0xFF for b in startcmd)

            brom_pids = [0x3, 0xF200, 0xD1E9, 0xD1E2, 0xD1EC, 0xD1DD]
            if self.cdc.pid not in brom_pids:
                try:
                    ep_out(b"\xa0")
                except Exception:
                    pass

            leftovers = bytearray()

            def safe_read(size, timeout_ms=500):
                nonlocal leftovers
                if len(leftovers) >= size:
                    out = leftovers[:size]
                    leftovers = leftovers[size:]
                    return bytes(out)
                needed = size - len(leftovers)
                read_sz = max(needed, maxinsize) if sys.platform.startswith("darwin") else needed
                raw = ep_in(read_sz, timeout=timeout_ms)
                leftovers.extend(raw)
                if len(leftovers) >= size:
                    out = leftovers[:size]
                    leftovers = leftovers[size:]
                    return bytes(out)
                out = bytes(leftovers)
                leftovers = bytearray()
                return out

            for attempt in range(retries):
                leftovers.clear()
                received = b""
                try:
                    for byte in startcmd:
                        written = ep_out(bytes([byte]), timeout=500)
                        if written != 1:
                            raise ValueError("Write failed")

                        echo = safe_read(1, timeout_ms=500)
                        if len(echo) != 1 or echo[0] != (~byte & 0xFF):
                            raise ValueError(f"Echo mismatch: got {echo!r}, expected {~byte & 0xFF:02x}")

                        received += echo

                    if received == expected_echo:
                        self.info("Device detected :)")
                        return True

                except Exception as e:
                    self.debug(f"Handshake attempt {attempt + 1} failed: {e}")
                    time.sleep(0.01)

                try:
                    ep_in(maxinsize, timeout=50)
                except Exception:
                    pass

            self.info("Handshake failed after retries")
            return False

        Port.Port.run_handshake = patched_run_handshake

        def patched_handshake(self, maxtries=None, loop=0):
            counter = 0
            while not self.cdc.connected:
                try:
                    if maxtries is not None and counter == maxtries:
                        break
                    counter += 1
                    if self.cdc.connect():
                        if self.run_handshake():
                            return True
                        self.cdc.connected = False

                    if loop == 5:
                        sys.stdout.write('\n')
                        self.info("Hint:\n\nPower off the phone before connecting.\n" +
                                  "For brom mode, press and hold vol up, vol dwn, or all hw buttons and " +
                                  "connect usb.\n" +
                                  "For preloader mode, don't press any hw button and connect usb.\n"
                                  "If it is already connected and on, hold power for 10 seconds to reset.\n")
                        sys.stdout.write('\n')
                    if loop >= 10:
                        sys.stdout.write('.')
                    if loop >= 20:
                        sys.stdout.write('\n')
                        loop = 0
                    loop += 1
                    time.sleep(0.3)
                    sys.stdout.flush()

                except Exception as serr:
                    if "access denied" in str(serr):
                        self.warning(str(serr))
                    self.debug(str(serr))
                    pass
            return False

        Port.Port.handshake = patched_handshake
    except Exception as e:
        logger.debug("Could not patch Port: %s", e)

    try:
        from struct import pack
        from mtkclient.Library.DA.legacy import dalegacy_lib

        def patched_finish(self, value):
            self.usbwrite(self.Cmd.FINISH_CMD)  # 0xD9
            ack = self.usbread(1)
            ack_val = ack[0] if ack else None
            expected = self.Rsp.ACK[0] if isinstance(self.Rsp.ACK, (bytes, bytearray)) else self.Rsp.ACK
            if ack_val in (expected, 0x5A):
                self.usbwrite(pack(">I", value))
                ack2 = self.usbread(1)
                ack2_val = ack2[0] if ack2 else None
                if ack2_val in (expected, 0x5A):
                    return True
            return False

        dalegacy_lib.DALegacy.finish = patched_finish

        def patched_legacy_shutdown(self, async_mode: int = 0, dl_bit: int = 0, bootmode: int = 0):
            res = self.finish(bootmode)
            try:
                self.mtk.port.close(reset=True)
            except Exception:
                pass
            return res

        dalegacy_lib.DALegacy.shutdown = patched_legacy_shutdown
    except Exception as e:
        logger.debug("Could not patch DALegacy finish/shutdown: %s", e)


class _DetachShield:
    """Stand-in stream used only while mtkclient is imported.

    The bundled (Chinese-fork) ``Library/utils.py`` re-wraps the real streams at
    import time — ``io.TextIOWrapper(sys.stdout.detach())`` — with no guard. In a
    frozen build (``console=False``) ``sys.stdout`` is None and under pytest it
    is a capture object, so that call raises and takes the whole import with it.
    Detaching therefore hands back a throwaway binary sink: the wrapper
    mtkclient builds writes nowhere for the few milliseconds the import takes,
    and the real streams are put back immediately afterwards, so nothing
    downstream ever sees it.
    """

    def __init__(self, stream):
        self._stream = stream

    def detach(self):
        return io.BytesIO()

    def write(self, s):
        if self._stream and hasattr(self._stream, "write"):
            return self._stream.write(s)

    def flush(self):
        if self._stream and hasattr(self._stream, "flush"):
            return self._stream.flush()

    def __getattr__(self, name):
        if self._stream is not None:
            return getattr(self._stream, name)
        raise AttributeError(name)


def _ensure_imports():
    """Lazily import mtkclient after fixing stdout/stderr for frozen builds."""
    global _DaHandler, _Mtk, _MtkConfig, _imports_done
    if _imports_done:
        return

    # Frozen GUI builds (console=False) start with sys.stdout / sys.stderr
    # set to None, and in pytest or redirected streams detach() breaks the parent
    # capture; mtkclient's Library/utils.py re-wraps both streams at import time
    # (io.TextIOWrapper(sys.stdout.detach(), ...)). Shield them with a stream
    # whose detach() is a throwaway sink, then restore them afterwards.
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    sys.stdout = _DetachShield(orig_stdout)
    sys.stderr = _DetachShield(orig_stderr)

    try:
        _apply_runtime_patches()

        from mtkclient.Library.DA.mtk_da_handler import DaHandler  # type: ignore
        from mtkclient.Library.mtk_class import Mtk  # type: ignore
        from mtkclient.config.mtk_config import MtkConfig  # type: ignore

        _DaHandler = DaHandler
        _Mtk = Mtk
        _MtkConfig = MtkConfig
        _imports_done = True
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr


def init(loader=None, preloader=None, serialport=None, loglevel=logging.INFO, auth=None):
    """Initialise an MTK connection context.

    Mirrors the upstream (InniUpdaterChin / mtkclient) signature. The camelCase
    variant was a porting typo that made every MTKClient install die with a
    TypeError.

    ``loader`` is mtkclient's download-agent override (its CLI's ``--loader``):
    when given, DA autodetection is disabled and exactly that file is uploaded.
    ``auth`` is the secure-boot authentication file (its CLI's ``--auth``); the
    preloader only sends one when explicitly present and the target needs it,
    so a missing file is reported and ignored rather than fatal.
    """
    _ensure_imports()

    config = _MtkConfig(loglevel=loglevel, gui=None, guiprogress=None)
    config.loader = loader
    config.reconnect = False
    if preloader and os.path.exists(preloader):
        config.preloader_filename = preloader
        config.preloader = open(config.preloader_filename, "rb").read()
    if auth:
        if os.path.exists(auth):
            # mtkclient reads config.auth itself during the BROM handshake
            # (mtk_preloader.send_auth), so only the path is stored here.
            config.auth = auth
        else:
            logging.getLogger(__name__).warning(
                "Auth file not found, continuing without one: %s", auth
            )
    mtk = _Mtk(config=config, loglevel=loglevel, serialportname=serialport)
    return mtk


def ensure_config_defaults(mtk):
    """Fill in the config fields mtkclient's own CLI would have set.

    ``gpt_settings`` is dereferenced while parsing partition tables; mtkclient
    only creates it in its argparse path, so an embedded session must create
    it or partition reads fail with ``NoneType`` errors.

    ``reconnect`` is explicitly disabled so ``dalegacy_lib.py`` does not
    issue a USB bus reset (port.close(reset=True)) after Stage 2 DA upload,
    matching reference Chinese build behavior and preventing macOS/Linux hangs.
    """
    if getattr(mtk.config, "gpt_settings", None) is None:
        from mtkclient.Library.Partitions.gpt import GptSettings  # type: ignore

        mtk.config.gpt_settings = GptSettings(0, 0, 0)
    if hasattr(mtk.config, "reconnect"):
        mtk.config.reconnect = False
    return mtk


def handshake(mtk, directory="."):
    """Open the port and complete the preloader/BROM handshake.

    Split out of :func:`connect` because the mode the target answers in
    decides how the session is brought up (a preloader-mode target has to be
    restarted into BROM before the DA can run, see :func:`arm_brom_boot`), and
    the handshake is single-shot: it must not be repeated once DA
    configuration has started.

    Returns ``(mtk, da_handler)`` with ``mtk`` set to ``None`` on failure.
    """
    _ensure_imports()
    ensure_config_defaults(mtk)

    da_handler = _DaHandler(mtk, logging.INFO)
    mtk = da_handler.connect(mtk, directory)
    if mtk is None:
        return (None, None)
    if getattr(mtk.config, "target_config", None) is None:
        return (None, None)
    try:
        mtk._da_handler = da_handler
    except Exception:
        pass
    return (mtk, da_handler)


def attach(mtk, directory=".", da_handler=None):
    """Upload the DA on a session whose handshake is already done.

    ``DaHandler.connect()`` always calls ``preloader.init()`` again, and the
    handshake is one-shot: a second one fails. Only the steps that come after
    the handshake are performed here, which is what lets the worker handshake
    once, decide on the target's mode, and then upload the DA.
    """
    _ensure_imports()
    ensure_config_defaults(mtk)

    if da_handler is None:
        da_handler = getattr(mtk, "_da_handler", None)
    if da_handler is None:
        da_handler = _DaHandler(mtk, logging.INFO)
    mtk.config.hwparam_path = str(directory)
    mtk = da_handler.configure_da(mtk)
    return (mtk, da_handler)


def connect(mtk, directory=".", on_connected=None):
    mtk, da_handler = handshake(mtk, directory)
    if mtk is None:
        return (None, None)
    if on_connected:
        try:
            on_connected()
        except Exception as e:
            logging.getLogger(__name__).debug("on_connected callback failed: %s", e)
    mtk, da_handler = attach(mtk, directory)
    return (mtk, da_handler)


def is_brom(mtk) -> bool:
    """True when the target answered in BROM mode (USB 0E8D:0003)."""
    return bool(getattr(getattr(mtk, "config", None), "is_brom", False))


def mode_label(mtk) -> str:
    return "BROM" if is_brom(mtk) else "preloader"


def close_port(mtk):
    """Release the USB interface so the target can be polled/re-opened."""
    try:
        mtk.port.close(reset=True)
    except BaseException:
        try:
            mtk.port.close()
        except BaseException:
            pass


def arm_brom_boot(mtk):
    """Make the target's *next* boot come up in BROM download mode.

    Sets the USB-DL flag (download enabled, ``USBDL_BROM`` cleared so the
    boot ROM serves the port, max timeout, MT6582 magic) and marks BOOT_MISC0
    watchdog-resettable. Pair with :func:`trigger_reset`.
    """
    mtk.preloader.reset_to_brom(en=True)
    return True


def trigger_reset(mtk):
    """Reset the target by enabling its watchdog (mtkclient's meta recipe)."""
    wdg_addr, _value = mtk.config.get_watchdog_addr()
    return bool(mtk.preloader.write32(wdg_addr + 0x14, 0x00001209))


def fire_mtk_reset(directory=".", timeout: float = 3.0) -> bool:
    """Fire the mtk reset (mtk.py reset) command to reboot the device into the installed firmware.

    Spawns mtk.py reset (or --mtk-reset in frozen binaries) so the device
    boots automatically after an mtkclient install.
    """
    try:
        import subprocess
        cmd = [sys.executable]
        repo_root = str(getattr(paths, "REPO_ROOT", Path(__file__).resolve().parent.parent))
        env = dict(os.environ)
        if not getattr(sys, "frozen", False):
            mtk_script = str(getattr(paths, "MTKCLIENT_DIR", Path(repo_root) / "vendor" / "mtkclient") / "mtk.py")
            if os.path.exists(mtk_script):
                cmd.extend([mtk_script, "reset"])
            else:
                cmd.extend(["-m", "src.app", "--mtk-reset"])
        else:
            cmd.extend(["--mtk-reset"])
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=repo_root,
            env=env,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            logger.info("mtk reset output: %s", (stdout or "").strip())
            return proc.returncode == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            logger.debug("mtk reset process timed out after %ss (device may already be booting)", timeout)
            return True
    except BaseException as e:
        logger.debug("Could not spawn mtk reset: %s", e)
        return False


def main():
    """Leftover debug/demo: dump 16384 sectors from offset 0."""
    mtk = init(None, None)
    mtk, da_handler = connect(mtk, directory=".")
    data = da_handler.da_rs(start=0, sectors=16384, filename="", parttype="user", display=False)
    print(data.hex())


if __name__ == "__main__":
    main()
