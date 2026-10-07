"""Device model registry (``assets/device_models.xml``).

Two different questions must not be conflated:

* **What the user may pick** — the Select Software device drop-down offers only
  models that have a release in the live catalogue (``slidia_manifest.xml``).
  Offering a device with nothing to install just leads to a dead end. See
  :func:`selectable_models`.
* **What the app can recognise** — guidance has to name the right player even
  for firmware the user dropped in by hand, including models with no catalogue
  entry yet. The XML registry is that wider set, and
  :func:`model_from_filename` maps an imported ``..._g5.zip`` onto the
  Innioasis G5 instead of falling back to a generic prompt.

The registry is data, not code: extending the lineup means editing
``assets/device_models.xml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from . import paths

logger = logging.getLogger(__name__)

_DATA_FILENAME = "device_models.xml"

# Models whose short id is already the user-facing name in every existing
# prompt and translation ("Please make sure your Y1 is powered off..."). Newer
# models use their registry product name, so guidance reads "Innioasis G5"
# rather than "G5".
_LEGACY_SHORT_IDS = ("Y1", "Y2", "A5")

_cache: tuple | None = None


@dataclass(frozen=True)
class DeviceVariant:
    """A hardware revision sharing a model id (e.g. Y1 Type A / Type B)."""

    id: str
    label: str = ""
    tokens: tuple = ()


@dataclass(frozen=True)
class DeviceModel:
    """One known player model from the registry."""

    id: str
    brand: str = ""
    product: str = ""
    flashable: bool = False
    platform: str = "unknown"
    tokens: tuple = ()
    aliases: tuple = ()
    variants: tuple = ()
    notes: str = ""


def data_path() -> Path | None:
    """Locate ``device_models.xml`` in a dev checkout or a frozen bundle."""
    for base in (paths.RESOURCES_DIR, paths.REPO_ROOT / "assets"):
        candidate = base / _DATA_FILENAME
        if candidate.exists():
            return candidate
    return None


def _token_list(text: str) -> tuple:
    return tuple(t.strip().lower() for t in str(text or "").split(",") if t.strip())


def _parse_model(node) -> DeviceModel:
    variants = []
    for vnode in node.findall("./variants/variant"):
        variants.append(
            DeviceVariant(
                id=(vnode.get("id") or "").strip(),
                label=(vnode.get("label") or "").strip(),
                tokens=_token_list(vnode.get("tokens", "")),
            )
        )
    return DeviceModel(
        id=(node.get("id") or "").strip(),
        brand=(node.get("brand") or "").strip(),
        product=(node.get("product") or "").strip(),
        flashable=(node.get("flashable", "").strip().lower() == "true"),
        platform=((node.findtext("platform") or "unknown").strip()),
        tokens=tuple(
            (t.text or "").strip().lower()
            for t in node.findall("./tokens/token")
            if (t.text or "").strip()
        ),
        aliases=tuple(
            (a.text or "").strip()
            for a in node.findall("./aliases/alias")
            if (a.text or "").strip()
        ),
        variants=tuple(variants),
        notes=(node.findtext("notes") or "").strip(),
    )


def load_models(force: bool = False) -> tuple:
    """Parse the registry, cached after first use.

    Returns a tuple of :class:`DeviceModel` in document order. An unreadable or
    missing file yields an empty tuple rather than raising: the app must still
    run with the static catalogue if the data file is absent.
    """
    global _cache
    if _cache is not None and not force:
        return _cache
    models = ()
    path = data_path()
    if path is None:
        logger.debug("device_models.xml not found; registry disabled")
    else:
        try:
            root = ET.parse(str(path)).getroot()
            models = tuple(_parse_model(n) for n in root.findall("./model"))
        except Exception:
            logger.warning("Could not parse %s", path, exc_info=True)
            models = ()
    _cache = models
    return models


def all_models() -> tuple:
    return load_models()


def model_ids() -> list:
    return [m.id for m in all_models()]


def get(model_id: str) -> DeviceModel | None:
    wanted = str(model_id or "").strip().upper()
    if not wanted:
        return None
    for model in all_models():
        if model.id.upper() == wanted:
            return model
        for alias in model.aliases:
            if alias.upper() == wanted:
                return model
    return None


def guidance_name(model_id: str) -> str:
    """User-facing name for prompts; falls back to the raw id when unknown."""
    known = get(model_id)
    if known is None:
        return str(model_id or "")
    if known.id.upper() in _LEGACY_SHORT_IDS:
        return known.id
    return known.product or known.id


def is_flashable(model_id: str) -> bool:
    """True when Updater CE can write firmware to this model itself."""
    known = get(model_id)
    return bool(known and known.flashable)


def model_from_filename(name: str, exclude=()) -> tuple:
    """Detect ``(model_id, variant_id)`` from a firmware package name.

    ``exclude`` skips ids whose detection is already handled elsewhere (the
    legacy Y1/Y2/A5 rules in :mod:`src.config`), so this only has to recognise
    the wider lineup. Longer tokens win, so ``rom_a5.zip`` is never mistaken
    for a Y1 on the strength of a generic token.
    """
    text = str(name or "").lower().replace("\\", "/")
    if not text:
        return "", None
    skip = {str(e).strip().upper() for e in exclude}
    best = None  # (token_length, model_id, variant_id)
    for model in all_models():
        if model.id.upper() in skip:
            continue
        for token in model.tokens:
            if token and token in text and (best is None or len(token) > best[0]):
                best = (len(token), model.id, None)
        for variant in model.variants:
            for token in variant.tokens:
                if token and token in text and (best is None or len(token) > best[0]):
                    best = (len(token), model.id, variant.id)
    if best is None:
        return "", None
    return best[1], best[2]


def selectable_models(available_ids=None, fallback=()) -> list:
    """Model ids to offer in the device drop-down, in a stable order.

    ``available_ids`` is what the catalogue currently offers. Ids are ordered
    by the registry so the list does not reshuffle between launches, and
    anything the catalogue offers but the registry has never heard of is
    appended — a brand new release must never be hidden by a stale data file.
    """
    known = model_ids()
    offered = [str(i).strip() for i in (available_ids or []) if str(i).strip()]
    if not offered:
        offered = [str(i).strip() for i in fallback if str(i).strip()]
    ordered = [mid for mid in known if mid in offered]
    ordered += [mid for mid in offered if mid not in ordered]
    return ordered


def reset_cache():
    """Drop the parsed registry (used by tests)."""
    global _cache
    _cache = None
