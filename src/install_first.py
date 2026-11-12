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

#: The next step the window must take.
PROCEED = "proceed"
ASK_VERSION = "ask_version"
NEED_PREREQUISITE = "need_prerequisite"

_VERSION_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?![\d.])")
_REQUIRED_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def normalize_repo(value) -> str:
    """Canonical ``owner/name`` spelling, or ``""`` when it is not a handle."""
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if not text or not _REQUIRED_REPO_RE.match(text):
        return ""
    return text.casefold()


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
        if normalize_repo(getattr(candidate, "repo", "")) != repo:
            continue
        candidate_model = str(getattr(candidate, "model", "") or "").strip().upper()
        if not _models_can_share_a_source(model, candidate_model):
            continue
        return candidate, candidate_model
    return None, ""


def requirement_for(pkg, model: str = "", packages: Optional[Iterable] = None) -> Optional[Requirement]:
    """Resolve ``pkg``'s prerequisite against the catalogue for ``model``.

    Returns ``None`` when the firmware declares no prerequisite (or names
    itself). The declared repository is kept even when the catalogue does not
    list it for this model: the window can then explain the catalogue gap
    instead of silently installing an overlay on an unprepared player.
    """
    if pkg is None:
        return None
    repo = normalize_repo(getattr(pkg, "install_first", ""))
    if not repo:
        return None
    if repo == normalize_repo(getattr(pkg, "repo", "")):
        return None
    model = str(model or getattr(pkg, "model", "") or "").strip().upper()
    version_text = str(getattr(pkg, "install_first_version", "") or "").strip()
    prerequisite = None
    for candidate in _packages_for(model, packages):
        if not model:
            # Without a target model the catalogue row cannot be proven safe.
            continue
        if normalize_repo(getattr(candidate, "repo", "")) != repo:
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
        self.version_ok = False
        return NEED_PREREQUISITE

    def confirm_version(self, ok: bool) -> str:
        """Record the answer to the "3.2.0 or later?" question."""
        self.version_ok = bool(ok)
        return PROCEED if ok else NEED_PREREQUISITE

    def needs_prerequisite(self) -> bool:
        return self.answer not in ("", ANSWER_TARGET) and not self.version_ok

    def keep_data_allowed(self) -> bool:
        """Keep Data is only safe when the player already runs the target.

        The target may be an overlay that carries the player's data forward
        only when that data already belongs to it. A player coming from the
        base (or anything else) starts the target on a fresh data partition,
        matching the existing Keep Data rules for a switch between systems.
        """
        return self.answer == ANSWER_TARGET
