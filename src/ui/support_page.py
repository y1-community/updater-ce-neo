"""Support Us as a normal page of the window, not a dialog dropped into the stack."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from ..config import DONATION_LINKS
from ..i18n import tr
from .dark import page_margins
from .support_appeal import SupportIntro


class SupportPage(QWidget):
    """Coffee only. The page uses the same surface as the rest of the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("supportPage")
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*page_margins())
        layout.setSpacing(16)
        self._intro = SupportIntro()
        layout.addWidget(self._intro)
        self._coffee = QPushButton(tr("donate_buy_coffee"))
        self._coffee.setCursor(Qt.CursorShape.ArrowCursor)
        self._coffee.setStyleSheet("")
        self._coffee.clicked.connect(self._open_coffee)
        layout.addWidget(self._coffee, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def retranslate(self) -> None:
        self._intro.retranslate()
        self._coffee.setText(tr("donate_buy_coffee"))

    def _open_coffee(self) -> None:
        from ..browser import open_browser

        open_browser(DONATION_LINKS["kofi"])
