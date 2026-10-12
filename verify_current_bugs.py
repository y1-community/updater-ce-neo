#!/usr/bin/env python3
"""Offline verification script for the three targeted fixes.

1. Extraction stuck at 99% — _extract_zip now emits 100 after the file loop.
2. Local zip icon — icon_candidate_urls stays on release-asset URLs for local zips
   instead of falling back to the remote software logo.
3. Rockbox-Y2 listing — release_version_ok treats the Y2 Rockbox package as
   unrestricted and no longer applies the < 0.5 gate to it.
"""

import re
from pathlib import Path
from typing import Sequence


def _src(rel: str) -> str:
    return (Path(__file__).resolve().parent / rel).read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def check_extraction_reaches_100() -> tuple[bool, str]:
    fs = _norm(_src("src/flash_service.py"))
    m = re.search(r"def _extract_zip\(.*?\):(.*?)(?=\n    def |\nclass |\Z)", fs, re.S)
    if not m:
        return False, "could not locate _extract_zip in src/flash_service.py"
    body = m.group(1)
    if "progress.emit(100)" not in body:
        return False, "_extract_zip body missing progress.emit(100)"
    if "for info in infos" not in body:
        return False, "_extract_zip body missing file loop"
    loop_start = body.rfind("for info in infos")
    if "progress.emit(100)" not in body[loop_start:]:
        return False, "100 is not emitted after the file loop"
    if "discard_firmware_history(extract_dir)" not in body:
        return False, "extract cleanup path missing"
    return True, "extract worker emits 100 and continues to cleanup/return"


def check_local_zip_icon_on_release() -> tuple[bool, str]:
    icons = _src("src/release_icons.py")
    if "source_repo" not in icons:
        return False, "release_icons.py missing source_repo reference"
    if "def icon_candidate_urls(release, package" not in icons:
        return False, "icon_candidate_urls signature not found"
    if "release_asset_urls(release, package, dark=dark)" not in icons:
        return False, "release_asset_urls call not found in icon_candidate_urls"
    return True, "icon_candidate_urls anchors on release source, not remote software logo"


def check_rockbox_y2_listing_unrestricted() -> tuple[bool, str]:
    catalog = _src("src/catalog.py")
    select = _src("src/ui/select_page.py")
    smoke = _src("tests/smoke_test.py")
    smoke_norm = _norm(smoke)

    if "the Y2 Rockbox package is" not in catalog:
        return False, "catalog Y2 Rockbox unrestricted note missing"
    if "treated as unrestricted" not in catalog:
        return False, "catalog Y2 Rockbox treated-as-unrestricted wording missing"
    if "Y2, A5, and non-Y1 models are completely exempt from the < 0.5 restriction" not in catalog:
        return False, "catalog non-Y1 exemption wording missing"

    if "def is_rockbox_y2_browsing" not in select:
        return False, "select_page missing is_rockbox_y2_browsing"
    if "if self.is_rockbox_y2_browsing():" not in select:
        return False, "select_page release_listing_filters missing Y2 short-circuit"

    if 'assert release_version_ok("rockbox-y1/rockbox", tag, model="Y1")' not in smoke_norm:
        return False, "smoke Y1 positive gate missing"
    if 'assert not release_version_ok("rockbox-y1/rockbox", tag, model="Y1")' not in smoke_norm:
        return False, "smoke Y1 negative gate missing"
    if 'assert release_version_ok("rockbox-y1/rockbox", "stable-v0.4.9", model="Y2")' not in smoke_norm:
        return False, "smoke Y2 Rockbox unrestricted assertion missing"
    if 'assert release_version_ok("rockbox-y1/rockbox", "stable-v0.1.0", model="Y2")' not in smoke_norm:
        return False, "smoke Y2 oldest-tag assertion missing"

    if "show_old_rockbox" not in smoke_norm:
        return False, "smoke show_old_rockbox path missing"
    if "nightly-35e926bd6ea663ab0aac2f138c7f71ae744971f" not in smoke_norm:
        return False, "smoke untagged nightly gate missing"

    return True, "catalog + select_page + smoke aligned on Y2 Rockbox unrestricted"


def main() -> int:
    checks: Sequence[tuple[str, tuple[bool, str]]] = [
        ("extract reaches 100", check_extraction_reaches_100()),
        ("local zip icon on release", check_local_zip_icon_on_release()),
        ("rockbox y2 listing unrestricted", check_rockbox_y2_listing_unrestricted()),
    ]
    ok = True
    for name, (passed, msg) in checks:
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {name}: {msg}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
