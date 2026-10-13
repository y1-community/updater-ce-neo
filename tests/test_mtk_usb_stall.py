"""A stalled USB endpoint must end usbread, not spin it forever.

The legacy DA handshake reads a few bytes back from the download agent. On
macOS a stalled endpoint can return a zero-length read *without* raising, and
mtkclient's read loop then re-reads immediately: the install sits at its last
log line ("Checksum: 0000" in the DRAM-config step) with no error and no
progress. These tests drive ``UsbClass.usbread`` with a fake endpoint so the
budget is checked without a device.
"""

import sys
from pathlib import Path
from queue import Queue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "mtkclient"))


class _FakeEndpoint:
    def __init__(self, chunks):
        self.wMaxPacketSize = 64
        self._chunks = list(chunks)
        self.reads = 0

    def read(self, size, timeout=None):
        """PyUSB: a size asks for bytes, a buffer asks for a byte count."""
        self.reads += 1
        chunk = self._chunks.pop(0) if self._chunks else b""
        if isinstance(size, int):
            return chunk
        return len(chunk)


class _StubPort:
    """Just enough of UsbClass for usbread to run."""

    def __init__(self, chunks, fast=False):
        self.maxsize = 0x100000
        self.loglevel = 40  # logging.ERROR: keep the read loop quiet
        self.EP_IN = _FakeEndpoint(chunks)
        self.queue = Queue()
        self.buffer = bytearray(64)
        self.fast = fast
        self.timeout = 1
        self.messages = []

    # LogBase surface used by the read loop.
    def debug(self, message):
        self.messages.append(("debug", message))

    def info(self, message):
        self.messages.append(("info", message))

    def warning(self, message):
        self.messages.append(("warning", message))

    def error(self, message):
        self.messages.append(("error", message))

    def verify_data(self, data, direction):
        del data, direction


def _usb_class():
    from src import mtk_api
    mtk_api._ensure_imports()
    from mtkclient.Library.Connection.usblib import UsbClass

    return UsbClass


def test_silent_stall_finishes_instead_of_spinning():
    usb_class = _usb_class()
    port = _StubPort(chunks=[])
    result = usb_class.usbread(port, 4, maxtimeout=3)
    assert result == b""
    # One read per budget unit: it gives up rather than looping for ever.
    assert port.EP_IN.reads <= 5, port.EP_IN.reads


def test_silent_stall_counts_in_fast_mode_too():
    usb_class = _usb_class()
    port = _StubPort(chunks=[], fast=True)
    result = usb_class.usbread(port, 4, maxtimeout=3)
    assert result == b""
    assert port.EP_IN.reads <= 5, port.EP_IN.reads


def test_pending_bytes_are_still_returned():
    usb_class = _usb_class()
    port = _StubPort(chunks=[b"\x00\x00\x00\x00"])
    assert usb_class.usbread(port, 4) == b"\x00\x00\x00\x00"
    assert port.EP_IN.reads == 1


def test_short_then_complete_read_is_joined():
    usb_class = _usb_class()
    port = _StubPort(chunks=[b"\x01\x02", b"\x03\x04"])
    assert usb_class.usbread(port, 4) == b"\x01\x02\x03\x04"
