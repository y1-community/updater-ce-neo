"""Branding and build flavor helpers for Updater CE and MediaTek Installer."""

from .config import (
    APP_NAME,
    APP_VERSION,
    CONTACT_EMAIL,
    build_brand,
    get_app_name,
    is_generic_mtk,
    is_mediatek_installer,
    is_offline_mode,
)


def is_generic_mtk_brand() -> bool:
    """Return True if running under generic MediaTek Installer branding."""
    return bool(is_mediatek_installer() or is_generic_mtk())
