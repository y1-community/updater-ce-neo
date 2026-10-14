"""Test MTK reset command and DA finish handling."""

from __future__ import annotations

import sys
from struct import pack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import mtk_api
from src.flash_service import mtk_reset_to_normal


def test_dalegacy_finish_patch_sends_finish_packet():
    """Verify DALegacy.finish runtime patch correctly handles ACK bytes and sends FINISH_CMD with value 0."""
    mtk_api._ensure_imports()
    from mtkclient.Library.DA.legacy.dalegacy_lib import DALegacy
    from mtkclient.Library.DA.legacy.dalegacy_param import Cmd, Rsp

    class MockLegacy(DALegacy):
        def __init__(self):
            self.Cmd = Cmd()
            self.Rsp = Rsp()
            self.writes = []
            self.reads = [b"\x5a", b"\x5a"]
            self.mtk = SimpleNamespace(port=MagicMock())

        def usbwrite(self, data):
            self.writes.append(bytes(data) if isinstance(data, (bytes, bytearray)) else data)
            return True

        def usbread(self, n):
            if self.reads:
                return self.reads.pop(0)
            return b""

    mock = MockLegacy()
    # Call patched finish
    result = mock.finish(0)
    assert result is True
    # First write must be FINISH_CMD (0xD9)
    assert mock.writes[0] == b"\xd9"
    # Second write must be 32-bit big-endian 0 (DISCONNECT_USB_AND_RELEASE_POWERKEY)
    assert mock.writes[1] == pack(">I", 0)

    # Call patched shutdown
    mock.reads = [b"\x5a", b"\x5a"]
    mock.writes.clear()
    shut_res = mock.shutdown(bootmode=0)
    assert shut_res is True
    assert mock.writes[0] == b"\xd9"
    assert mock.writes[1] == pack(">I", 0)
    mock.mtk.port.close.assert_called_with(reset=True)


def test_fire_mtk_reset_timeout_handling():
    """Verify fire_mtk_reset executes without raising exceptions and respects timeout."""
    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.communicate.return_value = ("Reset command was sent.", "")
        proc.returncode = 0
        mock_popen.return_value = proc

        assert mtk_api.fire_mtk_reset(timeout=1.0) is True
        mock_popen.assert_called_once()
        cmd_args = mock_popen.call_args[0][0]
        assert "reset" in cmd_args or "--mtk-reset" in cmd_args


def test_mtk_reset_to_normal_success_flow():
    """Verify mtk_reset_to_normal calls loader shutdown."""
    loader = SimpleNamespace(shutdown=MagicMock(return_value=True))
    mtk = SimpleNamespace(daloader=loader, port=MagicMock())
    assert mtk_reset_to_normal(mtk) is True
    loader.shutdown.assert_called_once_with(bootmode=0)
    mtk.port.close.assert_called_once_with(reset=True)
