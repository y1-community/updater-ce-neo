"""OS-standard iconography provider.

Integrates native system iconography across desktop operating systems:
- macOS: Apple SF Symbols via AppKit (NSImage.imageWithSystemSymbolName:accessibilityDescription:)
- Windows: MS Segoe Symbols (Segoe Fluent Icons / Segoe MDL2 Assets) via system font glyphs
- Linux: FreeDesktop standard theme icons via QIcon.fromTheme()
- Universal: Handcrafted high-DPI vector SVGs guaranteeing crisp rendering across all platforms
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Optional

from PySide6.QtCore import QByteArray, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontInfo, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

logger = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32" or platform.system() == "Windows"


def tint_pixmap(pixmap: QPixmap, color: str | QColor) -> QPixmap:
    """Tint a monochrome glyph or icon pixmap to target color preserving alpha and DPI."""
    if pixmap.isNull():
        return pixmap
    qcolor = QColor(color) if isinstance(color, str) else color
    dpr = pixmap.devicePixelRatio()
    tinted = QPixmap(pixmap.size())
    tinted.setDevicePixelRatio(dpr)
    tinted.fill(Qt.transparent)
    p = QPainter(tinted)
    p.drawPixmap(0, 0, pixmap)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(tinted.rect(), qcolor)
    p.end()
    return tinted

# Fallback clean vector SVG definitions (stroke: currentColor / white)
_SVG_TEMPLATES = {
    "install": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="7 10 12 15 17 10"/>
        <line x1="12" y1="15" x2="12" y2="3"/>
    </svg>""",
    "cancel": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"/>
        <line x1="6" y1="6" x2="18" y2="18"/>
    </svg>""",
    "settings": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>""",
    "translate": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="2" y1="12" x2="22" y2="12"/>
        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
    </svg>""",
    "diagnostics": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
    </svg>""",
    "update": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="23 4 23 10 17 10"/>
        <polyline points="1 20 1 14 7 14"/>
        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
    </svg>""",
    "support": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
    </svg>""",
    "tools": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
    </svg>""",
    "file": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
        <polyline points="13 2 13 9 20 9"/>
    </svg>""",
    "folder": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
    </svg>""",
    "complete": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="9" fill="{color}" stroke="none"/>
        <polyline points="8 12.5 11 15.5 16.5 9" stroke="#ffffff"/>
    </svg>""",
}

# Platform mappings
_SF_SYMBOL_MAP = {
    "install": "arrow.down.circle",
    "cancel": "xmark",
    "settings": "gearshape",
    "translate": "globe",
    "diagnostics": "waveform.path.ecg",
    "update": "arrow.triangle.2.circlepath",
    "support": "heart",
    "tools": "wrench.and.screwdriver",
    "file": "doc",
    "folder": "folder",
    "complete": "checkmark.circle.fill",
}

_SEGOE_SYMBOL_MAP = {
    "install": 0xE896,     # Download
    "cancel": 0xE711,      # ChromeClose
    "settings": 0xE713,    # Settings
    "translate": 0xE774,   # Globe
    "diagnostics": 0xEC7A, # Diagnostic
    "update": 0xE72C,      # Refresh
    "support": 0xEB51,     # Heart
    "tools": 0xE756,       # Repair
    "file": 0xE8A5,        # Document
    "folder": 0xED25,      # FolderOpen
    "complete": 0xE73E,    # CheckMark
}

_FREEDESKTOP_MAP = {
    "install": ["system-software-install-symbolic", "system-software-install", "document-save-symbolic", "document-save"],
    "cancel": ["process-stop-symbolic", "process-stop", "dialog-cancel-symbolic", "dialog-cancel"],
    "settings": ["preferences-system-symbolic", "preferences-system", "settings-configure-symbolic", "settings-configure"],
    "translate": ["preferences-desktop-locale-symbolic", "preferences-desktop-locale", "locale-symbolic", "locale"],
    "diagnostics": ["utilities-system-monitor-symbolic", "utilities-system-monitor", "system-run-symbolic", "system-run"],
    "update": ["view-refresh-symbolic", "view-refresh", "system-software-update-symbolic", "system-software-update"],
    "support": ["emblem-favorite-symbolic", "emblem-favorite", "love"],
    "tools": ["applications-utilities-symbolic", "applications-utilities", "system-run-symbolic", "system-run"],
    "file": ["text-x-generic-symbolic", "text-x-generic", "document-symbolic", "document"],
    "folder": ["folder-open-symbolic", "folder-open", "folder-symbolic", "folder"],
    "complete": ["emblem-ok-symbolic", "emblem-ok", "object-select-symbolic", "dialog-ok"],
}

_segoe_font_family: Optional[str] = None


def _detect_segoe_font() -> Optional[str]:
    """Detect available Segoe symbol font on Windows."""
    global _segoe_font_family
    if _segoe_font_family is not None:
        return _segoe_font_family

    for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        font = QFont(family)
        if QFontInfo(font).exactMatch():
            _segoe_font_family = family
            return family
    _segoe_font_family = ""
    return None


def _get_sf_symbol_pixmap(symbol_name: str, size: int, color: str = "#FFFFFF") -> Optional[QPixmap]:
    """Load native Apple SF Symbol on macOS and tint to target color with Retina clarity."""
    if not IS_MAC:
        return None
    sf_name = _SF_SYMBOL_MAP.get(symbol_name)
    if not sf_name:
        return None

    try:
        from AppKit import NSImage, NSImageSymbolConfiguration

        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sf_name, None)
        if not img:
            return None
        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(float(size), 5)  # Medium weight
        configured = img.imageWithSymbolConfiguration_(cfg) if hasattr(img, "imageWithSymbolConfiguration_") else img
        tiff = (configured or img).TIFFRepresentation()
        if not tiff:
            return None
        qimg = QImage.fromData(bytes(tiff))
        if qimg.isNull():
            return None
        target_px = size * 2
        raw_pix = QPixmap.fromImage(qimg).scaled(
            target_px, target_px, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        raw_pix.setDevicePixelRatio(2.0)
        return tint_pixmap(raw_pix, color)
    except Exception as e:
        logger.debug("Failed to load SF Symbol %s: %s", sf_name, e)
        return None


def _get_segoe_pixmap(symbol_name: str, size: int, color: str = "#FFFFFF") -> Optional[QPixmap]:
    """Render an MS Segoe symbol glyph on Windows, crisp and unclipped.

    The glyph is drawn on a 4x canvas and scaled down. Drawing straight into a
    small box clipped the taller Segoe glyphs (the font's em box is bigger than
    the requested pixmap), which is why only parts of a symbol showed.
    """
    if not IS_WINDOWS:
        return None
    code = _SEGOE_SYMBOL_MAP.get(symbol_name)
    if not code:
        return None

    family = _detect_segoe_font()
    if not family:
        return None

    try:
        canvas = max(int(size), 1) * 4
        pix = QPixmap(canvas, canvas)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        font = QFont(family)
        font.setStyleStrategy(QFont.PreferAntialias)
        # Leave headroom in the box so the full em box (ascent + descent, plus
        # any glyph overhang) always fits instead of being cropped.
        font.setPixelSize(max(1, int(canvas * 0.78)))
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRect(0, 0, canvas, canvas), Qt.AlignCenter, chr(code))
        painter.end()

        out = pix.scaled(
            max(int(size), 1) * 2, max(int(size), 1) * 2,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        out.setDevicePixelRatio(2.0)
        return out
    except Exception as e:
        logger.debug("Failed to render Segoe symbol %s: %s", symbol_name, e)
        return None


def _get_freedesktop_pixmap(symbol_name: str, size: int, color: str = "#FFFFFF") -> Optional[QPixmap]:
    """Query standard FreeDesktop theme icons on Linux and tint symbolic icons."""
    if IS_MAC or IS_WINDOWS:
        return None
    names = _FREEDESKTOP_MAP.get(symbol_name, [])
    for name in names:
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            # Ask for a 2x pixmap and mark it hi-DPI: requesting `size` alone
            # gave a blurry, partly-clipped icon on scaled desktops.
            pix = icon.pixmap(QSize(max(int(size), 1) * 2, max(int(size), 1) * 2))
            if not pix.isNull():
                pix.setDevicePixelRatio(2.0)
                if "symbolic" in name:
                    return tint_pixmap(pix, color)
                return pix
    return None


def _get_svg_pixmap(symbol_name: str, size: int, color: str = "#FFFFFF") -> QPixmap:
    """Render bundled vector SVG fallback in target color at 2x high-DPI."""
    template = _SVG_TEMPLATES.get(symbol_name, _SVG_TEMPLATES["settings"])
    svg_code = template.format(color=color).encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg_code))
    px_size = size * 2
    pix = QPixmap(px_size, px_size)
    pix.setDevicePixelRatio(2.0)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return pix


def resolve_symbol_colors(
    color: Optional[str] = None,
    focused_color: Optional[str] = None,
    match_accent: bool = False,
) -> tuple[str, str]:
    """Resolve (normal_color, focused_color) based on system appearance and accent color.

    Normal state:
    - If color provided: uses color.
    - Otherwise if match_accent: colour-matched to accent color (e.g. green or OS accent).
    - Otherwise: white (#FFFFFF) in dark mode, dark (#111827) in light mode.

    Focused / active state:
    - If focused_color provided: uses focused_color.
    - If color was explicitly provided and focused_color not: uses color.
    - Otherwise: white (#FFFFFF) in dark mode, black/white matching nav_active_text in light mode.
    """
    try:
        from .dark import T, is_dark
        t = T()
        dark = is_dark()
        accent = getattr(t, "accent", "#52b036")
        active_text = getattr(t, "nav_active_text", "#FFFFFF" if dark else "#000000")
    except Exception:
        dark = True
        accent = "#52b036"
        active_text = "#FFFFFF"

    if color is not None:
        norm = color
    elif match_accent and accent:
        norm = accent
    else:
        norm = "#FFFFFF" if dark else "#111827"

    if focused_color is not None:
        foc = focused_color
    elif color is not None:
        foc = color
    else:
        foc = "#FFFFFF" if dark else active_text

    return norm, foc


def get_symbol_pixmap(
    symbol_name: str,
    size: int = 16,
    color: Optional[str] = None,
    match_accent: bool = False,
) -> QPixmap:
    """Return a crisp QPixmap for the requested symbol adapting to host OS and theme color."""
    norm_color, _ = resolve_symbol_colors(color=color, match_accent=match_accent)

    # 1. macOS: Apple SF Symbols
    if IS_MAC:
        mac_pix = _get_sf_symbol_pixmap(symbol_name, size, norm_color)
        if mac_pix and not mac_pix.isNull():
            return mac_pix

    # 2. Windows: MS Segoe Symbols
    if IS_WINDOWS:
        win_pix = _get_segoe_pixmap(symbol_name, size, norm_color)
        if win_pix and not win_pix.isNull():
            return win_pix

    # 3. Linux: FreeDesktop theme
    if not IS_MAC and not IS_WINDOWS:
        linux_pix = _get_freedesktop_pixmap(symbol_name, size, norm_color)
        if linux_pix and not linux_pix.isNull():
            return linux_pix

    # 4. Universal high-DPI vector SVG fallback
    return _get_svg_pixmap(symbol_name, size, norm_color)


def get_symbol_icon(
    symbol_name: str,
    size: int = 16,
    color: Optional[str] = None,
    focused_color: Optional[str] = None,
    match_accent: bool = False,
) -> QIcon:
    """Return a multi-state QIcon adapting to host OS, dark/light mode, and focus/checked states.

    - Unfocused / normal: colour-matched to accent colour (or white in dark mode, dark in light mode).
    - Focused / checked: displayed in white (dark mode) or black/white (light mode depending on accent contrast).
    - Disabled: muted disabled tone.
    """
    norm_color, foc_color = resolve_symbol_colors(
        color=color,
        focused_color=focused_color,
        match_accent=match_accent,
    )

    # 1. Normal pixmap (unfocused / unchecked)
    pix_norm = get_symbol_pixmap(symbol_name, size=size, color=norm_color, match_accent=False)

    # 2. Focused pixmap (checked / highlighted / focused)
    pix_foc = get_symbol_pixmap(symbol_name, size=size, color=foc_color, match_accent=False)

    # 3. Disabled pixmap
    try:
        from .dark import T
        disabled_col = getattr(T(), "disabled_fg", "#64748b")
    except Exception:
        disabled_col = "#64748b"
    pix_dis = get_symbol_pixmap(symbol_name, size=size, color=disabled_col, match_accent=False)

    icon = QIcon()
    # 1. Normal state (unfocused, unchecked, unhighlighted) -> norm_color
    icon.addPixmap(pix_norm, QIcon.Mode.Normal, QIcon.State.Off)

    # 2. Highlighted / hovered / active state -> foc_color (white in dark mode, black/white in light mode)
    icon.addPixmap(pix_foc, QIcon.Mode.Active, QIcon.State.Off)

    # 3. Selected / focused state
    icon.addPixmap(pix_foc, QIcon.Mode.Selected, QIcon.State.Off)
    icon.addPixmap(pix_foc, QIcon.Mode.Selected, QIcon.State.On)

    # 4. Checked (State.On) -> foc_color (e.g. White on solid accent fill)
    icon.addPixmap(pix_foc, QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(pix_foc, QIcon.Mode.Active, QIcon.State.On)

    # 5. Disabled
    icon.addPixmap(pix_dis, QIcon.Mode.Disabled, QIcon.State.Off)
    icon.addPixmap(pix_dis, QIcon.Mode.Disabled, QIcon.State.On)

    return icon


def get_symbol_data_uri(
    symbol_name: str,
    size: int = 14,
    color: Optional[str] = None,
    match_accent: bool = False,
) -> str:
    """Return an inline data:image/png;base64,... URI for embedding in rich text HTML labels."""
    from PySide6.QtCore import QBuffer, QIODevice

    pix = get_symbol_pixmap(symbol_name, size=size, color=color, match_accent=match_accent)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "PNG")
    b64 = bytes(buf.data().toBase64()).decode("ascii")
    return f"data:image/png;base64,{b64}"

