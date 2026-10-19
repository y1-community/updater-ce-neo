"""Compact Support Us page featuring the heartfelt personal appeal and clean donation methods."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..browser import open_browser
from ..config import DONATION_CRYPTO, DONATION_LINKS
from ..i18n import tr
from .dark import page_margins, theme_foreground
from .support_appeal import bundled_portrait_path, circle_pixmap
from .surfaces import show_glass_backdrop
from .widgets import Card


class SupportPage(QWidget):
    """Compact Support Us page matching the Select Software screen dimensions and glass material."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("supportPage")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*page_margins())
        layout.setSpacing(10)

        # 1. Main Card matching native bento card style across all pages
        self._card = Card()

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        # Developer Portrait & Identity Header
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        portrait_lbl = QLabel()
        portrait_lbl.setFixedSize(48, 48)
        portrait_lbl.setAutoFillBackground(False)
        portrait_lbl.setStyleSheet("background: transparent; border: none;")
        shipped = bundled_portrait_path()
        if shipped.exists():
            pix = circle_pixmap(QPixmap(str(shipped)), 48)
            portrait_lbl.setPixmap(pix)
        header_row.addWidget(portrait_lbl, 0, Qt.AlignmentFlag.AlignTop)

        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        self._author_lbl = QLabel("Ryan Specter & Community Contributors")
        self._author_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: palette(window-text); background: transparent; border: none;"
        )
        info_col.addWidget(self._author_lbl)

        self._role_lbl = QLabel("Lead Developer · Updater CE & Community Firmware Project")
        self._role_lbl.setStyleSheet(
            "font-size: 11px; color: palette(placeholder-text); background: transparent; border: none;"
        )
        info_col.addWidget(self._role_lbl)
        header_row.addLayout(info_col, 1)

        card_layout.addLayout(header_row)

        # Heartfelt Wikipedia-style Personal Appeal
        self._appeal_body = QLabel()
        self._appeal_body.setWordWrap(True)
        self._appeal_body.setTextFormat(Qt.TextFormat.RichText)
        self._appeal_body.setStyleSheet(
            "font-size: 12px; line-height: 1.4; color: palette(window-text); background: transparent; border: none;"
        )
        self._appeal_body.setText(
            "<b>Donations directly help me support my family and fund the test devices</b> "
            "that make this free service possible for everyone. If this tool saved you time or rescued your player today, "
            "please consider a small donation — it truly means the world to us."
        )
        card_layout.addWidget(self._appeal_body)

        # Primary Payment Methods Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        primary_methods = [
            ("donate_kofi", "kofi"),
            ("donate_paypal", "paypal"),
            ("donate_revolut", "revolut"),
            ("donate_patreon", "patreon"),
        ]

        self._method_buttons = []
        for key, link_id in primary_methods:
            url = DONATION_LINKS.get(link_id, "")
            btn = QPushButton(tr(key))
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.setMinimumHeight(28)
            btn.clicked.connect(lambda _=False, u=url: open_browser(u))
            btn_row.addWidget(btn, 1)
            self._method_buttons.append((btn, key))

        card_layout.addLayout(btn_row)

        # Small Discrete Text Buttons towards bottom for Honeygain & Crypto
        bottom_links_row = QHBoxLayout()
        bottom_links_row.setContentsMargins(2, 4, 2, 0)
        bottom_links_row.setSpacing(14)

        self._honeygain_link = QPushButton(tr("donate_honeygain"))
        self._honeygain_link.setFlat(True)
        self._honeygain_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._honeygain_link.setStyleSheet(
            "QPushButton { font-size: 11px; color: palette(placeholder-text); background: transparent; border: none; text-decoration: underline; text-align: left; }"
            "QPushButton:hover { color: palette(window-text); }"
        )
        self._honeygain_link.clicked.connect(
            lambda _=False, u=DONATION_LINKS.get("honeygain", ""): open_browser(u)
        )
        bottom_links_row.addWidget(self._honeygain_link, 0, Qt.AlignmentFlag.AlignLeft)

        bottom_links_row.addStretch(1)

        self._crypto_toggle_link = QPushButton(tr("donate_crypto_toggle"))
        self._crypto_toggle_link.setFlat(True)
        self._crypto_toggle_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._crypto_toggle_link.setStyleSheet(
            "QPushButton { font-size: 11px; color: palette(placeholder-text); background: transparent; border: none; text-decoration: underline; text-align: right; }"
            "QPushButton:hover { color: palette(window-text); }"
        )
        self._crypto_toggle_link.clicked.connect(self._toggle_crypto_drawer)
        bottom_links_row.addWidget(self._crypto_toggle_link, 0, Qt.AlignmentFlag.AlignRight)

        card_layout.addLayout(bottom_links_row)

        # Collapsible Crypto Drawer
        self._crypto_drawer = QWidget()
        self._crypto_drawer.setAutoFillBackground(False)
        self._crypto_drawer.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        drawer_layout = QVBoxLayout(self._crypto_drawer)
        drawer_layout.setContentsMargins(2, 6, 2, 2)
        drawer_layout.setSpacing(4)

        for label, addr in DONATION_CRYPTO.items():
            c_row = QHBoxLayout()
            c_row.setSpacing(6)

            tag = QLabel(f"<b>{label.split(' ')[0]}:</b>")
            tag.setMinimumWidth(55)
            tag.setStyleSheet("font-size: 11px; color: palette(window-text); background: transparent; border: none;")
            c_row.addWidget(tag)

            addr_box = QLabel(addr)
            addr_box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            addr_box.setStyleSheet(
                "font-family: monospace; font-size: 10px; background: rgba(120, 120, 120, 0.15); "
                "padding: 2px 4px; border-radius: 3px; color: palette(window-text);"
            )
            c_row.addWidget(addr_box, 1)

            copy_btn = QPushButton(tr("support_copy_address"))
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setStyleSheet("font-size: 10px; padding: 2px 6px;")
            copy_btn.clicked.connect(lambda _=False, a=addr, b=copy_btn: self._copy_crypto(a, b))
            c_row.addWidget(copy_btn)

            drawer_layout.addLayout(c_row)

        self._crypto_drawer.setVisible(False)
        card_layout.addWidget(self._crypto_drawer)

        self._card.set_layout(card_layout)
        layout.addWidget(self._card)
        layout.addStretch(1)

    def content_height_hint(self) -> int:
        """Height needed by the Support page to display its card and methods without compression."""
        if self.layout() is not None:
            self.layout().activate()
        if hasattr(self, "_card") and self._card.layout() is not None:
            self._card.layout().activate()
        card_h = self._card.sizeHint().height() if hasattr(self, "_card") else 240
        top, right, bottom, left = page_margins()
        return card_h + top + bottom + 24

    def _toggle_crypto_drawer(self):
        show = not self._crypto_drawer.isVisible()
        self._crypto_drawer.setVisible(show)
        self._crypto_toggle_link.setText(
            tr("donate_crypto_hide") if show else tr("donate_crypto_toggle")
        )
        win = self.window()
        if win is not None and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _copy_crypto(self, address: str, button: QPushButton):
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(address)
        orig_text = tr("support_copy_address")
        button.setText(tr("support_copied_btn"))
        button.setEnabled(False)
        QTimer.singleShot(1500, lambda: (button.setText(orig_text), button.setEnabled(True)))

    def retranslate(self) -> None:
        self._honeygain_link.setText(tr("donate_honeygain"))
        self._crypto_toggle_link.setText(
            tr("donate_crypto_hide") if self._crypto_drawer.isVisible() else tr("donate_crypto_toggle")
        )
        for btn, key in getattr(self, "_method_buttons", []):
            btn.setText(tr(key))

    def refresh_theme(self) -> None:
        self.update()
