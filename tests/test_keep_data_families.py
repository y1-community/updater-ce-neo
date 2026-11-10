"""Keep Data eligibility for Reborn (Y2Linux) and declared firmware families.

Reborn (Y2Linux) always carries the player's data forward: whoever made the
current install — Y2Player, Classy, stock, an older Y2Linux release — a Reborn
install is never refused Keep Data. Only the catalogue can take that away,
remotely, with ``no_keep_data``/``do_not_keep_data`` or
``PMT_format_needed``. Every other firmware keeps the family rule: the same
software or a declared derivative shares the data partition, a firmware with
no family shares it with itself only.
"""

from unittest.mock import patch

from src import keep_data
from src.catalog import FirmwarePackage


def _pkg(name, repo, derivative=""):
    return FirmwarePackage(
        slug=name.lower().replace(" ", "-"),
        name=name,
        model="Y2",
        repo=repo,
        package_name="rom_y2.zip",
        derivative=derivative,
    )


REBORN = _pkg("Reborn (Y2Linux)", "schulzcode/y2linux")
STOCK = _pkg("Original Software", "y1-community/y1-stock-rom")
Y2PLAYER = _pkg("Y2Player", "schulzcode/Y2Player")
ROCKBOX = _pkg("Rockbox", "spycemagic/y2-rockbox")
KOENSAYR = _pkg(
    "Koensayr", "ryan-specter/koensayr-auto", derivative="y1-community/y1-stock-rom"
)
OTHER_FAMILY = _pkg(
    "Solar", "thesolarproject/solar", derivative="thesolarproject/solar-root"
)


def _recorded(software_name):
    return {"software_name": software_name, "package_slug": ""}


def _check(packages, recorded, pkg):
    with patch("src.keep_data._packages", return_value=packages), patch(
        "src.keep_data.get_device_install", return_value=_recorded(recorded)
    ):
        return (
            keep_data.tracked_is_compatible("Y2", pkg),
            keep_data.keep_data_available("Y2", pkg),
            keep_data.needs_confirmation("Y2", pkg),
        )


def test_a_reborn_target_keeps_data_whatever_was_installed_before():
    # Y2Player, Classy and stock are all systems a Reborn user may come from.
    # Reborn carries the player's data forward, so the choice is always there
    # and the player is not asked to confirm what was running.
    for previous in ("Y2Player", "Classy"):
        compatible, available, confirm = _check(
            [REBORN, Y2PLAYER, STOCK], previous, REBORN
        )
        assert compatible is True
        assert available is True
        assert confirm is False


def test_the_manifest_veto_wins_even_for_reborn():
    from dataclasses import replace

    for vetoed in (
        replace(REBORN, do_not_keep_data=True),
        replace(REBORN, pmt_format_needed=True),
    ):
        with patch("src.keep_data._packages", return_value=[REBORN, STOCK]), patch(
            "src.keep_data.get_device_install", return_value=_recorded("Y2Player")
        ):
            assert keep_data.keep_data_available("Y2", vetoed) is False
            assert keep_data.needs_confirmation("Y2", vetoed) is False


def test_another_release_of_reborn_is_still_the_same_firmware():
    # Reborn updates itself on the player, so the tracked record names the
    # release (``Y2Linux 1.0 Beta 9.2.13``) rather than the catalogue row.
    # Every Y2Linux release is the same system and may carry data forward.
    compatible, available, confirm = _check(
        [REBORN, STOCK], "Y2Linux 1.0 Beta 9.2.13", REBORN
    )
    assert compatible is True
    assert available is True
    assert confirm is False


def test_the_old_catalogue_name_is_still_the_same_firmware():
    # The row used to be called ``Y2Reborn (Y2Linux)``; a player tracked
    # under that name is on the same firmware, not another system.
    compatible, available, _ = _check([REBORN, STOCK], "Y2Reborn (Y2Linux)", REBORN)
    assert compatible is True
    assert available is True


def test_the_confirmation_names_reborn_itself():
    with patch("src.keep_data._packages", return_value=[REBORN, STOCK]):
        assert keep_data.compatible_software_names(REBORN, "Y2") == ["Reborn (Y2Linux)"]


def test_a_reborn_install_recorded_by_this_app_needs_no_confirmation():
    compatible, available, confirm = _check([REBORN, STOCK], "Reborn (Y2Linux)", REBORN)
    assert compatible is True
    assert available is True
    assert confirm is False


def test_a_declared_family_that_matches_is_compatible():
    compatible, available, confirm = _check([KOENSAYR, STOCK], "Original Software", KOENSAYR)
    assert compatible is True
    assert available is True
    assert confirm is False


def test_only_the_same_tracked_firmware_defaults_keep_data_on():
    with patch(
        "src.keep_data.get_device_install",
        return_value={"software_name": "Rockbox", "package_slug": "rockbox"},
    ):
        assert keep_data.tracked_is_same("Y2", ROCKBOX) is True
        assert keep_data.tracked_is_same("Y2", REBORN) is False


def test_a_declared_family_that_differs_stays_incompatible():
    # One side names its parent, so the mismatch is evidence, not ignorance.
    compatible, available, confirm = _check([KOENSAYR, ROCKBOX], "Rockbox", KOENSAYR)
    assert compatible is False
    assert available is False
    assert confirm is False


def test_two_declared_families_that_differ_are_incompatible():
    compatible, available, _ = _check([KOENSAYR, OTHER_FAMILY], "Solar", KOENSAYR)
    assert compatible is False
    assert available is False


def test_the_select_page_reason_is_the_incompatible_installed_firmware():
    # Koensayr over Rockbox: both families are declared and differ, so the
    # select page can say why Keep Data will be withheld before any install.
    with patch("src.keep_data._packages", return_value=[KOENSAYR, ROCKBOX]), patch(
        "src.keep_data.get_device_install", return_value=_recorded("Rockbox")
    ):
        assert keep_data.incompatible_with_current("Y2", KOENSAYR) is True
        assert keep_data.incompatible_with_current("Y2", ROCKBOX) is False


def test_an_unknown_current_firmware_gets_no_keep_data_note():
    with patch("src.keep_data._packages", return_value=[REBORN, STOCK]), patch(
        "src.keep_data.get_device_install", return_value={}
    ):
        assert keep_data.incompatible_with_current("Y2", REBORN) is False


def test_other_refusals_do_not_get_the_current_firmware_reason(monkeypatch):
    from dataclasses import replace

    pkg = replace(_pkg("Locked", "y1-community/locked"), do_not_keep_data=True)
    assert keep_data.incompatible_with_current("Y2", pkg) is False

    plain = _pkg("Plain", "y1-community/plain")
    monkeypatch.setattr(keep_data, "allow_keep_data_any", lambda settings=None: True)
    assert keep_data.incompatible_with_current("Y2", plain) is False
