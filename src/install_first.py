"""``install_first`` prerequisites declared by the firmware catalogue.

``slidia_manifest.xml`` may say that a firmware can only be installed after a
different firmware has been put on the player, because it is an overlay that
relies on the base build's preloader, partition table and bootloader::

    <package name="Reborn (Y2Linux)" repo="schulzcode/y2linux"
             device="Y2" install_first="y1-community/y1-stock-rom"
             install_first_version="3.2.0=>" ... />

``install_first`` is the repository of the base firmware and
``install_first_version`` is the version the player must already be running
(the attribute is optional; no attribute means any version of that base).

This module is deliberately pure: no Qt, no network, no window. The window asks
the user what the player is running, and :class:`InstallFirstJourney` turns the
answer into the next UI step. Keeping that decision here is what lets the same
rules be tested without a device and lets future firmwares use the attributes
without code changes.

Version syntax (``install_first_version``):

* ``3.2.0``, ``3.2.0+``, ``3.2.0=>``, ``3.2.0>=``,
  ``>=3.2.0``, ``=>3.2.0``                            - 3.2.0 or newer
* ``3.2.0>``, ``>3.2.0``                              - newer than 3.2.0
* ``<=3.2.0``, ``3.2.0<=``, ``<3.2.0``, ``3.2.0<``  - capped versions
* ``=3.2.0``, ``==3.2.0``, ``3.2.0=``                 - exactly 3.2.0
* ``2.3.0-3.0.0``, ``2.3.0..3.0.0``                  - inclusive range
* ``>=3.2.0,<4.0.0``                                 - combined bounds

A bare version is a minimum, not an exact match: write ``=3.2.0`` when only
that build will do.

A tag is read as its first dotted numeric version, so ``stable-v3.2.0`` and
``v3.2.0-beta.1`` both compare as ``3.2.0``.

One firmware can serve several models: the same stock build ships for Timmkoo
Q3 and Q5, and for Innioasis G1 and G3, and ``slidia_manifest.xml`` names its
repository once per model. A prerequisite is therefore resolved against the
target model's rows first and then against the same repository listed for a
sibling model (see :func:`_models_can_share_a_source`). The families that must
not mix (Y1, Y2, A5) are still refused, and whatever row is found is still
filtered for the target model when its releases are listed, so a Y1 base can
never be downloaded for a Y2 overlay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: The user says the player already runs the firmware being installed.
ANSWER_TARGET = "target"
#: The user says the player runs the firmware named by ``install_first``.
ANSWER_PREREQUISITE = "prerequisite"
#: The user says the player runs anything else.
ANSWER_OTHER = "other"
#: The user says the player runs another catalogue firmware that the manifest
#: does not name in ``install_first_when``. The base is the cleanest route for
#: this one but the catalogue does not require it, so the window recommends it
#: and the user decides.
ANSWER_FIRMWARE = "firmware"

#: The next step the window must take.
PROCEED = "proceed"
ASK_VERSION = "ask_version"
NEED_PREREQUISITE = "need_prerequisite"
#: The base is recommended rather than required: offer it, and install the
#: target anyway when the user says no.
RECOMMEND_PREREQUISITE = "recommend_prerequisite"

#: ``install_first_when`` is a CSV list. Split on the separators a catalogue
#: editor can produce: ASCII commas and semicolons, plus the full-width forms
#: a Chinese-market manifest is written with. Empty entries (a trailing comma,
#: a doubled comma) are dropped, so a hand-edited list still parses.
_WHEN_SPLIT_RE = re.compile(r"[,;，；]")

_VERSION_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?![\d.])")
_REQUIRED_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def normalize_repo(value) -> str:
    """Canonical ``owner/name`` spelling, or ``""`` when it is not a handle."""
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if not text or not _REQUIRED_REPO_RE.match(text):
        return ""
    return text.casefold()


def repo_key(value) -> str:
    """Canonical ``owner/name`` with the catalogue's legacy names resolved.

    ``install_first`` is mapped through ``config.resolve_firmware_repo`` when
    the manifest is parsed, and ``install_first_when`` has to be mapped the
    same way. A manifest that still spells a renamed repository the old way
    (``y1-community/rockbox-y2-rom`` for today's ``spycemagic/y2-rockbox``)
    would otherwise name a system that never matches the row it means, and that
    row would stay on the menu as an answer that skips the base.
    """
    repo = normalize_repo(value)
    if not repo:
        return ""
    try:
        from .config import resolve_firmware_repo

        return normalize_repo(resolve_firmware_repo(repo)) or repo
    except Exception:
        return repo


def version_tuple(value) -> Optional[tuple[int, ...]]:
    """The first dotted numeric version in ``value`` as a tuple of ints."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        parts = tuple(int(part) for part in value if str(part).isdigit())
        return parts or None
    match = _VERSION_RE.search(str(value).
                               strip())
    if not match:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None


def _pad(left: tuple, right: tuple) -> tuple[tuple, tuple]:
    width = max(len(left), len(right))
    return (
        left + (0,) * (width - len(left)),
        right + (0,) * (width - len(right)),
    )


def compare_versions(left, right) -> Optional[int]:
    """``-1``/``0``/``1`` for two dotted versions, ``None`` when unreadable."""
    lv = version_tuple(left)
    rv = version_tuple(right)
    if lv is None or rv is None:
        return None
    lv, rv = _pad(lv, rv)
    return (lv > rv) - (lv < rv)


@dataclass(frozen=True)
class VersionRange:
    """An inclusive/exclusive dotted-version range.

    ``text`` is the attribute as written, so a caller can show the catalogue's
    own spelling alongside the parsed bounds.
    """

    minimum: Optional[tuple[int, ...]] = None
    maximum: Optional[tuple[int, ...]] = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True
    text: str = ""

    @property
    def exact(self) -> bool:
        """True when both bounds pin one version (``=3.2.0``)."""
        return (
            self.minimum is not None
            and self.maximum is not None
            and compare_versions(self.minimum, self.maximum) == 0
            and self.minimum_inclusive
            and self.maximum_inclusive
        )

    def allows(self, version) -> bool:
        candidate = version_tuple(version)
        if candidate is None:
            return False
        if self.minimum is not None:
            candidate, minimum = _pad(candidate, self.minimum)
            if candidate < minimum or (candidate == minimum and not self.minimum_inclusive):
                return False
        if self.maximum is not None:
            candidate, maximum = _pad(candidate, self.maximum)
            if candidate > maximum or (candidate == maximum and not self.maximum_inclusive):
                return False
        return True

    def label(self) -> str:
        """A short, language-neutral form of the range.

        Symbols (``3.2.0+``, ``≤4.0.0``) read the same in every catalogue
        language; the sentence around them is what gets translated.
        """
        if self.exact:
            return f"={_dotted(self.minimum)}"
        if self.minimum and self.maximum:
            low = _dotted(self.minimum)
            high = _dotted(self.maximum)
            if self.minimum_inclusive and self.maximum_inclusive:
                return f"{low}–{high}"
            left = f"≥{low}" if self.minimum_inclusive else f">{low}"
            right = f"≤{high}" if self.maximum_inclusive else f"<{high}"
            return f"{left}, {right}"
        if self.minimum:
            if self.minimum_inclusive:
                return f"{_dotted(self.minimum)}+"
            return f">{_dotted(self.minimum)}"
        if self.maximum:
            mark = "≤" if self.maximum_inclusive else "<"
            return f"{mark}{_dotted(self.maximum)}"
        return self.text


def _dotted(value: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in value)


#: Comparison spellings accepted before or after a version. ``=>`` and ``=<``
#: are the catalogue's own forms; the rest are the conventional ones.
_PREFIX_OPERATORS = (">=", "=>", "<=", "=<", "==", ">", "<", "=")
_SUFFIX_OPERATORS = (">=", "=>", "<=", "=<", "==", ">", "<", "=", "+")
_OPERATOR_RE = re.compile(r"[<>=+]")


def _constraint_bound(token) -> Optional[tuple[int, ...]]:
    """A version used as one side of a constraint, never another operator.

    ``version_tuple`` is deliberately lenient because release tags carry
    prefixes and suffixes (``stable-v3.2.0``). A constraint bound must not:
    otherwise ``3.2.0>4.0.0`` would silently compare as 3.2.0.
    """
    text = str(token or "").strip()
    if not text or _OPERATOR_RE.search(text):
        return None
    return version_tuple(text)


def _range_for_operator(operator: str, value: tuple[int, ...], raw: str) -> VersionRange:
    if operator in (">=", "=>"):
        return VersionRange(minimum=value, text=raw)
    if operator == ">":
        return VersionRange(minimum=value, minimum_inclusive=False, text=raw)
    if operator in ("<=", "=<"):
        return VersionRange(maximum=value, text=raw)
    if operator == "<":
        return VersionRange(maximum=value, maximum_inclusive=False, text=raw)
    # ``=`` and ``==`` (and their trailing spellings) pin one build.
    return VersionRange(minimum=value, maximum=value, text=raw)


def _single_constraint(token: str, raw: str) -> Optional[VersionRange]:
    """One comparison, with the operator on either side of the version."""
    text = token.strip()
    if not text:
        return None
    for operator in _PREFIX_OPERATORS:
        if text.startswith(operator):
            value = _constraint_bound(text[len(operator):])
            if value is None:
                return None
            return _range_for_operator(operator, value, raw)
    for operator in _SUFFIX_OPERATORS:
        if text.endswith(operator):
            value = _constraint_bound(text[: -len(operator)])
            if value is None:
                return None
            if operator == "+":
                return VersionRange(minimum=value, text=raw)
            return _range_for_operator(operator, value, raw)
    value = _constraint_bound(text)
    if value is not None:
        # A bare version means "this version or later". Exactness needs an
        # explicit ``=`` so a catalogue that forgets one still gets a safe
        # (not older) base rather than silently pinning an old build.
        return VersionRange(minimum=value, text=raw)
    return None


def _merge_constraints(parts: list[str], raw: str) -> Optional[VersionRange]:
    """Combine comma- or space-separated bounds into one range."""
    minimum = None
    maximum = None
    minimum_inclusive = True
    maximum_inclusive = True
    for part in parts:
        parsed = _single_constraint(part, raw)
        if parsed is None:
            return None
        if parsed.minimum is not None:
            if minimum is None:
                minimum = parsed.minimum
                minimum_inclusive = parsed.minimum_inclusive
            else:
                order = compare_versions(parsed.minimum, minimum)
                if order is not None and order > 0:
                    minimum = parsed.minimum
                    minimum_inclusive = parsed.minimum_inclusive
                elif order == 0:
                    minimum_inclusive = minimum_inclusive and parsed.minimum_inclusive
        if parsed.maximum is not None:
            if maximum is None:
                maximum = parsed.maximum
                maximum_inclusive = parsed.maximum_inclusive
            else:
                order = compare_versions(parsed.maximum, maximum)
                if order is not None and order < 0:
                    maximum = parsed.maximum
                    maximum_inclusive = parsed.maximum_inclusive
                elif order == 0:
                    maximum_inclusive = maximum_inclusive and parsed.maximum_inclusive
    if minimum is None and maximum is None:
        return None
    return VersionRange(
        minimum=minimum,
        maximum=maximum,
        minimum_inclusive=minimum_inclusive,
        maximum_inclusive=maximum_inclusive,
        text=raw,
    )


def parse_version_constraint(text) -> Optional[VersionRange]:
    """Parse ``install_first_version``; ``None`` means "any version".

    Operators may lead or trail the version, and a bare version is a lower
    bound. See the module docstring for the accepted spellings.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    normalized = (
        raw.replace("≥", ">=")
        .replace("≤", "<=")
        .replace("＞", ">")
        .replace("＜", "<")
        .replace("＝", "=")
        .replace("–", "-")
        .replace("—", "-")
    )
    # Keep a space between two constraints, but not between an operator and
    # its version: ">= 3.2.0 < 4.0.0" must split into two bounds, not become
    # one malformed token.
    normalized = re.sub(r"(?<=[<>=])\s+", "", normalized)
    compact = re.sub(r"\s+", "", normalized)
    lower = compact.casefold()

    # Natural-language minimums. Keep these ahead of the space-separated
    # parser so "3.2.0 or later" is not split into two constraints.
    for suffix in (
        "orlater",
        "ornewer",
        "orabove",
        "orgreater",
        "orhigher",
        "andlater",
        "andnewer",
        "andabove",
        "andgreater",
        "andhigher",
    ):
        if lower.endswith(suffix):
            value = _constraint_bound(compact[: -len(suffix)])
            if value is None:
                return None
            return VersionRange(minimum=value, text=raw)

    # >=3.2.0,<4.0.0 / >=3.2.0 <4.0.0
    if "," in normalized or ";" in normalized:
        parts = [part for part in re.split(r"[,;]", normalized) if part.strip()]
        if len(parts) > 1:
            merged = _merge_constraints(parts, raw)
            if merged is not None:
                return merged
    pieces = [part for part in normalized.split() if part]
    if len(pieces) > 1 and not any(part in {"-", ".."} for part in pieces):
        merged = _merge_constraints(pieces, raw)
        if merged is not None:
            return merged

    # 2.3.0..3.0.0 / 2.3.0-3.0.0. Checked after the operators so a suffix
    # comparison is never mistaken for the left side of a span.
    for separator in ("..", "-"):
        if separator in compact:
            left, _, right = compact.partition(separator)
            low = _constraint_bound(left)
            high = _constraint_bound(right)
            if low is not None and high is not None:
                return VersionRange(minimum=low, maximum=high, text=raw)

    return _single_constraint(compact, raw)


@dataclass(frozen=True)
class Requirement:
    """A firmware's ``install_first`` declaration resolved for one model."""

    repo: str
    version_text: str
    version_range: Optional[VersionRange]
    #: ``install_first_when``: the systems this overlay needs its base when
    #: coming *from*. Empty means "from anything".
    when_repos: tuple = ()
    #: ``install_first_once``: the base only has to be installed the first
    #: time this firmware goes on the player. It never removes the question.
    once: bool = False
    #: ``once`` and this firmware is the software the player is tracked as
    #: running, so its base has already been installed once: the base is
    #: offered, never required.
    once_satisfied: bool = False
    package: object = None
    model: str = ""
    #: The catalogue model the resolved base row is listed under when it is not
    #: the target's own (one firmware serving several models). Empty when the
    #: row matched the target model directly.
    source_model: str = ""

    @property
    def software_name(self) -> str:
        name = str(getattr(self.package, "name", "") or "").strip()
        if name:
            return name
        tail = self.repo.split("/")[-1] if self.repo else ""
        return tail.replace("-", " ").replace("_", " ").title() or self.repo

    def label(self) -> str:
        """``Original Software 3.2.0+`` or ``Original Software =3.2.0``."""
        if self.version_range is None:
            return self.software_name
        return f"{self.software_name} {self.version_range.label()}"

    def allows(self, version) -> bool:
        if self.version_range is None:
            return True
        return self.version_range.allows(version)


def _packages_for(model: str, packages: Optional[Iterable]) -> list:
    if packages is not None:
        return list(packages)
    try:
        from . import catalog

        return list(catalog.packages_for_model(model))
    except Exception:
        return []


def _catalogue_packages() -> list:
    """Every catalogue row, whatever model it is listed for.

    ``packages_for_model`` answers "what can be installed on this player"; a
    shared base firmware is listed under another model, so resolving a
    prerequisite has to see those rows too.
    """
    try:
        from . import catalog

        return list(catalog.LIVE_CATALOG or catalog.CATALOG)
    except Exception:
        return []


#: Models whose stock firmware is the same build, so one catalogue repository
#: can serve both. These players share MediaTek stock layouts and the catalogue
#: lists one firmware for all of them (Timmkoo Q3 and Q5, Innioasis G1 and G3).
#: Y1, Y2 and A5 are deliberately absent: their bases are not interchangeable,
#: and pairing one with another model's overlay is the mistake the model check
#: exists to stop.
_SHARED_SOURCE_MODEL_TOKENS = ("Y1", "Y2", "A5")


def _models_can_share_a_source(left, right) -> bool:
    """True when two catalogue models may run the same firmware image.

    An unknown (empty) model proves nothing and is never a wildcard.
    """
    left = str(left or "").strip().upper()
    right = str(right or "").strip().upper()
    if not left or not right:
        return False
    if left == right:
        return True
    if any(token in left for token in _SHARED_SOURCE_MODEL_TOKENS):
        return False
    if any(token in right for token in _SHARED_SOURCE_MODEL_TOKENS):
        return False
    return True


def _shared_source_package(repo: str, model: str, packages: Optional[Iterable] = None):
    """The catalogue row for ``repo`` when it is listed for a sibling model.

    ``slidia_manifest.xml`` names one repository per model. When a firmware is
    the same build for several models, its ``install_first`` names a base that
    is only listed under one of them, and the model that has no base row of its
    own must still resolve it: the catalogue declared that base for the
    software this model is listed with, and a release that publishes a single
    non-source zip is compatible with every model that package serves. The
    model families that must not mix (Y1/Y2/A5) are still refused.
    """
    # An explicit ``packages`` list is the caller's whole catalogue (the
    # console hands its own rows in); only the default lookup widens to every
    # model the app's catalogue knows.
    rows = list(packages) if packages is not None else _catalogue_packages()
    for candidate in rows:
        if repo_key(getattr(candidate, "repo", "")) != repo:
            continue
        candidate_model = str(getattr(candidate, "model", "") or "").strip().upper()
        if not _models_can_share_a_source(model, candidate_model):
            continue
        return candidate, candidate_model
    return None, ""


def running_repo_for(model: str, settings=None, hardware_type: str = "") -> str:
    """The catalogue repository of the software ``model`` is known to run.

    ``install_first_when`` and ``install_first_once`` decide on what the player
    already runs, while the tracking stores a software *name*. Resolving it
    here keeps the window and the terminal agreeing on when the gate applies.
    """
    from . import keep_data

    try:
        known = keep_data.known_running_software(model, settings, hardware_type)
        name = str((known or {}).get("software_name") or "").strip().lower()
        if not name:
            return ""
        for candidate in _catalogue_packages():
            if str(getattr(candidate, "name", "")).strip().lower() != name:
                continue
            candidate_model = str(getattr(candidate, "model", "") or "").strip().upper()
            if _models_can_share_a_source(model, candidate_model):
                return normalize_repo(getattr(candidate, "repo", ""))
        return ""
    except Exception:
        return ""


def requirement_for(
    pkg,
    model: str = "",
    packages: Optional[Iterable] = None,
    running_repo: str = "",
) -> Optional[Requirement]:
    """Resolve ``pkg``'s prerequisite against the catalogue for ``model``.

    Returns ``None`` when the firmware declares no prerequisite (or names
    itself). The declared repository is kept even when the catalogue does not
    list it for this model: the window can then explain the catalogue gap
    instead of silently installing an overlay on an unprepared player.
    """
    if pkg is None:
        return None
    repo = repo_key(getattr(pkg, "install_first", ""))
    if not repo:
        return None
    if repo == repo_key(getattr(pkg, "repo", "")):
        return None
    # ``install_first_when="a/classy,b/rondo"`` names the systems this base is
    # required *from*: the window still asks what the player runs, and the
    # named systems are the answers that need the base installed first. The
    # question is not skipped for a tracked player who runs something else —
    # that system is exactly what the answer has to be able to say.
    when_repos = tuple(
        dict.fromkeys(
            part
            for part in (
                repo_key(item)
                for item in _WHEN_SPLIT_RE.split(
                    str(getattr(pkg, "install_first_when", "") or "")
                )
            )
            if part
        )
    )
    running = normalize_repo(running_repo)
    # ``install_first_once``: the base only has to be installed the first time
    # this firmware goes on the player. It never suppresses the question — an
    # untracked player, or one that has moved to another system since, needs the
    # base exactly like a first install — and running the base says nothing
    # about whether this overlay was ever installed. The only thing it changes
    # is that the trackED firmware's own base is not required a second time.
    once = bool(getattr(pkg, "install_first_once", False))
    once_satisfied = bool(
        once and running and running == repo_key(getattr(pkg, "repo", ""))
    )
    model = str(model or getattr(pkg, "model", "") or "").strip().upper()
    version_text = str(getattr(pkg, "install_first_version", "") or "").strip()
    prerequisite = None
    for candidate in _packages_for(model, packages):
        if not model:
            # Without a target model the catalogue row cannot be proven safe.
            continue
        if repo_key(getattr(candidate, "repo", "")) != repo:
            continue
        candidate_model = str(getattr(candidate, "model", "") or "").strip().upper()
        # The base must be the catalogue row for this player model.  An empty
        # model is not a wildcard: it cannot prove that a Y1 package is safe
        # for a Y2 install.
        if model and candidate_model != model:
            continue
        prerequisite = candidate
        break
    source_model = str(getattr(prerequisite, "model", "") or "").strip().upper() if prerequisite else ""
    if prerequisite is None:
        prerequisite, source_model = _shared_source_package(repo, model, packages)
    return Requirement(
        repo=repo,
        when_repos=when_repos,
        once=once,
        once_satisfied=once_satisfied,
        version_text=version_text,
        version_range=parse_version_constraint(version_text),
        package=prerequisite,
        model=model,
        source_model=source_model if source_model != model else "",
    )


def _family_root(pkg, packages: Iterable) -> str:
    """Follow ``derivative`` links upward within ``packages`` (pure)."""
    by_repo = {
        normalize_repo(getattr(candidate, "repo", "")): candidate
        for candidate in packages
    }
    repo = normalize_repo(getattr(pkg, "repo", ""))
    seen: set[str] = set()
    while repo and repo not in seen:
        seen.add(repo)
        parent = by_repo.get(repo)
        parent_repo = normalize_repo(getattr(parent, "derivative", "")) if parent else ""
        if not parent_repo or parent_repo == repo:
            break
        repo = parent_repo
    return repo


def related_by_derivative(target, requirement: Requirement, packages: Optional[Iterable] = None) -> bool:
    """True when the prerequisite is inside the target's derivative family.

    The window uses this for Keep Data: an overlay whose base is the firmware
    it was built from can be judged by the ordinary family rules, while an
    unrelated base still has to ask the Keep Data questions (or refuse them).
    """
    if target is None or requirement is None or not requirement.repo:
        return False
    model = requirement.model or str(getattr(target, "model", "") or "")
    candidates = _packages_for(model, packages)
    if target not in candidates:
        candidates = [*candidates, target]
    target_repo = normalize_repo(getattr(target, "repo", ""))
    if target_repo == requirement.repo:
        return True
    target_derivative = normalize_repo(getattr(target, "derivative", ""))
    if target_derivative and target_derivative == requirement.repo:
        return True
    target_root = _family_root(target, candidates)
    prerequisite = next(
        (
            candidate
            for candidate in candidates
            if normalize_repo(getattr(candidate, "repo", "")) == requirement.repo
        ),
        requirement.package,
    )
    if prerequisite is None:
        return False
    return bool(target_root) and _family_root(prerequisite, candidates) == target_root


def same_firmware_family(left, right, packages: Optional[Iterable] = None) -> bool:
    """True when two catalogue rows share a ``derivative`` root.

    A firmware with no ``derivative`` link is only itself; rows that climb to
    the same root share a data partition. That relationship is the manifest's
    own statement about Keep Data — ``install_first`` says nothing about it.
    """
    if left is None or right is None:
        return False
    left_repo = normalize_repo(getattr(left, "repo", ""))
    right_repo = normalize_repo(getattr(right, "repo", ""))
    if left_repo and left_repo == right_repo:
        return True
    model = str(getattr(right, "model", "") or getattr(left, "model", "") or "")
    candidates = _packages_for(model, packages)
    if left not in candidates:
        candidates = [*candidates, left]
    if right not in candidates:
        candidates = [*candidates, right]
    left_root = _family_root(left, candidates)
    right_root = _family_root(right, candidates)
    return bool(left_root) and left_root == right_root


def when_software_name(
    requirement: Optional[Requirement],
    model: str,
    packages: Optional[Iterable] = None,
) -> str:
    """The software ``model`` runs when ``install_first_when`` names one here.

    ``install_first_when`` lists every system the base is required from, and
    the catalogue lists a firmware once per model. Of that list, only the rows
    listed for the model being installed to can be what the player is coming
    from, so one them is a name the card can print instead of talking about
    "the software already on your player". Two matches mean the catalogue has
    not singled one out, and nothing is named.
    """
    if requirement is None:
        return ""
    wanted = {
        repo_key(repo)
        for repo in (getattr(requirement, "when_repos", ()) or ())
        if repo_key(repo)
    }
    if not wanted:
        return ""
    menu_model = str(model or "").strip().upper()
    names: list[str] = []
    for row in _packages_for(model, packages):
        if repo_key(getattr(row, "repo", "")) not in wanted:
            continue
        candidate_model = str(getattr(row, "model", "") or "").strip().upper()
        if menu_model and candidate_model and candidate_model != menu_model:
            continue
        name = str(getattr(row, "name", "") or "").strip()
        if name and name.casefold() not in {item.casefold() for item in names}:
            names.append(name)
    return names[0] if len(names) == 1 else ""


def answer_for_choice(label, repo, target, requirement: Optional[Requirement]) -> str:
    """What one drop-down row says the player is running.

    The rows are built from the catalogue, so a row's repository is the
    evidence; the label is only compared when it is all there is (a local
    package can carry no repository). A repository named by
    ``install_first_when`` needs the base exactly as "Other" does: that
    attribute is the catalogue saying this system must not install the overlay
    directly.
    """
    text = str(label or "").strip()
    if not text:
        return ANSWER_OTHER
    normalized = normalize_repo(repo)
    key = repo_key(repo)
    if requirement is not None and requirement.repo:
        if key and key == repo_key(requirement.repo):
            return ANSWER_PREREQUISITE
        if not normalized and text == requirement.label():
            return ANSWER_PREREQUISITE
    target_name = str(getattr(target, "name", "") or "").strip()
    if target_name and text.casefold() == target_name.casefold():
        return ANSWER_TARGET
    target_repo = repo_key(getattr(target, "repo", ""))
    if key and target_repo and key == target_repo:
        return ANSWER_TARGET
    if requirement is not None and key:
        if key in requirement.when_repos:
            return ANSWER_OTHER
        # ``install_first_when`` names the systems this overlay may not be
        # installed over. A catalogue firmware it does not name is exactly the
        # case the attribute exists to allow, so the base is only recommended.
        # A manifest that declares no ``install_first_when`` has named nothing:
        # then only the base or the target itself can be vouched for, and every
        # other system needs the base first.
        return ANSWER_FIRMWARE if requirement.when_repos else ANSWER_OTHER
    return ANSWER_OTHER


def software_choices(
    pkg, model: str, requirement: Optional[Requirement], packages: Optional[Iterable] = None
) -> list[tuple[str, str]]:
    """``(label, repo)`` rows for the "which software is running?" menu.

    The base comes first, because it is the firmware the catalogue wants there
    first, and the target second: those are the two answers that let the
    install go ahead. The model's other firmwares follow by name — except the
    ones this entry's ``install_first_when`` names, which must not be offered as
    themselves because the catalogue says the base has to be installed before
    this one can go over them. Those are what "Other" covers, and the caller
    appends its translated row last. Rows are built from repositories, so a
    firmware the manifest starts or stops naming changes this menu with no code
    change.
    """
    rows = _packages_for(model or str(getattr(pkg, "model", "") or ""), packages)
    choices: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label, repo) -> None:
        text = str(label or "").strip()
        normalized = normalize_repo(repo)
        keys = {f"name:{text.casefold()}"}
        if normalized:
            keys.add(f"repo:{normalized}")
        if not text or keys & seen:
            return
        seen.update(keys)
        choices.append((text, normalized))

    blocked = {
        repo_key(repo)
        for repo in (getattr(requirement, "when_repos", ()) or ())
    } if requirement is not None else set()
    if requirement is not None:
        add(requirement.label(), requirement.repo)
    add(str(getattr(pkg, "name", "") or ""), getattr(pkg, "repo", ""))
    if requirement is not None and not blocked:
        # No ``install_first_when``: the catalogue named no system this overlay
        # may be installed over, so the base and the target are the only
        # answers that can go ahead. Every other system is "Other", which the
        # caller appends; listing them as themselves would duplicate that row
        # with a label that then behaves exactly like it.
        return choices
    menu_model = str(model or getattr(pkg, "model", "") or "").strip().upper()
    for candidate in sorted(
        rows, key=lambda item: str(getattr(item, "name", "") or "").casefold()
    ):
        # The question is what this player runs: a row listed for another model
        # is not an answer about it, even when the caller handed in the whole
        # catalogue (a Y2 player never runs Y1's Android 4.2 build). Rows with
        # no model at all are kept: a local package cannot prove otherwise.
        candidate_model = str(getattr(candidate, "model", "") or "").strip().upper()
        if menu_model and candidate_model and candidate_model != menu_model:
            continue
        candidate_repo = normalize_repo(getattr(candidate, "repo", ""))
        if candidate_repo and repo_key(candidate_repo) in blocked:
            # ``install_first_when`` says the base is required from this system,
            # so the menu must not offer it as an answer that proceeds: the
            # user with that system answers "Other" like any other firmware the
            # catalogue did not clear.
            continue
        add(str(getattr(candidate, "name", "") or ""), getattr(candidate, "repo", ""))
    return choices


def answer_label(choice: str, target_name: str, requirement: Requirement) -> str:
    """Map a dropdown row back to an :data:`ANSWER_*` value.

    The dropdown is built from these same labels, so the mapping never has to
    guess at a translated "Other" row: the caller passes the row it built.
    """
    return str(choice or "")


@dataclass
class InstallFirstJourney:
    """The answer and the next step for one prerequisite-gated install.

    The window owns the prompts; this object owns what the answers mean. It is
    intentionally small so a future firmware only needs manifest attributes.
    """

    target: object
    target_rel: dict = field(default_factory=dict)
    requirement: Requirement = None
    #: A prerequisite parsed from a portable package's ``pre_stage`` folder.
    #: When set, the window flashes it locally instead of downloading a
    #: catalogue release.
    local_prerequisite: object = None
    answer: str = ""
    version_ok: Optional[bool] = None
    #: The software the user said is running when it is another catalogue
    #: firmware. Used by the recommendation, and stored as what the player runs.
    running_name: str = ""

    @property
    def target_name(self) -> str:
        return str(getattr(self.target, "name", "") or "").strip()

    @property
    def prerequisite_name(self) -> str:
        return self.requirement.software_name if self.requirement else ""

    def choose(self, answer: str, reported_version: str = "") -> str:
        """Record the current-software answer and return the next step.

        ``reported_version`` is the version the tracked install knows about, if
        any. When the player says it already runs the prerequisite, a known
        satisfying version skips the version question; an unknown one asks it,
        and a known older one goes straight to the prerequisite install.
        """
        self.answer = answer
        if self.requirement is not None and self.requirement.once_satisfied:
            # ``install_first_once``: this firmware was installed on the player
            # before, so its base has already been put down once. The answer is
            # still recorded — the player may have moved to another system
            # since — but it can only offer the base, never require it.
            self.version_ok = None
            if answer in (ANSWER_TARGET, ANSWER_PREREQUISITE):
                self.version_ok = True
                return PROCEED
            return RECOMMEND_PREREQUISITE
        if answer == ANSWER_TARGET:
            self.version_ok = True
            return PROCEED
        if answer == ANSWER_PREREQUISITE:
            if self.requirement is None or self.requirement.version_range is None:
                self.version_ok = True
                return PROCEED
            known = version_tuple(reported_version)
            if known is not None:
                self.version_ok = bool(self.requirement.allows(known))
                return PROCEED if self.version_ok else NEED_PREREQUISITE
            return ASK_VERSION
        if answer == ANSWER_FIRMWARE:
            # The catalogue does not name this system, so the target may be
            # installed over it. The base is still the cleanest route, and the
            # window asks whether to take it.
            self.version_ok = None
            return RECOMMEND_PREREQUISITE
        self.version_ok = False
        return NEED_PREREQUISITE

    def decline_prerequisite(self) -> str:
        """The user chose the target over another system: install it as asked.

        The answer stays :data:`ANSWER_FIRMWARE` so Keep Data can see the
        player came from a different system, and the run goes on without the
        base.
        """
        self.version_ok = None
        return PROCEED

    def confirm_version(self, ok: bool) -> str:
        """Record the answer to the "3.2.0 or later?" question."""
        self.version_ok = bool(ok)
        return PROCEED if ok else NEED_PREREQUISITE

    def needs_prerequisite(self) -> bool:
        """True when the base is required, not merely recommended."""
        if self.answer in ("", ANSWER_TARGET, ANSWER_FIRMWARE):
            return False
        return not self.version_ok

    def keep_data_allowed(self, packages: Optional[Iterable] = None) -> bool:
        """True when the answer says the player's data belongs to this target.

        The target itself always keeps its own data. Another catalogue firmware
        keeps it only when the manifest puts both in one ``derivative`` family:
        that shared root is what makes the data partition readable, and it is
        the relationship the catalogue declares for exactly this question. The
        ``install_first`` attribute is never evidence here — it only says what
        has to be installed first.
        """
        if self.answer == ANSWER_TARGET:
            return True
        if self.answer != ANSWER_FIRMWARE or not self.running_name:
            return False
        model = str(getattr(self.target, "model", "") or "")
        if self.requirement is not None and self.requirement.model:
            model = self.requirement.model
        rows = _packages_for(model, packages)
        wanted = str(self.running_name).strip().casefold()
        running = next(
            (
                row
                for row in rows
                if str(getattr(row, "name", "")).strip().casefold() == wanted
            ),
            None,
        )
        if running is None:
            return False
        return same_firmware_family(running, self.target, rows)
