"""A Markdown document shown as a scrollable view inside the app.

The notices body and the post-install guide are the same thing to read: a
Markdown file someone else wrote, shown on one of our cards. They share this
view so both open links in the browser, load their own images, and follow the
live palette when the theme changes.

``docs_source`` has already resolved the document's relative images when
``base_url`` is supplied; this widget only draws it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import QFrame, QTextBrowser

from .. import docs_source
from ..markdown_render import render_markdown_html


class MarkdownDocView(QTextBrowser):
    """Read-only Markdown pane: links out, remote images in, palette-following."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setAutoFillBackground(False)
        self._images: dict = {}
        self._image_replies: dict = {}
        self._network = None
        self._markdown = ""
        self._base_url = ""
        self._render_colors: dict = {}
        # Section a link asked for, delivered once the pane has its real width.
        self._pending_anchor = ""
        self.anchorClicked.connect(self._open_link)

    # -- content -----------------------------------------------------------

    def set_markdown(self, body: str, **colors) -> None:
        """Show ``body``; keyword colours come from the caller's live palette."""
        self._markdown = str(body or "")
        self._base_url = str(colors.pop("base_url", "") or "")
        anchor = str(colors.pop("anchor", "") or "")
        self._render_colors = dict(colors)
        self.setHtml(self.render_html())
        self.document().setDocumentMargin(0)
        self.verticalScrollBar().setValue(0)
        # A link into the middle of a guide (``install.md#first-start``) has to
        # still land there once the pane is at its real width: block positions
        # move when the text rewraps, so the immediate attempt is only a
        # starting point and the deferred one is the arrival.
        self._pending_anchor = anchor
        if anchor:
            self.scroll_to_anchor(anchor)
            QTimer.singleShot(0, self._deliver_pending_anchor)

    def render_html(self) -> str:
        """The document as app-styled HTML, with its links made absolute."""
        prepared = docs_source.prepare_markdown(self._markdown, self._base_url)
        return render_markdown_html(prepared, **self._render_colors)

    def markdown(self) -> str:
        return self._markdown

    def clear_document(self) -> None:
        self._markdown = ""
        self._base_url = ""
        self._pending_anchor = ""
        self.clear()
        self.verticalScrollBar().setValue(0)

    def scroll_to_anchor(self, anchor: str) -> bool:
        """Bring the heading ``anchor`` names to the top of the view.

        Used by a link into the middle of a guide (``install.md#first-start``)
        so the reader arrives at that section, not at the top of a long file.
        """
        title = docs_source.heading_text_for_anchor(self._markdown, anchor)
        if not title:
            return False
        cursor = self.document().find(title)
        if cursor.isNull():
            return False
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self._align_cursor_to_top(cursor)
        return True

    def _align_cursor_to_top(self, cursor) -> None:
        """Scroll so ``cursor``'s line is painted at the top of the pane.

        Measured from the cursor's own rectangle rather than from a stored block
        position, so it stays correct after the document rewraps to the pane's
        final width.
        """
        layout = self.document().documentLayout()
        if layout is not None:
            # Settle block positions for the current width before measuring.
            layout.documentSize()
        bar = self.verticalScrollBar()
        for _ in range(3):
            offset = int(self.cursorRect(cursor).top())
            if offset <= 0:
                break
            bar.setValue(bar.value() + offset)

    def _deliver_pending_anchor(self) -> None:
        """Apply the asked-for section once the pane is visible and sized.

        A pane built while another stage was showing has no width yet, so the
        delivery is left pending until it does — that is the moment the reader
        will actually see it.
        """
        anchor = getattr(self, "_pending_anchor", "")
        if not anchor:
            return
        if not self.isVisible() or self.viewport().width() <= 0:
            return
        self._pending_anchor = ""
        self.scroll_to_anchor(anchor)

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        QTimer.singleShot(0, self._deliver_pending_anchor)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        QTimer.singleShot(0, self._deliver_pending_anchor)

    def refresh_theme(self, **colors) -> None:
        """Re-render after a palette change, keeping the reading position."""
        if not self._markdown:
            return
        offset = self.verticalScrollBar().value()
        if colors:
            self._render_colors = dict(colors)
        self.setHtml(self.render_html())
        self.document().setDocumentMargin(0)
        self.verticalScrollBar().setValue(offset)

    # -- links and images --------------------------------------------------

    def _open_link(self, url) -> None:
        text = url.toString() if hasattr(url, "toString") else str(url)
        if text.startswith("#"):
            self.scroll_to_anchor(text[1:])
            return
        if text.startswith(("http://", "https://")):
            # A document, repository or release the catalogue knows opens on
            # the site's parser page; anything else keeps its own target.
            docs_source.open_site_link(text)

    def loadResource(self, resource_type, name):  # noqa: N802 (Qt naming)
        if resource_type == QTextDocument.ResourceType.ImageResource:
            key = name.toString() if hasattr(name, "toString") else str(name)
            if key in self._images:
                return self._images[key]
            if key.startswith(("http://", "https://")) and key not in self._image_replies:
                self._fetch_image(key)
        return super().loadResource(resource_type, name)

    def _fetch_image(self, url: str) -> None:
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

        if self._network is None:
            self._network = QNetworkAccessManager(self)
        reply = self._network.get(QNetworkRequest(QUrl(url)))
        self._image_replies[url] = reply
        reply.finished.connect(lambda u=url, r=reply: self._image_finished(u, r))

    def _image_finished(self, url: str, reply) -> None:
        from PySide6.QtNetwork import QNetworkReply

        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                image = QImage.fromData(reply.readAll().data())
                if not image.isNull():
                    self._images[url] = image
                    self.document().addResource(
                        QTextDocument.ResourceType.ImageResource, QUrl(url), image
                    )
                    self.document().markContentsDirty(
                        0, self.document().characterCount()
                    )
                    self.viewport().update()
        finally:
            self._image_replies.pop(url, None)
            reply.deleteLater()
