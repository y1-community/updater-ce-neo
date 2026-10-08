"""SP Flash Tool console configuration — the only way to pass an auth file.

Console mode has no ``--auth`` switch: the tool's own usage text lists only
``-i -s -c -d -e -p -r -t -rsc -b -h``. The payload's ``console_mode.xsd`` and
the bundled ConfigFile help page show that an authentication file is handed
over in the console configuration file instead::

    <flashtool-config version="2.0">
      <general>
        <chip-name>MT6572</chip-name>
        <storage-type>EMMC</storage-type>
        <download-agent>MTK_AllInOne_DA.bin</download-agent>
        <scatter>MT6572_Android_scatter.txt</scatter>
        <authentication>custom.auth</authentication>
        <connection type="BromUSB" high-speed="true" without-battery="true" .../>
      </general>
      <commands><format-download/></commands>
    </flashtool-config>

``without-battery="true"`` is the documented equivalent of the CLI's
``-t without`` ("power supply comes from USB, and battery is optional"), and
``-r`` still redirects the debug log to us alongside ``-i``, so the app keeps
parsing the same progress lines.

Auth files are optional — most targets are not secure-booted — so the plain
``-c format-download -s ... -d ... -t without -r`` command line stays the
default and this file is written only when a user has actually chosen one.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "console_config.xml"
STORAGE_TYPES = ("NAND", "EMMC", "UFS", "NOR", "SDMMC")
# The schema's chip-name pattern: MT + four digits + optional letter, or ELBRUS.
_CHIP_RE = re.compile(r"^(MT\d{4}[A-Z]?|ELBRUS)$")
# "platform: MT6572" / "storage: EMMC" in the scatter's general section.
_GENERAL_RE = re.compile(
    r"^\s*-?\s*(platform|storage)\s*:\s*([A-Za-z0-9_]+)", re.IGNORECASE
)


class SpConfigError(Exception):
    """A console configuration could not be built from what we know."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def config_dir() -> Path:
    """Directory holding generated console configuration files."""
    from .downloads import downloads_dir

    d = downloads_dir().parent / "sp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_scatter_general(scatter) -> dict:
    """Platform and storage type as declared by a scatter file's general block.

    Every stock scatter carries both (``platform: MT6572``, ``storage: EMMC``);
    they are the two values the console configuration file requires and neither
    can be guessed safely.
    """
    info = {"platform": "", "storage": ""}
    try:
        text = Path(scatter).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        logger.debug("Could not read scatter %s: %s", scatter, e)
        return info
    for line in text.splitlines():
        if info["platform"] and info["storage"]:
            break
        m = _GENERAL_RE.match(line)
        if m:
            key = m.group(1).lower()
            if not info[key]:
                info[key] = m.group(2).upper()
    return info


def auth_file_usable(path) -> bool:
    """True when ``path`` is a real, non-empty authentication file."""
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def build_console_config_xml(scatter, da_file, auth_file) -> str:
    """Console configuration XML for a flash that needs an auth file.

    Raises :class:`SpConfigError` when the scatter does not tell us the chip
    or storage type, since a wrong guess there is worse than no flash.
    """
    if not auth_file:
        raise SpConfigError("no_auth_file", "no authentication file was given")
    general = parse_scatter_general(scatter)
    chip = general["platform"]
    if not _CHIP_RE.match(chip or ""):
        raise SpConfigError("missing_platform", f"scatter platform was {chip!r}")
    storage = general["storage"]
    if storage not in STORAGE_TYPES:
        raise SpConfigError("missing_storage", f"scatter storage was {storage!r}")

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8" ?>',
            '<flashtool-config version="2.0">',
            "    <general>",
            f"        <chip-name>{escape(str(chip))}</chip-name>",
            f"        <storage-type>{escape(str(storage))}</storage-type>",
            f"        <download-agent>{escape(str(da_file))}</download-agent>",
            f"        <scatter>{escape(str(scatter))}</scatter>",
            f"        <authentication>{escape(str(auth_file))}</authentication>",
            '        <connection type="BromUSB" high-speed="true"'
            ' without-battery="true" timeout-count="3600000" com-port="" />',
            "    </general>",
            "    <commands>",
            "        <format-download/>",
            "    </commands>",
            "</flashtool-config>",
            "",
        ]
    )


def write_console_config(
    scatter, da_file, auth_file, target_dir: Optional[Path] = None
) -> Path:
    """Write the console configuration for an authenticated flash."""
    directory = Path(target_dir) if target_dir else config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILE_NAME
    path.write_text(
        build_console_config_xml(scatter, da_file, auth_file), encoding="utf-8"
    )
    return path
