"""Keep Data eligibility from the catalogue's derivative relationships.

``slidia_manifest.xml`` may give a package a ``derivative`` attribute naming
the repository it was built from. Firmware with the same root (the original
software) shares a data partition, so a Keep Data install is safe when the
software already tracked for the model is the same firmware or a relative of
the incoming one. A firmware with no derivative is only compatible with
itself.

Reborn (Y2Linux) is the exception the catalogue cannot express: it carries
the player's data forward whatever system it is installed over, so Keep Data
is never refused for it. Only ``slidia_manifest.xml`` can take that away,
with ``no_keep_data``/``do_not_keep_data`` or ``PMT_format_needed``.

The helpers here are deliberately pure over ``FirmwarePackage`` objects so the
same rules can be tested without a window or a device.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QSettings

from . import catalog
from .player_update_check import get_device_install

ALLOW_ANY_KEY = "allow_keep_data_any_firmware"


def _norm(repo: str) -> str:
    return str(repo or "").strip().replace("\\", "/").strip("/").casefold()


def _key(value) -> str:
    return str(value or "").strip().casefold()


def _record_software_keys(record) -> set[str]:
    """Names/slugs a tracked install was recorded under."""
    keys = {_key(record.get("software_name")), _key(record.get("package_slug"))}
    keys.discard("")
    return keys


def _package_software_keys(pkg) -> set[str]:
    keys = {_key(getattr(pkg, "name", "")), _key(getattr(pkg, "slug", ""))}
    keys.discard("")
    return keys


def _compact(value) -> str:
    return _key(value).replace(" ", "").replace("-", "").replace("_", "")


def _record_names_firmware(pkg, record) -> bool:
    """True when the tracked record names this package's own firmware.

    Each release of a firmware is still that firmware. The catalogue has
    called the same one ``Y2Reborn (Y2Linux)`` and then ``Reborn (Y2Linux)``,
    while its releases are ``Y2Linux 1.0 Beta 9.3.x``, so a name comparison
    alone would read every older version as a different system. The
    repository's name is what identifies the firmware; another system
    (``Y2Player``, ``Classy``) does not carry it.
    """
    repo_name = _norm(getattr(pkg, "repo", "")).split("/")[-1]
    needle = _compact(repo_name)
    if not needle:
        return False
    for value in (record.get("software_name"), record.get("package_slug")):
        if needle in _compact(value):
            return True
    return False


def is_reborn_firmware(pkg) -> bool:
    """True for the Reborn (Y2Linux) catalogue entry.

    Reborn installs over any system and carries the player's data forward, so
    Keep Data is never refused for it. The repository names the firmware
    (``schulzcode/y2linux``); the name keeps working for the older
    ``Y2Reborn (Y2Linux)`` label and for local copies without a repository.
    """
    if pkg is None:
        return False
    repo_name = _norm(getattr(pkg, "repo", "")).split("/")[-1]
    if repo_name == "y2linux":
        return True
    return any(
        "y2linux" in _compact(getattr(pkg, field, ""))
        for field in ("name", "slug")
    )


def _same_software_record(pkg, record) -> bool:
    """True when ``pkg`` is the software the tracked install names.

    A local package (a downloaded zip or a folder the user picked) carries no
    catalogue repository. Comparing repositories alone therefore read it as a
    different firmware family and withheld Keep Data for a reinstall of the
    software already on the player. The recorded name/slug is the evidence the
    catalogue cannot lose.
    """
    return bool(_package_software_keys(pkg) & _record_software_keys(record))


def _model_packages(model: str) -> list:
    """Catalogue packages for ``model`` only; engine and mod families never
    cross a model boundary, so neither may Keep Data compatibility."""
    packages = _packages(model)
    wanted = str(model or "").strip().upper()
    if not wanted:
        return packages
    return [
        p
        for p in packages
        if str(getattr(p, "model", "") or "").strip().upper() == wanted
    ]


def allow_keep_data_any(settings: Optional[QSettings] = None) -> bool:
    """The advanced override, off by default."""
    s = settings or QSettings("innioasis", "updater")
    return bool(s.value(ALLOW_ANY_KEY, False, type=bool))


def set_allow_keep_data_any(enabled: bool, settings: Optional[QSettings] = None) -> None:
    s = settings or QSettings("innioasis", "updater")
    s.setValue(ALLOW_ANY_KEY, bool(enabled))


def _packages(model: str = "") -> list:
    packages = catalog.packages_for_model(model) if model else list(catalog.LIVE_CATALOG or catalog.CATALOG)
    return list(packages or [])


def _package_for_repo(repo: str, packages: Iterable) -> object | None:
    wanted = _norm(repo)
    if not wanted:
        return None
    for pkg in packages or ():
        if _norm(getattr(pkg, "repo", "")) == wanted:
            return pkg
    return None


def root_repo(pkg, packages: Optional[Iterable] = None) -> str:
    """Follow ``derivative`` upward and return the family's root repository."""
    packages = list(packages if packages is not None else _packages(getattr(pkg, "model", "")))
    repo = _norm(getattr(pkg, "repo", ""))
    seen: set[str] = set()
    while repo and repo not in seen:
        seen.add(repo)
        parent = _package_for_repo(repo, packages)
        derivative = _norm(getattr(parent, "derivative", "")) if parent is not None else ""
        if not derivative or derivative == repo:
            break
        repo = derivative
    return repo


def family_repos(pkg, packages: Optional[Iterable] = None) -> set[str]:
    """Every repository in the same derivative family as ``pkg``."""
    packages = list(packages if packages is not None else _packages(getattr(pkg, "model", "")))
    root = root_repo(pkg, packages)
    if not root:
        return set()
    family: set[str] = set()
    for candidate in packages:
        repo = _norm(getattr(candidate, "repo", ""))
        if repo and root_repo(candidate, packages) == root:
            family.add(repo)
    return family


def compatible_packages(pkg, model: str = "") -> list:
    """Catalogue packages whose data partition matches ``pkg``.

    The incoming package is part of its own family even when it came from a
    local selection rather than the cached catalogue, so the derivative being
    installed is always one of the choices.
    """
    packages = _model_packages(model or getattr(pkg, "model", ""))
    incoming_repo = _norm(getattr(pkg, "repo", ""))
    if pkg is not None and incoming_repo and all(
        _norm(getattr(candidate, "repo", "")) != incoming_repo
        for candidate in packages
    ):
        packages = [*packages, pkg]
    family = family_repos(pkg, packages)
    return [p for p in packages if _norm(getattr(p, "repo", "")) in family]


def compatible_software_names(pkg, model: str = "") -> list:
    """Software names compatible with ``pkg`` on this model, original first.

    The list drives the sanity-check question: one name is a yes/no question,
    several become a drop-down. It never crosses a model boundary, so a Y1
    package cannot offer a Y2 firmware's name.
    """
    packages = compatible_packages(pkg, model)
    names: list[str] = []
    seen: set[str] = set()
    for candidate in packages:
        name = str(getattr(candidate, "name", "") or "").strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    names.sort(key=lambda n: (n.casefold() != "original software", n.casefold()))
    return names


def tracked_package(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = ""):
    """The tracked install's catalogue package, or ``None`` when unknown."""
    record = get_device_install(model, hardware_type=hardware_type, settings=settings)
    if not record:
        return None
    name = str(record.get("software_name") or "").strip()
    packages = _packages(model)
    for candidate in packages:
        if name and str(candidate.name or "").casefold() == name.casefold():
            return candidate
    slug = str(record.get("package_slug") or "").strip()
    for candidate in packages:
        if slug and str(candidate.slug or "") == slug:
            return candidate
    return None


def tracked_is_same(
    model: str,
    pkg,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> bool:
    """True when ``pkg`` is exactly the firmware recorded for this player.

    A family match is enough for Keep Data to be offered, but a reinstall of
    the software already on the player is a stronger signal: it is an update,
    not a first install or a switch to a different firmware. Only that
    narrower case defaults the tick on. The comparison is by name and slug so
    a local copy of the same release (no catalogue repository) still counts,
    and it reads the tracked install for this model only.
    """
    if pkg is None:
        return False
    record = get_device_install(
        model, hardware_type=hardware_type, settings=settings
    )
    if not record:
        return False
    return _same_software_record(pkg, record)


def declares_family(pkg) -> bool:
    """True when the catalogue names the firmware ``pkg`` was built from."""
    return bool(_norm(getattr(pkg, "derivative", "")))


def tracked_is_compatible(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> Optional[bool]:
    """True/False for a tracked install, ``None`` when it cannot be known.

    Reborn (Y2Linux) may always carry data forward: whoever made the player's
    current install — Y2Player, Classy, stock, an older Y2Linux release — Keep
    Data is still safe for a Reborn install. The catalogue's
    ``no_keep_data``/``PMT_format_needed`` vetoes are checked by the callers
    first, so they win even here.

    Every other firmware follows the family rule: a reinstall of the software
    already tracked always shares the player's data partition, and so does any
    member of its derivative family (stock, Better-Y and Koensayr are one
    family because both mods declare stock as ``derivative``). A firmware that
    declares no family shares it with itself only, so it needs its own tracked
    install. Comparison stays inside one model: a Y2 Rockbox is never
    compatible with a Y1 install.
    """
    if pkg is None:
        return False
    if is_reborn_firmware(pkg):
        return True
    record = get_device_install(model, hardware_type=hardware_type, settings=settings)
    if not record:
        return None
    # The same software, whichever way it was obtained: a reinstall is safe
    # even when the incoming copy is local and has no repository.
    if _same_software_record(pkg, record):
        return True
    # Another release, or the older catalogue name, of the same firmware.
    # Reborn (Y2Linux) carries its own data forward across its own versions.
    if _record_names_firmware(pkg, record):
        return True
    tracked = tracked_package(model, pkg, settings=settings, hardware_type=hardware_type)
    # Neither side names a parent firmware, so nothing says their data
    # partitions match. That used to read as "unknown": Keep Data was offered
    # and the install asked which firmware was running. A firmware that
    # declares no family is compatible with its own releases only, though, and
    # the player that came from a different system — Y2Player, Classy, stock —
    # must start on a fresh data partition. Asking after the box was already
    # ticked offered it to exactly those players.
    if not declares_family(pkg) and not declares_family(tracked):
        return False
    if tracked is None:
        return None
    incoming_repo = _norm(getattr(pkg, "repo", ""))
    tracked_repo = _norm(getattr(tracked, "repo", ""))
    if incoming_repo and incoming_repo == tracked_repo:
        return True
    packages = _model_packages(model or getattr(pkg, "model", ""))
    if family_repos(pkg, packages).intersection(family_repos(tracked, packages)):
        return True
    # A local copy carries no repository, so its family cannot be placed. That
    # is unknown rather than proof of a different data partition.
    if not incoming_repo or not tracked_repo:
        return None
    return False


def incompatible_with_current(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> bool:
    """True when Keep Data is withheld only because the installed firmware differs.

    That is the one refusal the select page can explain in advance: both
    firmwares have a known family in the catalogue and they do not share a data
    partition. A firmware that forbids Keep Data outright, or one that needs
    Format All + Download, is refused for a different reason (or no reason at
    all), so it must not get this note.
    """
    if pkg is None or getattr(pkg, "do_not_keep_data", False):
        return False
    if allow_keep_data_any(settings):
        return False
    from .pmt_format import pmt_format_required

    if pmt_format_required(pkg, model=model, settings=settings):
        return False
    return tracked_is_compatible(model, pkg, settings=settings, hardware_type=hardware_type) is False


def keep_data_available(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> bool:
    """Whether the Keep Data choice may be offered for this install.

    Only two things take the choice away: the catalogue's
    ``no_keep_data``/``do_not_keep_data`` attribute for this software, or a
    genuine partition-table change that needs Format All + Download. Reborn
    (Y2Linux) is otherwise always offered, and a reinstall of the software
    already on the player always defaults the tick on.
    """
    if pkg is not None and getattr(pkg, "do_not_keep_data", False):
        return False
    from .pmt_format import pmt_format_required

    if pmt_format_required(pkg, model=model, settings=settings):
        # Format All + Download reformats internal storage by design, so a
        # keep-data scatter would be undone by the format command.
        return False
    if pkg is None:
        return True
    if allow_keep_data_any(settings):
        return True
    tracked = tracked_is_compatible(model, pkg, settings=settings, hardware_type=hardware_type)
    return tracked is not False


def needs_confirmation(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> bool:
    """True when the user must confirm the currently installed firmware."""
    if pkg is None or getattr(pkg, "do_not_keep_data", False) or allow_keep_data_any(settings):
        return False
    from .pmt_format import pmt_format_required

    if pmt_format_required(pkg, model=model, settings=settings):
        return False
    return tracked_is_compatible(model, pkg, settings=settings, hardware_type=hardware_type) is None


def keep_data_label(model: str) -> str:
    """One novice-facing label for every model; the warning carries the detail."""
    from .i18n import tr

    return tr("flash_keep_data_option")
