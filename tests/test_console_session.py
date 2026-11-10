"""Text-session rules: catalogue filters, mouse, prefetch, arch, simulation.

These tests do not import PySide and do not start the window.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.console.catalog import Package
from src.console.components import (
    COMPONENTS,
    ensure,
    find_bundled,
    prefetch_plan,
    resolve,
    session_component_keys,
)
from src.console.flash import install_engine, mtk_write_command, sp_flash_tool_console_args
from src.console.releases import rows_from_releases, software_names
from src.console.session import (
    LIST_TOP,
    activate,
    interface_kind,
    move_index,
    parse_mouse_report,
    release_index_at,
    screen_lines,
)
from src.console.session import BrowseState
from src.console.simulate import install_progress, rockbox_themes
from src.console.tui import _card_action_at, _install_card_lines


def _package(name, model, repo, brand="Innioasis"):
    return Package(name=name, brand=brand, model=model, repo=repo, package_name="rom.zip")


def _asset(name):
    return {"name": name, "browser_download_url": "https://example.invalid/" + name, "size": 10}


def _release(tag, assets, body="Since v0.5 most work went into stability."):
    return {
        "tag_name": tag,
        "name": tag,
        "body": body,
        "prerelease": False,
        "draft": False,
        "html_url": "https://example.invalid/" + tag,
        "assets": assets,
    }


def _rockbox_releases():
    return [
        _release("0.6", [_asset("rom.zip")]),
        _release("0.5", [_asset("rom.zip")]),
        _release("0.4", [_asset("rom.zip"), _asset("rom_240p.zip"), _asset("rom_type_b.zip")]),
        _release("0.3", [_asset("rom.zip"), _asset("rom_240p.zip")]),
    ]


class CatalogueTests(unittest.TestCase):
    def test_brand_model_type_and_software_filters(self):
        items = [
            _package("Rockbox", "Y1", "rockbox-y1/rockbox"),
            _package("Original Software", "Y1", "y1-community/y1-stock-rom"),
            _package("Rockbox", "Y2", "spycemagic/y2-rockbox"),
            _package("Other", "Y1", "example/other", brand="Elsewhere"),
        ]
        self.assertEqual(software_names(items, "Innioasis", "Y1"), ["Rockbox", "Original Software"])
        self.assertEqual(software_names(items, "Innioasis", "Y2"), ["Rockbox"])
        self.assertEqual(software_names(items, "Elsewhere", "Y1"), ["Other"])

    def test_y1_rockbox_rows_match_the_window_filters(self):
        rows = rows_from_releases(
            _rockbox_releases(),
            model="Y1",
            hardware_type="A",
            repo="rockbox-y1/rockbox",
            show_old=True,
            show_240p=True,
        )
        self.assertEqual(
            [row.label for row in rows],
            ["0.6", "0.5", "0.4", "0.4 (240p)", "0.3", "0.3 (240p)"],
        )
        hidden = rows_from_releases(
            _rockbox_releases(),
            model="Y1",
            hardware_type="A",
            repo="rockbox-y1/rockbox",
            show_old=False,
            show_240p=False,
        )
        self.assertEqual([row.label for row in hidden], ["0.6", "0.5"])
        type_b = rows_from_releases(
            _rockbox_releases(),
            model="Y1",
            hardware_type="B",
            repo="rockbox-y1/rockbox",
            show_old=True,
            show_240p=True,
        )
        self.assertEqual([row.label for row in type_b], ["0.4"])

    def test_screen_lists_real_rows_not_the_stub(self):
        state = BrowseState(brand="Innioasis", model="Y1", hardware_type="A", software="Rockbox")
        state.releases = rows_from_releases(
            _rockbox_releases(),
            model="Y1",
            hardware_type="A",
            repo="rockbox-y1/rockbox",
            show_old=True,
            show_240p=True,
        )
        text = "\n".join(screen_lines(state))
        self.assertIn("Select Software", text)
        self.assertIn("Brand: Innioasis", text)
        self.assertIn("> 0.6", text)
        self.assertIn("0.4 (240p)", text)
        self.assertIn("Install", text)
        self.assertIn("Settings", text)
        self.assertNotIn("Nothing is downloaded until this step needs a file.", text)


class PointerTests(unittest.TestCase):
    def test_mouse_and_keyboard_activate_the_same_row(self):
        rows = rows_from_releases(
            _rockbox_releases(),
            model="Y1",
            hardware_type="A",
            repo="rockbox-y1/rockbox",
            show_old=True,
            show_240p=True,
        )
        clicked = release_index_at(LIST_TOP, scroll=0, count=len(rows), list_height=10)
        self.assertEqual(activate(rows, clicked).label, "0.6")
        moved = move_index(0, "down", len(rows))
        clicked_next = release_index_at(LIST_TOP + 1, scroll=0, count=len(rows), list_height=10)
        self.assertEqual(moved, clicked_next)
        self.assertEqual(activate(rows, moved).label, activate(rows, clicked_next).label)
        report = parse_mouse_report("\x1b[<0;1;5M")
        self.assertEqual(report, (0, 4))
        self.assertEqual(release_index_at(report[1], scroll=0, count=len(rows), list_height=10), 0)

    def test_power_off_card_buttons_are_clickable(self):
        state = SimpleNamespace(model="Y1", software="Rockbox")
        lines = _install_card_lines(state, offered=True, keep=False)
        self.assertEqual(_card_action_at(lines, len(lines) - 2), "next")
        self.assertEqual(_card_action_at(lines, len(lines) - 1), "cancel")
        keep_row = next(index for index, line in enumerate(lines) if line.startswith("["))
        self.assertEqual(_card_action_at(lines, keep_row), "keep")
        self.assertIsNone(_card_action_at(lines, 0))
        plain = _install_card_lines(state, offered=False, keep=False)
        self.assertEqual(_card_action_at(plain, len(plain) - 2), "next")
        self.assertEqual(_card_action_at(plain, len(plain) - 1), "cancel")

    def test_plain_failover(self):
        self.assertEqual(interface_kind(stdin_tty=False, stdout_tty=True, term="xterm", curses_ok=True), "plain")
        self.assertEqual(interface_kind(stdin_tty=True, stdout_tty=False, term="xterm", curses_ok=True), "plain")
        self.assertEqual(interface_kind(stdin_tty=True, stdout_tty=True, term="dumb", curses_ok=True), "plain")
        self.assertEqual(interface_kind(stdin_tty=True, stdout_tty=True, term="xterm", curses_ok=False), "plain")
        self.assertEqual(interface_kind(stdin_tty=True, stdout_tty=True, term="xterm", curses_ok=True), "curses")


class ComponentTests(unittest.TestCase):
    def test_prefetch_threshold(self):
        needed = ["spflash", "adb"]
        self.assertEqual(prefetch_plan(61, needed, []), ["spflash", "adb"])
        self.assertEqual(prefetch_plan(100, needed, ["adb"]), ["spflash"])
        self.assertEqual(prefetch_plan(60, needed, []), [])
        self.assertEqual(prefetch_plan(10, needed, []), [])
        self.assertEqual(prefetch_plan(None, needed, []), [])

    def test_ensure_skips_downloader_when_bundled(self):
        root = Path(self.id().replace(".", "_"))
        folder = REPO / "tests" / "_tmp_adb_bundle"
        binary = folder / "tools" / "adb" / "macos-arm64" / "adb"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"x" * 200_000)
        calls = []

        def downloader(key):
            calls.append(key)
            raise AssertionError("downloader was called")

        try:
            found = ensure(
                "adb",
                verbose=False,
                roots=[folder],
                platform="macos-arm64",
                downloader=downloader,
            )
        finally:
            binary.unlink(missing_ok=True)
        self.assertEqual(calls, [])
        self.assertEqual(found, binary)

    def test_32bit_does_not_select_x86_64_sp_or_adb(self):
        folder = REPO / "tests" / "_tmp_sp_arch"
        tool = folder / "flash_tool"
        adb = folder / "tools" / "adb" / "linux-x64" / "adb"
        tool.parent.mkdir(parents=True, exist_ok=True)
        adb.parent.mkdir(parents=True, exist_ok=True)
        tool.write_bytes(b"x" * 200_000)
        adb.write_bytes(b"x" * 200_000)
        try:
            self.assertIsNone(resolve(COMPONENTS["spflash"], roots=[folder], platform="linux-armhf"))
            self.assertIsNone(resolve(COMPONENTS["spflash"], roots=[folder], platform="linux-i686"))
            self.assertEqual(resolve(COMPONENTS["spflash"], roots=[folder], platform="linux-x86_64"), tool)
            self.assertNotEqual(
                find_bundled(COMPONENTS["adb"], roots=[folder], platform="linux-i686"),
                adb,
            )
            self.assertIsNone(find_bundled(COMPONENTS["adb"], roots=[folder], platform="linux-armhf"))
            self.assertEqual(session_component_keys("linux-i686"), [])
            self.assertEqual(session_component_keys("linux-armhf"), [])
            self.assertEqual(session_component_keys("linux-x86_64", headless=True), ["adb"])
            self.assertEqual(session_component_keys("linux-x86_64", headless=False), ["spflash", "adb"])
        finally:
            tool.unlink(missing_ok=True)
            adb.unlink(missing_ok=True)

    def test_sp_console_args_match_the_window(self):
        args = sp_flash_tool_console_args("scatter.txt", "DA.bin")
        self.assertEqual(args, ["-c", "download", "-s", "scatter.txt", "-d", "DA.bin", "-t", "without", "-r"])
        formatted = sp_flash_tool_console_args("scatter.txt", "DA.bin", format_all=True)
        self.assertEqual(formatted[1], "format-download")

    def test_headless_and_32bit_use_mtk(self):
        engine, reason = install_engine("linux", "x86_64", headless=True)
        self.assertEqual(engine, "mtk")
        self.assertIn("Qt", reason)
        self.assertIn("mtk wl", reason)
        engine, reason = install_engine("linux", "armhf", headless=False)
        self.assertEqual(engine, "mtk")
        self.assertIn("not the Linux x86_64", reason)
        command = mtk_write_command(["mtk"], Path("/fw"), preloader=Path("/p.bin"), da=Path("/da.bin"), auth=Path("/a.auth"))
        self.assertEqual(command, ["mtk", "wl", "/fw", "--preloader", "/p.bin", "--loader", "/da.bin", "--auth", "/a.auth"])


class SimulationTests(unittest.TestCase):
    def test_simulated_install_and_themes_do_not_spawn_tools(self):
        spawned = []

        def boom(*_args, **_kwargs):
            spawned.append(1)
            raise AssertionError("a real tool was started")

        original = subprocess.Popen
        subprocess.Popen = boom
        try:
            install = install_progress(sleep=0)
            themes = rockbox_themes(sleep=0)
            failed = install_progress(sleep=0, fail=True)
        finally:
            subprocess.Popen = original
        self.assertEqual(spawned, [])
        self.assertEqual(install[0]["message"], "Downloading firmware")
        self.assertTrue(any("45%" in event["message"] for event in install))
        self.assertTrue(install[-1]["ok"])
        self.assertIn("Flash complete", install[-1]["message"])
        self.assertFalse(failed[-1]["ok"])
        self.assertIn("Install failed", failed[-1]["message"])
        self.assertIn("Downloading 360p Theme Pack", themes[0]["message"])
        self.assertTrue(themes[-1]["ok"])
        self.assertIn("Theme pack successfully installed", themes[-1]["message"])

    def test_simulate_flag_blocks_subprocess(self):
        from src.console import flash

        original = os.environ.get("INNIOASIS_SIMULATE")
        os.environ["INNIOASIS_SIMULATE"] = "1"
        try:
            code = flash._run(["mtk", "wl", "/nowhere"])
            adb_code = flash.adb(["push", "theme", "/sdcard"])
        finally:
            if original is None:
                os.environ.pop("INNIOASIS_SIMULATE", None)
            else:
                os.environ["INNIOASIS_SIMULATE"] = original
        self.assertEqual(code, 0)
        self.assertEqual(adb_code, 0)


class LauncherTests(unittest.TestCase):
    def test_no_flag_still_reaches_the_gui_source(self):
        text = (REPO / "launcher.py").read_text(encoding="utf-8")
        tui_at = text.index('if "--tui" in sys.argv:')
        gui_at = text.index("from src.app import main")
        self.assertLess(tui_at, gui_at)
        self.assertIn("sys.exit(_tui_main(sys.argv))", text[tui_at:gui_at])
        self.assertIn('if "--cli" in sys.argv or "--lite" in sys.argv:', text)
        self.assertNotIn("PySide6", (REPO / "src" / "tui_session.py").read_text(encoding="utf-8"))

    def test_tui_import_does_not_load_pyside(self):
        code = (
            "import src.console, src.console.tui, src.tui_session, sys\n"
            "assert 'PySide6' not in sys.modules\n"
            "assert 'shiboken6' not in sys.modules\n"
            "print('ok')\n"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
