from __future__ import annotations

"""Bundled 360p theme pack archive shipped in the app payload.

Historically the launcher extracted a local ``assets/360p/themes-themepack``
tree at build time. That tree is large enough to dominate portable-app and
Inno Setup file placement time, so the app now ships the same payload as a
single compressed archive instead. The runtime code still writes to a
``.rockbox`` target; it extracts the bundled zip over that target so
same-name files are overwritten and any pre-existing files are never deleted.

Layout: the zip mirrors ``assets/360p/themes-themepack/.rockbox`` directly
(no top-level folder). See :func:`bundled_themepack_zip` and
:func:`extract_theme_pack_zip` in ``theme_pack.py``.
"""

from pathlib import Path

#: Current bundled archive, relative to the project root / asset bundle.
BUNDLED_THEME_PACK_ZIP_NAME = "themes-themepack.zip"
