"""Dropped firmware is accepted only for zip, rar, scatter files, and scatter folders."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.drop_install import (
    classify_drop,
    first_accepted_drop,
    package_check_error,
)
from src.i18n import _STRINGS


def test_accepted_extensions_and_scatter_folders():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        archive = root / "rom.zip"
        archive.write_bytes(b"PK")
        rar = root / "rom.rar"
        rar.write_bytes(b"Rar")
        other = root / "notes.txt"
        other.write_text("hello", encoding="utf-8")
        loose = root / "loose"
        loose.mkdir()
        scatter = loose / "MT6572_Android_scatter.txt"
        scatter.write_text("file_name: boot.img\n", encoding="utf-8")
        empty = root / "empty_dir"
        empty.mkdir()
        packed = root / "firmware"
        packed.mkdir()
        (packed / "MT6582_Android_scatter.txt").write_text(
            "file_name: system.img\n", encoding="utf-8"
        )

        assert classify_drop(archive) == "archive"
        assert classify_drop(rar) == "archive"
        assert classify_drop(scatter) == "scatter"
        assert classify_drop(other) is None
        assert classify_drop(empty) is None
        assert classify_drop(packed) == "folder"
        assert first_accepted_drop([other, archive]) == archive
        assert first_accepted_drop([other]) is None

        assert package_check_error(loose) == "missing_images"
        (loose / "boot.img").write_bytes(b"img")
        assert package_check_error(loose) == ""
        assert package_check_error(packed) == "missing_images"
        (packed / "system.img").write_bytes(b"img")
        assert package_check_error(packed) == ""


def test_drop_overlay_string_in_six_locales():
    table = _STRINGS["drop_firmware_here"]
    assert table["en"] == "Drop firmware file here to install"
    for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
        text = table.get(lang) or ""
        assert text.strip()
        assert text != "drop_firmware_here"


if __name__ == "__main__":
    test_accepted_extensions_and_scatter_folders()
    test_drop_overlay_string_in_six_locales()
    print("drop install tests passed")
