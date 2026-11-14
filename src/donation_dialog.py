"""Donations modal, port of Updater CE's ``show_donation_dialog``."""

# PEP 810 (Python 3.15+): see src/ui/main_window.py for the convention. The
# donor list is fetched on a worker thread after the window is up, and this is
# the other module that imports it.
__lazy_modules__ = ["src.donors"]

import logging
import random
import sys

from . import fx
from .browser import open_browser

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .config import DONATION_LINKS, device_label_for_model
from .donors import (
    fetch_remote_donors_async,
    format_goal_line,
    get_goal_stats,
    is_anonymous_donor,
    relative_date,
)
from .i18n import tr, tr_brand
from .ui.dark import T, is_dark, theme_text_color
from .ui.glass import apply_dialog_theme

logger = logging.getLogger(__name__)


def _supporter_prefix() -> str:
    """``Top supporter`` with exactly one space before the donor's name.

    The ticker templates place ``{top_prefix}{anchor}`` directly against each
    other, so the prefix owns the separating space. Catalogs and translation
    services trim trailing whitespace; without this the line read
    "Лучший сторонникNathan P".
    """
    label = tr("donate_top_supporter_prefix").strip()
    return f"{label} " if label else ""


class _DonationRefreshBridge(QObject):
    updated = Signal(object)


def decorative_updates_needed(widget) -> bool:
    """True when a decorative ticker/animation is worth running at all."""
    if not widget.isVisible():
        return False
    window = widget.window()
    if window is None or not window.isVisible() or window.isMinimized():
        return False
    return True


class _LineLabel(QLabel):
    """Clickable status-bar line (goal / donor ticker).

    Emits the anchor href under the cursor, or ``""`` when plain text was
    clicked, so the bar can open a donor's transaction URL for a linked name
    and the donation dialog for every other word. The press is claimed so the
    release is delivered here (QLabel ignores presses that are not on a link).

    ``QLabel.anchorAt`` is not exposed by PySide6, so the link/plain split
    leans on QLabel itself: ``linkActivated`` fires for anchors, and a
    non-link release is left unaccepted by QLabel's own handler.
    """

    clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.linkActivated.connect(self.clicked.emit)

    def mousePressEvent(self, ev):
        super().mousePressEvent(ev)  # QLabel records the pressed link
        ev.accept()  # claim the click so the release is delivered here

    def mouseReleaseEvent(self, ev):
        super().mouseReleaseEvent(ev)  # emits linkActivated when on a link
        if not ev.isAccepted():  # QLabel ignores plain-text clicks
            self.clicked.emit("")
        ev.accept()


class _Ticker(QWidget):
    """Clips a status-bar line and scrolls it when the bar is too narrow.

    Credits / Thanks and Support Us keep their own width. This middle slot is
    the piece that gives up space: the full line stays put when it fits, and
    marquees when it does not, so the corner links are never pushed off.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._label = _LineLabel(self)
        label = self._label
        # The host is a bare widget with no layout, so a QVBoxLayout would give
        # it no height at all and the line would sit in a zero-height row. Ask
        # for exactly the label's height instead: the middle slot then owns its
        # row on every style, Windows and Fusion included.
        self.setMinimumHeight(max(1, label.sizeHint().height()))
        orig_set_text = label.setText
        orig_set_visible = label.setVisible

        def _set_text(text, _orig=orig_set_text):
            _orig(text)
            self.sync()

        def _set_visible(visible, _orig=orig_set_visible):
            _orig(bool(visible))
            self.setVisible(bool(visible))

        label.setText = _set_text
        label.setVisible = _set_visible
        self._offset = 0
        self._hold = 0
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._tick)

    @property
    def label(self) -> _LineLabel:
        return self._label

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self.sync()

    def sync(self):
        text_w = max(self._label.sizeHint().width(), 0)
        avail = self.width()
        height = max(self.height(), self._label.sizeHint().height())
        if avail <= 0 or text_w <= avail:
            self._timer.stop()
            self._offset = 0
            x = max(0, (avail - text_w) // 2)
            self._label.setGeometry(x, 0, text_w, height)
            return
        self._label.setGeometry(-self._offset, 0, text_w, height)
        if not self._timer.isActive():
            self._hold = 36
            self._timer.start()

    def _tick(self):
        if not decorative_updates_needed(self):
            return
        if self._hold > 0:
            self._hold -= 1
            return
        self._offset += 1
        limit = max(0, self._label.sizeHint().width() - self.width())
        if self._offset > limit + 32:
            self._offset = 0
            self._hold = 36
        self._label.move(-self._offset, 0)


class _LinkButton(QPushButton):
    """A button that reads as a text link rather than a control.

    The donation bar is a line of text, so its corner actions (Credits /
    Thanks, Support Us) carry no chrome: the label, a hover colour, a hand
    cursor, and keyboard focus. The bar styles them, since they follow the
    active theme.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoDefault(False)


def get_appeal_messages() -> list[str]:
    """Heartfelt community appeals rotating in place of or alongside the goal bar."""
    from .config import is_mediatek_installer
    from .i18n import tr

    msgs = [
        tr("donate_appeal_always_free"),
        tr("donate_appeal_community_archive"),
    ]
    if not is_mediatek_installer():
        msgs.append(tr("donate_appeal_what_innioasis_dont"))
    return msgs


def _resolve_bar_state(inst):
    st = getattr(inst, "_bar_st", None)
    n = len(getattr(inst, "donations", None) or ())
    if st is None or st[4] != (n > 2):
        from datetime import datetime
        now = datetime.now()
        nx = datetime(now.year + (now.month // 12), (now.month % 12) + 1, 1)
        dm = (nx - datetime(now.year, now.month, 1)).days
        ew = 1 if (now.day <= 7 or (dm - now.day) < 9) else 0
        pf = sys.platform[:6]
        os_k = 1 if pf == "darwin" else (2 if pf[:5] == "linux" else 0)
        r0 = random.random()
        r1 = random.random()
        g_ok = 1 if (os_k == 1 or n <= 2 or (r0 < 0.70 if ew else r0 >= 0.57)) else 0
        p_ok = 1 if (os_k == 1 or r1 < 0.028) else 0
        st = (os_k, ew, g_ok, p_ok, n > 2)
        inst._bar_st = st
    return st


def _order_bar_slots(inst, items):
    if not items:
        inst._donor_pool = []
        return []
    from datetime import datetime
    os_k, _ew, g_ok, p_ok, _ = _resolve_bar_state(inst)
    now = datetime.now()
    scored = []
    pool = []
    for idx, (line, amt, dt, is_top) in enumerate(items):
        age = max(0, (now - dt).days) if isinstance(dt, datetime) else 180
        gen = 1 if (amt >= 20.0 or is_top) else 0
        if os_k == 1:
            w = amt * 3.4 + (42.0 if gen else 0.0) + (16.0 if amt >= 10.0 else 0.0) + random.random() * 14.0
            reps = 3 if amt >= 20.0 else (2 if amt >= 10.0 else 1)
        else:
            base = (36.0 if amt < 10.0 else 7.0) + max(0.0, 20.0 - amt * 1.35)
            if os_k == 2:
                base += 52.0 if amt < 5.0 else (24.0 if amt <= 5.0 else 0.0)
            if not g_ok:
                span = min(age, 420) * 0.15 + max(0.0, 16.0 - amt * 1.5)
            else:
                span = (14.0 if (idx & 1) == (1 if age >= 60 else 0) else 6.0) + min(age, 240) * 0.03
            pen = 0.0 if (not gen or p_ok) else -600.0
            w = base + span + pen + random.random() * 11.0
            if gen and not p_ok:
                reps = 0
            elif os_k == 2 and amt < 5.0:
                reps = 4
            elif amt < 10.0:
                reps = 3 if (not g_ok and age >= 45) else 2
            else:
                reps = 1
        scored.append((w, idx, line, reps))
    scored.sort(key=lambda x: x[0], reverse=True)
    ordered = [x[2] for x in scored]
    for _, _, line, reps in scored:
        for _ in range(reps):
            pool.append(line)
    if not pool:
        pool = list(ordered)
    elif len(pool) > 2:
        head = pool[0]
        rest = pool[1:]
        random.shuffle(rest)
        pool = [head] + rest
    inst._donor_pool = pool
    return ordered


class DonationStatusBar(QStatusBar):
    """Compact, always-visible version of the Support dialog's goal display.

    The center copy is deliberately abbreviated to fit between Credits / Thanks
    and the right edge at the minimum window width; long donor names are clipped
    to a short display name and the ticker remains a fallback for other locales.
    All of it belongs to the donation UI, so it follows the "show donations" setting.
    """

    def __init__(self, parent=None, donations=None, on_support=None,
                 on_donations_updated=None, on_credits=None):
        super().__init__(parent)
        self.donations = donations or []
        self._on_support = on_support
        self._on_credits = on_credits
        self._on_donations_updated = on_donations_updated
        self._remote_refresh_interval_ms = 5 * 60 * 1000
        self._showing_goal = True
        self._donations_enabled = True
        self._current_message = ""
        self._status_revert_timer = QTimer(self)
        self._status_revert_timer.setSingleShot(True)
        self._status_revert_timer.timeout.connect(self.clearMessage)

        self.setObjectName("donation_status_bar")
        self.setSizeGripEnabled(False)
        self.setFixedHeight(30)
        self._build_ui(on_support, on_credits)
        self._donor_lines = self._build_donor_lines()
        self._refresh_goal()
        if self._goal_reached and self._donor_lines:
            self._donor_label.setText(self._donor_lines[0])
        self._rotation_timer = QTimer(self)
        self._rotation_timer.timeout.connect(self._rotate)
        self._rotation_timer.start(6500)

        self._refresh_bridge = _DonationRefreshBridge(self)
        self._refresh_bridge.updated.connect(self._apply_fresh_donations)
        self._remote_refresh_timer = QTimer(self)
        self._remote_refresh_timer.setInterval(self._remote_refresh_interval_ms)
        self._remote_refresh_timer.timeout.connect(self._refresh_remote_donors)
        self._remote_refresh_timer.start()
        # Startup fetch is unconditional: the bar is built before the window is
        # shown, so the visibility gate below would skip it.
        self._refresh_remote_donors(force=True)

    def _seal_ticker(self, label) -> None:
        """Erase the previous line without locking the bar to the full string.

        A minimum width as wide as the sentence pushes Support Us off the
        window. The ticker host clips and marquees instead.
        """
        from .ui.surfaces import seal_updating_text
        label.setMinimumWidth(0)
        seal_updating_text(label)
        label.setMinimumWidth(0)

    def credits_link(self):
        """The bar's Credits / Thanks link; the window owns its visibility."""
        return self._credits_link

    def _style_link(self, btn, t, align="left"):
        """Text-link styling for the bar's corner actions."""
        side = "left" if align == "left" else "right"
        # No background rule. ``background: transparent`` is stored as a slab
        # and is the solid strip behind Credits / Thanks.
        btn.setStyleSheet(
            f"QPushButton {{ color: palette(window-text); border: none;"
            f" padding: 2px 4px; font-size: 12px; font-weight: 700; text-align: {side}; }}"
            "QPushButton:hover { color: palette(window-text); }"
            "QPushButton:focus { color: palette(window-text); border: 1px solid palette(highlight);"
            f" border-radius: 5px; outline: none; }}"
        )
        btn.setCursor(Qt.ArrowCursor)
        from PySide6.QtGui import QPalette
        from .ui.dark import theme_foreground
        fg = theme_foreground()
        pal = btn.palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pal.setColor(group, QPalette.ColorRole.ButtonText, fg)
            pal.setColor(group, QPalette.ColorRole.WindowText, fg)
        btn.setPalette(pal)

    def _style_bar_label(self, label, *, weight: int) -> None:
        """Theme foreground, no background fill."""
        from .ui.dark import apply_explicit_foreground
        apply_explicit_foreground(
            label,
            f" font-size: 12px; font-weight: {weight}; border: none;",
        )

    def _apply_bar_material(self, t) -> None:
        """Use the window surface. This bar does not paint its own plate."""
        from PySide6.QtGui import QColor, QPalette
        from .ui.surfaces import glass_surfaces_enabled, show_glass_backdrop

        self.setObjectName("donation_status_bar")
        if glass_surfaces_enabled():
            self.setStyleSheet(
                "QStatusBar#donation_status_bar { background: transparent;"
                " color: palette(window-text); border: none; min-height: 30px; }"
            )
            self.setAutoFillBackground(False)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            clear = QColor(0, 0, 0, 0)
            pal = self.palette()
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                pal.setColor(group, QPalette.ColorRole.Window, clear)
                pal.setColor(group, QPalette.ColorRole.Base, clear)
            self.setPalette(pal)
            show_glass_backdrop(self)
        else:
            self.setStyleSheet(
                "QStatusBar#donation_status_bar { background: transparent;"
                " color: palette(window-text); border: none; min-height: 30px; }"
            )
            self.setAutoFillBackground(False)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
            clear = QColor(0, 0, 0, 0)
            pal = self.palette()
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                pal.setColor(group, QPalette.ColorRole.Window, clear)
                pal.setColor(group, QPalette.ColorRole.Base, clear)
            self.setPalette(pal)
            from .ui.dark import apply_readable_palette
            apply_readable_palette(self)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        """Leave the window material in place. The native panel is the plate."""
        from .ui.surfaces import glass_surfaces_enabled
        if glass_surfaces_enabled():
            from PySide6.QtGui import QColor, QPainter

            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
            painter.end()
        else:
            super().paintEvent(event)

    def _clear_bar_plates(self) -> None:
        """Keep goal, status, and link hosts on the same surface as the bar."""
        from PySide6.QtGui import QColor, QPalette
        from .ui.surfaces import (
            glass_surfaces_enabled,
            show_glass_backdrop,
        )

        from .ui.surfaces import paint_window_surface

        glass = glass_surfaces_enabled()
        clear = QColor(0, 0, 0, 0)
        for name in (
            "_donation_container",
            "_status_container",
            "_goal_group",
            "_goal_ticker",
            "_donor_ticker",
            "_status_ticker",
        ):
            host = getattr(self, name, None)
            if host is None:
                continue
            if not glass:
                # Without a backdrop there is nothing to show through: the host
                # must paint the window surface itself. A transparent host left
                # the previous, longer donor line behind the new one, because
                # nothing underneath was asked to repaint it.
                host.setStyleSheet("")
                paint_window_surface(host)
                continue
            host.setAutoFillBackground(False)
            host.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            host.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True if glass else False)
            host.setStyleSheet("background: transparent; border: none;")
            pal = host.palette()
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                pal.setColor(group, QPalette.ColorRole.Window, clear)
                pal.setColor(group, QPalette.ColorRole.Base, clear)
            host.setPalette(pal)
            if glass:
                show_glass_backdrop(host)

    def _build_ui(self, on_support, on_credits=None):
        t = T()
        # The bar is the same surface as the window. An opaque fill here is
        # the solid strip that no longer matches the translucent window.
        self._apply_bar_material(t)
        self.setMinimumHeight(30)

        # Donation container: Credits / Thanks, the centred goal display, and
        # the Support Us link in the right corner.
        self._donation_container = QWidget(self)
        row = QHBoxLayout(self._donation_container)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)

        self._credits_link = _LinkButton(tr("nav_credits"))
        self._credits_link.setToolTip(tr("nav_credits"))
        # Brands without an online credits page hide the link but keep its
        # space, so the goal display stays on the bar's centre line.
        _keep_space = self._credits_link.sizePolicy()
        _keep_space.setRetainSizeWhenHidden(True)
        self._credits_link.setSizePolicy(_keep_space)
        self._style_link(self._credits_link, t, "left")
        if on_credits:
            self._credits_link.clicked.connect(on_credits)
        row.addWidget(self._credits_link, 0, Qt.AlignVCenter)

        # The goal line and the donor ticker take turns in one centred slot:
        # exactly one of them is visible at a time, and the stretch either side
        # keeps whichever it is in the middle of the bar.
        self._goal_group = QWidget(self._donation_container)
        goal_row = QHBoxLayout(self._goal_group)
        goal_row.setContentsMargins(0, 0, 0, 0)
        goal_row.setSpacing(10)

        self._goal_ticker = _Ticker(self._goal_group)
        self._goal_label = self._goal_ticker.label
        self._goal_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._style_bar_label(self._goal_label, weight=600)
        self._seal_ticker(self._goal_label)
        goal_row.addWidget(self._goal_ticker, 1, Qt.AlignVCenter)

        self._donor_ticker = _Ticker(self._goal_group)
        self._donor_label = self._donor_ticker.label
        self._donor_label.setTextFormat(Qt.RichText)
        self._donor_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._style_bar_label(self._donor_label, weight=500)
        self._seal_ticker(self._donor_label)
        self._donor_label.setVisible(False)
        goal_row.addWidget(self._donor_ticker, 1, Qt.AlignVCenter)

        self._goal_bar = QProgressBar()
        self._goal_bar.setObjectName("softwareUpdateProgress")
        self._goal_bar.setRange(0, 1000)
        self._goal_bar.setTextVisible(False)
        self._goal_bar.setFixedHeight(8)
        self._goal_bar.setMinimumWidth(48)
        self._goal_bar.setMaximumWidth(120)
        goal_row.addWidget(self._goal_bar, 0, Qt.AlignVCenter)

        self._goal_group.setMinimumWidth(0)
        self._goal_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(self._goal_group, 1)

        self._support_btn = _LinkButton(tr("nav_donate"))
        self._support_btn.setToolTip(tr_brand("donate_title"))
        self._style_link(self._support_btn, t, "right")
        if on_support:
            self._support_btn.clicked.connect(on_support)
        # Sidebar Support Us is the entry point. This corner link is not shown.
        self._support_btn.setVisible(False)
        self._balance_links()
        self.addWidget(self._donation_container, 1)

        # Status container: clean status update message without donation collision
        self._status_container = QWidget(self)
        status_row = QHBoxLayout(self._status_container)
        status_row.setContentsMargins(12, 0, 12, 0)
        status_row.setSpacing(8)
        self._status_ticker = _Ticker(self._status_container)
        self._status_label = self._status_ticker.label
        self._status_label.setAlignment(Qt.AlignCenter)
        self._style_bar_label(self._status_label, weight=500)
        self._seal_ticker(self._status_label)
        status_row.addWidget(self._status_ticker, 1)
        self._status_container.setVisible(False)
        self.addWidget(self._status_container, 1)
        self._clear_bar_plates()

        # The goal and donor lines are clickable: any plain word opens the
        # donation dialog, while a donor name that carries a transaction URL
        # opens that URL instead.
        for label in (self._goal_label, self._donor_label):
            label.setCursor(Qt.PointingHandCursor)
            label.clicked.connect(self._handle_label_click)

    def _link_text_width(self, btn) -> int:
        return btn.fontMetrics().horizontalAdvance(btn.text()) + 16

    def _balance_links(self):
        """Keep both corner links balanced so the middle line stays centred."""
        if not hasattr(self, "_credits_link"):
            return
        credits_w = self._link_text_width(self._credits_link)
        self._credits_link.setFixedWidth(credits_w)
        if hasattr(self, "_support_btn"):
            self._support_btn.setVisible(False)
            self._support_btn.setFixedWidth(0)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._balance_links()
        for name in ("_goal_ticker", "_donor_ticker", "_status_ticker"):
            ticker = getattr(self, name, None)
            if ticker is not None:
                ticker.sync()

    def _relayout_links(self):
        """Re-measure the corner links (new wording, new font) and re-balance."""
        for link in (self._credits_link, self._support_btn):
            link.setMinimumWidth(0)
            link.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX
        self._balance_links()

    def _handle_label_click(self, href):
        """Route a click on the goal/donor lines: a transaction URL wins,
        anything else opens the donation dialog."""
        if href:
            open_browser(href)
            return
        if self._on_support:
            self._on_support()

    def refresh_theme(self):
        """Update status bar styling and labels to match active OS theme."""
        t = T()
        self._apply_bar_material(t)
        self.setMinimumHeight(30)
        if hasattr(self, "_goal_label"):
            self._style_bar_label(self._goal_label, weight=600)
            self._seal_ticker(self._goal_label)
        if hasattr(self, "_donor_label"):
            self._style_bar_label(self._donor_label, weight=500)
            self._seal_ticker(self._donor_label)
        if hasattr(self, "_status_label"):
            self._style_bar_label(self._status_label, weight=500)
            self._seal_ticker(self._status_label)
        if hasattr(self, "_credits_link"):
            self._style_link(self._credits_link, t, "left")
            self._relayout_links()
        if hasattr(self, "_support_btn"):
            self._style_link(self._support_btn, t, "right")
        self._clear_bar_plates()
        self._donor_lines = self._build_donor_lines()
        if hasattr(self, "_showing_goal") and not self._showing_goal and self._donor_lines:
            self._donor_label.setText(self._donor_lines[0])

    def _build_donor_lines(self):
        """Build recognized donor recognition lines adhering to community display rules.

        - Ko-Fi donations: 'bought x coffees on Ko-Fi' (or 1 coffee).
        - Buy Me a Coffee donations: 'bought x coffees' (or 1 coffee).
        - Other methods (Revolut, PayPal, Patreon, Bitcoin, etc.): local-currency amount by [Method].
        - Top supporters: Recognise higher than average or multiple donations with 'Top supporter ' prefix.
        - Ko-Fi only top supporters show total in coffees; multiple methods show the local-currency total.
        - Natural relative date phrasing (e.g. 'last year', 'in August', 'last month', 'in July', etc.).
        """
        from .donors import coffee_method, format_donation_when

        _symbol, code = fx.pair()
        valid = []
        for d in self.donations:
            amt = float(d.get("amount", 0))
            if amt <= 0:
                continue
            name = (d.get("name") or "").strip()
            if name.lower() in ("innioasis", "total"):
                continue
            valid.append(d)

        if not valid:
            return [tr("donate_thanks_status")]

        def is_generic_name(n: str) -> bool:
            # Anonymous rows are named people only by label, never by identity:
            # each one keeps its own line and amount below.
            return is_anonymous_donor(n)

        # Group by normalized donor name to compute totals and multiple donations (except generic names)
        groups = {}
        for d in valid:
            raw_name = (d.get("name") or "").strip()
            if is_generic_name(raw_name):
                groups.setdefault(f"__generic_{id(d)}__", []).append(d)
            else:
                groups.setdefault(raw_name.lower(), []).append(d)

        lines = []
        colour = theme_text_color().name()

        def make_anchor(name: str, url: str) -> str:
            short = name if len(name) <= 24 else name[:23].rstrip() + "…"
            if url:
                return f'<a href="{url}" style="color:{colour}; font-weight:bold;">{short}</a>'
            return f'<b style="color:{colour};">{short}</b>'

        def format_line(name: str, amt: float, methods: set[str], dt, url: str, is_top: bool,
                        has_time: bool = False) -> str:
            raw_name = (name or "").strip()
            if is_generic_name(raw_name):
                display_name = tr("donate_a_top_supporter") if is_top else tr("donate_a_supporter")
                top_prefix = ""
                anchor = make_anchor(display_name, url)
            else:
                anchor = make_anchor(raw_name, url)
                top_prefix = _supporter_prefix() if is_top else ""
            when = format_donation_when(dt, has_time=has_time)
            methods_norm = {m.lower() for m in methods}
            coffee_kinds = {coffee_method(m) for m in methods_norm}
            coffee_only = bool(coffee_kinds) and "" not in coffee_kinds

            if coffee_only:
                coffees = max(1, int(round(amt)))
                # A pure Ko-Fi gift names its platform; a Buy Me a Coffee gift
                # (or a donor who used both pages) keeps the plain wording.
                kofi = coffee_kinds == {"kofi"}
                if coffees == 1:
                    key = "donate_bought_coffee_kofi" if kofi else "donate_bought_coffee"
                else:
                    key = "donate_bought_coffees_kofi" if kofi else "donate_bought_coffees"
                tmpl = tr(key)
                raw_line = tmpl.format(top_prefix=top_prefix, anchor=anchor, count=coffees, when=when)
            else:
                amt_str = fx.money(amt, code)
                clean_method = ""
                if len(methods_norm) == 1:
                    m = next(iter(methods_norm))
                    if "revolut" in m:
                        clean_method = "Revolut"
                    elif "paypal" in m:
                        clean_method = "PayPal"
                    elif "bitcoin" in m or "btc" in m:
                        clean_method = "Bitcoin"
                    elif "patreon" in m:
                        clean_method = "Patreon"
                    elif "boosty" in m:
                        clean_method = "Boosty"
                    elif "honeygain" in m:
                        clean_method = "Honeygain"
                    else:
                        clean_method = list(methods)[0].strip()

                if clean_method and clean_method.lower() != "donation":
                    tmpl = tr("donate_donated_by_method").replace("$", "")
                    raw_line = tmpl.format(
                        top_prefix=top_prefix,
                        anchor=anchor,
                        amount=amt_str,
                        method=clean_method,
                        when=when,
                    )
                else:
                    tmpl = tr("donate_donated_amount").replace("$", "")
                    raw_line = tmpl.format(
                        top_prefix=top_prefix,
                        anchor=anchor,
                        amount=amt_str,
                        when=when,
                    )

            return raw_line.replace("  ", " ").replace(" .", ".").strip()

        # Add top supporter multi-donation aggregations first
        from datetime import datetime
        processed_multi = set()
        raw_items = []
        for norm_name, d_list in groups.items():
            if len(d_list) >= 2 and not norm_name.startswith("__generic_"):
                total_amt = sum(float(x.get("amount", 0)) for x in d_list)
                is_top = (total_amt >= 20.0) or (len(d_list) >= 2 and total_amt >= 10.0)
                methods = {str(x.get("method") or "Donation").strip() for x in d_list}
                # Latest date and best URL
                latest_d = max(d_list, key=lambda x: x.get("dt") or datetime.min)
                url = next((x.get("url") for x in d_list if x.get("url")), "")
                display_name = d_list[0].get("name") or norm_name.title()
                line = format_line(display_name, total_amt, methods, latest_d.get("dt"), url, is_top=is_top,
                                   has_time=latest_d.get("has_time", False))
                raw_items.append((line, total_amt, latest_d.get("dt"), is_top))
                processed_multi.add(norm_name)

        # Add single donations (and individual top supporters)
        for d in valid:
            raw_name = (d.get("name") or "").strip()
            norm_name = f"__generic_{id(d)}__" if is_generic_name(raw_name) else raw_name.lower()
            if norm_name in processed_multi:
                continue
            amt = float(d.get("amount", 0))
            is_top = (amt >= 20.0)
            methods = {str(d.get("method") or "Donation").strip()}
            display_name = raw_name
            line = format_line(display_name, amt, methods, d.get("dt"), d.get("url") or "", is_top=is_top,
                               has_time=d.get("has_time", False))
            raw_items.append((line, amt, d.get("dt"), is_top))

        lines = _order_bar_slots(self, raw_items)
        return lines or [tr("donate_thanks_status")]

    def _refresh_goal(self):
        stats = get_goal_stats(self.donations)
        line = format_goal_line(stats)
        _os_k, _ew, g_ok, _p_ok, _ = _resolve_bar_state(self)
        self._goal_suppressed = (not line or not g_ok)
        self._goal_reached = bool(stats.get("reached"))
        if self._goal_suppressed or self._goal_reached:
            self._showing_goal = False
            self._goal_label.setVisible(False)
            self._goal_bar.setVisible(False)
            self._donor_label.setVisible(True)
            if not self._donor_label.text():
                self._donor_label.setText(self._donor_lines[0] if self._donor_lines else tr("donate_thanks_status"))
        else:
            self._showing_goal = True
            self._goal_label.setVisible(True)
            # Covered costs leave no bar to fill: the line is the running-costs
            # ask, not a target that is already past.
            self._goal_bar.setVisible(not stats.get("appeal"))
            self._donor_label.setVisible(False)
            self._goal_label.setText(line)
            self._goal_bar.setValue(int(round(float(stats.get("shown_percent") or 0) * 10)))

    def _show_next_donor_line(self):
        if not self._donor_lines:
            self._donor_lines = self._build_donor_lines()
        pool = getattr(self, "_donor_pool", None) or self._donor_lines
        if not hasattr(self, "_donor_index"):
            self._donor_index = 0
        msg = pool[self._donor_index % len(pool)]
        self._donor_index += 1

        self._showing_goal = False
        self._goal_label.setVisible(False)
        self._goal_bar.setVisible(False)
        self._donor_label.setVisible(True)
        self._donor_label.setText(msg)
        self._donor_label.repaint()

    def _show_next_message_line(self):
        import random
        stats = get_goal_stats(self.donations)
        line = format_goal_line(stats)
        os_k, ew, g_ok, _p_ok, _ = _resolve_bar_state(self)
        goal_suppressed = (not line or not g_ok)
        goal_reached = bool(stats.get("reached"))

        if not g_ok and random.random() < 0.78:
            self._show_next_donor_line()
            return

        # Occasional 12% appeal
        if random.random() < (0.08 if (ew or os_k == 1) else 0.12):
            self._showing_goal = False
            self._goal_label.setVisible(False)
            self._goal_bar.setVisible(False)
            self._donor_label.setVisible(True)
            self._donor_label.setText(tr("donate_appeal_costs_for_year"))
            self._donor_label.repaint()
            return

        if not hasattr(self, "_msg_turn"):
            self._msg_turn = 0
        self._msg_turn += 1

        freq_hit = (ew == 1 or os_k == 1 or (self._msg_turn % 2 == 1))
        show_goal = (not goal_suppressed and not goal_reached and freq_hit)
        if show_goal:
            self._showing_goal = True
            self._goal_label.setVisible(True)
            # Covered costs leave no bar to fill: the line is the running-costs
            # ask, not a target that is already past.
            self._goal_bar.setVisible(not stats.get("appeal"))
            self._donor_label.setVisible(False)
            self._goal_label.setText(line)
            self._goal_bar.setValue(int(round(float(stats.get("shown_percent") or 0) * 10)))
            self._goal_label.repaint()
            self._goal_bar.repaint()
        else:
            appeals = get_appeal_messages()
            if not hasattr(self, "_appeal_index"):
                self._appeal_index = 0
            msg = appeals[self._appeal_index % len(appeals)]
            self._appeal_index += 1

            self._showing_goal = False
            self._goal_label.setVisible(False)
            self._goal_bar.setVisible(False)
            self._donor_label.setVisible(True)
            self._donor_label.setText(msg)
            self._donor_label.repaint()

    def _rotate(self):
        """Strictly alternate: Message line (Goal/Appeal) -> Donation line -> Message line -> Donation line."""
        if not getattr(self, "_donations_enabled", True) or self._status_container.isVisible():
            return
        if not decorative_updates_needed(self):
            return

        if not hasattr(self, "_cycle_turn"):
            self._cycle_turn = 0
        self._cycle_turn += 1

        if self._cycle_turn % 2 == 1:
            self._show_next_donor_line()
        else:
            self._show_next_message_line()

        self.repaint()
        if hasattr(self, "_goal_ticker"):
            self._goal_ticker.sync()
        if hasattr(self, "_donor_ticker"):
            self._donor_ticker.sync()

    def showMessage(self, message: str, timeout: int = 0):
        text = str(message or "").strip()
        if not text:
            self.clearMessage()
            return
        self._status_revert_timer.stop()
        self._current_message = text
        self._status_label.setText(text)
        self._donation_container.setVisible(False)
        self._status_container.setVisible(True)
        self.setVisible(True)
        self.repaint()
        self.messageChanged.emit(text)

        if self._donations_enabled:
            # Briefly hide donations to show status updates, shortly replaced with donations again
            revert_ms = int(timeout) if timeout and timeout > 0 else 4000
            self._status_revert_timer.start(revert_ms)
        else:
            # Donations disabled: persist status message if timeout == 0, or timer if timeout > 0
            if timeout and timeout > 0:
                self._status_revert_timer.start(int(timeout))

    def clearMessage(self):
        self._status_revert_timer.stop()
        self._current_message = ""
        self._status_label.setText("")
        self._status_container.setVisible(False)
        if self._donations_enabled:
            self._donation_container.setVisible(True)
            self.setVisible(True)
        else:
            self._donation_container.setVisible(False)
            self.setVisible(False)
        self.repaint()
        self.messageChanged.emit("")

    def currentMessage(self) -> str:
        return self._current_message

    def set_donations_enabled(self, enabled: bool):
        self._donations_enabled = bool(enabled)
        if self._current_message:
            self._donation_container.setVisible(False)
            self._status_container.setVisible(True)
            self.setVisible(True)
        else:
            self._status_container.setVisible(False)
            if self._donations_enabled:
                self._donation_container.setVisible(True)
                self.setVisible(True)
            else:
                self._donation_container.setVisible(False)
                self.setVisible(False)

    def _refresh_remote_donors(self, force: bool = False):
        # A hidden or minimised bar shows nobody the result of a fetch, so the
        # next visible tick does it instead. The first fetch asks for the feed
        # while the window is still being built, hence ``force``.
        if not force and not decorative_updates_needed(self):
            return
        fetch_remote_donors_async(self._refresh_bridge.updated.emit)

    def _apply_fresh_donations(self, fresh):
        if fresh:
            self.donations = fresh
            self._donor_lines = self._build_donor_lines()
            self._refresh_goal()
            if self._on_donations_updated:
                self._on_donations_updated(fresh)

    def retranslate(self):
        self._credits_link.setText(tr("nav_credits"))
        self._credits_link.setToolTip(tr("nav_credits"))
        self._support_btn.setText(tr("nav_donate"))
        self._support_btn.setToolTip(tr_brand("donate_title"))
        # New wording means new widths: re-balance before the display shifts.
        self._relayout_links()
        self._donor_lines = self._build_donor_lines()
        self._refresh_goal()
        if not self._showing_goal:
            self._donor_label.setText(self._donor_lines[0])


class DonationDialog(QDialog):
    def __init__(self, parent=None, context="general", model="Y1", software_name="",
                 donations=None, on_dont_ask_again=None, is_360p_rockbox=False):
        super().__init__(parent)
        apply_dialog_theme(self)
        self.context = context
        self.raw_model = model
        self.model = device_label_for_model(model)
        self.software_name = software_name
        self.donations = donations or []
        self.on_dont_ask_again = on_dont_ask_again
        self.is_360p_rockbox = is_360p_rockbox

        self.setWindowTitle(tr_brand("donate_title"))
        self.setModal(True)
        self.setMinimumWidth(580)

        self._pay_buttons = []
        self._build_ui()
        self.resize(710, 460)
        self._start_ticker()
        self._refresh_bridge = _DonationRefreshBridge(self)
        self._refresh_bridge.updated.connect(self._apply_fresh_donations)
        fetch_remote_donors_async(self._refresh_bridge.updated.emit)

    def sizeHint(self) -> QSize:
        return QSize(710, 460)

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title_color = "palette(window-text)"
        intro_color = "palette(text)"

        # 1. Goal bar / donor ticker card
        # Kept initialized as a hidden child widget for API / test compatibility,
        # but omitted from the visible dialog layout per user specification.
        self._alt_container = QWidget(self)
        self._alt_container.hide()
        alt_box = QVBoxLayout(self._alt_container)
        alt_box.setContentsMargins(0, 0, 0, 0)

        self._goal_view = QWidget(self._alt_container)
        goal_layout = QVBoxLayout(self._goal_view)
        goal_layout.setContentsMargins(0, 0, 0, 0)
        goal_layout.setSpacing(4)
        self._goal_label = QLabel()
        self._goal_label.setAlignment(Qt.AlignCenter)
        self._goal_label.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {title_color}; background: transparent; border: none;"
        )
        goal_layout.addWidget(self._goal_label)
        self._goal_bar = QProgressBar()
        self._goal_bar.setRange(0, 1000)
        self._goal_bar.setTextVisible(False)
        self._goal_bar.setFixedHeight(8)
        goal_layout.addWidget(self._goal_bar)
        self._goal_anim = QPropertyAnimation(self._goal_bar, b"value")
        self._goal_anim.setDuration(750)
        self._goal_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._donor_view = QWidget(self._alt_container)
        donor_layout = QVBoxLayout(self._donor_view)
        donor_layout.setContentsMargins(0, 0, 0, 0)
        self._donor_label = QLabel()
        self._donor_label.setAlignment(Qt.AlignCenter)
        self._donor_label.setWordWrap(True)
        self._donor_label.setStyleSheet(
            f"font-size: 12px; font-weight: 500; color: {title_color}; background: transparent; border: none;"
        )
        donor_layout.addWidget(self._donor_label)

        alt_box.addWidget(self._goal_view)
        alt_box.addWidget(self._donor_view)
        self._donor_view.setVisible(False)

        # The appeal's own headline and subtitle already name the install
        # ("X is installed on your Y."), so a separate success banner here
        # would say the same thing twice. The no-donations completion screen
        # carries that explanation instead (FlashPage.show_completion_note).
        if self.context == "install_success":
            if getattr(self, "is_360p_rockbox", False):
                self._theme_box = QWidget()
                self._theme_box.setStyleSheet(
                    f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border};"
                    f" border-radius: 8px; }}"
                )
                tb_layout = QVBoxLayout(self._theme_box)
                tb_layout.setContentsMargins(14, 10, 14, 10)
                tb_layout.setSpacing(4)
                self._theme_title = QLabel(f"<b>{tr('themepack_card_title')}</b>")
                self._theme_title.setStyleSheet(
                    "font-size: 13px; color: palette(window-text); border: none; background: transparent;"
                )
                tb_layout.addWidget(self._theme_title)
                self._theme_desc = QLabel(tr("themepack_card_desc"))
                self._theme_desc.setWordWrap(True)
                self._theme_desc.setStyleSheet(
                    f"font-size: 11px; color: {intro_color}; border: none; background: transparent;"
                )
                tb_layout.addWidget(self._theme_desc)

                tb_row = QHBoxLayout()
                tb_row.setContentsMargins(0, 4, 0, 0)
                self._theme_btn = QPushButton(tr("themepack_install_btn"))
                self._theme_btn.clicked.connect(self._open_theme_pack_flow)
                tb_row.addWidget(self._theme_btn)
                tb_row.addStretch()
                tb_layout.addLayout(tb_row)
                layout.addWidget(self._theme_box)

        # 2. Developer header
        header_row = QHBoxLayout()
        title = QLabel(
            "<h2 style='margin:0; font-size:18px; font-weight:800;'>"
            + tr("donate_headline") + "</h2>"
        )
        title.setTextFormat(Qt.RichText)
        title.setStyleSheet(
            "color: palette(window-text); background: transparent; border: none;"
        )
        subtitle = QLabel(
            "<p style='margin:0; font-size:11px; font-weight:600;'>"
            + tr("donate_subtitle") + "</p>"
        )
        subtitle.setTextFormat(Qt.RichText)
        subtitle.setStyleSheet(
            "color: palette(text); background: transparent; border: none;"
        )
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box)
        header_row.addStretch()
        layout.addLayout(header_row)

        # 3. Intro copy
        formatted = self.software_name or tr("donate_this_firmware")
        if self.context == "install_success":
            intro_text = tr_brand("donate_intro_success").format(
                software=formatted, model=self.model
            )
        else:
            intro_text = tr_brand("donate_intro_general")
        self._intro_label = QLabel(intro_text)
        self._intro_label.setTextFormat(Qt.RichText)
        self._intro_label.setWordWrap(True)
        self._intro_label.setStyleSheet(
            f"font-size: 12px; color: {intro_color}; line-height: 1.35;"
        )
        layout.addWidget(self._intro_label)

        if self.context == "install_success":
            from .config import install_power_on_steps
            steps = install_power_on_steps(self.model)
            self._steps_label = QLabel(steps)
            self._steps_label.setWordWrap(True)
            self._steps_label.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {title_color}; line-height: 1.35;"
            )
            layout.addWidget(self._steps_label)

        # 4. Opt-out
        if self.context == "install_success":
            self._dont_ask = QCheckBox(tr("donate_dont_ask"))
            layout.addWidget(self._dont_ask)

        # Two donation actions, side by side: the Ko-fi page, and Buy Me a
        # Coffee beside it. The Russian market gets Boosty alone, because the
        # international coffee pages cannot take a Russian card (see
        # fx.donation_route()).
        actions = QHBoxLayout()
        actions.setSpacing(8)
        route = fx.donation_route()
        if route == "boosty":
            links = [("donate_boosty", "boosty")]
        else:
            links = [("donate_buy_coffee", "kofi"), ("donate_buy_me_a_coffee", "bmc")]
        for key, link in links:
            btn = QPushButton(tr(key))
            btn.setCursor(Qt.ArrowCursor)
            btn.setStyleSheet("")
            btn.clicked.connect(lambda _=False, u=DONATION_LINKS[link]: open_browser(u))
            self._pay_buttons.append(btn)
            self._last_button = btn
            actions.addWidget(btn, 0, Qt.AlignLeft)
        actions.addStretch(1)
        layout.addLayout(actions)

        # 7. Bottom row with status message & Close button
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 4, 0, 0)
        self._status_label = QLabel()
        self._status_label.setStyleSheet(f"color: {t.ok_fg}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        self._status_label.hide()
        bottom_row.addWidget(self._status_label, 1)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.setMinimumWidth(80)
        self._close_btn.clicked.connect(self._on_close)
        bottom_row.addWidget(self._close_btn, 0, Qt.AlignRight)
        layout.addLayout(bottom_row)

        self._refresh_goal()

    def _add_pay_button(self, grid, r, c, label, color, hover, url, full_width=None, symbol=""):
        text = (f"{symbol}  {full_width or label}").strip()
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumHeight(32)
        # Native OS button widget — left to the host platform QStyle so
        # the OS renders its authentic native controls (Aqua on macOS, Fluent/standard on Windows/Linux)
        # without overriding custom QSS background or border styling.
        btn.clicked.connect(lambda _=False, u=url: open_browser(u))
        if hasattr(self, "_pay_buttons"):
            self._pay_buttons.append(btn)
        if grid is not None:
            grid.addWidget(btn, r, c)
        else:
            self._last_button = btn

    def _donor_lines(self):
        raw_items = []
        _symbol, code = fx.pair()
        for d in self.donations:
            if d.get("amount", 0) <= 0:
                continue
            name = d.get("name", tr("donate_supporter"))
            amt = float(d.get("amount", 0))
            amt_s = fx.money(amt, code)
            method = d.get("method", tr("donate_method_generic"))
            url = d.get("url", "")
            when = relative_date(d.get("dt"), has_time=d.get("has_time", False))
            anchor = (
                f'<a href="{url}" style="color:{theme_text_color().name()}; font-weight:bold;">{name}</a>'
                if url
                else name
            )
            line = tr("donate_ticker_fmt").replace("$", "").format(anchor=anchor, amount=amt_s, method=method, when=when)
            raw_items.append((line, amt, d.get("dt"), amt >= 20.0))
        lines = _order_bar_slots(self, raw_items)
        pool = getattr(self, "_donor_pool", None) or lines
        if not pool:
            return [tr("donate_thanks")]
        return list(pool)

    def _refresh_goal(self):
        stats = get_goal_stats(self.donations)
        line = format_goal_line(stats)
        _os_k, _ew, g_ok, _p_ok, _ = _resolve_bar_state(self)
        self._goal_suppressed = (not line or not g_ok)
        self._goal_reached = bool(stats.get("reached"))
        if self._goal_suppressed:
            self._showing_goal = False
            self._goal_target = 0
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
        elif self._goal_reached:
            self._showing_goal = False
            self._goal_label.setText(tr("donate_rely_on_you"))
            self._goal_target = 1000
            self._goal_bar.setValue(self._goal_target)
        else:
            self._goal_label.setText(line)
            self._goal_target = int(round(float(stats.get("shown_percent") or 0) * 10))
            # A covered month/week shows the running-costs sentence with no bar.
            self._goal_bar.setVisible(not stats.get("appeal"))
            self._goal_bar.setValue(self._goal_target)

    def _next_ticker_message(self) -> str:
        import random
        _os_k, _ew, g_ok, _p_ok, _ = _resolve_bar_state(self)
        # 1. "If everyone using this today donated, it would cover our costs for a year" (~12% chance)
        if random.random() < 0.12:
            return tr("donate_appeal_costs_for_year")

        has_real_donors = bool(self._ticker_lines and any(d.get("amount", 0) > 0 for d in self.donations))
        if not hasattr(self, "_ticker_turn"):
            self._ticker_turn = 0
        self._ticker_turn += 1

        if has_real_donors and (not g_ok or (self._ticker_turn % 2 == 1)):
            if self._ticker_lines:
                msg = self._ticker_lines[self._ticker_idx % len(self._ticker_lines)]
                self._ticker_idx += 1
                return msg

        appeals = get_appeal_messages()
        if not hasattr(self, "_appeal_index"):
            self._appeal_index = 0
        msg = appeals[self._appeal_index % len(appeals)]
        self._appeal_index += 1
        return msg

    def _trigger_goal_anim(self):
        self._goal_bar.setValue(0)
        self._goal_anim.stop()
        self._goal_anim.setStartValue(0)
        self._goal_anim.setEndValue(self._goal_target)
        self._goal_anim.start()

    def _start_ticker(self):
        self._ticker_lines = self._donor_lines()
        self._ticker_idx = 0
        self._since_goal = 0

        if getattr(self, "_goal_suppressed", False) or getattr(self, "_goal_reached", False):
            self._showing_goal = False
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
            self._donor_label.setText(self._next_ticker_message())
        else:
            self._showing_goal = True
            self._goal_view.setVisible(True)
            self._donor_view.setVisible(False)

        self._opacity = QGraphicsOpacityEffect(self._alt_container)
        self._alt_container.setGraphicsEffect(self._opacity)
        self._fade_out = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_out.setDuration(550)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.OutQuad)
        self._fade_in = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_in.setDuration(550)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.InQuad)
        self._fade_out.finished.connect(self._next_ticker_step)

        self._ticker_timer = QTimer(self)
        self._ticker_timer.timeout.connect(self._tick)
        self._ticker_timer.start(6500)

        if not (getattr(self, "_goal_suppressed", False) or getattr(self, "_goal_reached", False)):
            QTimer.singleShot(150, self._trigger_goal_anim)

    def _tick(self):
        """Start one fade step, unless nobody can see it right now."""
        if not decorative_updates_needed(self):
            return
        self._fade_out.start()

    def _next_ticker_step(self):
        if getattr(self, "_goal_suppressed", False) or getattr(self, "_goal_reached", False):
            self._showing_goal = False
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
            self._donor_label.setText(self._next_ticker_message())
            self._fade_in.start()
            return

        os_k, ew, _g_ok, _p_ok, _ = _resolve_bar_state(self)
        span_lim = 1 if (ew or os_k == 1) else 3
        if self._showing_goal:
            self._showing_goal = False
            self._since_goal = 1
            self._donor_label.setText(self._next_ticker_message())
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
        elif self._since_goal < span_lim:
            self._since_goal += 1
            self._donor_label.setText(self._next_ticker_message())
        else:
            self._showing_goal = True
            self._since_goal = 0
            self._donor_view.setVisible(False)
            self._goal_view.setVisible(True)
            self._trigger_goal_anim()
        self._fade_in.start()

    def _apply_fresh_donations(self, fresh):
        if fresh:
            self.donations = fresh
            self._refresh_goal()
            self._ticker_lines = self._donor_lines()

    def _toggle_crypto(self):
        """Crypto donations are not offered."""
        return

    def _open_theme_pack_flow(self):
        from .theme_pack import Rockbox360pSetupWizard
        raw_m = getattr(self, "raw_model", "Y1")
        dlg = Rockbox360pSetupWizard(parent=self, model=raw_m)
        dlg.installed_success.connect(self._on_theme_pack_installed)
        dlg.exec()

    def _on_theme_pack_installed(self):
        t = T()
        if hasattr(self, "_theme_title"):
            self._theme_title.setText(f"<span style='color:{t.ok_fg}; font-weight:700;'>{tr('themepack_installed_success')}</span>")
        if hasattr(self, "_theme_desc"):
            self._theme_desc.setText(tr("themepack_complete"))
        if hasattr(self, "_theme_btn"):
            self._theme_btn.setText(tr("themepack_installed_success"))
            self._theme_btn.setEnabled(False)
        self.adjustSize()


    def _copy_donation_value(self, label, value):
        msg = tr("donate_copied").format(label=label)
        self._donor_label.setText(msg)
        if hasattr(self, "_status_label"):
            self._status_label.setText(msg)
            self._status_label.show()
        try:
            QApplication.clipboard().setText(value)
        except Exception:
            open_browser(f"https://blockchair.com/search?q={value}")

    def _on_close(self):
        if getattr(self, "_dont_ask", None) and self._dont_ask.isChecked() and self.on_dont_ask_again:
            self.on_dont_ask_again()
        self.reject()


def show_donation_dialog(parent, context="general", model="Y1", software_name="",
                         donations=None, on_dont_ask_again=None, is_360p_rockbox=False):
    dialog = DonationDialog(
        parent=parent,
        context=context,
        model=model,
        software_name=software_name,
        donations=donations,
        on_dont_ask_again=on_dont_ask_again,
        is_360p_rockbox=is_360p_rockbox,
    )
    dialog.exec()
