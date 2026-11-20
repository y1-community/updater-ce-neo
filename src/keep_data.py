"""Keep Data eligibility from the catalogue's derivative relationships.

``slidia_manifest.xml`` may give a package a ``derivative`` attribute naming
the repository it was built from. Firmware with the same root (the original
software) shares a data partition, so a Keep Data install is safe when the
software already tracked for the model is the same firmware or a relative of
the incoming one. A firmware with no derivative is only compatible with
itself.

Reborn (Y2Linux) has no special exception: it follows the same graph. A
release of Reborn updates itself, but without a ``derivative`` link it does
not inherit data from Classy, Y2Player, stock or any other system. Only
``slidia_manifest.xml`` can add that relationship or veto Keep Data with
``no_keep_data``/``do_not_keep_data`` or ``PMT_format_needed``.

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


def tracked_installs(
    model: str,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> list:
    """Every firmware tracked for this player (one unless multi is on)."""
    from . import player_update_check

    if not player_update_check.is_track_multiple_software_enabled(settings):
        record = get_device_install(
            model, hardware_type=hardware_type, settings=settings
        )
        return [record] if record else []
    return list(
        player_update_check.get_tracked_installs(
            model, hardware_type=hardware_type, settings=settings
        )
        or []
    )


def tracked_is_ambiguous(
    model: str,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> bool:
    """True when several firmwares are tracked, so the app cannot know which runs.

    The install questions then have to be put to the user: reading the newest
    record as the answer would silently decide Keep Data for a player that may
    be running one of the others.
    """
    return len(tracked_installs(model, settings, hardware_type)) > 1


def _package_for_record(model: str, record: dict):
    """The catalogue package one tracked record names, or ``None``."""
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


def tracked_package(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = ""):
    """The tracked install's catalogue package, or ``None`` when unknown."""
    for record in tracked_installs(model, settings, hardware_type):
        found = _package_for_record(model, record)
        if found is not None:
            return found
    return None


def tracked_is_same(
    model: str,
    pkg,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> bool:
    """True when ``pkg`` is exactly one of the firmwares recorded for this player.

    A family match is enough for Keep Data to be offered, but a reinstall of
    software already on the player is a stronger signal: it is an update, not a
    first install or a switch to a different firmware. Only that narrower case
    defaults the tick on. The comparison is by name and slug so a local copy of
    the same release (no catalogue repository) still counts, and it reads the
    tracked installs for this model only.
    """
    if pkg is None:
        return False
    return any(
        _same_software_record(pkg, record)
        for record in tracked_installs(model, settings, hardware_type)
    )


def known_running_software(
    model: str,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> dict:
    """What this model is known to run, ``{}`` when nothing is known.

    An answer the user gave ("it is still running X") wins over the last
    recorded install; both share one shape, with ``tag_name`` empty when only
    an answer is known.
    """
    from .player_update_check import get_running_software

    return get_running_software(
        model, hardware_type=hardware_type, settings=settings
    ) or {}


def note_running_software(
    model: str,
    software_name: str,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> None:
    """Store what an answer said this model runs, without claiming a version."""
    from .player_update_check import set_running_software

    set_running_software(
        model, software_name, settings=settings, hardware_type=hardware_type
    )


def forget_running_software(
    model: str,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> None:
    """Clear the tracked software for this model, so the next run asks again."""
    from .player_update_check import clear_device_install, clear_running_software

    clear_running_software(model, hardware_type=hardware_type, settings=settings)
    clear_device_install(model, hardware_type=hardware_type, settings=settings)


def forget_running_answer(
    model: str,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> None:
    """Clear the "it is still running X" answer, keeping the install record.

    An answer is a claim the app asked for and can be contradicted by the next
    one; the install record is the player's own entry in Settings and the
    software name reminders and install copy read. Answering the install
    menu with something else drops the claim, not the player.
    """
    from .player_update_check import clear_running_software

    clear_running_software(model, hardware_type=hardware_type, settings=settings)


def declares_family(pkg) -> bool:
    """True when the catalogue names the firmware ``pkg`` was built from."""
    return bool(_norm(getattr(pkg, "derivative", "")))


def _compatible_with_record(
    model: str, pkg, record: dict, packages: Optional[Iterable] = None
) -> Optional[bool]:
    """Whether ``pkg`` can keep the data of one tracked install."""
    # The same software, whichever way it was obtained: a reinstall is safe
    # even when the incoming copy is local and has no repository.
    if _same_software_record(pkg, record):
        return True
    # Another release, or the older catalogue name, of the same firmware.
    if _record_names_firmware(pkg, record):
        return True
    tracked = _package_for_record(model, record)
    # Neither side names a parent firmware, so nothing says their data
    # partitions match. A firmware that declares no family is compatible with
    # its own releases only, and a player that came from a different system
    # must start on a fresh data partition.
    if not declares_family(pkg) and not declares_family(tracked):
        return False
    if tracked is None:
        return None
    incoming_repo = _norm(getattr(pkg, "repo", ""))
    tracked_repo = _norm(getattr(tracked, "repo", ""))
    if incoming_repo and incoming_repo == tracked_repo:
        return True
    if packages is None:
        packages = _model_packages(model or getattr(pkg, "model", ""))
    if family_repos(pkg, packages).intersection(family_repos(tracked, packages)):
        return True
    # A local copy carries no repository, so its family cannot be placed. That
    # is unknown rather than proof of a different data partition.
    if not incoming_repo or not tracked_repo:
        return None
    return False


def tracked_is_compatible(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> Optional[bool]:
    """True/False for the tracked installs, ``None`` when it cannot be known.

    The ``derivative`` graph is the only evidence that two systems share a
    data partition. A reinstall of the software already tracked always shares
    the player's data partition, and so does any member of its derivative
    family (stock, Better-Y and Koensayr are one family because both mods
    declare stock as ``derivative``). A firmware that declares no family
    shares it with itself and its own releases only, so Reborn does not carry
    Classy or Y2Player data merely because it is Reborn. The catalogue's
    ``no_keep_data``/``PMT_format_needed`` vetoes are checked by the callers
    first, so they win even here. Comparison stays inside one model: a Y2
    Rockbox is never compatible with a Y1 install.

    With several firmwares tracked, any one of them being compatible is enough
    for Keep Data to be possible; the install still asks which one is running
    (see :func:`tracked_is_ambiguous`).
    """
    if pkg is None:
        return False
    records = tracked_installs(model, settings, hardware_type)
    if not records:
        return None
    packages = _model_packages(model or getattr(pkg, "model", ""))
    verdicts = [
        _compatible_with_record(model, pkg, record, packages)
        for record in records
    ]
    if any(verdict is True for verdict in verdicts):
        return True
    if any(verdict is None for verdict in verdicts):
        return None
    return False


def install_first_allows_keep_data(
    model: str,
    pkg,
    settings: Optional[QSettings] = None,
    hardware_type: str = "",
) -> bool:
    """Whether a package that specifies install_first may use Keep Data.

    Generally speaking users should not be able to keep settings / retain data
    when installing a firmware with an install_first specified, unless
    keep_data_on_first_install="true" is declared in slidia_manifest.xml.
    However, Keep Data is still allowed when:
    1. Installing a newer release of the firmware already used (e.g. when that
       first_install firmware has already been installed and a user is updating it
       without needing to do a first install).
    2. Going between two firmwares with a derivative relationship in the firmware manifest.
    3. keep_data_on_first_install="true" is declared on the package.
    """
    if pkg is None or not str(getattr(pkg, "install_first", "") or "").strip():
        return True
    if getattr(pkg, "keep_data_on_first_install", False):
        return True
    if tracked_is_same(model, pkg, settings=settings, hardware_type=hardware_type):
        return True
    records = tracked_installs(model, settings, hardware_type)
    if records:
        packages = _model_packages(model or getattr(pkg, "model", ""))
        for record in records:
            tracked = _package_for_record(model, record)
            if tracked is not None and declares_family(pkg) and declares_family(tracked):
                if family_repos(pkg, packages).intersection(family_repos(tracked, packages)):
                    return True
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
    if not install_first_allows_keep_data(model, pkg, settings=settings, hardware_type=hardware_type):
        return False
    if allow_keep_data_any(settings):
        return False
    from .pmt_format import pmt_format_required

    if pmt_format_required(pkg, model=model, settings=settings):
        return False
    return tracked_is_compatible(model, pkg, settings=settings, hardware_type=hardware_type) is False


def keep_data_available(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> bool:
    """Whether the Keep Data choice may be offered for this install.

    The catalogue's ``no_keep_data``/``do_not_keep_data`` attribute for this
    software and a genuine partition-table change that needs Format All +
    Download take the choice away. Otherwise the ``derivative`` graph must
    show that the tracked software shares the data partition; a firmware with
    no declared family is not offered Keep Data for a different system. An
    untracked player is treated as unknown and asked to confirm its software.
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
    if not install_first_allows_keep_data(model, pkg, settings=settings, hardware_type=hardware_type):
        return False
    if allow_keep_data_any(settings):
        return True
    tracked = tracked_is_compatible(model, pkg, settings=settings, hardware_type=hardware_type)
    return tracked is not False


def needs_confirmation(model: str, pkg, settings: Optional[QSettings] = None, hardware_type: str = "") -> bool:
    """True when the user must confirm the currently installed firmware.

    2.5.5's rule, restored: the sanity check is for the player whose software
    the app cannot know. A tracked install the catalogue can compare is
    answered already, so the run does not add a question to it.
    """
    if pkg is None or getattr(pkg, "do_not_keep_data", False):
        return False
    if not install_first_allows_keep_data(model, pkg, settings=settings, hardware_type=hardware_type):
        return False
    if allow_keep_data_any(settings):
        return False
    from .pmt_format import pmt_format_required

    if pmt_format_required(pkg, model=model, settings=settings):
        return False
    if tracked_is_ambiguous(model, settings, hardware_type):
        # Several firmwares are on record: only the user can say which one is
        # actually running, and that answer is what decides Keep Data.
        return True
    return tracked_is_compatible(model, pkg, settings=settings, hardware_type=hardware_type) is None


def keep_data_label(model: str) -> str:
    """One novice-facing label for every model; the warning carries the detail."""
    from .i18n import tr

    return tr("flash_keep_data_option")
