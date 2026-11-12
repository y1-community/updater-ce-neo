"""The terminal install gate: answer, prerequisite run, then the target.

This drives ``_install_first_gate`` with a stubbed flash function, so it checks
the TUI route (plain and curses both call the same gate) without downloading or
touching a player.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.console import tui
from src.console.releases import ReleaseRow
from src.console.session import BrowseState


def package(name, repo, model="Y2", **attrs):
    return SimpleNamespace(
        name=name,
        repo=repo,
        model=model,
        brand="Innioasis",
        package_name="rom_y2.zip",
        slug=repo.split("/")[-1],
        install_first=attrs.get("install_first", ""),
        install_first_version=attrs.get("install_first_version", ""),
        derivative=attrs.get("derivative", ""),
        do_not_keep_data=attrs.get("do_not_keep_data", False),
        pmt_format_needed=False,
        kind="img",
        handler="Custom Firmware",
        post_install="",
        post_install_2="",
    )


def row(label, version):
    return ReleaseRow(
        label=label,
        tag=f"stable-v{'.'.join(str(part) for part in version)}",
        name="",
        body="",
        asset_name="rom_y2.zip",
        asset_url="https://example.invalid/rom_y2.zip",
        version=version,
        resolution="",
        html_url="",
        hardware_type="A",
    )


@pytest.fixture
def state_and_items():
    target = package(
        "Reborn",
        "schulzcode/y2linux",
        install_first="y1-community/y1-stock-rom",
        install_first_version="3.2.0=>",
        do_not_keep_data=True,
    )
    base = package("Original Software", "y1-community/y1-stock-rom")
    state = BrowseState(
        model="Y2",
        hardware_type="A",
        software=target.name,
        package=target,
        releases=[row("Reborn 1.0", (1, 0))],
        index=0,
    )
    return state, [target, base]


def stub_base(monkeypatch, calls):
    monkeypatch.setattr(tui, "fetch_releases", lambda repo, force=False: [{}])
    monkeypatch.setattr(tui, "rows_from_releases", lambda *a, **k: [row("Original 3.2.0", (3, 2, 0))])
    monkeypatch.setattr(tui, "tracked_install", lambda *a, **k: {})

    def fake_run(inner, output, **kwargs):
        calls.append((inner.package.name, inner.install_first_keep, kwargs.get("_skip_install_first")))
        inner.install_ok = True
        inner.result_lines = ["Base installed."]
        return "Base installed."

    monkeypatch.setattr(tui, "_run_install", fake_run)


def test_already_on_target_allows_keep_data(state_and_items, monkeypatch):
    state, items = state_and_items
    calls = []
    monkeypatch.setattr(tui, "tracked_install", lambda *a, **k: {})
    gate = tui._install_first_gate(
        state,
        items,
        lambda title, options, selected=0: 0,
        lambda title, detail="": True,
        Path("."),
        None,
        None,
    )
    assert gate is True
    assert calls == []
    assert state.install_first_keep == "allow"
    assert state.package.name == "Reborn"


def test_other_installs_the_base_first(state_and_items, monkeypatch):
    state, items = state_and_items
    calls = []
    stub_base(monkeypatch, calls)
    gate = tui._install_first_gate(
        state,
        items,
        lambda title, options, selected=0: 2,
        lambda title, detail="": True,
        Path("."),
        None,
        None,
    )
    assert gate is True
    assert calls == [("Original Software", "block", True)]
    # The target is exactly what the outer install continues with.
    assert state.package.name == "Reborn"
    assert state.releases[0].label == "Reborn 1.0"
    assert state.install_first_keep == "block"


def test_prerequisite_unknown_version_asks_then_proceeds(state_and_items, monkeypatch):
    state, items = state_and_items
    questions = []

    def confirm(title, detail=""):
        questions.append(title)
        return True

    gate = tui._install_first_gate(
        state,
        items,
        lambda title, options, selected=0: 1,
        confirm,
        Path("."),
        None,
        None,
    )
    assert gate is True
    assert questions and "3.2.0+" in questions[0]
    # The tracked record is unknown, so the version question was asked and the
    # player said yes: the target may continue without a base run.
    assert state.package.name == "Reborn"


def test_prerequisite_old_version_installs_the_base(state_and_items, monkeypatch):
    state, items = state_and_items
    calls = []
    stub_base(monkeypatch, calls)
    monkeypatch.setattr(
        tui,
        "tracked_install",
        lambda *a, **k: {"software_name": "Original Software", "release_label": "3.1.0"},
    )
    gate = tui._install_first_gate(
        state,
        items,
        lambda title, options, selected=1: 1,
        lambda title, detail="": True,
        Path("."),
        None,
        None,
    )
    assert gate is True
    assert calls and calls[0][0] == "Original Software"


def test_exact_version_without_matching_release_does_not_fall_back(
    state_and_items, monkeypatch, capsys
):
    state, items = state_and_items
    items[0].install_first_version = "=3.2.0"
    monkeypatch.setattr(tui, "fetch_releases", lambda repo, force=False: [{}])
    monkeypatch.setattr(
        tui,
        "rows_from_releases",
        lambda *a, **k: [row("Original 3.2.1", (3, 2, 1))],
    )
    monkeypatch.setattr(tui, "tracked_install", lambda *a, **k: {})
    calls = []
    monkeypatch.setattr(
        tui,
        "_run_install",
        lambda *a, **k: calls.append(a) or "unexpected",
    )

    gate = tui._install_first_gate(
        state,
        items,
        lambda title, options, selected=0: 2,
        lambda title, detail="": True,
        Path("."),
        None,
        None,
    )
    assert gate is False
    assert calls == []
    assert state.package.name == "Reborn"
    message = tui._tr("install_first_version_unavailable").format(
        software="Original Software", version="=3.2.0"
    )
    assert message in capsys.readouterr().out


def test_cancel_does_not_download(state_and_items):
    state, items = state_and_items
    calls = []
    gate = tui._install_first_gate(
        state,
        items,
        lambda title, options, selected=0: None,
        lambda title, detail="": True,
        Path("."),
        None,
        None,
    )
    assert gate is False
    assert calls == []


def test_local_portable_gate_flashes_the_bundled_pre_stage(tmp_path, monkeypatch):
    target = SimpleNamespace(
        folder=tmp_path / "SP_Flash_Tool",
        name="Reborn",
        version="1.0",
        model="Y2",
        repo="someone/reborn",
        install_first="y1-community/y1-stock-rom",
        install_first_version="3.2.0=>",
        install_first_package=None,
    )
    base = SimpleNamespace(
        folder=tmp_path / "pre_stage",
        name="Original Software",
        version="3.2.0",
        model="Y2",
        repo="y1-community/y1-stock-rom",
        install_first="",
        install_first_version="",
        install_first_package=None,
    )
    target.install_first_package = base
    import src.fw_package as fw_package

    monkeypatch.setattr(fw_package, "locate_package", lambda *a, **k: target)
    flashed = []
    monkeypatch.setattr(tui.flash, "flash_directory", lambda folder, **k: flashed.append((folder, k)) or 0)

    gate = tui._local_install_first_gate(
        target.folder,
        lambda title, options, selected=0: 2,
        lambda title, detail="": True,
        ready=lambda *a: True,
    )

    assert gate is True
    assert flashed == [(str(base.folder), {"keep_data": False})]


def test_local_portable_gate_reports_a_missing_pre_stage(tmp_path, monkeypatch):
    target = SimpleNamespace(
        folder=tmp_path / "SP_Flash_Tool",
        name="Reborn",
        version="1.0",
        model="Y2",
        repo="someone/reborn",
        install_first="y1-community/y1-stock-rom",
        install_first_version="3.2.0=>",
        install_first_package=None,
    )
    import src.fw_package as fw_package

    monkeypatch.setattr(fw_package, "locate_package", lambda *a, **k: target)

    gate = tui._local_install_first_gate(
        target.folder,
        lambda title, options, selected=0: 2,
        lambda title, detail="": True,
        ready=lambda *a: True,
    )

    assert isinstance(gate, str)
    assert "requires" in gate.lower()
