"""Device install tracking and release reminder management.

Tracks the last firmware package and release tag installed on a user's
Innioasis Y1 and Y2 players via persistent QSettings. Provides functions
to check for newer releases relative to currently installed versions and
manages preferences for device reminders and donation UI visibility.
"""

from datetime import datetime
import logging
from typing import Optional

from PySide6.QtCore import QSettings

from . import catalog
from .config import DEVICE_MODELS, device_label_for_model

logger = logging.getLogger(__name__)

# Setting keys
_GROUP_INSTALLS = "device_installs"
_GROUP_PREFS = "preferences"
_GROUP_LATEST_PACKAGE = "latest_package"
_KEY_REMINDER_PREFIX = "reminders_enabled_"
_KEY_LAST_NOTIFIED_PREFIX = "last_notified_tag_"
_KEY_DONATION_UI_DISABLED = "donation_ui_disabled"
_KEY_DONATION_INSTALL_PROMPT_DISABLED = "donation_install_prompt_disabled"


def _get_settings(settings: Optional[QSettings] = None) -> QSettings:
    return settings if settings is not None else QSettings("innioasis", "updater")


# ---------------------------------------------------------------------------
# Device Installation Records
# ---------------------------------------------------------------------------

def record_device_install(
    model: str,
    software_name: str,
    tag_name: str,
    release_label: str = "",
    package_slug: str = "",
    settings: Optional[QSettings] = None,
) -> None:
    """Record that ``software_name`` with ``tag_name`` was installed on ``model``."""
    if not model or not tag_name:
        return
    s = _get_settings(settings)
    now_iso = datetime.now().isoformat()
    s.beginGroup(f"{_GROUP_INSTALLS}/{model}")
    try:
        s.setValue("software_name", software_name or "")
        s.setValue("tag_name", tag_name or "")
        s.setValue("release_label", release_label or tag_name or "")
        s.setValue("package_slug", package_slug or "")
        s.setValue("installed_at", now_iso)
    finally:
        s.endGroup()
    logger.info("Recorded install for %s: %s (%s)", model, software_name, tag_name)


def get_device_install(model: str, settings: Optional[QSettings] = None) -> Optional[dict]:
    """Return the last recorded install for ``model``, or None if none recorded."""
    if not model:
        return None
    s = _get_settings(settings)
    s.beginGroup(f"{_GROUP_INSTALLS}/{model}")
    try:
        tag_name = s.value("tag_name", "", type=str)
        if not tag_name:
            return None
        return {
            "model": model,
            "software_name": s.value("software_name", "", type=str),
            "tag_name": tag_name,
            "release_label": s.value("release_label", "", type=str) or tag_name,
            "package_slug": s.value("package_slug", "", type=str),
            "installed_at": s.value("installed_at", "", type=str),
        }
    finally:
        s.endGroup()


def clear_device_install(model: str, settings: Optional[QSettings] = None) -> None:
    """Clear recorded install history for ``model``."""
    if not model:
        return
    s = _get_settings(settings)
    s.beginGroup(f"{_GROUP_INSTALLS}/{model}")
    try:
        s.remove("")
    finally:
        s.endGroup()
    logger.info("Cleared install history for %s", model)


def get_all_device_installs(settings: Optional[QSettings] = None) -> dict:
    """Return dictionary of {model: install_record} for all tracked models."""
    out = {}
    for m in DEVICE_MODELS:
        rec = get_device_install(m, settings=settings)
        if rec:
            out[m] = rec
    return out


# ---------------------------------------------------------------------------
# Reminders & Preferences
# ---------------------------------------------------------------------------

def is_device_reminder_enabled(model: str, settings: Optional[QSettings] = None) -> bool:
    """True if release reminders are enabled for ``model`` (default True)."""
    s = _get_settings(settings)
    return s.value(f"{_GROUP_PREFS}/{_KEY_REMINDER_PREFIX}{model}", True, type=bool)


def set_device_reminder_enabled(model: str, enabled: bool, settings: Optional[QSettings] = None) -> None:
    """Set whether release reminders are enabled for ``model``."""
    s = _get_settings(settings)
    s.setValue(f"{_GROUP_PREFS}/{_KEY_REMINDER_PREFIX}{model}", bool(enabled))


def get_last_notified_tag(model: str, settings: Optional[QSettings] = None) -> str:
    """Return the last release tag user was notified about for ``model``."""
    s = _get_settings(settings)
    return s.value(f"{_GROUP_PREFS}/{_KEY_LAST_NOTIFIED_PREFIX}{model}", "", type=str)


def set_last_notified_tag(model: str, tag: str, settings: Optional[QSettings] = None) -> None:
    """Save the last release tag user was notified about for ``model``."""
    s = _get_settings(settings)
    s.setValue(f"{_GROUP_PREFS}/{_KEY_LAST_NOTIFIED_PREFIX}{model}", tag or "")


def is_donation_ui_disabled(settings: Optional[QSettings] = None) -> bool:
    """True if donor names, Thank You screens, and donation prompts are hidden."""
    s = _get_settings(settings)
    val = s.value(_KEY_DONATION_UI_DISABLED, None)
    if val is not None:
        return s.value(_KEY_DONATION_UI_DISABLED, False, type=bool)
    return s.value(f"{_GROUP_PREFS}/{_KEY_DONATION_UI_DISABLED}", False, type=bool)


def set_donation_ui_disabled(disabled: bool, settings: Optional[QSettings] = None) -> None:
    """Set whether donor recognition and donation UI are hidden."""
    s = _get_settings(settings)
    s.setValue(_KEY_DONATION_UI_DISABLED, bool(disabled))
    s.setValue(f"{_GROUP_PREFS}/{_KEY_DONATION_UI_DISABLED}", bool(disabled))


def is_donation_install_prompt_disabled(settings: Optional[QSettings] = None) -> bool:
    """True if post-installation donation prompts should be skipped."""
    s = _get_settings(settings)
    if is_donation_ui_disabled(settings):
        return True
    val = s.value(_KEY_DONATION_INSTALL_PROMPT_DISABLED, None)
    if val is not None:
        return s.value(_KEY_DONATION_INSTALL_PROMPT_DISABLED, False, type=bool)
    return s.value(f"{_GROUP_PREFS}/{_KEY_DONATION_INSTALL_PROMPT_DISABLED}", False, type=bool)


def set_donation_install_prompt_disabled(disabled: bool, settings: Optional[QSettings] = None) -> None:
    """Set whether post-installation donation prompt is skipped."""
    s = _get_settings(settings)
    s.setValue(_KEY_DONATION_INSTALL_PROMPT_DISABLED, bool(disabled))
    s.setValue(f"{_GROUP_PREFS}/{_KEY_DONATION_INSTALL_PROMPT_DISABLED}", bool(disabled))


# ---------------------------------------------------------------------------
# Latest Downloaded / Attempted Package Tracking
# ---------------------------------------------------------------------------

def record_latest_package(
    model: str,
    software_name: str,
    tag_name: str,
    package_path: str,
    extract_dir: str = "",
    scatter_path: str = "",
    settings: Optional[QSettings] = None,
) -> None:
    """Record the most recently attempted or downloaded firmware package details."""
    s = _get_settings(settings)
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/model", model or "")
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/software_name", software_name or "")
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/tag_name", tag_name or "")
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/package_path", package_path or "")
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/extract_dir", extract_dir or "")
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/scatter_path", scatter_path or "")
    s.setValue(f"{_GROUP_LATEST_PACKAGE}/timestamp", datetime.now().isoformat())


def get_latest_package(settings: Optional[QSettings] = None) -> Optional[dict]:
    """Retrieve the most recently attempted or downloaded firmware package details."""
    s = _get_settings(settings)
    pkg_path = s.value(f"{_GROUP_LATEST_PACKAGE}/package_path", "", type=str)
    if not pkg_path:
        return None
    return {
        "model": s.value(f"{_GROUP_LATEST_PACKAGE}/model", "", type=str),
        "software_name": s.value(f"{_GROUP_LATEST_PACKAGE}/software_name", "", type=str),
        "tag_name": s.value(f"{_GROUP_LATEST_PACKAGE}/tag_name", "", type=str),
        "package_path": pkg_path,
        "extract_dir": s.value(f"{_GROUP_LATEST_PACKAGE}/extract_dir", "", type=str),
        "scatter_path": s.value(f"{_GROUP_LATEST_PACKAGE}/scatter_path", "", type=str),
        "timestamp": s.value(f"{_GROUP_LATEST_PACKAGE}/timestamp", "", type=str),
    }


def clear_latest_package(settings: Optional[QSettings] = None) -> None:
    """Clear recorded latest package details."""
    s = _get_settings(settings)
    s.remove(_GROUP_LATEST_PACKAGE)


# ---------------------------------------------------------------------------
# Release Update Checking
# ---------------------------------------------------------------------------

def check_device_updates(
    releases_client: Optional[catalog.ReleasesClient] = None,
    settings: Optional[QSettings] = None,
    ignore_last_notified: bool = False,
) -> list:
    """Check whether newer releases exist for recorded Y1 or Y2 installations.

    Returns a list of update detail dicts:
    [{
        "model": model,
        "software_name": str,
        "package": FirmwarePackage,
        "installed_tag": str,
        "installed_label": str,
        "installed_at": str,
        "latest_release": dict,
        "latest_tag": str,
        "latest_label": str,
    }, ...]
    """
    client = releases_client or catalog.ReleasesClient()
    updates = []

    models_to_check = list(DEVICE_MODELS)
    for model in models_to_check:
        if not is_device_reminder_enabled(model, settings=settings):
            continue

        install = get_device_install(model, settings=settings)
        if not install or not install.get("tag_name"):
            continue

        installed_tag = install["tag_name"]
        slug = install.get("package_slug")
        sw_name = install.get("software_name")

        # Locate the corresponding package
        packages = catalog.packages_for_model(model)
        matched_pkg = None
        if slug:
            for p in packages:
                if p.slug == slug:
                    matched_pkg = p
                    break
        if not matched_pkg and sw_name:
            for p in packages:
                if p.name.lower() == sw_name.lower():
                    matched_pkg = p
                    break
        if not matched_pkg:
            continue

        try:
            releases = client.releases_for_package(matched_pkg, model, show_nightly=False)
        except Exception as e:
            logger.debug("Failed checking releases for %s on %s: %s", matched_pkg.name, model, e)
            continue

        if not releases:
            continue

        latest_rel = releases[0]
        latest_tag = latest_rel.get("tag_name", "")
        if not latest_tag or latest_tag == installed_tag:
            continue

        # Check if already notified about this exact tag
        last_notified = get_last_notified_tag(model, settings=settings)
        if not ignore_last_notified and last_notified == latest_tag:
            continue

        # Compare chronological sorting: newer release has a higher release_sort_key
        installed_pseudo_rel = {
            "tag_name": installed_tag,
            "published_at": install.get("installed_at", ""),
        }
        latest_key = catalog.release_sort_key(latest_rel)
        installed_key = catalog.release_sort_key(installed_pseudo_rel)

        if latest_key > installed_key:
            updates.append({
                "model": model,
                "software_name": matched_pkg.name,
                "package": matched_pkg,
                "installed_tag": installed_tag,
                "installed_label": install.get("release_label") or installed_tag,
                "installed_at": install.get("installed_at", ""),
                "latest_release": latest_rel,
                "latest_tag": latest_tag,
                "latest_label": catalog.format_release_display_label(latest_rel),
            })

    return updates
