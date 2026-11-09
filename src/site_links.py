"""Links from innioasis.app and themes.innioasis.app that open the app.

The sites start a firmware or theme download by opening
``innioasis-updater://open?what=firmware&software=Rockbox&version=v0.5&model=Y1``.
The OS hands that URL to the app as a launch argument (Windows, Linux) or as a
URL-open event (macOS); a second launch forwards it to the running copy over
the same focus channel a dropped package uses.

The query is the one the app already sends the sites (see
``browser._with_site_preferences`` and ``docs_source.SITE``), so a site page
and an app link name the same things the same way:

``what``        ``firmware`` or ``theme`` (required)
``software``    catalogue software name (``Rockbox``); ``slug`` is accepted too
``version``     release tag, or ``latest``
``model``       device model id (``Y1``, ``Y2``, ``Q8``…)
``brand``       ``Innioasis`` or ``Timmkoo``
``type``        Y1 hardware type, ``A`` or ``B``
``resolution``  ``240p`` or ``360p``: which build of the release
``action``      ``open`` (select it, the default) or ``install`` (start the
                guided install; every prompt still asks before writing)
``id``          theme id on themes.innioasis.app (``what=theme``)

``utm_source``, ``utm_medium``, ``utm_campaign``, ``utm_content`` and ``ref``
are kept for the log so a visit the site counted can be matched to what the
app did; the app never acts on them. ``theme`` is *not* a theme id: the app
already sends ``theme=light|dark|high-contrast`` to the sites.

The same query on an ``https://innioasis.app/…`` or
``https://themes.innioasis.app/…`` URL passed to the app is read the same way;
a themes-site URL without ``what`` means ``what=theme``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

SCHEME = "innioasis-updater"

KIND_FIRMWARE = "firmware"
KIND_THEME = "theme"
ACTION_OPEN = "open"
ACTION_INSTALL = "install"

_KIND_ALIASES = {
    "firmware": KIND_FIRMWARE,
    "rom": KIND_FIRMWARE,
    "software": KIND_FIRMWARE,
    "theme": KIND_THEME,
    "themes": KIND_THEME,
}
_ACTION_ALIASES = {
    "open": ACTION_OPEN,
    "select": ACTION_OPEN,
    "show": ACTION_OPEN,
    "install": ACTION_INSTALL,
    "download": ACTION_INSTALL,
}
ATTRIBUTION_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "ref")

#: Values come from a web page, so each one is a short, plain token. Anything
#: else is dropped rather than passed on to the catalogue lookup.
_SAFE_VALUE = re.compile(r"^[\w .+\-()/]{1,100}$")
_MAX_LINK_LENGTH = 2048


def _site_host(host: str) -> bool:
    host = (host or "").lower()
    return host == "innioasis.app" or host.endswith(".innioasis.app")


def _clean(value) -> str:
    text = str(value or "").strip()
    return text if _SAFE_VALUE.match(text) else ""


@dataclass(frozen=True)
class SiteLink:
    kind: str
    action: str = ACTION_OPEN
    software: str = ""
    version: str = ""
    model: str = ""
    brand: str = ""
    hw_type: str = ""
    resolution: str = ""
    theme_id: str = ""
    attribution: dict = field(default_factory=dict)
    raw: str = ""

    @property
    def wants_240p(self) -> bool:
        return self.resolution == "240p"

    @property
    def wants_latest(self) -> bool:
        return not self.version or self.version.lower() == "latest"

    def query(self) -> str:
        """The link's query in the shared contract, for logs and forwarding."""
        pairs = [("what", self.kind)]
        for key, value in (
            ("software", self.software),
            ("version", self.version),
            ("model", self.model),
            ("brand", self.brand),
            ("type", self.hw_type),
            ("resolution", self.resolution),
            ("id", self.theme_id),
        ):
            if value:
                pairs.append((key, value))
        if self.action != ACTION_OPEN:
            pairs.append(("action", self.action))
        pairs.extend((k, self.attribution[k]) for k in ATTRIBUTION_KEYS if k in self.attribution)
        return urlencode(pairs)

    def describe(self) -> str:
        return f"{SCHEME}://open?{self.query()}"


def parse(url) -> Optional[SiteLink]:
    """A :class:`SiteLink` for an app or site URL, or ``None`` when it is not one."""
    text = str(url or "").strip().strip('"')
    if not text or len(text) > _MAX_LINK_LENGTH:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    query = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        query.setdefault(key.lower(), value)

    # ``innioasis-updater://theme?…`` / ``innioasis-updater://install?…``: the
    # first path step may name the kind or the action when the query does not.
    steps = []
    if scheme == SCHEME:
        steps = [s for s in [host, *parts.path.split("/")] if s]
    elif scheme in ("http", "https") and _site_host(host):
        if host.startswith("themes.") and "what" not in query:
            query["what"] = KIND_THEME
    else:
        return None

    kind = _KIND_ALIASES.get(str(query.get("what", "")).strip().lower(), "")
    action = _ACTION_ALIASES.get(str(query.get("action", "")).strip().lower(), "")
    for step in steps:
        step = step.lower()
        kind = kind or _KIND_ALIASES.get(step, "")
        action = action or _ACTION_ALIASES.get(step, "")
    if not kind:
        return None
    # A value that names something but is not a plain token must not fall
    # back to a default (the first software, the latest release) instead.
    for key in ("software", "slug", "version", "tag", "model", "brand", "id"):
        if query.get(key) and not _clean(query[key]):
            return None

    hw_type = _clean(query.get("type")).upper()
    resolution = _clean(query.get("resolution")).lower()
    return SiteLink(
        kind=kind,
        action=action or ACTION_OPEN,
        software=_clean(query.get("software") or query.get("slug")),
        version=_clean(query.get("version") or query.get("tag")),
        model=_clean(query.get("model")).upper(),
        brand=_clean(query.get("brand")),
        hw_type=hw_type if hw_type in ("A", "B") else "",
        resolution=resolution if resolution in ("240p", "360p") else "",
        theme_id=_clean(query.get("id")),
        attribution={k: _clean(query[k]) for k in ATTRIBUTION_KEYS if _clean(query.get(k))},
        raw=text,
    )


def from_arguments(args: Iterable[str]) -> Optional[SiteLink]:
    """The site link the app was launched with, if any."""
    for raw in args:
        text = str(raw or "").strip()
        if not text or text.startswith("-"):
            continue
        link = parse(text)
        if link is not None:
            return link
    return None


def is_app_link(url) -> bool:
    return str(url or "").strip().lower().startswith(SCHEME + ":")
