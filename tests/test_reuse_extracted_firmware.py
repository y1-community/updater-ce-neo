"""A finished extract is reused on a new install attempt, even with no archive.

``_extract`` deletes the zip once its files are on disk, so requiring the
archive to exist made every retry after a successful extract download the
firmware again.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import flash_service  # noqa: E402


def test_a_finished_extract_is_complete_without_its_archive(tmp_path):
    extract = tmp_path / ".Y1_extracted"
    extract.mkdir()
    (extract / "MT6582_Android_scatter.txt").write_text("file_name: a.img\n", encoding="utf-8")
    (extract / "a.img").write_bytes(b"x")
    flash_service._mark_extract_complete(extract)

    assert flash_service._is_extract_complete(extract) is True
    assert not any(p.suffix == ".zip" for p in tmp_path.iterdir())

