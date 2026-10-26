"""OS-native controls the app places inside its own widgets.

The round action chips (next / back / cancel) are painted by Qt everywhere
except macOS, where AppKit owns the control instead:

* macOS 26 and later draws the chip as Liquid Glass (``NSBezelStyleGlass``)
  with the system's press animation and metal edge.
* Monterey through Sequoia draw the system's circular bezel — the one Safari,
  Finder and Mail use — which is what the painted chip had been imitating.

Either way the Qt widget keeps the layout slot, the tooltip and the
``clicked`` signal; AppKit draws and animates what the user sees. If AppKit
cannot be reached (no PyObjC, the offscreen platform plugin, or
``INNIOASIS_NATIVE_ROUND_BUTTONS=0``) the caller paints its own chip.

Nothing here is referenced without an availability check, so the app still
launches on a macOS without these symbols.
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QComboBox

logger = logging.getLogger(__name__)

#: Set to ``0`` to keep every chip Qt-painted (support, and tests).
DISABLE_ENV = "INNIOASIS_NATIVE_ROUND_BUTTONS"

#: Room the painted chip leaves around its disc for the drop shadow.
CHIP_INSET_PX = 3

#: Glyph point size: the painted chip draws the same symbol at 15 px.
CHIP_GLYPH_PT = 15.0

# The app's chip names, as SF Symbols. Qt's own fallback map (icons.py) is
# keyed by the same names, so a chip translates to both without the caller
# knowing which renderer is live.
_SF_SYMBOLS = {
    "next": "arrow.right",
    "back": "arrow.left",
    "cancel": "xmark",
    "complete": "checkmark",
    "download": "arrow.down.circle",
    "folder": "folder",
    "close": "xmark",
    "trash": "trash",
    "delete": "trash",
    "bin": "trash",
    "retry": "arrow.clockwise",
}

_SEGMENTED_SF_SYMBOLS = {
    "back": "chevron.backward",
    "next": "chevron.forward",
    "cancel": "xmark",
    "close": "xmark",
    "retry": "arrow.clockwise",
    "complete": "checkmark",
    "download": "arrow.down.circle",
    "trash": "trash",
    "delete": "trash",
    "bin": "trash",
    "retry": "arrow.clockwise",
}

_target_class = None
_segmented_target_class = None


def _segmented_target_class_obj():
    """AppKit object handling clicks on a 2-segment capsule control."""
    global _segmented_target_class
    if _segmented_target_class is not None:
        return _segmented_target_class
    import objc
    from AppKit import NSObject

    try:
        cls = objc.lookUpClass("InnioasisSegmentedPairTarget")
    except objc.nosuchclass_error:
        cls = None

    if cls is None:

        class InnioasisSegmentedPairTarget(NSObject):
            def segmentClicked_(self, sender):  # noqa: N802 (ObjC selector)
                pair = getattr(self, "_pair", None)
                if pair is not None:
                    pair._handle_click(int(sender.selectedSegment()))

        cls = InnioasisSegmentedPairTarget
    _segmented_target_class = cls
    return cls


def _chip_target_class():
    """The AppKit object that forwards a button press to one chip.

    PyObjC needs an Objective-C class for the target/action pair. One class is
    created per process and each button gets its own instance carrying a
    Python reference to its chip, which is also what keeps the target alive:
    ``NSControl`` does not retain its target.
    """
    global _target_class
    if _target_class is not None:
        return _target_class
    import objc
    from AppKit import NSObject

    try:
        cls = objc.lookUpClass("InnioasisRoundChipTarget")
    except objc.nosuchclass_error:
        cls = None

    if cls is None:

        class InnioasisRoundChipTarget(NSObject):
            def chipClicked_(self, sender):  # noqa: N802 (ObjC selector)
                chip = getattr(self, "_chip", None)
                if chip is not None:
                    chip._emit_click()

        cls = InnioasisRoundChipTarget
    _target_class = cls
    return cls


def _cocoa_session() -> bool:
    """True when Qt is drawing into real NSViews (not the offscreen plugin)."""
    app = QGuiApplication.instance()
    if app is None:
        return False
    try:
        return app.platformName() == "cocoa"
    except Exception:
        return False


_availability: bool | None = None


def native_round_buttons_available() -> bool:
    """Whether a round chip can be handed to AppKit in this process.

    Answering costs an AppKit import probe, and every chip asks on its first
    show, so the answer is taken once per process.
    """
    global _availability
    if _availability is not None:
        return _availability
    _availability = _probe_native_round_buttons()
    return _availability


def _probe_native_round_buttons() -> bool:
    if sys.platform != "darwin":
        return False
    if os.environ.get(DISABLE_ENV, "").strip().lower() in ("0", "off", "false", "no"):
        return False
    # Headless runs paint the chip themselves even if an app instance was
    # created before the plugin name could be honoured.
    plugin = os.environ.get("QT_QPA_PLATFORM", "").split(":")[0].strip().lower()
    if plugin in ("offscreen", "minimal", "vnc"):
        return False
    if not _cocoa_session():
        return False
    try:
        import AppKit  # noqa: F401
        import objc  # noqa: F401
    except Exception:
        logger.debug("PyObjC/AppKit unavailable; round chips stay Qt-painted", exc_info=True)
        return False
    return True


def _ns_color(value) -> "object":
    """An NSColor from a QColor (or a plain colour string)."""
    from AppKit import NSColor

    color = value if isinstance(value, QColor) else QColor(value)
    if not color.isValid():
        color = QColor("#808080")
    return NSColor.colorWithSRGBRed_green_blue_alpha_(
        color.redF(), color.greenF(), color.blueF(), color.alphaF()
    )


def _macos_major_version() -> int:
    try:
        from AppKit import NSProcessInfo

        # An NSOperatingSystemVersion is a named tuple, not an object with
        # attributes: (27, 2, 0).
        major, _minor, _patch = NSProcessInfo.processInfo().operatingSystemVersion()
        return int(major)
    except Exception:
        return 0


def _glass_available() -> bool:
    """Liquid Glass bezels exist from macOS 26 on."""
    if _macos_major_version() < 26:
        return False
    try:
        from AppKit import NSBezelStyleGlass  # noqa: F401
    except Exception:
        return False
    return True


class NativeRoundChip:
    """An AppKit bevel button living inside a Qt widget's own NSView.

    ``live`` is False until AppKit has the button on screen; until then (and
    whenever attaching fails) the host paints its Qt chip instead. The frame is
    kept symmetric inside the host so AppKit's bottom-left origin and Qt's
    top-left one describe the same rectangle.
    """

    def __init__(self, host):
        self._host = host
        self._button = None
        self._target = None
        self._symbol = "next"
        self._accent = True
        self._disc = QColor()
        self._content = QColor("#ffffff")
        self._tooltip = ""
        self._enabled = True
        self._glass = False
        self._failed = False
        self._filter = None

    # -- state ---------------------------------------------------------
    @property
    def live(self) -> bool:
        return self._button is not None

    def set_symbol(self, symbol: str) -> None:
        self._symbol = symbol or "next"
        if self.live:
            self._apply_image()

    def set_style(self, *, accent: bool, disc, content) -> None:
        """Whether the chip wears the accent fill, and its colours.

        Only the step that moves the install on is filled. A neutral chip
        keeps AppKit's own bezel — the light/graphite disc Safari and Finder
        draw — rather than a hand-tinted one.
        """
        self._accent = bool(accent)
        self._disc = QColor(disc) if disc is not None else QColor()
        self._content = QColor(content) if content is not None else QColor("#ffffff")
        if self.live:
            self._apply_colors()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if self.live:
            self._button.setEnabled_(self._enabled)

    def set_tooltip(self, text: str) -> None:
        self._tooltip = text or ""
        if self.live:
            self._apply_text()

    # -- lifecycle -----------------------------------------------------
    def attach(self) -> bool:
        """Create the AppKit button. Returns True once it is in the view."""
        if self.live:
            return True
        if self._failed:
            return False
        if getattr(self._host, "_companion", None) is not None or getattr(
            self._host, "is_segmented_active", lambda: False
        )():
            return False
        if not native_round_buttons_available():
            # winId() is not an NSView under the offscreen plugin, and asking
            # AppKit to add a subview to it is a segfault rather than an
            # exception. Never reach AppKit without the session check.
            self._failed = True
            return False
        win = self._host.window()
        if win is None or not win.isVisible():
            # Window is not visible yet; do not treat as fatal, retry on show/paint.
            return False
        try:
            self._build()
        except Exception:
            logger.debug("could not attach the native round chip", exc_info=True)
            self._failed = True
            self._button = None
        return self.live

    def _build(self) -> None:
        import objc
        from AppKit import (
            NSBezelStyleCircular,
            NSButton,
            NSFocusRingTypeNone,
            NSImageOnly,
            NSImageScaleProportionallyDown,
        )

        win = self._host.window()
        if win is None or not win.isVisible():
            raise RuntimeError("the host window is not available or not visible yet")

        # Attach to the top-level window's NSView directly.
        # Calling self._host.winId() would turn self._host and its parent Card
        # into native QNSViews with opaque layers, glitching out card materials
        # and creating rectangular bounding boxes around buttons.
        from .glass import _get_nsview
        view = _get_nsview(win)
        if view is None:
            raise RuntimeError("the host window has no NSView yet")

        button = NSButton.alloc().initWithFrame_(self._frame_rect(view))
        self._glass = _glass_available()
        if self._glass:
            from AppKit import NSBezelStyleGlass

            button.setBezelStyle_(NSBezelStyleGlass)
            if hasattr(button, "setBorderShape_"):
                from AppKit import NSControlBorderShapeCircle

                button.setBorderShape_(NSControlBorderShapeCircle)
        else:
            from AppKit import NSBezelStyleTexturedRounded

            button.setBezelStyle_(NSBezelStyleTexturedRounded)
            if hasattr(button, "setBorderShape_"):
                try:
                    from AppKit import NSControlBorderShapeRounded

                    button.setBorderShape_(NSControlBorderShapeRounded)
                except Exception:
                    pass
        button.setBordered_(True)
        button.setTitle_("")
        button.setImagePosition_(NSImageOnly)
        button.setImageScaling_(NSImageScaleProportionallyDown)
        button.setFocusRingType_(NSFocusRingTypeNone)

        target = _chip_target_class().alloc().init()
        target._chip = self
        button.setTarget_(target)
        button.setAction_("chipClicked:")
        self._target = target
        self._button = button

        self._apply_image()
        self._apply_colors()
        self._apply_text()
        button.setEnabled_(self._enabled)
        visible = bool(self._host.isVisible() and self._host.isVisibleTo(win))
        button.setHidden_(not visible)
        view.addSubview_(button)

        self._install_filter()

    def _find_scroll_area(self):
        from PySide6.QtWidgets import QAbstractScrollArea

        p = self._host.parentWidget()
        while p is not None:
            if isinstance(p, QAbstractScrollArea):
                return p
            p = p.parentWidget()
        return None

    def _install_filter(self) -> None:
        if self._filter is not None:
            return

        class _HostFilter(QObject):
            def __init__(self, chip):
                super().__init__(chip._host)
                self._chip = chip

            def eventFilter(self, watched, event):
                if self._chip.live:
                    t = event.type()
                    if t in (
                        QEvent.Type.Move,
                        QEvent.Type.Resize,
                        QEvent.Type.LayoutRequest,
                        QEvent.Type.Paint,
                    ):
                        self._chip.sync_frame()
                    elif t == QEvent.Type.Hide:
                        if self._chip._button is not None:
                            self._chip._button.setHidden_(True)
                    elif t == QEvent.Type.Show:
                        self._chip.sync_frame()
                return False

        self._filter = _HostFilter(self)
        self._host.installEventFilter(self._filter)
        self._scroll_area = self._find_scroll_area()
        if self._scroll_area is not None:
            try:
                self._scroll_area.verticalScrollBar().valueChanged.connect(self.sync_frame)
                self._scroll_area.horizontalScrollBar().valueChanged.connect(self.sync_frame)
                vp = self._scroll_area.viewport()
                if vp is not None:
                    vp.installEventFilter(self._filter)
            except Exception:
                pass

    def sync_frame(self) -> None:
        """Keep the AppKit button on the host's disc as the widget resizes and scrolls."""
        if not self.live:
            return
        if getattr(self._host, "_companion", None) is not None or getattr(
            self._host, "is_segmented_active", lambda: False
        )():
            if self._button is not None:
                self._button.setHidden_(True)
            return
        try:
            win = self._host.window()
            visible = bool(
                win
                and win.isVisible()
                and self._host.isVisible()
                and self._host.isVisibleTo(win)
            )
            if visible:
                scroll_area = getattr(self, "_scroll_area", None)
                if scroll_area is None:
                    scroll_area = self._find_scroll_area()
                    if scroll_area is not None:
                        self._scroll_area = scroll_area
                        try:
                            scroll_area.verticalScrollBar().valueChanged.connect(self.sync_frame)
                            scroll_area.horizontalScrollBar().valueChanged.connect(self.sync_frame)
                            vp = scroll_area.viewport()
                            if vp is not None and self._filter is not None:
                                vp.installEventFilter(self._filter)
                        except Exception:
                            pass
                if scroll_area is not None:
                    vp = scroll_area.viewport()
                    if vp is not None and vp.isVisible():
                        p_in_vp = self._host.mapTo(vp, QPoint(0, 0))
                        host_rect_in_vp = QRect(p_in_vp, self._host.size())
                        if not vp.rect().intersects(host_rect_in_vp):
                            visible = False
            self._button.setHidden_(not visible)
            if visible:
                self._button.setFrame_(self._frame_rect())
        except Exception:
            pass

    def detach(self) -> None:
        if self._filter is not None:
            try:
                self._host.removeEventFilter(self._filter)
            except Exception:
                pass
            if getattr(self, "_scroll_area", None) is not None:
                try:
                    self._scroll_area.verticalScrollBar().valueChanged.disconnect(self.sync_frame)
                    self._scroll_area.horizontalScrollBar().valueChanged.disconnect(self.sync_frame)
                    vp = self._scroll_area.viewport()
                    if vp is not None:
                        vp.removeEventFilter(self._filter)
                except Exception:
                    pass
                self._scroll_area = None
            self._filter = None
        if not self.live:
            return
        try:
            self._button.removeFromSuperview()
        except Exception:
            pass
        self._button = None
        self._target = None

    # -- internals -----------------------------------------------------
    def _frame_rect(self, view=None):
        from AppKit import NSMakeRect
        from PySide6.QtCore import QPoint

        inset = CHIP_INSET_PX
        w = max(1, int(self._host.width()) - inset * 2)
        h = max(1, int(self._host.height()) - inset * 2)

        win = self._host.window()
        if win is None:
            return NSMakeRect(inset, inset, w, h)

        if view is None:
            from .glass import _get_nsview
            view = _get_nsview(win)

        top_left = self._host.mapTo(win, QPoint(0, 0))
        x = float(top_left.x() + inset)
        if view is not None and getattr(view, "isFlipped", lambda: True)():
            y = float(top_left.y() + inset)
        elif view is not None:
            y = float(view.bounds().size.height - (top_left.y() + self._host.height()) + inset)
        else:
            y = float(top_left.y() + inset)
        return NSMakeRect(x, y, w, h)

    def _apply_image(self) -> None:
        from AppKit import NSFontWeightSemibold, NSImage, NSImageSymbolConfiguration

        name = _SF_SYMBOLS.get(self._symbol)
        if not name:
            return
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            name, self._tooltip or self._symbol
        )
        if image is None:
            return
        config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            CHIP_GLYPH_PT, NSFontWeightSemibold
        )
        configured = image.imageWithSymbolConfiguration_(config)
        self._button.setImage_(configured if configured is not None else image)

    def _apply_colors(self) -> None:
        from AppKit import NSColor

        # A circular bezel tints its own glyph from the window's appearance, so
        # the control has to agree with the app's theme when the user has
        # forced one. Only this button's subtree is affected.
        try:
            from .dark import is_dark

            from AppKit import NSAppearance

            name = "NSAppearanceNameDarkAqua" if is_dark() else "NSAppearanceNameAqua"
            appearance = NSAppearance.appearanceNamed_(name)
            if appearance is not None:
                self._button.setAppearance_(appearance)
        except Exception:
            logger.debug("could not match the chip appearance", exc_info=True)
        if self._accent and self._disc.isValid():
            # A filled bezel wears the app's accent; the system still owns the
            # press animation and the glass edge.
            self._button.setBezelColor_(_ns_color(self._disc))
        else:
            try:
                self._button.setBezelColor_(None)
            except Exception:
                logger.debug("could not clear the chip bezel colour", exc_info=True)
        content = self._content if self._content.isValid() else QColor("#ffffff")
        self._button.setContentTintColor_(_ns_color(content))

    def _apply_text(self) -> None:
        self._button.setToolTip_(self._tooltip)
        try:
            self._button.setAccessibilityLabel_(self._tooltip)
        except Exception:
            pass

    def _emit_click(self) -> None:
        """A real click on the AppKit button, delivered as the Qt signal."""
        emit = getattr(self._host, "_native_chip_clicked", None)
        if callable(emit):
            emit()


class NativeSegmentedPair:
    """An AppKit NSSegmentedControl (NSSegmentStyleTexturedRounded).

    Matches the macOS Safari / Finder toolbar back/forward segmented pair aesthetic:
    [ < | > ]
    """

    def __init__(self, host, companion):
        self._host = host
        self._companion = companion
        self._control = None
        self._target = None
        self._failed = False
        self._filter = None

    @property
    def live(self) -> bool:
        return self._control is not None

    def attach(self) -> bool:
        if self.live:
            return True
        if self._failed:
            return False
        if not native_round_buttons_available():
            self._failed = True
            return False
        win = self._host.window()
        if win is None or not win.isVisible():
            return False
        try:
            self._build()
        except Exception:
            logger.debug("could not attach the native segmented pair", exc_info=True)
            self._failed = True
            self._control = None
        return self.live

    def _build(self) -> None:
        from AppKit import (
            NSSegmentedControl,
            NSSegmentSwitchTrackingMomentary,
            NSSegmentStyleTexturedRounded,
        )

        win = self._host.window()
        if win is None or not win.isVisible():
            raise RuntimeError("host window not available or not visible yet")

        from .glass import _get_nsview
        view = _get_nsview(win)
        if view is None:
            raise RuntimeError("host window has no NSView yet")

        frame = self._frame_rect(view)

        control = NSSegmentedControl.alloc().initWithFrame_(frame)
        control.setSegmentCount_(2)
        control.setTrackingMode_(NSSegmentSwitchTrackingMomentary)
        control.setSegmentStyle_(NSSegmentStyleTexturedRounded)

        half_w = float(frame.size.width) / 2.0
        control.setWidth_forSegment_(half_w, 0)
        control.setWidth_forSegment_(half_w, 1)

        target = _segmented_target_class_obj().alloc().init()
        target._pair = self
        control.setTarget_(target)
        control.setAction_("segmentClicked:")
        self._target = target
        self._control = control

        self._sync_content()
        visible = bool(
            self._host.isVisible()
            and self._companion.isVisible()
            and self._host.isVisibleTo(win)
        )
        control.setHidden_(not visible)
        view.addSubview_(control)
        self._install_filter()

    def _sync_content(self) -> None:
        if not self.live:
            return
        from AppKit import (
            NSFontWeightRegular,
            NSImage,
            NSImageSymbolConfiguration,
            NSAppearance,
        )
        from .dark import is_dark

        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            14.0, NSFontWeightRegular
        )

        def _get_img(btn, fallback):
            sym = getattr(btn, "_symbol", "") or fallback
            name = _SEGMENTED_SF_SYMBOLS.get(sym) or _SF_SYMBOLS.get(sym) or "chevron.forward"
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
            if img is not None and hasattr(img, "imageWithSymbolConfiguration_"):
                return img.imageWithSymbolConfiguration_(cfg)
            return img

        img0 = _get_img(self._host, "back")
        img1 = _get_img(self._companion, "next")
        if img0:
            self._control.setImage_forSegment_(img0, 0)
        if img1:
            self._control.setImage_forSegment_(img1, 1)

        self._control.setToolTip_forSegment_(self._host.toolTip() or "", 0)
        self._control.setToolTip_forSegment_(self._companion.toolTip() or "", 1)
        self._control.setEnabled_forSegment_(bool(self._host.isEnabled()), 0)
        self._control.setEnabled_forSegment_(bool(self._companion.isEnabled()), 1)

        try:
            self._control.setAccessibilityLabel_forSegment_(
                self._host.accessibleName() or self._host.toolTip() or "", 0
            )
            self._control.setAccessibilityLabel_forSegment_(
                self._companion.accessibleName() or self._companion.toolTip() or "", 1
            )
        except Exception:
            pass

        try:
            name = "NSAppearanceNameDarkAqua" if is_dark() else "NSAppearanceNameAqua"
            app = NSAppearance.appearanceNamed_(name)
            if app is not None:
                self._control.setAppearance_(app)
        except Exception:
            pass

    def _frame_rect(self, view=None):
        from AppKit import NSMakeRect
        from PySide6.QtCore import QPoint

        win = self._host.window()
        if win is None:
            return NSMakeRect(0, 0, 72, 36)
        if view is None:
            from .glass import _get_nsview
            view = _get_nsview(win)

        p0 = self._host.mapTo(win, QPoint(0, 0))
        p1 = self._companion.mapTo(win, QPoint(self._companion.width(), 0))
        w = max(40.0, float(p1.x() - p0.x()))
        h = max(24.0, float(self._host.height()))

        x = float(p0.x())
        if view is not None and getattr(view, "isFlipped", lambda: True)():
            y = float(p0.y())
        elif view is not None:
            y = float(view.bounds().size.height - (p0.y() + h))
        else:
            y = float(p0.y())
        return NSMakeRect(x, y, float(w), float(h))

    def _find_scroll_area(self):
        from PySide6.QtWidgets import QAbstractScrollArea

        p = self._host.parentWidget()
        while p is not None:
            if isinstance(p, QAbstractScrollArea):
                return p
            p = p.parentWidget()
        return None

    def sync_frame(self) -> None:
        if not self.live:
            return
        try:
            win = self._host.window()
            visible = bool(
                win
                and win.isVisible()
                and self._host.isVisible()
                and self._companion.isVisible()
                and self._host.isVisibleTo(win)
            )
            if visible:
                scroll_area = getattr(self, "_scroll_area", None)
                if scroll_area is None:
                    scroll_area = self._find_scroll_area()
                    if scroll_area is not None:
                        self._scroll_area = scroll_area
                        try:
                            scroll_area.verticalScrollBar().valueChanged.connect(self.sync_frame)
                            scroll_area.horizontalScrollBar().valueChanged.connect(self.sync_frame)
                            vp = scroll_area.viewport()
                            if vp is not None and self._filter is not None:
                                vp.installEventFilter(self._filter)
                        except Exception:
                            pass
                if scroll_area is not None:
                    vp = scroll_area.viewport()
                    if vp is not None and vp.isVisible():
                        p_in_vp = self._host.mapTo(vp, QPoint(0, 0))
                        host_rect_in_vp = QRect(p_in_vp, self._host.size())
                        if not vp.rect().intersects(host_rect_in_vp):
                            visible = False
            self._control.setHidden_(not visible)
            if visible:
                frame = self._frame_rect()
                self._control.setFrame_(frame)
                half_w = float(frame.size.width) / 2.0
                self._control.setWidth_forSegment_(half_w, 0)
                self._control.setWidth_forSegment_(half_w, 1)
                self._sync_content()
        except Exception:
            pass

    def _install_filter(self) -> None:
        if self._filter is not None:
            return

        class _PairFilter(QObject):
            def __init__(self, pair):
                super().__init__(pair._host)
                self._pair = pair

            def eventFilter(self, watched, event):
                if self._pair.live:
                    t = event.type()
                    if t in (
                        QEvent.Type.Move,
                        QEvent.Type.Resize,
                        QEvent.Type.LayoutRequest,
                        QEvent.Type.Paint,
                    ):
                        self._pair.sync_frame()
                    elif t == QEvent.Type.Hide:
                        if self._pair._control is not None:
                            self._pair._control.setHidden_(True)
                    elif t == QEvent.Type.Show:
                        self._pair.sync_frame()
                    elif t == QEvent.Type.EnabledChange:
                        self._pair._sync_content()
                return False

        self._filter = _PairFilter(self)
        self._host.installEventFilter(self._filter)
        self._companion.installEventFilter(self._filter)
        self._scroll_area = self._find_scroll_area()
        if self._scroll_area is not None:
            try:
                self._scroll_area.verticalScrollBar().valueChanged.connect(self.sync_frame)
                self._scroll_area.horizontalScrollBar().valueChanged.connect(self.sync_frame)
                vp = self._scroll_area.viewport()
                if vp is not None:
                    vp.installEventFilter(self._filter)
            except Exception:
                pass

    def _handle_click(self, segment_idx: int) -> None:
        if segment_idx == 0 and self._host and self._host.isEnabled():
            self._host.click()
        elif segment_idx == 1 and self._companion and self._companion.isEnabled():
            self._companion.click()

    def detach(self) -> None:
        if self._filter is not None:
            try:
                self._host.removeEventFilter(self._filter)
                if self._companion is not None:
                    self._companion.removeEventFilter(self._filter)
            except Exception:
                pass
            if getattr(self, "_scroll_area", None) is not None:
                try:
                    self._scroll_area.verticalScrollBar().valueChanged.disconnect(self.sync_frame)
                    self._scroll_area.horizontalScrollBar().valueChanged.disconnect(self.sync_frame)
                    vp = self._scroll_area.viewport()
                    if vp is not None:
                        vp.removeEventFilter(self._filter)
                except Exception:
                    pass
                self._scroll_area = None
            self._filter = None
        if not self.live:
            return
        if self._control is not None:
            try:
                self._control.removeFromSuperview()
            except Exception:
                pass
            self._control = None
        self._target = None


def native_combo_menus_available() -> bool:
    """Whether a combo can open the macOS menu itself.

    The same conditions as the chips: a real cocoa session with AppKit
    reachable. The popup Qt draws for a QComboBox is a painted hard-edged
    rectangle on macOS, so this is how a dropdown gets the host's own menu
    material, rounding, checkmark and keyboard navigation.
    """
    return native_round_buttons_available()


_menu_target_class = None


def _menu_target_class_factory():
    global _menu_target_class
    if _menu_target_class is not None:
        return _menu_target_class
    import objc
    from AppKit import NSObject

    try:
        cls = objc.lookUpClass("InnioasisComboMenuTarget")
    except objc.nosuchclass_error:
        cls = None
    if cls is None:

        class InnioasisComboMenuTarget(NSObject):
            def pick_(self, sender):  # noqa: N802 (ObjC selector)
                owner = getattr(self, "_popup", None)
                if owner is not None:
                    owner._chosen = int(sender.tag())

        cls = InnioasisComboMenuTarget
    _menu_target_class = cls
    return cls


class _MenuPopup:
    """One NSMenu built from a QComboBox, and the choice it returned."""

    def __init__(self, combo):
        self._combo = combo
        self._chosen = None
        self._menu = None
        self._target = None

    def build(self) -> None:
        from AppKit import NSMenu, NSMenuItem

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        target = _menu_target_class_factory().alloc().init()
        target._popup = self
        self._target = target
        model = self._combo.model()
        count = self._combo.count()
        current = self._combo.currentIndex()
        for index in range(count):
            label = str(self._combo.itemText(index) or "")
            flags = model.flags(model.index(index, 0)) if model is not None else Qt.ItemFlag(0)
            enabled = bool(flags & Qt.ItemFlag.ItemIsEnabled)
            if not label.strip() and not enabled:
                menu.addItem_(NSMenuItem.separatorItem())
                continue
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "pick:", "")
            item.setTarget_(target)
            item.setTag_(index)
            item.setEnabled_(enabled)
            if index == current:
                item.setState_(1)  # NSControlStateValueOn: the tick on the current item
            menu.addItem_(item)
        self._menu = menu

    def popup(self) -> None:
        """Show at the combo's bottom-left and block until AppKit dismisses it."""
        from AppKit import NSMakePoint
        from PySide6.QtCore import QPoint

        win = self._combo.window()
        from .glass import _get_nsview
        view = _get_nsview(win) if win is not None else None
        if view is None:
            self._combo.showPopup()
            return

        # Use the top-level window's NSView directly. Calling self._combo.winId()
        # would force self._combo and its parent Card into native QNSViews with
        # opaque layers, glitching out card materials and losing transparency.
        pos = self._combo.mapTo(win, QPoint(0, self._combo.height()))
        x = float(pos.x())
        if getattr(view, "isFlipped", lambda: True)():
            y = float(pos.y())
        else:
            y = float(view.bounds().size.height - pos.y())

        self._menu.popUpMenuPositioningItem_atLocation_inView_(
            None, NSMakePoint(x, y), view
        )

    def apply_choice(self) -> None:
        if self._chosen is None or self._chosen != self._combo.currentIndex():
            if self._chosen is not None:
                self._combo.setCurrentIndex(self._chosen)


class _NativeComboFilter(QObject):
    """Sends a combo's press to AppKit's menu instead of Qt's painted popup."""

    _instance = None

    _KEYS = (
        Qt.Key.Key_Space,
        Qt.Key.Key_Return,
        Qt.Key.Key_Enter,
        Qt.Key.Key_Down,
        Qt.Key.Key_F4,
    )

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        try:
            # Cheap rejects first: an app-wide filter sees every event for
            # every object, including widgets that are still being built. A
            # QComboBox method called on one of those is a segfault, not an
            # exception, so nothing touches the combo until the event is one
            # the combo can only receive once it is alive.
            kind = event.type()
            if kind == QEvent.Type.MouseButtonPress:
                if event.button() != Qt.MouseButton.LeftButton:
                    return False
            elif kind == QEvent.Type.KeyPress:
                if event.key() not in self._KEYS:
                    return False
            else:
                return False
            if not isinstance(watched, QComboBox) or watched.isEditable():
                return False
            if watched.count() <= 0 or not watched.isEnabled():
                return False
            # AppKit's menu runs its own tracking loop. Starting it inside Qt's
            # event dispatch re-enters Qt mid-delivery (and crashes); one event
            # loop turn later the press is finished and the menu is safe.
            QTimer.singleShot(0, lambda c=watched: self._popup(c))
            return True
        except Exception:
            logger.debug("native combo popup failed", exc_info=True)
        return False

    def _popup(self, combo) -> bool:
        from PySide6.QtCore import QTimer as _QTimer  # noqa: F401  (local import kept light)

        try:
            popup = _MenuPopup(combo)
            popup.build()
            popup.popup()
            popup.apply_choice()
        except Exception:
            logger.debug("native combo menu could not open", exc_info=True)
            combo.showPopup()
            return False
        return True


def install_native_combo_menus(app) -> bool:
    """Route every QComboBox's press to the macOS menu. Idempotent."""
    if app is None or not native_combo_menus_available():
        return False
    filt = _NativeComboFilter.instance()
    if getattr(app, "_native_combo_filter", None) is filt:
        return True
    try:
        app.installEventFilter(filt)
    except Exception:
        logger.debug("could not install the native combo filter", exc_info=True)
        return False
    app._native_combo_filter = filt
    return True
