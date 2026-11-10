"""WSL2 USB passthrough notes for the terminal session.

Windows on ARM has no native SP Flash Tool, and MTKclient's Windows USB stack
is not always available there. A WSL2 distro can run the same text session and
the same MTKclient, but MTKclient still needs the player's USB device. Windows
does not hand USB devices to WSL automatically: ``usbipd-win`` must bind the
MediaTek device and attach it to the running distro.

This module only detects the host and writes the steps. It never changes USB
state, installs a driver, or runs an elevated command. The player resets its
USB identity when it enters BROM/preloader, so the attach step normally has to
be repeated during a flash; that is the one behaviour a normal desktop flash
does not have.
"""

from __future__ import annotations

import os
import platform
import shutil

WSL_USB_STEPS = (
    "Install usbipd-win on Windows: winget install --exact dorssel.usbipd-win",
    "In an elevated Windows terminal, list the player: usbipd list",
    "Bind the MediaTek device once: usbipd bind --busid <BUSID>",
    "Put the player in download mode, then attach it: usbipd attach --wsl --busid <BUSID>",
    "Run the terminal install; after a reset, attach again: usbipd attach --wsl --busid <BUSID>",
)

WSL_BACKEND_HINT = (
    "WSL2 is running. MTKclient needs the player attached through usbipd-win; "
    "run 'usbipd list' in Windows, bind the MediaTek device, then "
    "'usbipd attach --wsl --busid <BUSID>'. Re-attach after the player resets "
    "during the flash."
)


def is_wsl(environ: dict | None = None, release: str | None = None) -> bool:
    """True inside WSL1 or WSL2.

    ``WSL_DISTRO_NAME`` is set by WSL2 shells, ``WSL_INTEROP`` by WSL1/2
    interop, and Microsoft's kernel release carries ``microsoft``. Checking
    all three keeps the answer right in a stripped login shell and in tests
    that pass their own environment.
    """
    env = os.environ if environ is None else environ
    kernel = platform.release() if release is None else release
    return bool(
        env.get("WSL_DISTRO_NAME")
        or env.get("WSL_INTEROP")
        or "microsoft" in str(kernel or "").lower()
    )


def distro_name(environ: dict | None = None) -> str:
    env = os.environ if environ is None else environ
    return str(env.get("WSL_DISTRO_NAME") or "").strip()


def usbipd_command() -> str:
    """The Windows-side command when WSL interop puts it on PATH."""
    return shutil.which("usbipd.exe") or shutil.which("usbipd") or ""


def report(environ: dict | None = None, release: str | None = None) -> dict:
    env = os.environ if environ is None else environ
    inside = is_wsl(env, release)
    return {
        "wsl": inside,
        "wsl_distro": distro_name(env) if inside else "",
        "usbipd": usbipd_command() if inside else "",
        "wsl_steps": list(WSL_USB_STEPS) if inside else [],
    }


def startup_note() -> str:
    distro = distro_name()
    where = f" ({distro})" if distro else ""
    return (
        f"Windows Subsystem for Linux detected{where}. For flashing, attach the "
        "player with usbipd-win; run the terminal doctor for the attach steps."
    )


def no_backend_hint() -> str:
    return WSL_BACKEND_HINT
