"""The Install Firmware First rules, without a window or a player.

The catalogue can gate an overlay behind a base firmware. These tests pin the
attribute parser, the version ranges, the answer-to-next-step journey and the
Keep Data decision, because all four are shared by the GUI and the TUI.
"""

from types import SimpleNamespace

from src import install_first as first


def package(name, repo, model="Y2", **attrs):
    return SimpleNamespace(name=name, repo=repo, model=model, **attrs)


def test_bare_and_minimum_constraint_forms():
    for text in (
        "3.2.0",
        "3.2.0+",
        "3.2.0=>",
        "3.2.0>=",
        ">=3.2.0",
        "=>3.2.0",
        "3.2.0 or later",
        "3.2.0 or newer",
        "3.2.0 and later",
    ):
        parsed = first.parse_version_constraint(text)
        assert parsed is not None, text
        assert parsed.minimum == (3, 2, 0), text
        assert parsed.maximum is None, text
        assert parsed.minimum_inclusive, text
        assert parsed.allows("3.2.0")
        assert parsed.allows("stable-v3.5.1")
        assert not parsed.allows("3.1.9")
        assert parsed.label() == "3.2.0+", text


def test_equals_pins_one_exact_version():
    for text in ("=3.2.0", "==3.2.0", "3.2.0=", "3.2.0=="):
        parsed = first.parse_version_constraint(text)
        assert parsed is not None, text
        assert parsed.exact, text
        assert parsed.minimum == (3, 2, 0), text
        assert parsed.maximum == (3, 2, 0), text
        assert parsed.allows("3.2.0")
        assert parsed.allows("v3.2.0")
        assert not parsed.allows("3.2.1"), text
        assert not parsed.allows("3.1.9"), text
        assert parsed.label() == "=3.2.0", text


def test_exclusive_and_capped_forms():
    for text in (">3.2.0", "3.2.0>"):
        above = first.parse_version_constraint(text)
        assert not above.allows("3.2.0"), text
        assert above.allows("3.2.1"), text
        assert above.label() == ">3.2.0", text

    for text in ("<3.2.0", "3.2.0<"):
        below = first.parse_version_constraint(text)
        assert below.allows("3.1.9"), text
        assert not below.allows("3.2.0"), text
        assert below.label() == "<3.2.0", text

    for text in ("<=3.2.0", "3.2.0<=", "=<3.2.0"):
        capped = first.parse_version_constraint(text)
        assert capped.allows("3.2.0"), text
        assert capped.allows("3.1.9"), text
        assert not capped.allows("3.2.1"), text
        assert capped.label() == "≤3.2.0", text


def test_ranges_and_combined_bounds():
    for text in (
        "2.3.0-3.0.0",
        "2.3.0..3.0.0",
        "2.3.0 - 3.0.0",
        "2.3.0 – 3.0.0",
    ):
        span = first.parse_version_constraint(text)
        assert span is not None, text
        assert span.allows("2.9.0"), text
        assert span.allows("3.0.0"), text
        assert not span.allows("3.0.1"), text
        assert span.label() == "2.3.0–3.0.0", text

    for text in (">=3.2.0,<4.0.0", ">=3.2.0 <4.0.0", ">= 3.2.0 < 4.0.0"):
        bounded = first.parse_version_constraint(text)
        assert bounded.allows("3.2.0"), text
        assert bounded.allows("3.9.9"), text
        assert not bounded.allows("3.1.9"), text
        assert not bounded.allows("4.0.0"), text
        assert bounded.label() == "≥3.2.0, <4.0.0", text

    # Two operators on one version is a typo, not a silent 3.2.0 bound.
    assert first.parse_version_constraint("3.2.0<4.0.0") is None
    assert first.parse_version_constraint("") is None
    assert first.parse_version_constraint(None) is None
    assert first.parse_version_constraint("not a version") is None


def test_requirement_resolves_the_base_for_this_model():
    reborn = package(
        "Reborn (Y2Linux)",
        "schulzcode/y2linux",
        install_first="y1-community/y1-stock-rom",
        install_first_version="3.2.0=>",
    )
    stock_y1 = package("Original Software", "y1-community/y1-stock-rom", model="Y1")
    stock_y2 = package("Original Software", "y1-community/y1-stock-rom", model="Y2")
    requirement = first.requirement_for(reborn, "Y2", packages=[stock_y1, stock_y2])
    assert requirement is not None
    assert requirement.package is stock_y2
    assert requirement.software_name == "Original Software"
    assert requirement.label() == "Original Software 3.2.0+"
    assert requirement.allows("3.2.0") and not requirement.allows("3.1.0")


def test_requirement_rejects_a_base_for_another_model():
    reborn = package(
        "Reborn",
        "someone/reborn",
        model="Y2",
        install_first="y1-community/y1-stock-rom",
        install_first_version="3.2.0=>",
    )
    stock_y1 = package("Original Software", "y1-community/y1-stock-rom", model="Y1")
    unknown = package("Original Software", "y1-community/y1-stock-rom", model="")

    assert first.requirement_for(reborn, "Y2", packages=[stock_y1]).package is None
    assert first.requirement_for(reborn, "Y2", packages=[unknown]).package is None


def test_one_firmware_serving_two_models_resolves_the_shared_base():
    """Q3 and Q5 ship the same build, so one base repository serves both.

    ``slidia_manifest.xml`` names the base once per model. A firmware listed
    for both models declares the base on each row, but only one of them may
    have the base's own row; the other must still resolve it instead of
    failing with "the catalogue has no installable release".
    """
    lithium_q3 = package(
        "Lithium",
        "NoahDomingues/Lithium",
        model="Q3",
        install_first="y1-community/q3-stock-rom",
    )
    lithium_q5 = package(
        "Lithium",
        "NoahDomingues/Lithium",
        model="Q5",
        install_first="y1-community/q3-stock-rom",
    )
    stock_q3 = package("Original Software", "y1-community/q3-stock-rom", model="Q3")
    rows = [lithium_q3, lithium_q5, stock_q3]

    for target in (lithium_q3, lithium_q5):
        requirement = first.requirement_for(target, target.model, packages=rows)
        assert requirement is not None
        assert requirement.package is stock_q3
        assert requirement.software_name == "Original Software"

    shared = first.requirement_for(lithium_q5, "Q5", packages=rows)
    assert shared.source_model == "Q3"
    own = first.requirement_for(lithium_q3, "Q3", packages=rows)
    assert own.source_model == ""


def test_shared_source_still_refuses_a_base_from_another_family():
    reborn = package(
        "Reborn",
        "schulzcode/y2linux",
        model="Y2",
        install_first="y1-community/y1-stock-rom",
    )
    stock_y1 = package("Original Software", "y1-community/y1-stock-rom", model="Y1")
    rockbox_y2 = package("Rockbox", "spycemagic/y2-rockbox", model="Y2")

    requirement = first.requirement_for(
        reborn, "Y2", packages=[reborn, stock_y1, rockbox_y2]
    )

    assert requirement is not None and requirement.package is None
    assert not first._models_can_share_a_source("Y1", "Y2")
    assert not first._models_can_share_a_source("A5", "Y1")
    assert not first._models_can_share_a_source("", "Q5")
    assert first._models_can_share_a_source("Q3", "Q5")


def test_requirement_absent_or_self_referential_is_not_a_gate():
    plain = package("Rockbox", "rockbox-y1/rockbox")
    assert first.requirement_for(plain, "Y1", packages=[plain]) is None

    self_ref = package(
        "Original Software", "y1-community/y1-stock-rom", install_first="y1-community/y1-stock-rom"
    )
    assert first.requirement_for(self_ref, "Y2", packages=[self_ref]) is None


def test_requirement_keeps_an_unlisted_base_so_the_window_can_explain():
    reborn = package(
        "Reborn", "schulzcode/y2linux", install_first="missing/base", install_first_version="1.0"
    )
    requirement = first.requirement_for(reborn, "Y2", packages=[])
    assert requirement is not None
    assert requirement.package is None
    assert requirement.software_name == "Base"


def test_journey_target_proceeds_and_allows_keep_data():
    reborn = package("Reborn", "schulzcode/y2linux", do_not_keep_data=True)
    stock = package("Original Software", "y1-community/y1-stock-rom")
    requirement = first.Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="3.2.0=>",
        version_range=first.parse_version_constraint("3.2.0=>"),
        package=stock,
    )
    journey = first.InstallFirstJourney(target=reborn, requirement=requirement)
    assert journey.choose(first.ANSWER_TARGET, "3.1.0") == first.PROCEED
    assert journey.keep_data_allowed()


def test_journey_prerequisite_uses_the_tracked_version():
    stock = package("Original Software", "y1-community/y1-stock-rom")
    requirement = first.Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="3.2.0=>",
        version_range=first.parse_version_constraint("3.2.0=>"),
        package=stock,
    )
    target = package("Reborn", "schulzcode/y2linux")
    journey = first.InstallFirstJourney(target=target, requirement=requirement)
    assert journey.choose(first.ANSWER_PREREQUISITE, "3.2.0") == first.PROCEED
    assert not journey.keep_data_allowed()

    unknown = first.InstallFirstJourney(target=target, requirement=requirement)
    assert unknown.choose(first.ANSWER_PREREQUISITE, "") == first.ASK_VERSION
    assert unknown.confirm_version(True) == first.PROCEED
    assert unknown.confirm_version(False) == first.NEED_PREREQUISITE

    old = first.InstallFirstJourney(target=target, requirement=requirement)
    assert old.choose(first.ANSWER_PREREQUISITE, "3.1.9") == first.NEED_PREREQUISITE
    assert old.needs_prerequisite()


def test_journey_exact_version_rejects_a_newer_build():
    stock = package("Original Software", "y1-community/y1-stock-rom")
    requirement = first.Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="=3.2.0",
        version_range=first.parse_version_constraint("=3.2.0"),
        package=stock,
    )
    target = package("Reborn", "schulzcode/y2linux")
    newer = first.InstallFirstJourney(target=target, requirement=requirement)
    assert newer.choose(first.ANSWER_PREREQUISITE, "3.2.1") == first.NEED_PREREQUISITE
    assert newer.needs_prerequisite()

    exact = first.InstallFirstJourney(target=target, requirement=requirement)
    assert exact.choose(first.ANSWER_PREREQUISITE, "3.2.0") == first.PROCEED


def test_journey_other_always_needs_the_base():
    stock = package("Original Software", "y1-community/y1-stock-rom")
    requirement = first.Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="3.2.0=>",
        version_range=first.parse_version_constraint("3.2.0=>"),
        package=stock,
    )
    journey = first.InstallFirstJourney(
        target=package("Reborn", "schulzcode/y2linux"), requirement=requirement
    )
    assert journey.choose(first.ANSWER_OTHER, "99.0.0") == first.NEED_PREREQUISITE
    assert not journey.keep_data_allowed()


def test_derivative_family_relation():
    stock = package("Original Software", "y1-community/y1-stock-rom", model="Y2")
    reborn = package(
        "Reborn",
        "schulzcode/y2linux",
        derivative="y1-community/y1-stock-rom",
    )
    unrelated = package("Rockbox", "spycemagic/y2-rockbox")
    requirement = first.Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="3.2.0=>",
        version_range=first.parse_version_constraint("3.2.0=>"),
        package=stock,
        model="Y2",
    )
    assert first.related_by_derivative(reborn, requirement, [stock, reborn])
    assert not first.related_by_derivative(unrelated, requirement, [stock, unrelated])


def test_console_manifest_parses_both_attributes():
    from src.console import catalog as console_catalog

    data = b"""<catalogue>
      <package name="Reborn (Y2Linux)" repo="schulzcode/y2linux" device="Y2"
               install_first="y1-community/y1-stock-rom" install_first_version="3.2.0=>" />
      <package name="Rockbox" repo="rockbox-y1/rockbox" device="Y1" />
    </catalogue>"""
    items = console_catalog.parse_manifest(data)
    by_name = {item.name: item for item in items}
    assert by_name["Reborn (Y2Linux)"].install_first == "y1-community/y1-stock-rom"
    assert by_name["Reborn (Y2Linux)"].install_first_version == "3.2.0=>"
    assert by_name["Rockbox"].install_first == ""
    assert by_name["Rockbox"].install_first_version == ""
