"""The install-first card says why the base has to go on first.

A base the catalogue requires only when coming from the player's current
system is not a cosmetic suggestion: installed straight over that system the
player will not start up properly, and the card has to say so. A base that is
simply required keeps the clean-start explanation.
"""

from types import SimpleNamespace

from src import install_first
from src.ui.main_window import install_first_prerequisite_body


def _journey(when_repos=(), running="Rockbox"):
    base = SimpleNamespace(name="Original Software", repo="innioasis/y2-stock")
    requirement = install_first.Requirement(
        repo="innioasis/y2-stock",
        version_text="",
        version_range=None,
        when_repos=tuple(when_repos),
        package=base,
        model="Y2",
    )
    return SimpleNamespace(
        requirement=requirement,
        target=SimpleNamespace(name="Reborn", repo="innioasis/y2-reborn"),
        target_name="Reborn",
        running_name=running,
    )


def _body(journey):
    return install_first_prerequisite_body(journey, "Y2")


def test_a_conditional_base_says_the_player_will_not_start():
    body = _body(_journey(when_repos=("innioasis/y2-rockbox",)))
    assert "will not start up properly" in body
    assert "Rockbox" in body
    assert "Reborn" in body


def test_an_unnamed_system_says_the_same_without_inventing_a_name():
    body = _body(_journey(when_repos=("innioasis/y2-rockbox",), running=""))
    assert "will not start up properly" in body
    assert "software already on your Y2" in body
    assert "Other" not in body


def test_a_plain_base_keeps_the_clean_start_explanation():
    body = _body(_journey())
    assert "will not start up properly" not in body


def test_the_catalogue_names_the_system_the_overlay_replaces():
    from src.install_first import Requirement, when_software_name

    requirement = Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="",
        version_range=None,
        when_repos=("classyplayer/classy", "ismileblue/y1_launcher"),
    )
    rows = [
        SimpleNamespace(name="Classy", repo="ClassyPlayer/Classy", model="Y2"),
        SimpleNamespace(name="Y1 Launcher", repo="ismileblue/y1_launcher", model="Y1"),
    ]
    # Only the row listed for this model can be what the player is coming from.
    assert when_software_name(requirement, "Y2", rows) == "Classy"
    body = install_first_prerequisite_body(
        _journey(when_repos=("classyplayer/classy",), running=""), "Y2", "Classy"
    )
    assert "straight over Classy" in body
    assert "software already on" not in body

    # Two candidates for the same model name nothing rather than guessing.
    rows.append(SimpleNamespace(name="Classy 2", repo="ismileblue/y1_launcher", model="Y2"))
    assert when_software_name(requirement, "Y2", rows) == ""


def test_a_player_logged_in_settings_still_names_the_software(tmp_path):
    from PySide6.QtCore import QSettings

    from src import keep_data, player_update_check
    from src.ui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "install_first.ini"), QSettings.IniFormat)
    # The user recorded this player themselves, in Settings or the welcome
    # journey; nothing has been installed over it yet.
    player_update_check.record_device_install(
        "Y2", "Classy", "stable-v1.2.3", settings=settings
    )
    journey = _journey(when_repos=("classyplayer/classy",), running="")
    window = SimpleNamespace(
        settings=settings,
        _install_first_model=lambda: "Y2",
        _install_first_hardware_type=lambda: "",
        _install_first_journey=journey,
        _same_software_name=lambda a, b: a.strip().casefold() == b.strip().casefold(),
    )
    MainWindow._remember_install_first_answer(
        window, journey, install_first.ANSWER_OTHER
    )

    # The answer is dropped; the player the user logged is not. The card names
    # the software instead of describing "the software already on your Y2".
    known = keep_data.known_running_software("Y2", settings)
    assert known["software_name"] == "Classy"
    name = MainWindow._install_first_running_name(window)
    assert name == "Classy"
    body = install_first_prerequisite_body(journey, "Y2", name)
    assert "straight over Classy" in body
    assert "software already on" not in body


def test_a_picked_system_keeps_its_name_for_the_card():
    from src.ui.main_window import MainWindow

    class _Page:
        def keep_data_confirm_choice(self):
            return "Classy"

    journey = _journey(when_repos=("classyplayer/classy",), running="")
    window = SimpleNamespace(
        _install_first_journey=journey,
        _flash_page=_Page(),
        _install_first_choices={"Classy": "classyplayer/classy"},
    )
    answer = MainWindow._install_first_selected_answer(window)
    assert answer == install_first.ANSWER_OTHER
    assert journey.running_name == "Classy"
    body = install_first_prerequisite_body(journey, "Y2")
    assert "straight over Classy" in body
    assert "software already on" not in body

    # The "Other" row carries no repository, so it cannot name anything.
    window._flash_page = SimpleNamespace(keep_data_confirm_choice=lambda: "Other")
    journey.running_name = "left over"
    MainWindow._install_first_selected_answer(window)
    assert journey.running_name == ""
