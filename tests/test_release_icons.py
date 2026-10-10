"""Release icon URLs are built from the tag. Missing files stay a squircle."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from src.catalog import FirmwarePackage
from src.manifest import parse_manifest_xml
from src.release_icons import LIGHT_ICON_NAMES, icon_candidate_urls
from src.ui.dark import apply_theme, get_native_accent_color
from src.ui.flash_page import FlashPage
from src.ui.release_icon import glyph_box, placeholder_settings_pixmap, squircle_pixmap


def _release():
    return {
        "tag_name": "v3.0.7",
        "source_repo": "y1-community/y1-stock-rom",
        "assets": [{
            "name": "updater.png",
            "browser_download_url": "https://api.github.com/repos/y1-community/y1-stock-rom/releases/assets/1",
        }],
    }


def _package(**kwargs):
    return FirmwarePackage(
        "original-y1", "Original Software", "Y1",
        "y1-community/y1-stock-rom", "rom.zip",
        **kwargs,
    )


def test_icon_url_is_built_from_the_tag_without_the_api():
    urls = icon_candidate_urls(_release(), _package(), dark=False)
    assert urls[0] == (
        "https://github.com/y1-community/y1-stock-rom/releases/download/v3.0.7/updater.jpg"
    )
    assert [url.rsplit("/", 1)[-1] for url in urls[:3]] == list(LIGHT_ICON_NAMES)
    assert all("api.github.com" not in url for url in urls)
    assert urls[0].endswith("/updater.jpg")
    assert urls.index(urls[0]) < next(i for i, url in enumerate(urls) if url.endswith("/updater.png"))


def test_dark_names_then_light_then_manifest_icon():
    package = _package(
        icon="https://example.com/logo.jpg",
        icon_dark="https://example.com/logo-dark.jpeg",
    )
    dark_names = [url.rsplit("/", 1)[-1] for url in icon_candidate_urls(_release(), package, dark=True)]
    assert dark_names[:6] == [
        "updater_dark.jpg", "updater_dark.jpeg", "updater_dark.png",
        "updater.jpg", "updater.jpeg", "updater.png",
    ]
    assert dark_names[-2:] == ["logo-dark.jpeg", "logo.jpg"]
    light_names = [url.rsplit("/", 1)[-1] for url in icon_candidate_urls(_release(), package, dark=False)]
    assert light_names[-1] == "logo.jpg"
    assert "logo-dark.jpeg" not in light_names


def test_manifest_icon_key_accepts_jpg_and_image_dark():
    xml = """<slidia_manifest>
        <package name="Original Software" repo="y1-community/y1-stock-rom" device="Y1"
                 type="img" handler="Custom Firmware"
                 icon="https://example.com/logo.jpg"
                 image_dark="https://example.com/logo-dark.jpg" />
        <package name="Rockbox" repo="rockbox-y1/rockbox" device="Y1"
                 type="img" handler="Custom Firmware"
                 icon="https://example.com/rock.png"
                 icon_dark="https://example.com/rock-dark.png"
                 image_dark="https://example.com/ignored.jpg" />
    </slidia_manifest>"""
    parsed = parse_manifest_xml(xml)
    assert parsed[0].icon == "https://example.com/logo.jpg"
    assert parsed[0].icon_dark == "https://example.com/logo-dark.jpg"
    assert parsed[1].icon_dark == "https://example.com/rock-dark.png"


def test_missing_file_falls_through_to_the_squircle(tmp_path=None):
    import src.release_icons as icons
    import src.ui.release_icon as ui_icons

    calls = []

    def miss(url, timeout=8):
        calls.append(url)
        return None

    original = icons.fetch_icon_bytes
    icons.fetch_icon_bytes = miss
    ui_icons.fetch_icon_bytes = miss
    try:
        app = QApplication.instance() or QApplication(sys.argv)
        cache = Path(__file__).resolve().parent / "_icon_cache"
        cache.mkdir(exist_ok=True)
        for child in cache.glob("*"):
            child.unlink()
        from src.ui.release_icon import load_release_pixmap

        pixmap = load_release_pixmap(
            _release(), _package(icon=""), allow_network=True, cache_dir=cache, dark=False,
        )
        assert pixmap.isNull()
        painted = squircle_pixmap(pixmap, 40, complete=False)
        assert not painted.isNull()
        assert calls[0].endswith("/updater.jpg")
        assert [url.rsplit("/", 1)[-1] for url in calls] == list(LIGHT_ICON_NAMES)
        assert all("api.github.com" not in url for url in calls)
        checked = squircle_pixmap(pixmap, 40, complete=True)
        assert _has_green(checked)
        del app
    finally:
        icons.fetch_icon_bytes = original
        ui_icons.fetch_icon_bytes = original


def test_settings_glyph_is_centered_on_the_accent():
    app = QApplication.instance() or QApplication(sys.argv)
    side = 40
    origin, _, glyph, _ = glyph_box(side)
    assert glyph >= 24
    assert abs((origin + glyph / 2) - (side / 2)) <= 1
    pix = placeholder_settings_pixmap(side)
    assert pix.width() == side and pix.height() == side
    accent, text = get_native_accent_color()
    image = pix.toImage()
    fill = image.pixelColor(side // 2, 4)
    assert _near(fill, QColor(accent), 48), (fill.name(), accent)
    text_color = QColor(text)
    hits = []
    for y in range(side):
        for x in range(side):
            color = image.pixelColor(x, y)
            if color.alpha() > 80 and _near(color, text_color, 140):
                hits.append((x, y))
    assert hits, "settings glyph did not paint"
    xs = [x for x, _y in hits]
    ys = [y for _x, y in hits]
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    assert abs(center_x - side / 2) <= 3, center_x
    assert abs(center_y - side / 2) <= 3, center_y
    assert max(xs) - min(xs) >= 14
    assert min(xs) > 2 and min(ys) > 2
    del app


def test_dark_progress_text_has_no_backdrop():
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    try:
        page = FlashPage()
        page.set_model("Y1")
        page.set_package_name("Original Software 3.0.7 for Y1")
        page.show_flashing()
        page.update_step("step_write")
        page._eta_label.setText("— About 57 seconds remaining")
        page.refresh_theme()
        for label in (page._step_label, page._eta_label, page._flash_pkg_label, page._warning):
            assert label.autoFillBackground() is False
            assert "background" not in label.styleSheet()
            color = label.palette().color(label.foregroundRole())
            assert color.lightness() > 200, (label.text(), color.name())
    finally:
        apply_theme(app)


def test_preload_schedule_is_staggered():
    import random

    from src.release_icons import (
        JITTER_MAX_SECONDS,
        JITTER_MIN_SECONDS,
        PRELOAD_CONCURRENCY,
        preload_start_times,
    )

    times = preload_start_times(4, random.Random(3))
    assert times[0] == 0.0
    assert any(t > 0 for t in times)
    assert times != [0.0, 0.0, 0.0, 0.0]
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    assert all(JITTER_MIN_SECONDS - 1e-9 <= gap <= JITTER_MAX_SECONDS + 1e-9 for gap in gaps)
    assert len({round(gap, 5) for gap in gaps}) > 1
    assert PRELOAD_CONCURRENCY == 1


def test_cross_fade_starts_on_software_and_ends_on_release():
    from PySide6.QtGui import QPixmap

    from src.ui.release_icon import FadingIcon

    app = QApplication.instance() or QApplication(sys.argv)
    software = QPixmap(40, 40)
    software.fill(QColor("#e11d48"))
    release = QPixmap(40, 40)
    release.fill(QColor("#2563eb"))
    icon = FadingIcon(40)
    icon.show_stand_in(software)
    assert _near(icon.pixmap().toImage().pixelColor(20, 20), QColor("#e11d48"), 8)
    icon.cross_fade_to(release)
    assert icon.blend_amount() == 0.0
    assert _near(icon.pixmap().toImage().pixelColor(20, 20), QColor("#e11d48"), 8)
    icon.finish_cross_fade()
    assert _near(icon.pixmap().toImage().pixelColor(20, 20), QColor("#2563eb"), 8)
    del app


def test_page_change_is_a_short_cross_fade():
    from PySide6.QtWidgets import QLabel

    from src.ui import widgets as widget_mod
    from src.ui.widgets import PAGE_FADE_MS, CurrentPageStack

    app = QApplication.instance() or QApplication(sys.argv)
    assert 150 <= PAGE_FADE_MS <= 280
    stack = CurrentPageStack()
    stack.addWidget(QLabel("select"))
    stack.addWidget(QLabel("install"))
    stack.resize(320, 180)
    stack.show()
    app.processEvents()
    original = widget_mod.prefers_reduced_motion
    try:
        widget_mod.prefers_reduced_motion = lambda: True
        stack.setCurrentIndex(1)
        assert stack.currentIndex() == 1
        assert stack.widget(1).graphicsEffect() is None

        widget_mod.prefers_reduced_motion = lambda: False
        stack.setCurrentIndex(0)
        assert stack.currentIndex() == 0
        assert stack._page_fade is not None
        assert stack._page_fade.duration() == PAGE_FADE_MS
        from PySide6.QtCore import QEasingCurve
        assert stack._page_fade.easingCurve().type() == QEasingCurve.Type.OutCubic
        # A grabbed outgoing frame keeps the live page on the platform style.
        # Offscreen grab can be empty, and then the incoming page fades in.
        if stack._fade_overlay is not None:
            assert stack.widget(0).graphicsEffect() is None
        else:
            assert stack.widget(0).graphicsEffect() is not None
        stack._clear_page_fade()
        assert stack.widget(0).graphicsEffect() is None
        assert stack._fade_overlay is None
    finally:
        widget_mod.prefers_reduced_motion = original
        stack.close()


def test_update_toast_is_a_translucent_row():
    from PySide6.QtWidgets import QPushButton

    from src.ui.widgets import Banner, UpdateToast, banner_material_color

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    try:
        veil = banner_material_color("info")
        assert 0 < veil.alpha() <= 80
        assert veil.alpha() < 255
        toast = UpdateToast()
        toast.set_offers([{
            "software": "Original Software",
            "model": "Y1",
            "tag": "v3",
        }])
        assert "Original Software" in toast.text()
        assert "Y1" in toast.text()
        install = toast.findChild(QPushButton, "updateToastInstall")
        close = toast.findChild(QPushButton, "updateToastClose")
        assert install is not None and install.isVisible()
        assert install.text() == "Install"
        assert close is not None and close.isVisible()
        assert toast.findChild(QPushButton, "updateToastLater") is None
        assert toast.findChild(QPushButton, "updateToastDontRemind") is None
        toast.set_offers([
            {"software": "Original Software", "model": "Y1", "tag": "v3"},
            {"software": "Original Software", "model": "Y2", "tag": "v3"},
        ])
        assert not install.isVisible()
        models = toast.findChildren(QPushButton, "updateToastModel")
        assert [button.text() for button in models] == ["Y1", "Y2"]
        assert "background" not in toast.styleSheet()
        assert "background" not in toast._label.styleSheet()
        banner = Banner()
        banner.set_type("info")
        assert "background-color" not in banner.styleSheet()
        assert banner_material_color("info").alpha() <= 80
    finally:
        apply_theme(app)


def _near(color: QColor, other: QColor, tolerance: int) -> bool:
    return (
        abs(color.red() - other.red())
        + abs(color.green() - other.green())
        + abs(color.blue() - other.blue())
    ) <= tolerance


def _has_green(pixmap) -> bool:
    image = pixmap.toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.green() > 160 and color.red() < 80 and color.alpha() > 200:
                return True
    return False


if __name__ == "__main__":
    test_icon_url_is_built_from_the_tag_without_the_api()
    test_dark_names_then_light_then_manifest_icon()
    test_manifest_icon_key_accepts_jpg_and_image_dark()
    test_missing_file_falls_through_to_the_squircle()
    test_settings_glyph_is_centered_on_the_accent()
    test_dark_progress_text_has_no_backdrop()
    test_preload_schedule_is_staggered()
    test_cross_fade_starts_on_software_and_ends_on_release()
    test_page_change_is_a_short_cross_fade()
    test_update_toast_is_a_translucent_row()
    print("release icon tests passed")
