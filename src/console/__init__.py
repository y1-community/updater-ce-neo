"""Terminal front ends for Updater CE.

Everything in this package must import with the standard library alone. A
Raspberry Pi Zero, a 32-bit netbook or a headless server has no PySide6 wheel,
so the console UI, the catalogue client and the flasher wrappers cannot depend
on Qt, requests or any compiled module.
"""

__all__ = ["catalog", "download", "flash", "cli", "tui"]
