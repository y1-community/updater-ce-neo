"""A small, dependency-free QR encoder for the terminal link screen.

Only byte mode, error correction level L, and versions 1-10 are implemented.
That is enough for the URLs the updater shows (up to 271 bytes) and keeps the
lite payload free of Pillow, qrcode, or a native QR library. The result is a
boolean matrix; ``terminal_lines`` turns it into half-block text so a link page
does not need an image protocol.

The algorithm follows ISO/IEC 18004: data codewords, Reed-Solomon error
correction, block interleaving, function patterns, the eight data masks, and
the mask penalty rules. Invalid or oversized input raises ``QRError`` so the
caller can show the URL and actions without a QR block.
"""

from __future__ import annotations

from dataclasses import dataclass

# Byte-mode capacity, error correction level L.
_DATA_CODEWORDS = (0, 19, 34, 55, 80, 108, 136, 156, 194, 232, 274)
_ECC_CODEWORDS = (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18)
_ECC_BLOCKS = (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4)
_ALIGNMENT = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}


class QRError(ValueError):
    """The text cannot be represented by this compact encoder."""


@dataclass(frozen=True)
class QRCode:
    version: int
    size: int
    mask: int
    modules: tuple[tuple[bool, ...], ...]

    def is_dark(self, x: int, y: int) -> bool:
        return self.modules[y][x]


# ---------------------------------------------------------------------------
# Reed-Solomon over GF(256), primitive polynomial 0x11d.
# ---------------------------------------------------------------------------

def _gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for index in range(255):
        exp[index] = value
        log[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        exp[index] = exp[index - 255]
    return exp, log


_GF_EXP, _GF_LOG = _gf_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator(degree: int) -> list[int]:
    result = [1]
    for index in range(degree):
        factor = _GF_EXP[index]
        result.append(0)
        for position in range(len(result) - 1, 0, -1):
            result[position] ^= _gf_mul(result[position - 1], factor)
    return result


def _rs_remainder(data: bytes, degree: int) -> bytes:
    generator = _rs_generator(degree)
    remainder = [0] * degree
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for index in range(degree):
            remainder[index] ^= _gf_mul(generator[index + 1], factor)
    return bytes(remainder)


# ---------------------------------------------------------------------------
# Data codewords
# ---------------------------------------------------------------------------

def _data_codewords(text: str, version: int) -> bytes:
    payload = text.encode("utf-8")
    capacity = _DATA_CODEWORDS[version]
    bits: list[int] = []

    def append(value: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            bits.append((value >> shift) & 1)

    append(0b0100, 4)
    append(len(payload), 8 if version < 10 else 16)
    for byte in payload:
        append(byte, 8)

    limit = capacity * 8
    if len(bits) > limit:
        raise QRError("the URL is too long for a version 10 QR code")
    append(0, min(4, limit - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    codewords = bytearray()
    for index in range(0, len(bits), 8):
        value = 0
        for bit in bits[index:index + 8]:
            value = (value << 1) | bit
        codewords.append(value)
    pads = (0xEC, 0x11)
    index = 0
    while len(codewords) < capacity:
        codewords.append(pads[index % 2])
        index += 1
    return bytes(codewords)


def _interleave(version: int, codewords: bytes) -> bytes:
    block_count = _ECC_BLOCKS[version]
    ecc_length = _ECC_CODEWORDS[version]
    short_length = len(codewords) // block_count
    long_blocks = len(codewords) % block_count
    blocks: list[bytes] = []
    offset = 0
    for index in range(block_count):
        length = short_length + (1 if index >= block_count - long_blocks else 0)
        blocks.append(codewords[offset:offset + length])
        offset += length
    ecc = [_rs_remainder(block, ecc_length) for block in blocks]

    result = bytearray()
    for index in range(max(len(block) for block in blocks)):
        for block in blocks:
            if index < len(block):
                result.append(block[index])
    for index in range(ecc_length):
        for block_ecc in ecc:
            result.append(block_ecc[index])
    return bytes(result)


def _bits_from_bytes(data: bytes) -> list[bool]:
    return [bool((byte >> shift) & 1) for byte in data for shift in range(7, -1, -1)]


# ---------------------------------------------------------------------------
# Matrix construction
# ---------------------------------------------------------------------------

def _mask(mask: int, x: int, y: int) -> bool:
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (x // 3 + y // 2) % 2 == 0
    if mask == 5:
        return (x * y) % 2 + (x * y) % 3 == 0
    if mask == 6:
        return ((x * y) % 2 + (x * y) % 3) % 2 == 0
    return ((x + y) % 2 + (x * y) % 3) % 2 == 0


def _format_bits(mask: int) -> int:
    data = (0b01 << 3) | mask  # level L
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return ((data << 10) | remainder) ^ 0x5412


def _version_bits(version: int) -> int:
    remainder = version
    for _ in range(12):
        remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
    return (version << 12) | remainder


def _set(modules: list[list[bool]], function: list[list[bool]], x: int, y: int, dark: bool) -> None:
    size = len(modules)
    if 0 <= x < size and 0 <= y < size:
        modules[y][x] = bool(dark)
        function[y][x] = True


def _function_matrix(version: int) -> tuple[list[list[bool]], list[list[bool]]]:
    size = 17 + 4 * version
    modules = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    # Finder patterns and their separators.
    for center_x, center_y in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                _set(modules, function, center_x + dx, center_y + dy, distance not in (2, 4))

    # Timing patterns.
    for index in range(8, size - 8):
        _set(modules, function, index, 6, index % 2 == 0)
        _set(modules, function, 6, index, index % 2 == 0)

    # Alignment patterns, except the three that collide with finder patterns.
    for center_x in _ALIGNMENT[version]:
        for center_y in _ALIGNMENT[version]:
            if (center_x, center_y) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    _set(
                        modules,
                        function,
                        center_x + dx,
                        center_y + dy,
                        max(abs(dx), abs(dy)) != 1,
                    )

    # Reserve the format information. The bits are written per mask later.
    # Do not reserve (8, 6) or (6, 8): those two cells belong to the timing
    # patterns, and overwriting them shifts the whole data stream.
    for x, y in _format_coords(size)[0] + _format_coords(size)[1]:
        _set(modules, function, x, y, False)
    _set(modules, function, 8, size - 8, True)

    # Version information for versions 7-10.
    if version >= 7:
        bits = _version_bits(version)
        for index in range(18):
            value = bool((bits >> index) & 1)
            a = size - 11 + index % 3
            b = index // 3
            _set(modules, function, a, b, value)
            _set(modules, function, b, a, value)
    return modules, function


def _format_coords(size: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    first = [(8, index) for index in range(6)]
    first += [(8, 7), (8, 8), (7, 8)]
    first += [(14 - index, 8) for index in range(9, 15)]
    second = [(size - 1 - index, 8) for index in range(8)]
    second += [(8, size - 15 + index) for index in range(8, 15)]
    return first, second


def _draw_format(modules: list[list[bool]], mask: int) -> None:
    size = len(modules)
    bits = _format_bits(mask)
    first, second = _format_coords(size)
    for index, (x, y) in enumerate(first):
        modules[y][x] = bool((bits >> index) & 1)
    for index, (x, y) in enumerate(second):
        modules[y][x] = bool((bits >> index) & 1)
    modules[size - 8][8] = True


def _draw_data(modules: list[list[bool]], function: list[list[bool]], data: bytes, mask: int) -> None:
    bits = _bits_from_bytes(data)
    size = len(modules)
    index = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vertical in range(size):
            for column in range(2):
                x = right - column
                # The two columns left of the timing pattern run in the
                # opposite direction; without this flip every later codeword
                # lands one row off.
                upward = ((right & 2) == 0) ^ (x < 6)
                y = (size - 1 - vertical) if upward else vertical
                if function[y][x]:
                    continue
                bit = bits[index] if index < len(bits) else False
                index += 1
                modules[y][x] = bit ^ _mask(mask, x, y)
        right -= 2


def _penalty_run(values: list[bool]) -> int:
    score = 0
    run_color = values[0] if values else False
    run_length = 1
    for value in values[1:]:
        if value == run_color:
            run_length += 1
        else:
            if run_length >= 5:
                score += 3 + (run_length - 5)
            run_color = value
            run_length = 1
    if run_length >= 5:
        score += 3 + (run_length - 5)
    return score


def _penalty(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0
    for row in modules:
        score += _penalty_run(row)
    for column in range(size):
        score += _penalty_run([modules[row][column] for row in range(size)])

    for y in range(size - 1):
        for x in range(size - 1):
            color = modules[y][x]
            if (
                modules[y][x + 1] == color
                and modules[y + 1][x] == color
                and modules[y + 1][x + 1] == color
            ):
                score += 3

    pattern_a = (False, False, False, False, True, False, True, True, True, False, True)
    pattern_b = (True, False, True, True, True, False, True, False, False, False, False)
    for y in range(size):
        row = tuple(modules[y])
        for x in range(size - 10):
            if row[x:x + 11] in (pattern_a, pattern_b):
                score += 40
    for x in range(size):
        column = tuple(modules[y][x] for y in range(size))
        for y in range(size - 10):
            if column[y:y + 11] in (pattern_a, pattern_b):
                score += 40

    dark = sum(1 for row in modules for value in row if value)
    total = size * size
    balance = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += max(0, balance) * 10
    return score


def encode(text: str, version: int | None = None) -> QRCode:
    """Encode ``text`` as a QR matrix, choosing the smallest fitting version."""
    payload = str(text or "")
    if not payload:
        raise QRError("there is no URL to encode")
    try:
        data = payload.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise QRError("the URL cannot be encoded as UTF-8") from exc

    versions = [version] if version else list(range(1, 11))
    chosen = None
    raw = b""
    for candidate in versions:
        if candidate < 1 or candidate > 10:
            raise QRError("this QR encoder supports versions 1-10")
        header = 4 + (8 if candidate < 10 else 16)
        if len(data) * 8 + header <= _DATA_CODEWORDS[candidate] * 8:
            chosen = candidate
            raw = _data_codewords(payload, candidate)
            break
    if chosen is None:
        raise QRError("the URL is too long for a version 10 QR code")

    interleaved = _interleave(chosen, raw)
    best = None
    best_score = None
    best_mask = 0
    for mask in range(8):
        modules, function = _function_matrix(chosen)
        _draw_data(modules, function, interleaved, mask)
        _draw_format(modules, mask)
        score = _penalty(modules)
        if best_score is None or score < best_score:
            best = modules
            best_score = score
            best_mask = mask
    assert best is not None
    return QRCode(
        version=chosen,
        size=len(best),
        mask=best_mask,
        modules=tuple(tuple(bool(value) for value in row) for row in best),
    )


def terminal_lines(text: str, quiet: int = 2, version: int | None = None) -> list[str]:
    """Render ``text`` as half-block QR lines with a ``quiet``-module border."""
    code = encode(text, version=version)
    border = max(0, int(quiet))
    size = code.size + border * 2
    rows: list[str] = []
    for y in range(0, size, 2):
        line: list[str] = []
        for x in range(size):
            top = (
                border <= x < border + code.size
                and border <= y < border + code.size
                and code.is_dark(x - border, y - border)
            )
            bottom = (
                border <= x < border + code.size
                and border <= y + 1 < border + code.size
                and code.is_dark(x - border, y + 1 - border)
            )
            line.append("█" if top and bottom else "▀" if top else "▄" if bottom else " ")
        rows.append("".join(line))
    return rows
