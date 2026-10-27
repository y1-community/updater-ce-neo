"""Tests for RAR file extraction and tool location across platforms."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from src import paths
from src.flash_service import _calc_dir_size, ExtractWorker


def test_find_unrar_resolves():
    """Verify find_unrar locates bundled UnRAR.exe on Windows."""
    tool = paths.find_unrar()
    assert tool != "", "find_unrar should find bundled tools/windows/UnRAR.exe or system unar/unrar"
    assert Path(tool).exists()


def test_calc_dir_size(tmp_path):
    """Verify _calc_dir_size accurately sums directory bytes."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "file1.bin").write_bytes(b"A" * 1024)
    (sub / "file2.bin").write_bytes(b"B" * 2048)

    size = _calc_dir_size(tmp_path)
    assert size == 3072


def test_extract_rar_selects_unar_arguments(tmp_path):
    """When tool is unar, command line flags must be -f -o <dir> <archive>."""
    worker = ExtractWorker("fake.rar")
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()

    fake_proc = MagicMock()
    fake_proc.stdout.read1.return_value = b""
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = 0
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0

    with patch("src.paths.find_unrar", return_value="/usr/local/bin/unar"), \
         patch("subprocess.Popen", return_value=fake_proc) as mock_popen, \
         patch("os.path.getsize", return_value=5000):
        worker._extract_rar("fake.rar", extract_dir)
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/unar"
        assert "-f" in cmd
        assert "-o" in cmd
        assert str(extract_dir) in cmd


def test_extract_rar_selects_unrar_arguments(tmp_path):
    """When tool is UnRAR, command line flags must be x -o+ -y <archive> <dir>/."""
    worker = ExtractWorker("fake.rar")
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()

    fake_proc = MagicMock()
    fake_proc.stdout.read1.return_value = b""
    fake_proc.stdout.read.return_value = b""
    fake_proc.poll.return_value = 0
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0

    with patch("src.paths.find_unrar", return_value="C:\\tools\\UnRAR.exe"), \
         patch("subprocess.Popen", return_value=fake_proc) as mock_popen, \
         patch("os.path.getsize", return_value=5000):
        worker._extract_rar("fake.rar", extract_dir)
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "C:\\tools\\UnRAR.exe"
        assert "x" in cmd
        assert "-o+" in cmd
        assert "-y" in cmd
