"""USER-area address resolution for legacy MediaTek scatters.

The CMR flash worker does not trust a fixed MBR bias. It probes the connected
device at the addresses each candidate would produce, scores the MBR/EBR1
samples it finds against the package images, and only then chooses the write
layout. This module holds that pure logic; the caller supplies the parsed
scatter blocks and a ``read_blob(addr, length) -> bytes`` callback, so the
same code is testable with a fake device and usable by both the in-process and
``--flash-cli`` workers.

Ported from the shipped CMR build's ``FlashWorker`` helpers:
``_append_default_user_address_candidates``,
``_append_scatter_derived_user_address_candidates``,
``_probe_scatter_user_address_candidate``, ``_candidate_relative_layout_matches``
and ``_resolve_scatter_user_addr_for_candidate``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

LEGACY_MBR_USER_ADDR_BIAS = 0x800000
LEGACY_MBR_ALT_USER_ADDR_BIAS = 0xB80000
SCATTER_TAIL_ADDR_MIN = 0xFFFF0000
SCATTER_TAIL_ADDR_MAX = 0xFFFFFFFF
USER_TABLE_SAMPLE_BYTES = 512
DEFAULT_SCATTER_BLOCK_SIZE = 0x20000
LEGACY_FORMAT_SEGMENT_BYTES = 128 * 1024 * 1024
LEGACY_FORMAT_32BIT_ADDR_LIMIT = 0xFFFFFFFF
PARTITION_TABLE_PARTITIONS = frozenset(
    {"mbr", "ebr", "ebr1", "ebr2", "ebr3", "pgpt", "sgpt", "gpt", "pmt"}
)
RUNTIME_RESET_PARTITIONS = frozenset(
    {
        "metadata",
        "protect_s",
        "protect1",
        "protect_f",
        "seccfg",
        "expdb",
        "oemkeystore",
        "protect2",
        "keystore",
        "para",
        "nvdata",
    }
)
FULL_FLASH_SYSTEM_PARTITIONS = frozenset({"super", "android", "system"})
FULL_FLASH_BOOT_PARTITIONS = frozenset({"bootimg", "boot_b", "boot", "boot_a"})
# Partitions whose scatter address is not a real physical write address; the
# CMR excludes them from candidate-equivalence maps so a pseudo address can
# never make two otherwise identical layouts look different.
SCATTER_PSEUDO_USER_PARTITIONS = frozenset({"bmtpool"})


@dataclass(frozen=True)
class UserAddressCandidate:
    mode: str  # "physical" | "linear"
    bias: int
    source: str = ""


@dataclass(frozen=True)
class ProbeResult:
    score: int
    strong: bool
    notes: Tuple[str, ...] = ()


def parse_hex(value) -> Optional[int]:
    try:
        return int(str(value).strip(), 16)
    except (TypeError, ValueError):
        return None


def is_tail_addr(value) -> bool:
    # The boundary is explicit so a normal byte offset just below 4 GiB is
    # never treated as a scatter-relative address.
    return value is not None and SCATTER_TAIL_ADDR_MIN <= value <= SCATTER_TAIL_ADDR_MAX


def resolve_special_addr(addr, user_size, block_size):
    if addr is None or addr < 0:
        return None
    if not is_tail_addr(addr):
        return addr
    if not user_size or not block_size:
        return None
    resolved = int(user_size) - (addr & 0xFFFF) * int(block_size)
    return resolved if resolved >= 0 else None


def resolve_user_addr(part, candidate, user_size, block_size):
    """Byte address a scatter entry would be written at under ``candidate``."""
    if candidate.mode == "linear":
        linear = parse_hex(part.get("linear_start_addr", ""))
        if linear is None:
            return None
        addr = linear - candidate.bias
        if addr < 0:
            return None
        return resolve_special_addr(addr, user_size, block_size)

    physical = parse_hex(part.get("physical_start_addr", ""))
    if physical is not None:
        return resolve_special_addr(physical + candidate.bias, user_size, block_size)
    linear = parse_hex(part.get("linear_start_addr", ""))
    return resolve_special_addr(linear, user_size, block_size)


def user_part(part) -> bool:
    return str(part.get("region", "")).strip().upper() == "EMMC_USER"


def find_named_user_part(parts: Sequence[dict], name: str):
    wanted = name.strip().lower()
    for part in parts:
        if not user_part(part):
            continue
        if str(part.get("partition_name", "")).strip().lower() == wanted:
            return part
    return None


def scatter_candidates(parts: Sequence[dict], current: Optional[UserAddressCandidate] = None):
    """All USER-address policies the CMR probe considers for a scatter."""
    candidates: List[UserAddressCandidate] = []

    def add(mode, bias, source):
        for existing in candidates:
            if existing.mode == mode and existing.bias == bias:
                return
        candidates.append(UserAddressCandidate(mode, bias, source))

    add("physical", 0, "scatter physical_start_addr")
    add("physical", LEGACY_MBR_USER_ADDR_BIAS, "physical_start_addr + 0x800000")
    add("physical", LEGACY_MBR_ALT_USER_ADDR_BIAS, "physical_start_addr + 0xB80000")
    add("linear", 0, "scatter linear_start_addr")
    add("linear", LEGACY_MBR_USER_ADDR_BIAS, "linear_start_addr - 0x800000")
    add("linear", 0x880000, "linear_start_addr - 0x880000")
    add("linear", 0xC00000, "linear_start_addr - 0xC00000")

    if current is not None:
        add(current.mode, current.bias, current.source or "current policy")

    for part in parts:
        if not user_part(part):
            continue
        linear = parse_hex(part.get("linear_start_addr", ""))
        physical = parse_hex(part.get("physical_start_addr", ""))
        if linear is None or physical is None or is_tail_addr(physical):
            continue
        diff = linear - physical
        if diff <= 0:
            continue
        add("linear", diff, f"linear-physical delta 0x{diff:X}")
        if diff > LEGACY_MBR_USER_ADDR_BIAS:
            add(
                "linear",
                diff - LEGACY_MBR_USER_ADDR_BIAS,
                f"linear-physical delta minus boot reserved area 0x{diff - LEGACY_MBR_USER_ADDR_BIAS:X}",
            )
    return candidates


def has_mbr_signature(data: bytes) -> bool:
    return len(data) >= 512 and data[510:512] == b"\x55\xaa"


def mbr_entries_are_plausible(data: bytes) -> bool:
    """True when at least one of the four MBR/EBR entries looks usable."""
    if len(data) < 512:
        return False
    for index in range(4):
        entry = data[446 + index * 16 : 462 + index * 16]
        if len(entry) != 16:
            continue
        boot = entry[0]
        part_type = entry[4]
        start_lba = int.from_bytes(entry[8:12], "little")
        size_lba = int.from_bytes(entry[12:16], "little")
        if boot not in (0, 0x80):
            continue
        if part_type == 0 or start_lba == 0 or size_lba == 0:
            continue
        if start_lba > 0xFFFFFFFF - size_lba:
            continue
        return True
    return False


def _package_head(scatter_dir, part: dict) -> bytes:
    name = str(part.get("file_name", "") or "").strip()
    if not name or name.upper() == "NONE":
        return b""
    path = Path(scatter_dir) / name
    if not path.is_file():
        try:
            lowered = name.lower()
            for child in Path(scatter_dir).iterdir():
                if child.is_file() and child.name.lower() == lowered:
                    path = child
                    break
        except OSError:
            return b""
    try:
        with path.open("rb") as stream:
            return stream.read(USER_TABLE_SAMPLE_BYTES)
    except OSError:
        return b""


def relative_layout_matches(first_part, second_part, first_addr, second_addr) -> bool:
    first_linear = parse_hex(first_part.get("linear_start_addr", ""))
    second_linear = parse_hex(second_part.get("linear_start_addr", ""))
    first_physical = parse_hex(first_part.get("physical_start_addr", ""))
    second_physical = parse_hex(second_part.get("physical_start_addr", ""))
    actual_delta = second_addr - first_addr
    if first_linear is not None and second_linear is not None:
        return actual_delta == second_linear - first_linear
    if first_physical is not None and second_physical is not None:
        return actual_delta == second_physical - first_physical
    return False


def probe_candidate(
    parts: Sequence[dict],
    scatter_dir,
    candidate: UserAddressCandidate,
    read_blob: Callable[[int, int], Optional[bytes]],
    user_size: Optional[int],
    block_size: int,
) -> ProbeResult:
    """Score one candidate against MBR/EBR1 samples read from the device."""
    score = 0
    notes: List[str] = []
    hits = {}
    package_matches = set()

    for part_name in ("MBR", "EBR1"):
        part = find_named_user_part(parts, part_name)
        if part is None:
            continue
        addr = resolve_user_addr(part, candidate, user_size, block_size)
        if addr is None:
            continue
        if user_size is not None and (addr < 0 or addr + USER_TABLE_SAMPLE_BYTES > user_size):
            continue
        actual = read_blob(addr, USER_TABLE_SAMPLE_BYTES)
        if not actual:
            continue
        actual = bytes(actual)
        if len(actual) < USER_TABLE_SAMPLE_BYTES:
            continue
        lname = part_name.lower()
        hits[lname] = (addr, actual, part)
        if has_mbr_signature(actual):
            notes.append(f"{part_name}@0x{addr:X} has 55AA")
        if mbr_entries_are_plausible(actual):
            notes.append(f"{part_name}@0x{addr:X} has plausible partition entries")
        expected = _package_head(scatter_dir, part)
        if len(expected) == USER_TABLE_SAMPLE_BYTES and actual == expected:
            score += 8 if part_name == "MBR" else 6
            package_matches.add(lname)
            notes.append(f"{part_name}@0x{addr:X} matches package image")

    mbr_hit = hits.get("mbr")
    ebr_hit = hits.get("ebr1")
    dual_layout_match = False
    if mbr_hit is not None and ebr_hit is not None:
        mbr_addr, mbr_data, mbr_part = mbr_hit
        ebr_addr, ebr_data, ebr_part = ebr_hit
        if (
            has_mbr_signature(mbr_data)
            and has_mbr_signature(ebr_data)
            and mbr_entries_are_plausible(mbr_data)
            and mbr_entries_are_plausible(ebr_data)
            and relative_layout_matches(mbr_part, ebr_part, mbr_addr, ebr_addr)
        ):
            score = max(score, 8)
            if package_matches:
                score += 2
            notes.append(
                "MBR/EBR1 dual signatures, plausible partition entries, and matching relative layout"
            )
            dual_layout_match = True

    strong = len(package_matches) >= 2 or dual_layout_match
    if not strong and package_matches:
        notes.append("Only one package table matched; evidence is insufficient")
    return ProbeResult(score=score, strong=strong, notes=tuple(notes))


def is_pseudo_gpt_user_partition(part) -> bool:
    name = str(part.get("partition_name", "")).strip().lower()
    if not name:
        return True
    if name in {"pgpt", "sgpt", "gpt"} or name in SCATTER_PSEUDO_USER_PARTITIONS:
        return True
    if str(part.get("is_reserved", "")).strip().lower() == "true":
        return True
    return str(part.get("operation_type", "")).strip().upper() == "RESERVED"


def candidate_layout(candidate, parts, user_size, block_size):
    """Resolved USER addresses for equivalence checks between candidates.

    Mirrors the CMR's ``_candidate_user_address_map``: a tuple of
    ``(name, addr)`` in scatter order, ``None`` when any entry fails to
    resolve. Pseudo-GPT entries are excluded.
    """
    resolved = []
    for part in parts:
        if not user_part(part) or is_pseudo_gpt_user_partition(part):
            continue
        name = str(part.get("partition_name", "")).strip().lower()
        addr = resolve_user_addr(part, candidate, user_size, block_size)
        if not name or addr is None:
            return None
        resolved.append((name, addr))
    return tuple(resolved)
