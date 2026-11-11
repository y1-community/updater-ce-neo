"""Release-tag and semantic-version rules shared by both interfaces.

The window (``src/catalog.py``) and the terminal session
(``src/console/releases.py``) have to read a release the same way: the label
beside a row, the sort order, the Rockbox 0.5 cutoff and the Original Software
3.0.7 cutoff all come from here. The window's catalogue functions delegate to
this module, so there is one parser rather than two copies that can drift.

Nothing here imports Qt, which is what lets the lite payload use it on a
machine without PySide6. The two label helpers take a translated
``label_for_os`` pattern because the window translates "for OS {version}" and
the terminal's default is plain English.
"""

from __future__ import annotations

import re
from datetime import datetime


def extract_tag_timestamp(tag_name: str) -> int:
    """Extract a YYYYMMDD-HHMM timestamp from a tag name as a sortable int."""

    match = re.search(r"(\d{8})-(\d{4})\b", tag_name or "")
    if match:
        try:
            return int(match.group(1)) * 10000 + int(match.group(2))
        except ValueError:
            pass
    return 0


def parse_semver(version: str):
    """Convert a dotted numeric version string into a tuple of ints."""

    if version is None:
        return None
    version_str = str(version).strip()
    if not version_str or not re.fullmatch(r"\d+(?:\.\d+)*", version_str):
        return None
    try:
        parts = tuple(int(part) for part in version_str.split("."))
        if len(parts) == 1 and parts[0] >= 1000:
            return None
        return parts
    except ValueError:
        return None


def detect_title_start_or_end_semver(title: str) -> str | None:
    """Detect a semantic version at the start or end of a release title.

    For releases where a semantic version appears at the start or end of the
    release notes title (e.g. '1.0.4 - Initial Release', 'Y1 Rockbox 0.6',
    'Y2Linux 1.0 Beta 9.2.13', or '[v2.1.0] Notes'), that version is used in
    place of the release tag on the firmware list and the site.
    """

    text = str(title or "").strip()
    if not text:
        return None

    # 1. Check start of title (e.g. "1.0.4 - Release", "v2.1.0 Notes",
    # "[0.6] Rockbox", "Release 2.0.4: Fixes").
    match_start = re.match(
        r"^(?:release\s+|version\s+)?(?:\[|\()?(?:v|v\.)?(\d+(?:\.\d+)+)(?:\]|\))?(?:$|[\s:\-_|,])",
        text,
        re.IGNORECASE,
    )
    if match_start:
        return match_start.group(1)

    # 2. Check end of title (e.g. "Y1 Rockbox 0.6", "Custom Firmware v3.1.2",
    # "Y2Linux 1.0 Beta 9.2.13"). Clean trailing non-version tokens such as
    # (Beta), (240p), [Preview] and (ADB).
    candidate = text
    for _ in range(4):
        cleaned = re.sub(
            r"[\s\-_]*[\(\[](?:beta\d*|alpha\d*|rc\d*|preview|240p|360p|adb|wifi|wi-fi|root|magisk)[\)\]]\s*$",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        if cleaned == candidate:
            break
        candidate = cleaned.strip()

    match_end = re.search(
        r"(?:^|[\s:\-_|,\(\[])(?:v|v\.)?(\d+(?:\.\d+)+)(?:\]|\))?\s*$",
        candidate,
        re.IGNORECASE,
    )
    if match_end:
        return match_end.group(1)

    return None


def semver_from_release_title(title: str):
    """Read the product version from a release title when its tag has none.

    Y2Linux titles look like ``Y2Linux 1.0 Beta 9.2.13`` while their tags are
    build identifiers (``baseline-204``). The version at start or end, or after
    Beta/Alpha/RC, is the product version.
    """

    text = str(title or "").strip()
    if not text:
        return None
    detected = detect_title_start_or_end_semver(text)
    if detected:
        return detected
    after_prerelease = re.search(
        r"\b(?:beta|alpha|rc)\s*v?(\d+(?:\.\d+)+)\b", text, re.IGNORECASE
    )
    if after_prerelease:
        return after_prerelease.group(1)
    versions = re.findall(r"(?<![\d.])(\d+\.\d+(?:\.\d+)+)(?![\d.])", text)
    return versions[0] if len(versions) == 1 else None


def parse_version_designations(version_name: str, label_for_os: str = "OS {version}") -> dict:
    """Parse a version name and extract its designations.

    ``label_for_os`` is the window's translated "for OS {version}" pattern; the
    returned designations are only used for display.
    """

    designations = []
    adjectives = [
        "compatible",
        "aware",
        "supported",
        "enabled",
        "disabled",
        "ready",
        "optimized",
        "enhanced",
    ]
    excluded_parts = ["type", "b", "base", "stable", "koensayr", "os"]

    clean_version = re.sub(r"-[a-f0-9]{16,}!?$", "", version_name or "")
    clean_version = clean_version.rstrip("-!")
    extracted_version = None

    timestamp_match = re.search(r"(\d{8})-(\d{4})\b", clean_version)
    if timestamp_match:
        extracted_version = f"{timestamp_match.group(1)}-{timestamp_match.group(2)}"

    # Compound version check, e.g. 2.4.0-koensayr-3.0.2 or 2.0.4-os-3.0.2.
    if not extracted_version:
        compound_m = re.search(
            r"(?:^|[^\d.])(?:v|v\.)?(\d+\.\d+(?:\.\d+)?)[-_](?:[a-zA-Z0-9]+[-_]|os[-_])?v?(\d+\.\d+(?:\.\d+)?)(?:[^\d.]|$)",
            clean_version,
            re.IGNORECASE,
        )
        if compound_m:
            extracted_version = compound_m.group(1)
            os_version = compound_m.group(2)
            designations.append(label_for_os.format(version=os_version))

    if not extracted_version:
        match_release = re.search(
            r"\b(?:release|realease)\s*v?(\d+(?:\.\d+)*)\b", clean_version, re.IGNORECASE
        )
        if match_release:
            extracted_version = match_release.group(1)

    if not extracted_version:
        version_pattern = re.search(r"\bv(\d+(?:\.\d+)*)\b", clean_version, re.IGNORECASE)
        if version_pattern:
            extracted_version = version_pattern.group(1)

    if not extracted_version:
        match_dot = re.search(r"\b(\d+\.\d+(?:\.\d+)*)\b", clean_version)
        if match_dot:
            extracted_version = match_dot.group(1)

    if not extracted_version and "-" in clean_version:
        for part in clean_version.split("-"):
            part_clean = part.lstrip("vV")
            if re.match(r"^\d+(?:\.\d+)+$", part_clean):
                extracted_version = part_clean
                break

    if extracted_version:
        clean_version = extracted_version

    parts = (version_name or "").split("-")
    for index, part in enumerate(parts):
        part_to_check = part[1:] if part.lower().startswith("v") and len(part) > 1 else part
        if re.match(r"^[\d.]+$", part_to_check):
            continue
        if re.match(r"^[a-f0-9]{16,}!?$", part):
            continue
        if part.lower() in excluded_parts:
            continue
        if part.lower() == "nightly":
            designations.append("Nightly")
            continue
        elif part.lower() in ("dev", "devel", "development"):
            designations.append("Dev")
            continue
        elif part.lower() in ("beta", "alpha", "rc"):
            designations.append(part.upper() if len(part) <= 3 else part.title())
            continue
        elif part == "360p":
            designations.append("360p / Y1 Theme Compatible")
        elif part in ("wifi", "wi-fi"):
            designations.append("Wi-Fi")
        elif part == "rockbox":
            designations.append("with Rockbox")
        elif part.lower() in (
            "adb",
            "usb",
            "ethernet",
            "hdmi",
            "audio",
            "video",
            "camera",
            "gps",
            "nfc",
            "lte",
            "5g",
            "fm",
        ):
            designations.append(part.upper() if len(part) <= 4 else part.title())
        elif part == "ipod" and index + 1 < len(parts) and parts[index + 1] == "theme":
            if index + 2 < len(parts) and parts[index + 2] in adjectives:
                adjective = parts[index + 2]
                designations.append(f"iPod Classic/Video Rockbox Theme {adjective.title()}")
            else:
                designations.append("iPod Classic/Video (240p) Rockbox Themes")
        elif part == "theme" and index > 0 and parts[index - 1] == "ipod":
            continue
        else:
            if index + 1 < len(parts) and parts[index + 1] in adjectives:
                adjective = parts[index + 1]
                main_part = part.replace("-", " ").title()
                designations.append(f"{main_part} {adjective.title()}")
            else:
                if part not in adjectives:
                    designations.append(part.replace("-", " ").title())

    return {
        "clean_version": clean_version.strip(),
        "designations": designations,
    }


def is_base_release_tag(tag_name) -> bool:
    """True for base ROM tags, such as ``type-a-base``.

    A base tag is the firmware tree the next builds are produced from, not a
    build anyone installs or updates to, so it is kept out of every release
    list. Only the whole word counts: ``database-3.1`` and ``baseband-2.0`` are
    not base ROMs.
    """

    words = [word for word in re.split(r"[^a-z0-9]+", str(tag_name or "").lower()) if word]
    return "base" in words


def is_stable_release(release) -> bool:
    """True for a published release that is neither nightly nor a preview.

    GitHub's ``prerelease`` flag is authoritative, and a nightly/preview/beta
    token in the tag or title is treated the same way. Everything else is a
    stable release: the catalogue must not hide it because of a tag-parsing or
    snapshot rule (only the explicit blocklist and minimum-version gates may).
    """

    if not isinstance(release, dict) or release.get("draft"):
        return False
    if release.get("prerelease"):
        return False
    if is_base_release_tag(release.get("tag_name")):
        return False
    label = f"{release.get('tag_name') or ''} {release.get('name') or ''}".lower()
    return re.search(r"\b(?:nightly|preview|beta|alpha|rc\d*)\b", label) is None


def release_version_tuple(tag_name, release_name=None):
    """Dotted version a release carries (``stable-v0.5`` → ``(0, 5)``).

    Returns ``None`` when neither the tag nor the title has a dotted version to
    read (nightlies, branch names).
    """

    title_version = None
    if release_name:
        title_str = semver_from_release_title(release_name)
        if title_str:
            title_version = parse_semver(title_str.lstrip("vV"))

    clean = str(parse_version_designations(tag_name or "").get("clean_version") or "")
    parsed = parse_semver(clean.lstrip("vV"))

    if parsed is None or (len(parsed) == 1 and parsed[0] >= 1000):
        if title_version is not None:
            return title_version
        from_tag_title = semver_from_release_title(tag_name)
        if from_tag_title:
            return parse_semver(from_tag_title.lstrip("vV"))
        return None

    if title_version is not None and len(title_version) >= 2 and len(parsed) == 1:
        return title_version

    return parsed


def release_version_ok(
    repo,
    tag_name,
    model=None,
    show_old_rockbox=False,
    show_nightly=False,
    release_name=None,
    selected_type=None,
    minimums=None,
    resolve_repo=None,
):
    """Whether a release from ``repo`` may be listed for ``model``.

    Rockbox builds before 0.5 are not compatible with Y1 units sold after March
    2026 (shipping with Innioasis OS 3.0.7 pre-installed). For model ``Y1``,
    releases before 0.5 are hidden unless ``show_old_rockbox`` is True.

    For Y2, A5 and other non-Y1 models this gating is never applied. Y1 Type B
    units are exempt from the version cutoff too: they only ever shipped
    Original Software from before 3.0.7, so the newer Type A bootloader
    restriction does not describe them. Tags with no readable version stay
    hidden because compatibility cannot be proven.

    ``minimums`` maps a repository slug to its minimum version, and
    ``resolve_repo`` maps the manifest's repo name to a fetchable slug. The
    window passes its ``config`` table; the default is no restriction, which is
    only right for a caller with no table of its own.
    """

    if not tag_name:
        return False
    is_nightly = "nightly" in str(tag_name or "").lower()
    if is_nightly and not show_nightly:
        return False

    model_str = str(model or "").upper()
    slug = (repo or "").strip()
    if "github.com/" in slug:  # tolerate a full URL where a slug is expected
        slug = slug.split("github.com/", 1)[1]
    resolved_repo = resolve_repo(slug) if resolve_repo else slug
    is_non_y1 = bool(model) and model_str != "Y1" and "Y1" not in model_str
    is_type_b = model_str == "Y1" and str(selected_type or "").upper() == "B"
    if is_non_y1:
        # Y2, A5 and non-Y1 models are completely exempt from the < 0.5 gate.
        return True

    minimum_str = (minimums or {}).get(resolved_repo)
    if not minimum_str or show_old_rockbox:
        return True

    minimum = parse_semver(str(minimum_str).lstrip("vV"))
    if minimum is None:
        return True
    version = release_version_tuple(tag_name, release_name=release_name)
    if version is None:
        # The minimum protects against old builds. A release the project
        # publishes as stable is never hidden just because its tag does not
        # use a dotted version: a future naming scheme must not make a
        # published build disappear. Historical builds that do carry a
        # version remain behind the cutoff.
        return is_stable_release({"tag_name": tag_name, "name": release_name or ""})
    if is_type_b:
        # Type B units predate the Type A hardware this cutoff protects. A
        # versioned pre-0.5 build is safe for them.
        return True
    # Pad so 0.5 and 0.5.0 compare equal, and (0, 4, 9) < (0, 5).
    width = max(len(version), len(minimum))
    version = version + (0,) * (width - len(version))
    minimum = minimum + (0,) * (width - len(minimum))
    return version >= minimum


def effective_tag(rel: dict) -> str:
    """The effective release tag for display or tracking.

    A semantic version at the start or end of the release title replaces the
    raw release tag on the firmware list and the site.
    """

    if not rel or not isinstance(rel, dict):
        return ""
    name = (rel.get("name") or "").strip()
    title_semver = detect_title_start_or_end_semver(name)
    if title_semver:
        return title_semver
    return (rel.get("tag_name") or "").strip()


def release_datestamp_label(raw: str) -> str | None:
    """Human date for a tag, or None when the tag has no datestamp."""

    text = str(raw or "")
    match = re.search(
        r"(?<!\d)((?:19|20)\d{2})[-_./]?(0[1-9]|1[0-2])[-_./]?(0[1-9]|[12]\d|3[01])(?!\d)",
        text,
    )
    year = month = day = None
    if match:
        year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    else:
        short = re.search(
            r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)",
            text,
        )
        if not short:
            return None
        yy = int(short.group(1))
        year = 2000 + yy if yy < 70 else 1900 + yy
        month, day = int(short.group(2)), int(short.group(3))
    try:
        parsed = datetime(year, month, day)
    except ValueError:
        return None
    return f"{parsed.strftime('%B')} {parsed.day} {parsed.year}"


def parse_clean_release_tag_label(
    rel,
    prefer_240p=False,
    version_only=False,
    label_for_os: str = "OS {version}",
) -> str:
    """The label beside a release row, matching the window exactly.

    The semantic version (or datestamp) comes first, followed by the tag's own
    verbs in parentheses, e.g. ``2.1.9-adb`` → ``2.1.9 (ADB)``.
    """

    tag_name = (rel.get("tag_name") or "").strip()
    name = (rel.get("name") or "").strip()
    is_prerelease = bool(rel.get("prerelease"))
    raw = tag_name or name
    if not raw:
        return ""

    # 1. Datestamp check. Same shapes the catalogue and GitHub tags use:
    # YYYYMMDD, YYYY-MM-DD, YYYY.MM.DD, YYYY/MM/DD, YYMMDD.
    date_str = release_datestamp_label(raw)

    # 2. Semantic version check (e.g. 3.1.2, 0.9.0, 1.8.2). Check a compound
    # version like 2.4.0-koensayr-3.0.2 or 2.0.4-os-3.0.2 first.
    compound_m = re.search(
        r"(?:^|[^\d.])(?:v|v\.)?(\d+\.\d+(?:\.\d+)?)[-_](?:[a-zA-Z0-9]+[-_]|os[-_])?v?(\d+\.\d+(?:\.\d+)?)(?:[^\d.]|$)",
        raw,
        re.IGNORECASE,
    )
    compound_os_version = None
    if compound_m:
        semver_str = compound_m.group(1)
        compound_os_version = compound_m.group(2)
    else:
        v_match = re.search(
            r"(?:^|[^\d.])(?:v|v\.)?(\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9]+)?)(?:[^\d.]|$)",
            raw,
            re.IGNORECASE,
        )
        semver_str = v_match.group(1) if v_match else None

    version_part = semver_str or date_str
    title_semver = detect_title_start_or_end_semver(name)
    # A tag that carries both a version and a build date shows both, the date
    # beside the version (``2.0.4 (January 3 2026)``).
    tag_date = date_str if (semver_str and date_str) else None
    if title_semver:
        version_part = title_semver
    elif not version_part:
        # Tags such as ``baseline-204`` identify a build, not a version. Their
        # release titles carry the usable version (``Y2Linux 1.0 Beta 9.2.13``).
        version_part = semver_from_release_title(name)
    if not version_part:
        return "" if version_only else (name or raw)

    # 3. Extract verbs from tag_name (and parenthesized flags in name).
    lower_raw = raw.lower()
    verbs = []

    if name:
        for paren in re.findall(r"\(([a-zA-Z0-9_\-\s]+)\)", name):
            p_clean = paren.strip().lower()
            if p_clean in ("adb", "wifi", "wi-fi", "root", "magisk", "240p", "360p"):
                if "wi" in p_clean:
                    v_str = "Wi-Fi"
                elif p_clean in ("adb", "root"):
                    v_str = p_clean.upper() if p_clean == "adb" else "Root"
                elif "240p" in p_clean:
                    v_str = "240p"
                elif "360p" in p_clean:
                    v_str = "360p"
                else:
                    v_str = paren.strip().title()
                if v_str not in verbs:
                    verbs.append(v_str)

    if compound_os_version:
        os_label = label_for_os.format(version=compound_os_version)
        if os_label not in verbs:
            verbs.append(os_label)

    # ADB
    if (
        re.search(r"\badb\b", lower_raw)
        or "-adb" in lower_raw
        or "adb-" in lower_raw
        or "(adb)" in lower_raw
    ):
        verbs.append("ADB")

    # Wi-Fi permutations: wifi, wi-fi, wi_fi, or "wi".
    if re.search(r"\b(?:wi[-_]?fi|wifi|wi)\b", lower_raw):
        verbs.append("Wi-Fi")

    # 240p / 360p. The suffix follows the row's own asset/resolution only: a
    # global prefer_240p flag must never mark 360p rows as 240p.
    is_240p = (
        rel.get("selected_resolution") == "240p"
        or "240p" in str(rel.get("asset_name", "")).lower()
        or "240p" in lower_raw
    )
    if is_240p:
        if "240p" not in verbs:
            verbs.append("240p")
    elif "360p" in lower_raw:
        verbs.append("360p")

    # Root / Magisk
    if re.search(r"\b(?:root|magisk)\b", lower_raw):
        verbs.append("Root")

    # Beta / Alpha / RC
    rc_match = re.search(r"\b(rc\d*|beta\d*|alpha\d*)\b", lower_raw)
    if rc_match:
        value = rc_match.group(1)
        verbs.append(value.upper() if "rc" in value else value.title())

    # The tag's own date reads next to the version, ahead of the other verbs.
    if tag_date and not version_only and tag_date not in verbs:
        verbs.insert(0, tag_date)

    verb_suffix = " ".join(f"({verb})" for verb in verbs)
    label = f"{version_part} {verb_suffix}".strip()
    if (
        is_prerelease
        and "[preview]" not in label.lower()
        and "(beta)" not in label.lower()
        and "(alpha)" not in label.lower()
    ):
        label += "  [preview]"
    return label


def release_sort_key(release):
    """Sortable 5-tier key so newer releases appear first in a reverse sort."""

    tag_name = (release or {}).get("tag_name", "")
    name = (release or {}).get("name", "")
    title_semver = detect_title_start_or_end_semver(name)

    numeric_version = None
    if title_semver:
        numeric_version = parse_semver(title_semver)

    version_info = parse_version_designations(tag_name)
    clean_version = str(version_info.get("clean_version", "")).lstrip("vV")

    tag_timestamp = extract_tag_timestamp(tag_name)
    if not tag_timestamp and numeric_version is None:
        numeric_version = parse_semver(clean_version)
        if numeric_version is None:
            numeric_version = parse_semver(semver_from_release_title(name))

    published_at = (release or {}).get("published_at", "")
    try:
        published_timestamp = datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        ).timestamp()
    except Exception:
        published_timestamp = 0.0

    return (
        1 if (tag_timestamp or numeric_version is not None) else 0,
        tag_timestamp,
        numeric_version or tuple(),
        published_timestamp,
        str(tag_name).lower(),
    )
