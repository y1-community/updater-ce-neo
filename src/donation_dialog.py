"""Donations modal — port of Updater CE's ``show_donation_dialog``."""

import logging
import random
import sys

from .browser import open_browser

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, QTimer, Signal
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

from .config import DONATION_CRYPTO, DONATION_LINKS, device_label_for_model, install_power_on_steps
from .donors import fetch_remote_donors_async, get_monthly_goal_stats, relative_date
from .i18n import tr, tr_brand
from .ui.dark import T, is_dark

logger = logging.getLogger(__name__)


class _DonationRefreshBridge(QObject):
    updated = Signal(object)


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


class DonationStatusBar(QStatusBar):
    """Compact, always-visible version of the Support dialog's goal display."""

    def __init__(self, parent=None, donations=None, on_support=None,
                 on_donations_updated=None):
        super().__init__(parent)
        self.donations = donations or []
        self._on_support = on_support
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
        self.setFixedHeight(44)
        self._build_ui(on_support)
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
        self._refresh_remote_donors()

    def _build_ui(self, on_support):
        t = T()
        is_mac = sys.platform == "darwin"
        status_bg = "transparent" if is_mac else t.bg_card
        status_border = "none" if is_mac else f"1px solid {t.border}"
        self.setStyleSheet(
            f"QStatusBar#donation_status_bar {{ background-color: {status_bg};"
            f" border-top: {status_border}; color: {t.fg}; }}"
            f"QStatusBar#donation_status_bar QLabel {{ color: {t.fg};"
            f" background: transparent; border: none; }}"
        )

        # Donation container: goal / donor ticker and Support button
        self._donation_container = QWidget(self)
        row = QHBoxLayout(self._donation_container)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(10)

        self._goal_label = _LineLabel()
        self._goal_label.setMinimumWidth(260)
        self._goal_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {t.fg}; border: none; background: transparent;")
        row.addWidget(self._goal_label, 1)

        self._donor_label = _LineLabel()
        self._donor_label.setMinimumWidth(260)
        self._donor_label.setTextFormat(Qt.RichText)
        self._donor_label.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {t.fg}; border: none; background: transparent;")
        self._donor_label.setVisible(False)
        row.addWidget(self._donor_label, 1)

        self._goal_bar = QProgressBar()
        self._goal_bar.setRange(0, 1000)
        self._goal_bar.setTextVisible(False)
        self._goal_bar.setFixedSize(160, 10)
        row.addWidget(self._goal_bar, 0, Qt.AlignVCenter)

        self._support_btn = QPushButton(tr("nav_donate"))
        self._support_btn.setToolTip(tr_brand("donate_title"))
        if on_support:
            self._support_btn.clicked.connect(on_support)
        row.addWidget(self._support_btn)
        self.addWidget(self._donation_container, 1)

        # Status container: clean status update message without donation collision
        self._status_container = QWidget(self)
        status_row = QHBoxLayout(self._status_container)
        status_row.setContentsMargins(12, 0, 8, 0)
        status_row.setSpacing(8)
        self._status_label = QLabel()
        self._status_label.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {t.fg}; border: none; background: transparent;")
        status_row.addWidget(self._status_label, 1)
        self._status_container.setVisible(False)
        self.addWidget(self._status_container, 1)

        # The goal and donor lines are clickable: any plain word opens the
        # donation dialog, while a donor name that carries a transaction URL
        # opens that URL instead.
        for label in (self._goal_label, self._donor_label):
            label.setCursor(Qt.PointingHandCursor)
            label.clicked.connect(self._handle_label_click)

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
        is_mac = sys.platform == "darwin"
        status_bg = "transparent" if is_mac else t.bg_card
        status_border = "none" if is_mac else f"1px solid {t.border}"
        self.setStyleSheet(
            f"QStatusBar#donation_status_bar {{ background-color: {status_bg};"
            f" border-top: {status_border}; color: {t.fg}; }}"
            f"QStatusBar#donation_status_bar QLabel {{ color: {t.fg};"
            f" background: transparent; border: none; }}"
        )
        if hasattr(self, "_goal_label"):
            self._goal_label.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {t.fg}; border: none; background: transparent;"
            )
        if hasattr(self, "_donor_label"):
            self._donor_label.setStyleSheet(
                f"font-size: 12px; font-weight: 500; color: {t.fg}; border: none; background: transparent;"
            )
        if hasattr(self, "_status_label"):
            self._status_label.setStyleSheet(
                f"font-size: 12px; font-weight: 500; color: {t.fg}; border: none; background: transparent;"
            )

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
            when = relative_date(donation.get("dt"))
            t = T()
            anchor = (
                f'<a href="{url}" style="color:{t.fg}; font-weight:bold;">{name}</a>'
                if url else name
            )
            lines.append(tr("donate_ticker_fmt").format(
                anchor=anchor, amount=amount_text, method=method, when=when
            ))
        return lines or [tr("donate_thanks")]

    def _refresh_goal(self):
        raised, _remaining, percent, target = get_monthly_goal_stats(self.donations)
        raised = float(raised)
        self._goal_reached = raised >= float(target)
        raised_text = str(int(raised)) if raised.is_integer() else f"{raised:.2f}"
        if self._goal_reached:
            self._showing_goal = False
            self._goal_label.setVisible(False)
            self._goal_bar.setVisible(False)
            self._donor_label.setVisible(True)
        else:
            self._showing_goal = True
            self._goal_label.setVisible(True)
            self._goal_bar.setVisible(True)
            self._donor_label.setVisible(False)
            self._goal_label.setText(
                tr("donate_goal_fmt").format(raised=raised_text, target=target)
            )
            self._goal_bar.setValue(int(round(percent * 10)))

    def _rotate(self):
        if not getattr(self, "_donations_enabled", True) or self._status_container.isVisible():
            return
        if getattr(self, "_goal_reached", False):
            self._showing_goal = False
            self._goal_label.setVisible(False)
            self._goal_bar.setVisible(False)
            self._donor_label.setVisible(True)
            if self._donor_lines:
                self._donor_label.setText(self._donor_lines[0])
                self._donor_lines = self._donor_lines[1:] + self._donor_lines[:1]
            return
        self._showing_goal = not self._showing_goal
        self._goal_label.setVisible(self._showing_goal)
        self._goal_bar.setVisible(self._showing_goal)
        self._donor_label.setVisible(not self._showing_goal)
        if not self._showing_goal and self._donor_lines:
            self._donor_label.setText(self._donor_lines[0])
            self._donor_lines = self._donor_lines[1:] + self._donor_lines[:1]

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
        self._support_btn.setToolTip(tr_brand("donate_title"))
        self._donor_lines = self._build_donor_lines()
        self._refresh_goal()
        if not self._showing_goal:
            self._donor_label.setText(self._donor_lines[0])


class DonationDialog(QDialog):
    def __init__(self, parent=None, context="general", model="Y1", software_name="",
                 donations=None, on_dont_ask_again=None, is_360p_rockbox=False):
        super().__init__(parent)
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

        self._build_ui()
        self.adjustSize()
        self._start_ticker()
        self._refresh_bridge = _DonationRefreshBridge(self)
        self._refresh_bridge.updated.connect(self._apply_fresh_donations)
        fetch_remote_donors_async(self._refresh_bridge.updated.emit)

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title_color = t.fg
        intro_color = t.fg_dim

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

        # Installation Complete banner (prominently shown first after firmware install)
        if self.context == "install_success":
            steps = install_power_on_steps(self.model)
            formatted = self.software_name or tr("donate_this_firmware")
            if not self.software_name or self.software_name in ("firmware", "this firmware", "local", "browse"):
                header_text = f"We've installed the software on your <b>{self.model}</b>."
            else:
                header_text = f"We've installed <b>{formatted}</b> on your <b>{self.model}</b>."

            success_box = QWidget()
            success_box.setStyleSheet(
                f"QWidget {{ background-color: {t.bg_elev}; border: 1px solid {t.ok_fg};"
                f" border-radius: 8px; }}"
            )
            s_layout = QVBoxLayout(success_box)
            s_layout.setContentsMargins(14, 10, 14, 10)
            s_layout.setSpacing(4)
            s_title = QLabel(f"<span style='color:{t.ok_fg}; font-weight:700; font-size:13px;'>✓ {header_text}</span>")
            s_title.setTextFormat(Qt.RichText)
            s_title.setStyleSheet("border: none; background: transparent;")
            s_layout.addWidget(s_title)
            s_steps = QLabel(f"<span style='color:{title_color}; font-size:12px; font-weight:600;'>{steps}</span>")
            s_steps.setTextFormat(Qt.RichText)
            s_steps.setWordWrap(True)
            s_steps.setStyleSheet("border: none; background: transparent;")
            s_layout.addWidget(s_steps)
            layout.addWidget(success_box)

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
                self._theme_title.setStyleSheet(f"font-size: 13px; color: {t.accent}; border: none; background: transparent;")
                tb_layout.addWidget(self._theme_title)
                self._theme_desc = QLabel(tr("themepack_card_desc"))
                self._theme_desc.setWordWrap(True)
                self._theme_desc.setStyleSheet(f"font-size: 11px; color: {intro_color}; border: none; background: transparent;")
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
            f"<h2 style='margin:0; font-size:18px; font-weight:800; color:{title_color};'>"
            + tr("donate_headline") + "</h2>"
        )
        title.setTextFormat(Qt.RichText)
        subtitle = QLabel(
            f"<p style='margin:0; font-size:11px; font-weight:600; color:{intro_color};'>"
            + tr("donate_subtitle") + "</p>"
        )
        subtitle.setTextFormat(Qt.RichText)
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

        # 4. Opt-out
        if self.context == "install_success":
            self._dont_ask = QCheckBox(tr("donate_dont_ask"))
            layout.addWidget(self._dont_ask)

        # 5. Payment grid (2x2)
        grid = QGridLayout()
        grid.setSpacing(8)
        self._add_pay_button(grid, 0, 0, tr("donate_kofi"), "#ff5e5b", "#e04b48", DONATION_LINKS["kofi"])
        self._add_pay_button(grid, 0, 1, tr("donate_paypal"), "#0070ba", "#005ea6", DONATION_LINKS["paypal"])
        self._add_pay_button(grid, 1, 0, tr("donate_revolut"), "#5850ec", "#4338ca", DONATION_LINKS["revolut"])
        self._add_pay_button(grid, 1, 1, tr("donate_patreon"), "#e0533c", "#c9442e", DONATION_LINKS["patreon"])
        layout.addLayout(grid)

        # Space-saving text links row for Honeygain and Crypto
        links_box = QWidget()
        links_row = QHBoxLayout(links_box)
        links_row.setContentsMargins(0, 4, 0, 2)
        links_row.setSpacing(10)
        links_row.setAlignment(Qt.AlignCenter)

        honeygain_url = DONATION_LINKS.get("honeygain", "https://r.honeygain.me/RYANB0FEF2")
        self._last_button = QPushButton(f"⚡ {tr('donate_honeygain')}")
        self._last_button.setCursor(Qt.PointingHandCursor)
        self._last_button.setToolTip(honeygain_url)
        self._last_button.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {t.ok_fg};"
            f" font-size: 12px; font-weight: 600; text-decoration: underline; padding: 4px 6px; }}"
            f"QPushButton:hover {{ color: #059669; }}"
        )
        self._last_button.clicked.connect(lambda _=False, u=honeygain_url: open_browser(u))
        links_row.addWidget(self._last_button)

        dot = QLabel("·")
        dot.setStyleSheet(f"color: {t.border_strong}; font-size: 14px; font-weight: bold; background: transparent;")
        links_row.addWidget(dot)

        self._crypto_toggle = QPushButton(f"🪙 {tr('donate_crypto_toggle')}")
        self._crypto_toggle.setCursor(Qt.PointingHandCursor)
        self._crypto_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {t.accent};"
            f" font-size: 12px; font-weight: 600; text-decoration: underline; padding: 4px 6px; }}"
            f"QPushButton:hover {{ color: {t.accent_hover}; }}"
        )
        self._crypto_toggle.clicked.connect(self._toggle_crypto)
        links_row.addWidget(self._crypto_toggle)
        layout.addWidget(links_box)

        # 6. Crypto options box (compact, space-saving)
        self._crypto_box = QWidget()
        crypto_layout = QVBoxLayout(self._crypto_box)
        crypto_layout.setContentsMargins(8, 6, 8, 6)
        crypto_layout.setSpacing(4)
        self._crypto_box.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_elev}; border: 1px solid {t.border};"
            f" border-radius: 8px; }}"
        )

        colors = {"Bitcoin": "#d97706", "Ethereum": "#4f46e5", "SHIBA": "#dc2626"}
        for label, address in DONATION_CRYPTO.items():
            color = next((c for k, c in colors.items() if k.lower() in label.lower()), t.accent)
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent; border: none;")
            r_lay = QHBoxLayout(row_w)
            r_lay.setContentsMargins(2, 2, 2, 2)
            r_lay.setSpacing(8)

            coin_badge = QLabel(f"<span style='color:{color}; font-weight:bold;'>{label}</span>")
            coin_badge.setTextFormat(Qt.RichText)
            coin_badge.setMinimumWidth(140)
            r_lay.addWidget(coin_badge)

            addr_label = QLabel(address)
            addr_label.setStyleSheet(f"color: {t.fg_dim}; font-size: 11px; font-family: monospace;")
            addr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r_lay.addWidget(addr_label, 1)

            copy_btn = QPushButton(tr("copy_btn"))
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setStyleSheet(
                f"QPushButton {{ background-color: {t.bg_card}; color: {t.fg}; font-size: 11px;"
                f" font-weight: 600; padding: 2px 10px; border-radius: 4px; border: 1px solid {t.border}; }}"
                f"QPushButton:hover {{ background-color: {t.bg_hover}; color: {t.fg}; }}"
            )
            copy_btn.clicked.connect(lambda _=False, l=label, a=address: self._copy_donation_value(l, a))
            r_lay.addWidget(copy_btn)

            crypto_layout.addWidget(row_w)

        self._crypto_box.setVisible(False)
        layout.addWidget(self._crypto_box)

        # 7. Bottom row with status message & Close button
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 4, 0, 0)
        self._status_label = QLabel()
        self._status_label.setStyleSheet(f"color: {t.ok_fg}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        self._status_label.hide()
        bottom_row.addWidget(self._status_label, 1)

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.setProperty("cssClass", "ghost")
        self._close_btn.setMinimumWidth(80)
        self._close_btn.clicked.connect(self._on_close)
        bottom_row.addWidget(self._close_btn, 0, Qt.AlignRight)
        layout.addLayout(bottom_row)

        self._refresh_goal()

    def _add_pay_button(self, grid, r, c, label, color, hover, url, full_width=None):
        text = full_width or label
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {color}; color: #ffffff; font-weight: 700;"
            f" font-size: {'13px' if full_width else '14px'}; min-height: 38px; padding: 9px 16px; border-radius: 8px; border: none; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
        )
        btn.clicked.connect(lambda _=False, u=url: open_browser(u))
        if grid is not None:
            grid.addWidget(btn, r, c)
        else:
            self._last_button = btn

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
            when = relative_date(d.get("dt"))
            t = T()
            anchor = f'<a href="{url}" style="color:{t.fg}; font-weight:bold;">{name}</a>' if url else name
            lines.append(tr("donate_ticker_fmt").format(anchor=anchor, amount=amt_s, method=method, when=when))
        if not lines:
            lines.append(tr("donate_thanks"))
        random.shuffle(lines)
        return lines

    def _refresh_goal(self):
        raised, _, pct, target = get_monthly_goal_stats(self.donations)
        raised = float(raised)
        r_s = f"{int(raised)}" if raised.is_integer() else f"{raised:.2f}"
        self._goal_reached = raised >= float(target)
        if self._goal_reached:
            self._goal_label.setText(tr("donate_rely_on_you"))
            self._goal_target = 1000
        else:
            self._goal_label.setText(tr("donate_goal_fmt").format(raised=r_s, target=target))
            self._goal_target = int(round(pct * 10))
        self._goal_bar.setValue(self._goal_target)

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

        if getattr(self, "_goal_reached", False):
            self._showing_goal = False
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
            if self._ticker_lines:
                self._donor_label.setText(self._ticker_lines[0])
                self._ticker_idx = 1
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
        self._ticker_timer.timeout.connect(self._fade_out.start)
        self._ticker_timer.start(6500)

        if not getattr(self, "_goal_reached", False):
            QTimer.singleShot(150, self._trigger_goal_anim)

    def _next_ticker_step(self):
        if getattr(self, "_goal_reached", False):
            self._showing_goal = False
            self._goal_view.setVisible(False)
            self._donor_view.setVisible(True)
            if self._ticker_lines:
                self._donor_label.setText(self._ticker_lines[self._ticker_idx % len(self._ticker_lines)])
                self._ticker_idx += 1
            self._fade_in.start()
            return

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
        visible = not self._crypto_box.isVisible()
        self._crypto_box.setVisible(visible)
        toggle_text = tr("donate_crypto_hide") if visible else tr("donate_crypto_toggle")
        self._crypto_toggle.setText(f"🪙 {toggle_text}")
        self.adjustSize()

    def _open_theme_pack_flow(self):
        from .theme_pack import ThemePackGuidanceDialog
        raw_m = getattr(self, "raw_model", "Y1")
        dlg = ThemePackGuidanceDialog(parent=self, model=raw_m)
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
            self._status_label.setText(f"✓ {msg}")
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
