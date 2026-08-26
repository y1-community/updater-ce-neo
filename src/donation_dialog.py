"""Donations modal — port of Updater CE's ``show_donation_dialog``.

Features carried over: monthly-goal progress bar alternating with a rotating
donor ticker, developer header, Ko-fi / PayPal / Revolut / Patreon buttons,
a free Honeygain option, crypto addresses with copy-to-clipboard, and an
optional "don't ask again" checkbox after a successful install.
"""

import logging
import random
import webbrowser

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .config import DONATION_CRYPTO, DONATION_LINKS, device_label_for_model
from .donors import fetch_remote_donors_async, get_monthly_goal_stats
from .i18n import tr

logger = logging.getLogger(__name__)


class _DonationRefreshBridge(QObject):
    """Marshal background donor refreshes onto the Qt/UI thread."""

    updated = Signal(object)


class DonationStatusBar(QStatusBar):
    """Compact, always-visible version of the Support dialog's goal display."""

    def __init__(self, parent=None, donations=None, on_support=None,
                 on_donations_updated=None):
        super().__init__(parent)
        self.donations = donations or []
        self._on_donations_updated = on_donations_updated
        self._remote_refresh_interval_ms = 5 * 60 * 1000
        self._showing_goal = True
        self._is_dark = self._detect_dark()
        self.setObjectName("donation_status_bar")
        self.setSizeGripEnabled(False)
        self.setFixedHeight(44)
        self._build_ui(on_support)
        self._refresh_goal()
        self._donor_lines = self._build_donor_lines()
        self._rotation_timer = QTimer(self)
        self._rotation_timer.timeout.connect(self._rotate)
        self._rotation_timer.start(6500)

        self._refresh_bridge = _DonationRefreshBridge(self)
        self._refresh_bridge.updated.connect(self._apply_fresh_donations)
        self._remote_refresh_timer = QTimer(self)
        self._remote_refresh_timer.setInterval(self._remote_refresh_interval_ms)
        self._remote_refresh_timer.timeout.connect(self._refresh_remote_donors)
        self._remote_refresh_timer.start()
        # Fetch immediately, then keep retrying quietly. This preserves the
        # bundled donors.csv while offline and picks up the live file when
        # connectivity becomes available later in the session.
        self._refresh_remote_donors()

    def _detect_dark(self):
        try:
            return QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        except Exception:
            return False

    def _c(self, light, dark):
        return dark if self._is_dark else light

    def _build_ui(self, on_support):
        bg = self._c("#ffffff", "#1f2937")
        border = self._c("#e5e7eb", "#374151")
        fg = self._c("#111827", "#f9fafb")
        self.setStyleSheet(
            f"QStatusBar#donation_status_bar {{ background-color: {bg};"
            f" border-top: 1px solid {border}; color: {fg}; }}"
            f"QStatusBar#donation_status_bar QLabel {{ color: {fg}; background: transparent; border: none; }}"
            f"QStatusBar#donation_status_bar QProgressBar {{ background-color: {self._c('#e5e7eb', '#374151')};"
            f" border: 1px solid {self._c('#d1d5db', '#4b5563')}; border-radius: 4px; }}"
            "QStatusBar#donation_status_bar QProgressBar::chunk { background-color: #10b981; border-radius: 3px; }"
            f"QStatusBar#donation_status_bar QPushButton {{ background-color: #3b5bdb; color: white;"
            f" border: none; border-radius: 6px; padding: 5px 10px; font-size: 11px; font-weight: bold; }}"
            f"QStatusBar#donation_status_bar QPushButton:hover {{ background-color: #3451c7; }}"
        )

        content = QWidget(self)
        row = QHBoxLayout(content)
        row.setContentsMargins(10, 0, 6, 0)
        row.setSpacing(8)

        self._goal_label = QLabel()
        self._goal_label.setMinimumWidth(260)
        self._goal_label.setStyleSheet(f"font-size: 10px; color: {fg};")
        row.addWidget(self._goal_label, 1)

        self._donor_label = QLabel()
        self._donor_label.setMinimumWidth(260)
        self._donor_label.setTextFormat(Qt.RichText)
        self._donor_label.setStyleSheet(f"font-size: 10px; color: {fg};")
        self._donor_label.setVisible(False)
        row.addWidget(self._donor_label, 1)

        self._goal_bar = QProgressBar()
        self._goal_bar.setRange(0, 1000)
        self._goal_bar.setTextVisible(False)
        self._goal_bar.setFixedSize(150, 8)
        row.addWidget(self._goal_bar, 0, Qt.AlignVCenter)

        self._support_btn = QPushButton(tr("nav_donate"))
        self._support_btn.setCursor(Qt.PointingHandCursor)
        self._support_btn.setToolTip(tr("donate_title"))
        if on_support:
            self._support_btn.clicked.connect(on_support)
        row.addWidget(self._support_btn)
        self.addWidget(content, 1)

    def _build_donor_lines(self):
        lines = []
        for donation in self.donations:
            if donation.get("amount", 0) <= 0:
                continue
            name = donation.get("name", tr("donate_supporter"))
            amount = float(donation.get("amount", 0))
            amount_text = str(int(amount)) if amount.is_integer() else f"{amount:.2f}"
            method = donation.get("method", tr("donate_method_generic"))
            url = donation.get("url", "")
            anchor = (
                f'<a href="{url}" style="color:{self._c("#111827", "#f9fafb")}; font-weight:bold;">{name}</a>'
                if url else name
            )
            lines.append(tr("donate_ticker_fmt").format(
                anchor=anchor, amount=amount_text, method=method
            ))
        return lines or [tr("donate_thanks")]

    def _refresh_goal(self):
        raised, _remaining, percent, target = get_monthly_goal_stats(self.donations)
        raised = float(raised)
        self._goal_reached = raised >= float(target)
        raised_text = str(int(raised)) if raised.is_integer() else f"{raised:.2f}"
        self._goal_label.setText(
            tr("donate_goal_fmt").format(raised=raised_text, target=target)
        )
        self._goal_bar.setValue(int(round(percent * 10)))
        if self._goal_reached:
            # Monthly goal met — retire the goal panel from the rotation.
            self._showing_goal = False
            self._goal_label.setVisible(False)
            self._goal_bar.setVisible(False)
            self._donor_label.setVisible(True)

    def _rotate(self):
        if getattr(self, "_goal_reached", False):
            # Goal met: keep rotating donor shout-outs only.
            self._donor_label.setText(self._donor_lines[0])
            self._donor_lines = self._donor_lines[1:] + self._donor_lines[:1]
            return
        self._showing_goal = not self._showing_goal
        self._goal_label.setVisible(self._showing_goal)
        self._goal_bar.setVisible(self._showing_goal)
        self._donor_label.setVisible(not self._showing_goal)
        if not self._showing_goal:
            self._donor_label.setText(self._donor_lines[0])
            self._donor_lines = self._donor_lines[1:] + self._donor_lines[:1]

    def _refresh_remote_donors(self):
        fetch_remote_donors_async(self._refresh_bridge.updated.emit)

    def _apply_fresh_donations(self, fresh):
        if fresh:
            self.donations = fresh
            self._donor_lines = self._build_donor_lines()
            self._refresh_goal()
            if self._on_donations_updated:
                self._on_donations_updated(fresh)

    def retranslate(self):
        self._support_btn.setText(tr("nav_donate"))
        self._support_btn.setToolTip(tr("donate_title"))
        self._donor_lines = self._build_donor_lines()
        self._refresh_goal()
        if not self._showing_goal:
            self._donor_label.setText(self._donor_lines[0])


class DonationDialog(QDialog):
    """The support modal. ``context`` is ``"install_success"`` (opt-out shown)
    or ``"general"`` (plain support call to action)."""

    def __init__(self, parent=None, context="general", model="Y1", software_name="",
                 donations=None, on_dont_ask_again=None):
        super().__init__(parent)
        self.context = context
        self.model = device_label_for_model(model)
        self.software_name = software_name
        self.donations = donations or []
        self.on_dont_ask_again = on_dont_ask_again

        self.setWindowTitle(tr("donate_title"))
        self.setModal(True)
        self.setMinimumWidth(540)
        self.resize(560, 520)

        self._is_dark = self._detect_dark()
        self._apply_theme()
        self._build_ui()
        self._start_ticker()
        # Live refresh of donors while the dialog is open.
        fetch_remote_donors_async(self._apply_fresh_donations)

    # -- helpers ------------------------------------------------------------
    def _detect_dark(self):
        try:
            palette = QApplication.palette()
            bg = palette.color(QPalette.ColorRole.Window)
            return bg.lightness() < 128
        except Exception:
            return False

    def _c(self, light, dark):
        return dark if self._is_dark else light

    def _apply_theme(self):
        bg = self._c("#ffffff", "#1f2937")
        fg = self._c("#111827", "#f9fafb")
        self.setStyleSheet(
            f"QDialog, QWidget {{ background-color: {bg}; color: {fg}; }}"
            f"QLabel, QCheckBox {{ color: {fg}; }}"
            f"QLineEdit {{ background-color: {self._c('#ffffff', '#111827')}; color: {fg}; border: 1px solid {self._c('#d1d5db', '#4b5563')}; }}"
        )

    # -- UI -----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(8)

        title_color = self._c("#111827", "#f9fafb")
        intro_color = self._c("#374151", "#d1d5db")

        # 1. Goal bar / donor ticker card -----------------------------------
        self._alt_container = QWidget()
        self._alt_container.setFixedHeight(50)
        self._alt_container.setStyleSheet(
            f"QWidget {{ background-color: {self._c('#f9fafb', '#111827')};"
            f" border: 1px solid {self._c('#e5e7eb', '#374151')}; border-radius: 8px; }}"
        )
        alt_box = QVBoxLayout(self._alt_container)
        alt_box.setContentsMargins(12, 6, 12, 6)
        alt_box.setAlignment(Qt.AlignCenter)

        # Goal bar view
        self._goal_view = QWidget()
        goal_layout = QVBoxLayout(self._goal_view)
        goal_layout.setContentsMargins(0, 0, 0, 0)
        goal_layout.setSpacing(4)
        self._goal_label = QLabel()
        self._goal_label.setAlignment(Qt.AlignCenter)
        self._goal_label.setStyleSheet(f"font-size: 11px; color: {title_color}; background: transparent; border: none;")
        goal_layout.addWidget(self._goal_label)
        self._goal_bar = QProgressBar()
        self._goal_bar.setRange(0, 1000)
        self._goal_bar.setTextVisible(False)
        self._goal_bar.setFixedHeight(8)
        self._goal_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {self._c('#e5e7eb', '#374151')};"
            f" border: 1px solid {self._c('#d1d5db', '#4b5563')}; border-radius: 4px; }}"
            "QProgressBar::chunk {{ background-color: #10b981; border-radius: 3px; }}"
        )
        goal_layout.addWidget(self._goal_bar)
        self._goal_anim = QPropertyAnimation(self._goal_bar, b"value")
        self._goal_anim.setDuration(750)
        self._goal_anim.setEasingCurve(QEasingCurve.OutCubic)

        # Donor ticker view
        self._donor_view = QWidget()
        donor_layout = QVBoxLayout(self._donor_view)
        donor_layout.setContentsMargins(0, 0, 0, 0)
        self._donor_label = QLabel()
        self._donor_label.setAlignment(Qt.AlignCenter)
        self._donor_label.setWordWrap(True)
        self._donor_label.setStyleSheet(f"font-size: 11px; color: {title_color}; background: transparent; border: none;")
        donor_layout.addWidget(self._donor_label)

        alt_box.addWidget(self._goal_view)
        alt_box.addWidget(self._donor_view)
        self._donor_view.setVisible(False)
        layout.addWidget(self._alt_container)

        # 2. Developer header -------------------------------------------------
        header_row = QHBoxLayout()
        title = QLabel(f"<h2 style='margin:0; font-size:20px; font-weight:800; color:{title_color};'>"
                       + tr("donate_headline") + "</h2>")
        title.setTextFormat(Qt.RichText)
        subtitle = QLabel(f"<p style='margin:0; font-size:11px; font-weight:600; color:{intro_color};'>"
                          + tr("donate_subtitle") + "</p>")
        subtitle.setTextFormat(Qt.RichText)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box)
        header_row.addStretch()
        layout.addLayout(header_row)

        # 3. Intro copy (Ryan's message — translated) -------------------------
        formatted = self.software_name or tr("donate_this_firmware")
        if self.context == "install_success":
            intro_text = tr("donate_intro_success").format(
                software=formatted, model=self.model
            )
        else:
            intro_text = tr("donate_intro_general")
        self._intro_label = QLabel(intro_text)
        self._intro_label.setTextFormat(Qt.RichText)
        self._intro_label.setWordWrap(True)
        self._intro_label.setStyleSheet(f"font-size: 12px; color: {intro_color}; line-height: 1.35;")
        layout.addWidget(self._intro_label)

        # 4. Opt-out (install success only) ------------------------------------
        if self.context == "install_success":
            self._dont_ask = QCheckBox(tr("donate_dont_ask"))
            layout.addWidget(self._dont_ask)

        # 5. Payment grid -------------------------------------------------------
        grid = QGridLayout()
        grid.setSpacing(8)
        self._add_pay_button(grid, 0, 0, tr("donate_kofi"), "#ff5e5b", "#e04b48", DONATION_LINKS["kofi"])
        self._add_pay_button(grid, 0, 1, tr("donate_paypal"), "#0070ba", "#005ea6", DONATION_LINKS["paypal"])
        self._add_pay_button(grid, 1, 0, tr("donate_revolut"), "#5850ec", "#4338ca", DONATION_LINKS["revolut"])
        self._add_pay_button(grid, 1, 1, tr("donate_patreon"), "#e0533c", "#c9442e", DONATION_LINKS["patreon"])
        layout.addLayout(grid)

        self._add_pay_button(None, None, None, "", "#10b981", "#059669",
                             DONATION_LINKS["honeygain"], full_width=tr("donate_honeygain"))
        layout.addWidget(self._last_button)  # type: ignore[attr-defined]

        # 6. Crypto --------------------------------------------------------------
        crypto_bg = self._c("#f3f4f6", "#374151")
        crypto_fg = self._c("#374151", "#f9fafb")
        crypto_border = self._c("#d1d5db", "#4b5563")
        self._crypto_toggle = QPushButton(tr("donate_crypto_toggle"))
        self._crypto_toggle.setCursor(Qt.PointingHandCursor)
        self._crypto_toggle.setStyleSheet(
            f"QPushButton {{ background-color: {crypto_bg}; color: {crypto_fg}; font-weight:600;"
            f" font-size:12px; padding:7px; border-radius:8px; border:1px solid {crypto_border}; }}"
            f"QPushButton:hover {{ background-color: {self._c('#e5e7eb', '#4b5563')}; }}"
        )
        self._crypto_toggle.clicked.connect(self._toggle_crypto)
        layout.addWidget(self._crypto_toggle)

        self._crypto_box = QWidget()
        crypto_layout = QVBoxLayout(self._crypto_box)
        crypto_layout.setContentsMargins(0, 2, 0, 2)
        crypto_grid = QGridLayout()
        crypto_grid.setSpacing(6)
        row = 0
        col = 0
        colors = {"Bitcoin": "#f7931a", "Ethereum": "#627eea", "SHIBA": "#e04130"}
        for label, address in DONATION_CRYPTO.items():
            color = next((c for k, c in colors.items() if k.lower() in label.lower()), "#6b7280")
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white; font-weight: bold;"
                f" font-size: 12px; padding: 8px; border-radius: 6px; border: none; }}"
            )
            btn.clicked.connect(lambda _=False, l=label, a=address: self._copy_donation_value(l, a))
            crypto_grid.addWidget(btn, row, col)
            col += 1
            if col > 1:
                col = 0
                row += 1
        crypto_layout.addLayout(crypto_grid)
        self._crypto_box.setVisible(False)
        layout.addWidget(self._crypto_box)

        # 7. Close (custom button so the label follows our translator; Qt's
        # built-in Close button ignores it) ------------------------------------
        close_bg = self._c("#e5e7eb", "#374151")
        close_fg = self._c("#1f2937", "#f9fafb")
        self._close_btn = QPushButton(tr("close"))
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setStyleSheet(
            f"QPushButton {{ background-color: {close_bg}; color: {close_fg}; border-radius:6px;"
            f" padding:6px 16px; border:none; font-weight:bold; }}"
        )
        self._close_btn.clicked.connect(self._on_close)
        layout.addWidget(self._close_btn, 0, Qt.AlignRight)

        self._refresh_goal()

    def _add_pay_button(self, grid, r, c, label, color, hover, url, full_width=None):
        text = full_width or label
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: white; font-weight: bold;"
            f" font-size: {'13px' if full_width else '14px'}; padding: 10px; border-radius: 8px; border: none; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
        )
        btn.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
        if grid is not None:
            grid.addWidget(btn, r, c)
        else:
            self._last_button = btn

    # -- ticker + goal -------------------------------------------------------
    def _donor_lines(self):
        lines = []
        for d in self.donations:
            if d.get("amount", 0) <= 0:
                continue
            name = d.get("name", tr("donate_supporter"))
            amt = float(d.get("amount", 0))
            amt_s = f"{int(amt)}" if amt.is_integer() else f"{amt:.2f}"
            method = d.get("method", tr("donate_method_generic"))
            url = d.get("url", "")
            anchor = f'<a href="{url}" style="color:{self._c("#111827", "#f9fafb")}; font-weight:bold;">{name}</a>' if url else name
            lines.append(tr("donate_ticker_fmt").format(anchor=anchor, amount=amt_s, method=method))
        if not lines:
            lines.append(tr("donate_thanks"))
        random.shuffle(lines)
        return lines

    def _refresh_goal(self):
        raised, _, pct, target = get_monthly_goal_stats(self.donations)
        raised = float(raised)
        r_s = f"{int(raised)}" if raised.is_integer() else f"{raised:.2f}"
        self._goal_label.setText(tr("donate_goal_fmt").format(raised=r_s, target=target))
        self._goal_target = int(round(pct * 10))
        self._goal_reached = raised >= float(target)

    def _trigger_goal_anim(self):
        self._goal_bar.setValue(0)
        self._goal_anim.stop()
        self._goal_anim.setStartValue(0)
        self._goal_anim.setEndValue(self._goal_target)
        self._goal_anim.start()

    def _start_ticker(self):
        self._ticker_lines = self._donor_lines()
        self._ticker_idx = 0
        self._showing_goal = True
        self._since_goal = 0
        if getattr(self, "_goal_reached", False):
            # Monthly goal met — start straight on the donor ticker and never
            # rotate back to the goal line.
            self._showing_goal = False
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)

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
        self._ticker_timer.timeout.connect(self._fade_out.start)
        self._ticker_timer.start(6500)

        QTimer.singleShot(150, self._trigger_goal_anim)

    def _next_ticker_step(self):
        if self._showing_goal:
            self._showing_goal = False
            self._since_goal = 1
            self._donor_label.setText(self._ticker_lines[self._ticker_idx % len(self._ticker_lines)])
            self._ticker_idx += 1
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
        elif self._since_goal < 3:
            self._since_goal += 1
            self._donor_label.setText(self._ticker_lines[self._ticker_idx % len(self._ticker_lines)])
            self._ticker_idx += 1
        elif getattr(self, "_goal_reached", False):
            # Goal met: cycle donors indefinitely instead of returning to the
            # goal line.
            self._since_goal = 0
            self._donor_label.setText(self._ticker_lines[self._ticker_idx % len(self._ticker_lines)])
            self._ticker_idx += 1
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

    # -- interactions ---------------------------------------------------------
    def _toggle_crypto(self):
        visible = not self._crypto_box.isVisible()
        self._crypto_box.setVisible(visible)
        self._crypto_toggle.setText(tr("donate_crypto_hide") if visible else tr("donate_crypto_toggle"))

    def _copy_donation_value(self, label, value):
        try:
            QApplication.clipboard().setText(value)
            self._donor_label.setText(tr("donate_copied").format(label=label))
        except Exception:
            webbrowser.open(f"https://blockchair.com/search?q={value}")

    def _on_close(self):
        if getattr(self, "_dont_ask", None) and self._dont_ask.isChecked() and self.on_dont_ask_again:
            self.on_dont_ask_again()
        self.reject()


def show_donation_dialog(parent, context="general", model="Y1", software_name="",
                         donations=None, on_dont_ask_again=None):
    dialog = DonationDialog(
        parent=parent,
        context=context,
        model=model,
        software_name=software_name,
        donations=donations,
        on_dont_ask_again=on_dont_ask_again,
    )
    dialog.exec()
