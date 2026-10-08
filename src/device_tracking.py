"""Device install tracking and release reminder management — shim.

This module has been renamed to ``player_update_check.py``.
All functions and classes are re-exported here for backwards compatibility.
"""

from __future__ import annotations

import src.player_update_check as _puc
from .player_update_check import *  # noqa: F401, F403

# Re-export all public symbols and module attributes
for _k, _v in _puc.__dict__.items():
    if not _k.startswith("__"):
        globals()[_k] = _v
