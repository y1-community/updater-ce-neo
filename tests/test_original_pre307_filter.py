"""Y1 Type A Original Software releases older than 3.0.7 are hidden by default.

Packages built from the live manifest carry the slug ``original-software-y1``,
not the static catalogue's ``original-y1``; the hide must apply to both.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.catalog import FirmwarePackage, original_y1_typea_pre_307_hidden


def _pkg(slug):
    return FirmwarePackage(
        slug=slug, name="Original Software", model="Y1",
        repo="y1-community/y1-stock-rom", package_name="rom.zip",
    )


def test_manifest_slug_is_hidden_below_307():
    assert original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "A", "3.0.2")
    assert original_y1_typea_pre_307_hidden(_pkg("original-y1"), "Y1", "A", "v3.0.6")


def test_307_and_newer_are_not_hidden():
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "A", "3.0.7")
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "A", "3.1.0")


def test_opt_in_lifts_the_hide():
    assert not original_y1_typea_pre_307_hidden(
        _pkg("original-software-y1"), "Y1", "A", "3.0.2", show_pre_307=True
    )


def test_type_b_and_other_packages_are_not_hidden():
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "B", "3.0.2")
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y2"), "Y2", "A", "3.0.2")


def test_listing_gate_drops_only_pre307_tags_for_manifest_package():
    for tag, expect_hidden in (("3.0.2", True), ("3.0.7", False)):
        assert original_y1_typea_pre_307_hidden(
            _pkg("original-software-y1"), "Y1", "A", tag
        ) is expect_hidden


if __name__ == "__main__":
    test_manifest_slug_is_hidden_below_307()
    test_307_and_newer_are_not_hidden()
    test_opt_in_lifts_the_hide()
    test_type_b_and_other_packages_are_not_hidden()
    test_listing_gate_drops_only_pre307_tags_for_manifest_package()
    print("pre-3.0.7 filter tests passed")
