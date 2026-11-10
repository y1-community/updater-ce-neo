"""The text session must ask for the player during the USB search."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.console import progress


class ConnectPromptTests(unittest.TestCase):
    def test_search_stage_encourages_connecting_the_player(self):
        for line in (
            "Port - Hint:",
            "Power off the phone before connecting.",
            "search usb",
        ):
            self.assertEqual(progress.status_from_tool_line(line), progress.SEARCHING)
        lowered = progress.SEARCHING.casefold()
        self.assertIn("connect your player now", lowered)
        self.assertNotIn("keep unplugged", lowered)


if __name__ == "__main__":
    unittest.main()
