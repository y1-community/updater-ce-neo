"""Runtime fallback for English copy that slipped past the normal catalogs.

The catalogs are generated from the English strings the app owns. If a widget
still shows one of those English strings while the UI is set to another
language, this service fills the gap: the bundled catalog is used first, then a
background provider request translates the text, and the result is cached on
disk for the next launch.

Only strings in :func:`src.i18n.english_sources` are eligible. Model names,
firmware names, paths and user-entered text are therefore never sent to a
translation service. Failures are silent and leave the English text in place.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QWidget,
)

from . import i18n
from .paths import app_cache_dir

logger = logging.getLogger(__name__)

_MAX_BATCH = 8
_SCAN_INTERVAL_MS = 2500
_DOT_INTERVAL_MS = 380
_REQUEST_DEBOUNCE_MS = 250
_MAX_CACHE_ENTRIES_PER_LANGUAGE = 4000


@dataclass
class _TextTarget:
    key: tuple
    read: Callable[[], str]
    write: Callable[[str], None]
    # Set for a sentence built from an English template: the provider returns
    # the translated template and this renders the captured values into it.
    render: Optional[Callable[[str], str]] = None


class _TranslationWorker(QThread):
    """Translate one queued batch away from the UI thread."""

    translated = Signal(str, str, str)  # language, source, translation
    failed = Signal(str, str)  # language, source

    def __init__(self, lang: str, sources: list[str], fetch=None, parent=None):
        super().__init__(parent)
        self._lang = lang
        self._sources = list(sources)
        self._fetch = fetch

    def run(self) -> None:
        if i18n.is_english_language(self._lang):
            # English is the source language: there is nothing to translate,
            # and reaching a provider for it would be pure waste.
            return
        for source in self._sources:
            if self.isInterruptionRequested():
                return
            payload, tokens = _protect_placeholders(source)
            try:
                if self._fetch is not None:
                    result = self._fetch(payload, self._lang)
                else:
                    from .translate import fetch_google_translation

                    result = fetch_google_translation(
                        payload,
                        self._lang,
                        timeout=4.5,
                        raise_on_error=True,
                    )
            except Exception as exc:
                logger.debug("Runtime translation failed for %r: %s", source, exc)
                self.failed.emit(self._lang, source)
                continue
            value = result.strip() if isinstance(result, str) else ""
            if tokens:
                value = _restore_placeholders(value, tokens)
                if not _template_survived(source, value):
                    logger.debug(
                        "Runtime translation of %r damaged its placeholders", source
                    )
                    self.failed.emit(self._lang, source)
                    continue
            if value and value != source:
                self.translated.emit(self._lang, source, value)
            else:
                self.failed.emit(self._lang, source)


class RuntimeTranslator(QObject):
    """Fill missing UI translations at runtime, without blocking the window."""

    def __init__(
        self,
        parent=None,
        *,
        cache_path: Optional[Path] = None,
        fetch: Optional[Callable[[str, str], str]] = None,
        scan_interval_ms: int = _SCAN_INTERVAL_MS,
    ):
        super().__init__(parent)
        self._cache_path = (
            Path(cache_path)
            if cache_path is not None
            else app_cache_dir() / "runtime_i18n.json"
        )
        self._fetch = fetch
        self._cache: dict[str, dict[str, str]] = self._load_cache()
        self._lang = "en"
        self._stopped = False
        self._pending: dict[str, list[_TextTarget]] = {}
        self._queued_keys: dict[tuple, str] = {}
        self._loading: dict[tuple, tuple[_TextTarget, str, str]] = {}
        self._applied: dict[tuple, tuple[_TextTarget, str, str]] = {}
        self._failed: set[tuple[str, str]] = set()
        self._active_sources: set[str] = set()
        self._template_patterns: dict[str, Optional[re.Pattern]] = {}
        self._template_lookup: dict[str, Optional[tuple]] = {}
        self._worker: Optional[_TranslationWorker] = None
        self._dots = 0
        self._event_filter_installed = False
        self._learned: dict[str, dict[str, str]] = {}

        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._start_batch)

        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(_DOT_INTERVAL_MS)
        self._loading_timer.timeout.connect(self._tick_loading)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(max(1000, int(scan_interval_ms)))
        self._scan_timer.timeout.connect(self.scan)

    # -- lifecycle ---------------------------------------------------------
    def start(self, lang: str) -> None:
        self._stopped = False
        self.set_language(lang)

    def set_language(self, lang: str) -> None:
        code = (lang or "en").strip() or "en"
        self._drop_loading_and_pending()
        self._restore_applied()
        self._lang = code
        self._failed.clear()
        if i18n.is_english_language(code):
            # English copy needs no gap-filling: no timers, no scans and no
            # provider calls while the interface is in the source language.
            self._scan_timer.stop()
            self._set_event_filter(False)
            return
        self._set_event_filter(True)
        self._scan_timer.start()
        QTimer.singleShot(0, self.scan)

    def _set_event_filter(self, installed: bool) -> None:
        """Watch (or stop watching) new windows for untranslated copy."""
        if installed == self._event_filter_installed:
            return
        app = QApplication.instance()
        if app is None:
            return
        if installed:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._event_filter_installed = installed

    def stop(self) -> None:
        self._stopped = True
        self._scan_timer.stop()
        self._batch_timer.stop()
        self._loading_timer.stop()
        self._drop_loading_and_pending()
        self._restore_applied()
        self._set_event_filter(False)
        self._flush_learned()
        worker = self._worker
        if worker is not None:
            worker.requestInterruption()
            if not worker.wait(6000):
                logger.debug("Runtime translation worker did not stop in time")
        self._worker = None

    def eventFilter(self, obj, event):
        if (
            not getattr(self, "_stopped", False)
            and not i18n.is_english_language(getattr(self, "_lang", "en"))
            and event.type() == QEvent.Type.Show
            and isinstance(obj, QWidget)
            and obj.isWindow()
        ):
            QTimer.singleShot(0, lambda widget=obj: self.scan(widget))
        return super().eventFilter(obj, event)

    # -- scanning ----------------------------------------------------------
    def scan(self, root=None) -> None:
        if getattr(self, "_stopped", False) or i18n.is_english_language(getattr(self, "_lang", "en")):
            return
        app = QApplication.instance()
        if root is not None:
            try:
                widgets = [root, *root.findChildren(QWidget)]
            except RuntimeError:
                return
        elif app is not None:
            try:
                widgets = app.allWidgets()
            except RuntimeError:
                return
        else:
            return

        for widget in widgets:
            try:
                self._collect_widget(widget)
            except RuntimeError:
                continue
        if root is None and app is not None:
            try:
                for tray in app.findChildren(QSystemTrayIcon):
                    menu = tray.contextMenu()
                    if menu is not None:
                        self._collect_actions(menu)
            except Exception:
                logger.debug("Could not scan tray actions", exc_info=True)
        if self._pending:
            self._batch_timer.start(_REQUEST_DEBOUNCE_MS)
        if not self._loading:
            self._loading_timer.stop()

    def _collect_widget(self, widget) -> None:
        if getattr(widget, "_skip_runtime_i18n", False):
            return
        name = widget.objectName()
        if name in ("brandLabel", "brandVersion", "brandContainer"):
            return
        if isinstance(widget, QLabel):
            self._add_target(widget, "text", widget.text, widget.setText)
        elif isinstance(widget, QAbstractButton):
            self._add_target(widget, "text", widget.text, widget.setText)

        if isinstance(widget, QGroupBox):
            self._add_target(widget, "group", widget.title, widget.setTitle)
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            self._add_target(
                widget, "placeholder", widget.placeholderText, widget.setPlaceholderText
            )
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                self._add_scoped_target(
                    (id(widget), "combo", index),
                    lambda i=index: widget.itemText(i),
                    lambda text, i=index: widget.setItemText(i, text),
                )
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                self._add_scoped_target(
                    (id(widget), "tab", index),
                    lambda i=index: widget.tabText(i),
                    lambda text, i=index: widget.setTabText(i, text),
                )
        if isinstance(widget, QListWidget):
            for index in range(widget.count()):
                item = widget.item(index)
                if item is not None:
                    self._add_item_target(
                        (id(item), "list"), item.text, item.setText
                    )
        if isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None:
                        self._add_item_target(
                            (id(item), "table"), item.text, item.setText
                        )
            for column in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(column)
                if item is not None:
                    self._add_item_target(
                        (id(item), "table_header"), item.text, item.setText
                    )
        if isinstance(widget, QTreeWidget):
            for index in range(widget.topLevelItemCount()):
                self._collect_tree_item(widget.topLevelItem(index))
            for column in range(widget.columnCount()):
                item = widget.headerItem()
                if item is not None:
                    self._add_item_target(
                        (id(item), f"tree_header:{column}"),
                        lambda i=column, it=item: it.text(i),
                        lambda text, i=column, it=item: it.setText(i, text),
                    )
        if isinstance(widget, (QMenu, QMenuBar, QToolBar)):
            self._collect_actions(widget)

        self._add_target(widget, "tooltip", widget.toolTip, widget.setToolTip)
        self._add_target(widget, "status_tip", widget.statusTip, widget.setStatusTip)
        self._add_target(widget, "whats_this", widget.whatsThis, widget.setWhatsThis)
        self._add_target(
            widget, "accessible_name", widget.accessibleName, widget.setAccessibleName
        )
        self._add_target(
            widget,
            "accessible_description",
            widget.accessibleDescription,
            widget.setAccessibleDescription,
        )
        try:
            if widget.isWindow():
                self._add_target(
                    widget, "window_title", widget.windowTitle, widget.setWindowTitle
                )
        except RuntimeError:
            return

    def _collect_tree_item(self, item) -> None:
        if item is None:
            return
        for column in range(item.columnCount()):
            self._add_item_target(
                (id(item), f"tree:{column}"),
                lambda i=column, it=item: it.text(i),
                lambda text, i=column, it=item: it.setText(i, text),
            )
        for index in range(item.childCount()):
            self._collect_tree_item(item.child(index))

    def _collect_actions(self, owner) -> None:
        try:
            actions = owner.actions()
        except RuntimeError:
            return
        for action in actions:
            self._collect_action(action)

    def _collect_action(self, action: QAction) -> None:
        if action is None:
            return
        try:
            self._add_scoped_target(
                (id(action), "action_text"), action.text, action.setText
            )
            self._add_scoped_target(
                (id(action), "action_tooltip"), action.toolTip, action.setToolTip
            )
            self._add_scoped_target(
                (id(action), "action_status"), action.statusTip, action.setStatusTip
            )
        except RuntimeError:
            return

    def _add_target(self, owner, suffix: str, read, write) -> None:
        self._add_scoped_target((id(owner), suffix), read, write)

    def _add_item_target(self, key: tuple, read, write) -> None:
        self._add_scoped_target(key, read, write)

    def _add_scoped_target(self, key: tuple, read, write) -> None:
        self._consider(_TextTarget(key, read, write))

    def _consider(self, target: _TextTarget) -> None:
        try:
            text = target.read()
        except RuntimeError:
            return
        except Exception:
            return
        if not isinstance(text, str) or not text.strip():
            return
        if _looks_like_markup(text):
            self._consider_markup(target, text)
            return
        if target.key in self._loading:
            return
        applied = self._applied.get(target.key)
        if applied is not None and text == applied[2]:
            return

        source = self._source_for(text)
        if not source:
            # The catalogs only lost the *template*: the label shows the
            # formatted sentence, with values the template cannot contain.
            match = self._template_match(text)
            if match is None:
                return
            source, values = match
            target = _TextTarget(
                target.key,
                target.read,
                target.write,
                render=lambda translated, values=values: _render_template(
                    translated, values
                ),
            )
        if (self._lang, source) in self._failed:
            return

        translated = self._cached_translation(source)
        if translated:
            if self._write_translation(target, source, translated, text):
                return
            # A catalog translation that cannot render its values is unusable;
            # do not ask the same provider for it on every scan.
            self._failed.add((self._lang, source))
            return

        self._queue(target, source)

    def _consider_markup(self, target: _TextTarget, html: str) -> None:
        """Translate the text runs of a rich-text label, never its markup.

        The donations card builds its appeal as ``<b>lead</b> followup``, and
        completion copy carries emphasis tags of its own. Passing that HTML to
        a provider would translate tag names and attributes, so each text run
        is matched against the English allow-list on its own and only that run
        is replaced; the surrounding tags are put back byte for byte.
        """
        for index, part in enumerate(_markup_parts(html)):
            if not part or _is_tag(part) or not part.strip():
                continue
            self._consider(self._segment_target(target, index))

    def _segment_target(self, label_target: _TextTarget, index: int) -> _TextTarget:
        """A target for one text run inside a rich-text label."""

        def read() -> str:
            parts = _markup_parts(label_target.read())
            if index >= len(parts):
                return ""
            return parts[index].strip()

        def write(value: str) -> None:
            parts = _markup_parts(label_target.read())
            if index >= len(parts):
                return
            chunk = parts[index]
            prefix = chunk[: len(chunk) - len(chunk.lstrip())]
            suffix = chunk[len(chunk.rstrip()) :]
            parts[index] = f"{prefix}{str(value).strip()}{suffix}"
            label_target.write("".join(parts))

        return _TextTarget((*label_target.key, "rich", index), read, write)

    def _template_match(self, text: str) -> Optional[tuple[str, dict]]:
        """Find the English template that produced a formatted sentence."""
        if text in self._template_lookup:
            return self._template_lookup[text]
        found = None
        for template in i18n.english_templates():
            pattern = self._template_patterns.get(template, _UNSET)
            if pattern is _UNSET:
                pattern = _compile_template(template)
                self._template_patterns[template] = pattern
            if pattern is None:
                continue
            match = pattern.fullmatch(text)
            if match is not None:
                found = (template, dict(match.groupdict()))
                break
        if len(self._template_lookup) >= 2000:
            self._template_lookup.clear()
        self._template_lookup[text] = found
        return found

    def _source_for(self, text: str) -> str:
        sources = i18n.english_sources()
        candidates = (text, text.strip())
        if "&" in text:
            candidates += (text.replace("&", ""), text.replace("&", "").strip())
        for candidate in candidates:
            if candidate and candidate in sources:
                if candidate in ("Updater", "Innioasis Updater", "MediaTek Installer", "Installer"):
                    return ""
                return candidate
        return ""

    def _cached_translation(self, source: str) -> str:
        cached = (self._cache.get(self._lang) or {}).get(source, "")
        if cached and cached != source:
            return cached
        try:
            return i18n.translation_for_english(source, self._lang)
        except Exception:
            logger.debug("Catalog translation lookup failed", exc_info=True)
            return ""

    # -- loading and applying ---------------------------------------------
    def _queue(self, target: _TextTarget, source: str) -> None:
        if target.key in self._queued_keys:
            return
        original = target.read()
        self._pending.setdefault(source, []).append(target)
        self._queued_keys[target.key] = source
        self._loading[target.key] = (target, source, original)
        self._loading_timer.start()
        self._render_loading()
        self._batch_timer.start(_REQUEST_DEBOUNCE_MS)

    def _tick_loading(self) -> None:
        if not self._loading:
            self._loading_timer.stop()
            return
        self._dots = 1 if self._dots >= 3 else self._dots + 1
        self._render_loading()

    def _render_loading(self) -> None:
        dots = " " + ("." * max(1, self._dots))
        for key, (target, _source, original) in list(self._loading.items()):
            try:
                current = target.read()
                if current == original or current.startswith(original + " "):
                    target.write(original + dots)
                else:
                    self._forget_loading(key)
            except Exception:
                self._forget_loading(key)

    def _forget_loading(self, key: tuple) -> None:
        entry = self._loading.pop(key, None)
        source = self._queued_keys.pop(key, "")
        if entry is not None and source:
            remaining = [
                target
                for target in self._pending.get(source, [])
                if target.key != key
            ]
            if remaining:
                self._pending[source] = remaining
            else:
                self._pending.pop(source, None)
        if not self._loading:
            self._loading_timer.stop()

    def _write_translation(
        self, target: _TextTarget, source: str, translated: str, original: str
    ) -> bool:
        value = translated
        if target.render is not None:
            try:
                value = target.render(translated)
            except Exception:
                value = ""
        if not value:
            return False
        try:
            target.write(value)
        except Exception:
            return False
        self._applied[target.key] = (target, original, value)
        self._queued_keys.pop(target.key, None)
        self._loading.pop(target.key, None)
        self._remove_from_pending(target.key, source)
        if not self._loading:
            self._loading_timer.stop()
        return True

    def _remove_from_pending(self, key: tuple, source: str) -> None:
        remaining = [
            target for target in self._pending.get(source, []) if target.key != key
        ]
        if remaining:
            self._pending[source] = remaining
        else:
            self._pending.pop(source, None)

    # -- background batches ------------------------------------------------
    def _start_batch(self) -> None:
        if self._stopped or self._worker is not None:
            return
        if i18n.is_english_language(self._lang):
            return
        batch: list[str] = []
        for source in self._pending:
            if source in self._active_sources:
                continue
            batch.append(source)
            if len(batch) >= _MAX_BATCH:
                break
        if not batch:
            if not self._loading:
                self._loading_timer.stop()
            return
        self._active_sources = set(batch)
        worker = _TranslationWorker(
            self._lang, batch, self._fetch, parent=self
        )
        worker.translated.connect(self._on_translated)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(lambda w=worker: self._on_batch_finished(w))
        self._worker = worker
        worker.start()

    def _on_translated(self, lang: str, source: str, translated: str) -> None:
        self._active_sources.discard(source)
        if self._stopped or lang != self._lang:
            return
        for target in list(self._pending.get(source, [])):
            entry = self._loading.get(target.key)
            if entry is None:
                continue
            _target, _source, original = entry
            try:
                current = target.read()
            except Exception:
                self._forget_loading(target.key)
                continue
            if current == original or current.startswith(original + " "):
                if not self._write_translation(target, source, translated, original):
                    self._failed.add((lang, source))
                    try:
                        target.write(original)
                    except Exception:
                        pass
                    self._forget_loading(target.key)
            else:
                self._forget_loading(target.key)
        self._pending.pop(source, None)
        self._learned.setdefault(lang, {})[source] = translated
        cache = self._cache.setdefault(lang, {})
        cache[source] = translated
        self._trim_cache(cache)
        self._save_cache()
        if not self._loading:
            self._loading_timer.stop()

    def _on_failed(self, lang: str, source: str) -> None:
        self._active_sources.discard(source)
        if not self._stopped and lang == self._lang:
            self._failed.add((lang, source))
            for target in list(self._pending.get(source, [])):
                entry = self._loading.get(target.key)
                if entry is None:
                    continue
                _target, _source, original = entry
                try:
                    target.write(original)
                except Exception:
                    pass
                self._forget_loading(target.key)
            self._pending.pop(source, None)
        if not self._loading:
            self._loading_timer.stop()

    def _on_batch_finished(self, worker: _TranslationWorker) -> None:
        if self._worker is not worker:
            return
        self._worker = None
        self._active_sources.clear()
        worker.deleteLater()
        self._flush_learned()
        if self._pending:
            self._batch_timer.start(0)

    def _flush_learned(self) -> None:
        """Write this batch's translations into the app's own catalogs.

        The widget cache makes the next launch fast; the catalog is what makes
        it complete. ``tr()`` reads it too, so copy built later in this session
        arrives already translated, and the file survives a cleared cache.
        """
        learned = self._learned
        self._learned = {}
        for lang, mapping in learned.items():
            try:
                i18n.remember_translations(lang, mapping)
            except Exception:
                logger.debug("Could not store runtime translations", exc_info=True)

    # -- language changes / shutdown --------------------------------------
    def _drop_loading_and_pending(self) -> None:
        for target, _source, original in list(self._loading.values()):
            try:
                current = target.read()
                if current == original or current.startswith(original + " "):
                    target.write(original)
            except Exception:
                pass
        self._pending.clear()
        self._queued_keys.clear()
        self._loading.clear()
        self._active_sources.clear()
        self._batch_timer.stop()
        self._loading_timer.stop()

    def _restore_applied(self) -> None:
        for target, original, translated in list(self._applied.values()):
            try:
                if target.read() == translated:
                    target.write(original)
            except Exception:
                pass
        self._applied.clear()

    # -- persistence -------------------------------------------------------
    def _load_cache(self) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            entries = raw if isinstance(raw, dict) else {}
        loaded: dict[str, dict[str, str]] = {}
        for lang, table in entries.items():
            if not isinstance(lang, str) or not isinstance(table, dict):
                continue
            clean = {
                str(source): str(translated)
                for source, translated in table.items()
                if isinstance(source, str)
                and isinstance(translated, str)
                and translated.strip()
                and translated != source
            }
            if clean:
                loaded[lang] = clean
        return loaded

    def _trim_cache(self, table: dict[str, str]) -> None:
        if len(table) <= _MAX_CACHE_ENTRIES_PER_LANGUAGE:
            return
        for source in list(table)[: len(table) - _MAX_CACHE_ENTRIES_PER_LANGUAGE]:
            table.pop(source, None)

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_name(
                f"{self._cache_path.name}.{os.getpid()}.tmp"
            )
            temporary.write_text(
                json.dumps({"version": 1, "entries": self._cache}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary, self._cache_path)
        except Exception:
            logger.debug("Could not save runtime translations", exc_info=True)


_UNSET = object()
_TEMPLATE_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^{}]*)?\}")
_TEMPLATE_SPLIT = re.compile(r"(\{[A-Za-z_][A-Za-z0-9_]*(?::[^{}]*)?\})")
# A token the translation service leaves alone, so it cannot rename {fields}.
_TEMPLATE_TOKEN = "\u27e6{}\u27e7"


def _protect_placeholders(text: str) -> tuple[str, dict]:
    """Replace ``{field}`` with opaque tokens before a provider sees them."""
    tokens: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        token = _TEMPLATE_TOKEN.format(len(tokens))
        tokens[token] = match.group(1)
        return token

    return _TEMPLATE_FIELD.sub(replace, text), tokens


def _restore_placeholders(text: str, tokens: dict) -> str:
    for token, field in tokens.items():
        text = text.replace(token, "{" + field + "}")
    return text


def _template_survived(source: str, translated: str) -> bool:
    """Every field (and no invented one) survived the provider round trip."""
    if not translated or "\u27e6" in translated or "\u27e7" in translated:
        return False
    return set(_TEMPLATE_FIELD.findall(translated)) == set(
        _TEMPLATE_FIELD.findall(source)
    )


def _compile_template(template: str) -> Optional[re.Pattern]:
    """A regex that captures each placeholder from a rendered sentence."""
    pieces = []
    names: set[str] = set()
    literal = []
    for part in _TEMPLATE_SPLIT.split(template):
        if not part:
            continue
        field = _TEMPLATE_FIELD.fullmatch(part)
        if field is None:
            literal.append(part)
            pieces.append(re.escape(part))
            continue
        name = field.group(1)
        if name in names:
            pieces.append(f"(?P={name})")
        else:
            names.add(name)
            pieces.append(f"(?P<{name}>.+?)")
    # A pattern with almost no literal copy ("{task} ({pct}%)") would match
    # unrelated labels and translate text the app does not own.
    if not names or len(re.findall(r"[A-Za-z]", "".join(literal))) < 4:
        return None
    try:
        return re.compile("".join(pieces), re.DOTALL)
    except re.error:
        return None


def _render_template(translated: str, values: dict) -> str:
    try:
        return translated.format(**values)
    except Exception:
        return ""


def _looks_like_markup(text: str) -> bool:
    """True for labels that carry HTML or other rich-text markup."""
    return "<" in text and ">" in text


_TAG_SPLIT = re.compile(r"(<[^>]*>)")


def _markup_parts(text: str) -> list[str]:
    """Split markup into alternating tags and text runs, keeping both."""
    return _TAG_SPLIT.split(text)


def _is_tag(part: str) -> bool:
    return part.startswith("<") and part.endswith(">")
