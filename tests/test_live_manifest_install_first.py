"""The live catalogue decides the install-first menu, typos included.

``slidia_manifest.xml`` as published writes one row with a bare ``>`` instead
of ``/>``. That is not well-formed XML, and a parser that gives up on it loses
every row after it, including the ``install_first_when`` lists that say which
systems must install the base first. These tests use the published shape: one
unterminated row, several rows behind it, and menus that must follow the
repositories the file names.
"""

from src import install_first as first
from src import manifest

#: A reduced copy of the published file, including its unterminated Y2Player
#: row and the systems that row names (Android 4.4's repository among them).
LIVE_LIKE = """<?xml version="1.0"?>
<slidia>
  <package name="Original Software" repo="y1-community/y1-stock-rom" device="Y2" type="img" />
  <package name="Reborn" repo="schulzcode/y2linux" device="Y2" type="img"
           install_first="y1-community/y1-stock-rom"
           install_first_when="spycemagic/y2-rockbox,classyplayer/classy,FabianZettl/Inniclassic,y1-community/y2-ata-rom"
           install_first_once="true" />
  <package name="Y2Player" repo="schulzcode/Y2Player" device="Y2" type="img"
           install_first="y1-community/y1-stock-rom"
           install_first_when="ClassyPlayer/Classy,FabianZettl/inniclassic,ismileblue/y1_launcher,jarvissax/rondo,y1-community/y2-ata-rom" >
  <package name="Android 4.4 (Dev)" repo="y1-community/y2-ata-rom" device="Y2" type="img" />
  <package name="Classy" repo="ClassyPlayer/Classy" device="Y2" type="img" />
  <package name="Rockbox" repo="spycemagic/y2-rockbox" device="Y2" type="img" />
  <package name="Solar" repo="thesolarproject/solar" device="Y2" type="img" />
</slidia>
"""


def menu(target_name: str) -> list[str]:
    packages = manifest.parse_manifest_xml(LIVE_LIKE)
    target = next(pkg for pkg in packages if pkg.name == target_name)
    requirement = first.requirement_for(target, "Y2", packages)
    return [label for label, _repo in first.software_choices(
        target, "Y2", requirement, packages
    )]


def test_an_unterminated_row_does_not_cost_the_rows_after_it():
    packages = manifest.parse_manifest_xml(LIVE_LIKE)
    names = [pkg.name for pkg in packages]
    assert "Y2Player" in names
    # Everything after the broken row is still read.
    for name in ("Android 4.4 (Dev)", "Classy", "Rockbox", "Solar"):
        assert name in names


def test_only_this_entrys_own_list_hides_a_system():
    labels = menu("Y2Player")
    assert labels[:2] == ["Original Software", "Y2Player"]
    # install_first_when names Android 4.4's repository, so it cannot be a row
    # of its own: the base is required from it and "Other" is its answer.
    assert "Android 4.4 (Dev)" not in labels
    assert "Classy" not in labels
    # Systems this entry does not name stay answers of their own.
    assert {"Rockbox", "Solar", "Reborn"} <= set(labels)


def test_answering_a_named_system_still_requires_the_base():
    packages = manifest.parse_manifest_xml(LIVE_LIKE)
    target = next(pkg for pkg in packages if pkg.name == "Y2Player")
    requirement = first.requirement_for(target, "Y2", packages)
    assert "y1-community/y2-ata-rom" in requirement.when_repos
    assert (
        first.answer_for_choice(
            "Android 4.4 (Dev)", "y1-community/y2-ata-rom", target, requirement
        )
        == first.ANSWER_OTHER
    )


def test_reborn_follows_its_own_list_rather_than_y2players():
    labels = menu("Reborn")
    assert "Rockbox" not in labels
    assert "Classy" not in labels
    # Reborn's list now names Android 4.4, so it must not be in the menu.
    assert "Android 4.4 (Dev)" not in labels


def test_the_menu_is_only_about_this_model():
    packages = manifest.parse_manifest_xml(
        LIVE_LIKE.replace(
            "</slidia>",
            '<package name="Android 4.2 (Dev)" repo="y1-community/y1-ata-rom"'
            ' device="Y1" type="img" />\n</slidia>',
        )
    )
    target = next(pkg for pkg in packages if pkg.name == "Reborn")
    requirement = first.requirement_for(target, "Y2", packages)
    labels = [label for label, _repo in first.software_choices(
        target, "Y2", requirement, packages
    )]
    # A Y1-only build cannot be running on the Y2 player being asked about.
    assert "Android 4.2 (Dev)" not in labels


def test_a_y1_only_build_never_becomes_a_y2_answer():
    # The static table still lists JJ Launcher for Y2 while the manifest does
    # not, so a menu built from both must not hand a Y1 row to a Y2 question.
    packages = manifest.parse_manifest_xml(LIVE_LIKE)
    target = next(pkg for pkg in packages if pkg.name == "Reborn")
    requirement = first.requirement_for(target, "Y2", packages)
    y1_row = type(target)(
        slug="jj-launcher-y1",
        package_name="rom.zip",
        name="JJ Launcher",
        model="Y1",
        repo="ismileblue/y1_launcher",
    )
    labels = [label for label, _repo in first.software_choices(
        target, "Y2", requirement, [*packages, y1_row]
    )]
    assert "JJ Launcher" not in labels


def test_a_messy_comma_list_names_the_same_systems():
    packages = manifest.parse_manifest_xml(
        LIVE_LIKE.replace(
            'install_first_when="spycemagic/y2-rockbox,'
            'classyplayer/classy,FabianZettl/Inniclassic"',
            'install_first_when=" spycemagic/Y2-Rockbox ， ClassyPlayer/Classy,, '
            'classyplayer/classy ; "',
        )
    )
    target = next(pkg for pkg in packages if pkg.name == "Reborn")
    requirement = first.requirement_for(target, "Y2", packages)
    # Separators, padding, case and duplicates all collapse to the two repos.
    assert set(requirement.when_repos) == {
        "spycemagic/y2-rockbox",
        "classyplayer/classy",
        "fabianzettl/inniclassic",
        "y1-community/y2-ata-rom"
    }
    labels = [label for label, _repo in first.software_choices(
        target, "Y2", requirement, packages
    )]
    assert "Rockbox" not in labels
    assert "Classy" not in labels


def test_a_legacy_repository_spelling_in_install_first_when_still_blocks():
    packages = manifest.parse_manifest_xml(
        LIVE_LIKE.replace(
            "install_first_when=\"spycemagic/y2-rockbox,"
            "classyplayer/classy,FabianZettl/Inniclassic\"",
            "install_first_when=\"y1-community/rockbox-y2-rom\"",
        )
    )
    target = next(pkg for pkg in packages if pkg.name == "Reborn")
    requirement = first.requirement_for(target, "Y2", packages)
    # The published file may still use the repository's pre-rename spelling;
    # the row it means is today's spycemagic/y2-rockbox.
    labels = [label for label, _repo in first.software_choices(
        target, "Y2", requirement, packages
    )]
    assert "Rockbox" not in labels
