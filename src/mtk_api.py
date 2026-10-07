"""Thin wrapper over the bundled mtkclient (port of InniUpdaterChin's ``mtk_api``).

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

from . import paths

# Ensure mtkclient is importable (adds vendor dir to sys.path).
paths.ensure_mtkclient_importable()

# Module-level references populated on first use.
_DaHandler = None
_Mtk = None
_MtkConfig = None
_imports_done = False


def _ensure_imports():
    """Lazily import mtkclient after fixing stdout/stderr for frozen builds."""
    global _DaHandler, _Mtk, _MtkConfig, _imports_done
    if _imports_done:
        return

    # Frozen GUI builds (console=False) start with sys.stdout / sys.stderr
    # set to None; mtkclient's Library/utils.py re-wraps them at import time
    # (io.TextIOWrapper(sys.stdout.detach(), ...)) and crashes on None.
    # Provide no-op streams BEFORE any mtkclient import.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    from mtkclient.Library.DA.mtk_da_handler import DaHandler  # type: ignore
    from mtkclient.Library.mtk_class import Mtk  # type: ignore
    from mtkclient.config.mtk_config import MtkConfig  # type: ignore

    _DaHandler = DaHandler
    _Mtk = Mtk
    _MtkConfig = MtkConfig
    _imports_done = True


def init(loader=None, preloader=None, serialport=None, loglevel=logging.INFO):
    """Initialise an MTK connection context.

    Mirrors the upstream (InniUpdaterChin / mtkclient) signature. The camelCase
    variant was a porting typo that made every MTKClient install die with a
    TypeError.
    """
    _ensure_imports()

    config = _MtkConfig(loglevel=loglevel, gui=None, guiprogress=None)
    config.loader = loader
    config.reconnect = False
    if preloader and os.path.exists(preloader):
        config.preloader_filename = preloader
        config.preloader = open(config.preloader_filename, "rb").read()
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


def main():
    """Leftover debug/demo: dump 16384 sectors from offset 0."""
    mtk = init(None, None)
    mtk, da_handler = connect(mtk, directory=".")
    data = da_handler.da_rs(start=0, sectors=16384, filename="", parttype="user", display=False)
    print(data.hex())


if __name__ == "__main__":
    main()
