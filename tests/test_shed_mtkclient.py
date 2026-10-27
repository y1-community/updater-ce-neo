"""Tests for payload shedding on Windows and MTKClient optional availability."""

from unittest.mock import patch
from src import mtk_api
from src.app import _maybe_shed_redundant_payload


def test_is_mtkclient_available_reflects_status():
    """is_mtkclient_available() returns a boolean indicating importability."""
    res = mtk_api.is_mtkclient_available()
    assert isinstance(res, bool)


def test_shed_mtkclient_removes_dir_when_conditions_met(tmp_path):
    """When on x86_64 Windows and SP Flash Tool is present, mtkclient is shed."""
    fake_install = tmp_path / "install"
    fake_mtk = fake_install / "mtkclient"
    fake_mtk.mkdir(parents=True)
    (fake_mtk / "dummy.py").write_text("pass", encoding="utf-8")

    fake_sp = tmp_path / "SP_Flash_Tool"
    fake_sp.mkdir(parents=True)
    (fake_sp / "flash_tool.exe").write_bytes(b"dummy")

    with patch("src.paths.IS_WINDOWS", True), \
         patch("src.paths.IS_MAC", False), \
         patch("platform.machine", return_value="AMD64"), \
         patch("src.paths.SP_FLASH_TOOL_DIR", fake_sp), \
         patch("src.paths.INSTALL_DIR", fake_install), \
         patch("src.paths.BUNDLE_DIR", fake_install), \
         patch("sys.frozen", True, create=True):
        _maybe_shed_redundant_payload()
        assert not fake_mtk.exists(), "mtkclient folder should have been shed"


def test_shed_mtkclient_does_not_remove_on_arm64(tmp_path):
    """On Windows ARM64, MTKClient is required and must NOT be shed."""
    fake_install = tmp_path / "install"
    fake_mtk = fake_install / "mtkclient"
    fake_mtk.mkdir(parents=True)
    (fake_mtk / "dummy.py").write_text("pass", encoding="utf-8")

    fake_sp = tmp_path / "SP_Flash_Tool"
    fake_sp.mkdir(parents=True)
    (fake_sp / "flash_tool.exe").write_bytes(b"dummy")

    with patch("src.paths.IS_WINDOWS", True), \
         patch("src.paths.IS_MAC", False), \
         patch("platform.machine", return_value="ARM64"), \
         patch("src.paths.SP_FLASH_TOOL_DIR", fake_sp), \
         patch("src.paths.INSTALL_DIR", fake_install), \
         patch("src.paths.BUNDLE_DIR", fake_install), \
         patch("sys.frozen", True, create=True):
        _maybe_shed_redundant_payload()
        assert fake_mtk.exists(), "mtkclient folder must remain intact on ARM64"
