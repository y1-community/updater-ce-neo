from __future__ import annotations

import datetime as _dt
import os
import random
import re
import urllib.request

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import paths
from ..i18n import tr

PORTRAIT_URL = "https://innioasis.app/mtkclient/gui/images/developer.png"
PORTRAIT_DIAMETER = 56

INTRO_KEYS = ("donate_headline", "donate_subtitle")
COMPLETION_KEYS = ("donate_completion_headline", "donate_completion_subtitle")
THEMES_COMPLETION_KEYS = ("donate_completion_headline", "donate_completion_themes_subtitle")

# One pool for every completion message. The picker, the re-render after a
# language change and the tests all read this tuple, so a new variant can
# never be shown without being translatable and covered.
COMPLETION_BODY_KEYS = (
    "donate_completion_body",
    "donate_completion_body_reciprocity",
    "donate_completion_body_community",
    "donate_completion_body_effort",
    "donate_completion_body_momentum",
    "donate_completion_body_transparency",
    "donate_completion_body_family",
    "donate_completion_body_running_costs",
    "donate_completion_body_kindness",
    "donate_completion_body_small_hours",
)

LEGACY_APPEAL_KEY = "donate_support_appeal_lead"
#: One complete sentence pair for the legacy appeal. The pair below was two
#: fragments of a single English sentence, and translators punctuated the first
#: fragment as if it ended there ("...test devices. that make..."), so the card
#: read as a broken sentence in every language that did so.
FULL_APPEAL_KEY = "donate_support_appeal_full"
SUPPORT_APPEAL_KEYS = (
    LEGACY_APPEAL_KEY,
    "donate_appeal_always_free",
    "donate_appeal_community_archive",
    "donate_appeal_family_and_time",
    "donate_appeal_human_touch",
    "donate_appeal_keep_going",
    "donate_support_appeal_servers",
    "donate_support_appeal_evenings",
    "donate_support_appeal_independent",
    "donate_support_appeal_testbench",
    "donate_support_appeal_bandwidth",
    "donate_support_appeal_monthly_costs",
    "donate_support_appeal_small_change",
    "donate_support_appeal_long_nights",
    "donate_support_appeal_behind_the_scenes",
    "donate_support_appeal_next_feature",
    "donate_support_appeal_kindness",
    "donate_support_appeal_rescue",
    "donate_support_appeal_no_subscription",
    "donate_support_appeal_one_person",
    "donate_support_appeal_archive_forever",
    "donate_support_appeal_coffee_math",
)

FAMILY_APPEAL_KEYS = (
    "donate_family_appeal_expected",
    "donate_family_appeal_new_arrival",
    "donate_family_appeal_nursery",
)
FAMILY_COHORT_KEY = "donation_family_appeal_cohort"
FAMILY_COHORT_FRACTION = 0.30
FAMILY_APPEAL_LAST_DAY = _dt.date(2027, 4, 17)

FAMILY_APPEAL_WEIGHT = 3.0
FAMILY_NO_DONATION_BOOST = 2.0
FAMILY_SHORT_GOAL_BOOST = 1.8

_RECENT_KEYS: list[str] = []

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*")

def _fill(text: str, **fields) -> str:
    for name, value in fields.items():
        value = str(value or "").strip()
        if value:
            text = text.replace("{%s}" % name, value)
    return text

def family_appeal_window_open(now=None) -> bool:
    now = now or _dt.datetime.now()
    return now.date() <= FAMILY_APPEAL_LAST_DAY

def family_appeal_cohort(settings=None) -> bool:
    from PySide6.QtCore import QSettings

    store = settings if settings is not None else QSettings("innioasis", "updater")
    raw = store.value(FAMILY_COHORT_KEY, None)
    if raw is None:
        chosen = random.random() < FAMILY_COHORT_FRACTION
        store.setValue(FAMILY_COHORT_KEY, "1" if chosen else "0")
        try:
            store.sync()
        except Exception:
            pass
        return chosen
    return str(raw).strip().lower() in ("1", "true", "yes", "on")

def _donated_today(donations, now) -> bool:
    for row in donations or ():
        when = row.get("dt")
        if isinstance(when, _dt.datetime):
            when = when.date()
        if when == now.date():
            try:
                if float(row.get("amount") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False

def _short_period_goal(donations, now) -> bool:
    from ..donors import get_goal_stats, goal_scope

    try:
        scope = get_goal_stats(donations or [], now=now).get("scope")
    except Exception:
        return goal_scope(now) in ("daily", "weekly")
    return scope in ("daily", "weekly")

def _choose(pool, rng=random) -> str:
    fresh = [key for key in pool if key not in _RECENT_KEYS]
    key = rng.choice(fresh or list(pool))
    _RECENT_KEYS.append(key)
    del _RECENT_KEYS[:-4]
    return key

def pick_support_appeal_key(donations=None, now=None, settings=None, rng=random) -> str:
    now = now or _dt.datetime.now()
    weight = 0.0
    if family_appeal_window_open(now) and family_appeal_cohort(settings):
        weight = FAMILY_APPEAL_WEIGHT
        if not _donated_today(donations, now):
            weight *= FAMILY_NO_DONATION_BOOST
        if _short_period_goal(donations, now):
            weight *= FAMILY_SHORT_GOAL_BOOST
    if weight > 0 and rng.random() < weight / (weight + len(SUPPORT_APPEAL_KEYS)):
        return _choose(FAMILY_APPEAL_KEYS, rng)
    return _choose(SUPPORT_APPEAL_KEYS, rng)

def _bold_lead(text: str) -> str:
    text = str(text or "").strip()
    match = _SENTENCE_BREAK.search(text)
    if match is None:
        return text
    lead, rest = text[: match.start()].strip(), text[match.end():].strip()
    if not lead or not rest:
        return text
    return f"<b>{lead}</b>{match.group(0)}{rest}"

def support_appeal_text(key: str) -> str:
    key = key or LEGACY_APPEAL_KEY
    if key != LEGACY_APPEAL_KEY:
        return tr(key)
    full = tr(FULL_APPEAL_KEY)
    if full and full != FULL_APPEAL_KEY:
        return full
    return f"{tr('donate_support_appeal_lead')} {tr('donate_support_appeal_followup')}"


def support_appeal_html(key: str) -> str:
    return _bold_lead(support_appeal_text(key))

def bundled_portrait_path():
    return paths.RESOURCES_DIR / "developer.png"

def circle_pixmap(source: QPixmap, diameter: int = PORTRAIT_DIAMETER) -> QPixmap:
    diameter = max(1, int(diameter))
    image = QImage(diameter, diameter, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if source is not None and not source.isNull():
        scaled = source.scaled(
            diameter,
            diameter,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - diameter) // 2)
        y = max(0, (scaled.height() - diameter) // 2)
        cropped = scaled.copy(x, y, diameter, diameter)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addEllipse(0, 0, diameter, diameter)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
    return QPixmap.fromImage(image)

class _PortraitFetch(QThread):
    finished_bytes = Signal(bytes)

    def run(self) -> None:
        if self.isInterruptionRequested():
            self.finished_bytes.emit(b"")
            return
        try:
            request = urllib.request.Request(
                PORTRAIT_URL,
                headers={"User-Agent": "UpdaterCE"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read()
        except Exception:
            data = b""
        if self.isInterruptionRequested():
            data = b""
        self.finished_bytes.emit(data)

class DeveloperPortrait(QLabel):

    def __init__(self, diameter: int = PORTRAIT_DIAMETER, parent=None):
        super().__init__(parent)
        self._diameter = int(diameter)
        self._thread = None
        self.setFixedSize(self._diameter, self._diameter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        self.setPixmap(circle_pixmap(QPixmap(str(bundled_portrait_path())), self._diameter))
        self.destroyed.connect(self._abandon)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_fetch)
        self._maybe_fetch()

    def apply_bytes(self, data: bytes) -> bool:
        if not data:
            return False
        pix = QPixmap()
        if not pix.loadFromData(data) or pix.isNull():
            return False
        self.setPixmap(circle_pixmap(pix, self._diameter))
        return True

    def _maybe_fetch(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        thread = _PortraitFetch()
        thread.finished_bytes.connect(self.apply_bytes)
        self._thread = thread
        thread.start()

    def _abandon(self, *_args) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            thread.finished_bytes.disconnect(self.apply_bytes)
        except Exception:
            pass
        thread.requestInterruption()
        if thread.isRunning():
            thread.wait(1000)

    def _stop_fetch(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        thread.requestInterruption()
        if thread.isRunning():
            thread.wait(2000)

class SupportIntro(QWidget):

    def __init__(self, parent=None, keys=INTRO_KEYS, show_portrait=True, show_icon=False):
        super().__init__(parent)
        self._keys = tuple(keys)
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.icon = None
        if show_icon:
            # The firmware's own icon (the generic squircle when it has none)
            # beside the headline, so the first thing read is what finished.
            from .release_icon import squircle_pixmap

            self.icon = QLabel()
            self.icon.setFixedSize(40, 40)
            self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.icon.setAutoFillBackground(False)
            self.icon.setStyleSheet("")
            self.icon.setPixmap(squircle_pixmap(QPixmap(), 40))
            row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.portrait = DeveloperPortrait(PORTRAIT_DIAMETER) if show_portrait else None
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.headline = QLabel()
        self.headline.setTextFormat(Qt.TextFormat.RichText)
        self.headline.setWordWrap(True)
        self.headline.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        font = self.headline.font()
        font.setPixelSize(18)
        font.setWeight(QFont.Weight.DemiBold)
        self.headline.setFont(font)
        self.headline.setAutoFillBackground(False)
        self.headline.setStyleSheet("")
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setProperty("cssClass", "dimmed")
        self.subtitle.setAutoFillBackground(False)
        column.addWidget(self.headline)
        column.addWidget(self.subtitle)
        if self.portrait is not None:
            row.addWidget(self.portrait, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(column, 1)
        self.retranslate()

    def set_icon(self, pixmap) -> None:
        """Show ``pixmap`` beside the headline; a null one keeps the placeholder."""
        if self.icon is None:
            return
        from .release_icon import squircle_pixmap

        self.icon.setPixmap(squircle_pixmap(pixmap, 40))

    def set_keys(self, keys) -> None:
        self._keys = tuple(keys)
        self.retranslate()

    def retranslate(self, software: str = "", model: str = "") -> None:
        headline_key, subtitle_key = self._keys
        self.headline.setText(tr(headline_key))
        named = software
        if subtitle_key == "donate_completion_subtitle":
            from ..catalog import software_from_install_heading

            # A caller that still holds the install heading gets its release
            # name read back, rather than the placeholder sentence.
            named = software_from_install_heading(software, model) or software
        if subtitle_key == "donate_completion_subtitle" and (
            not named or named.startswith(("Get ready to install", "Installing software", "Get your"))
        ):
            self.subtitle.setText(_fill(tr("donate_completion_subtitle_the_software"), model=model))
        else:
            self.subtitle.setText(_fill(tr(subtitle_key), software=named, model=model))
