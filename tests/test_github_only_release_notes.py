"""Release notes: parts a project marks as GitHub-only are left out.

Y2Linux's release text keeps SP Flash Tool steps and the list of attached files
between ``<!-- github-only -->`` and ``<!-- /github-only -->``: right on the
GitHub release page, wrong here, where the app flashes the player itself. Text
without markers, or with markers that do not pair up, must come through
unchanged, so no other project's notes can lose a line.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src.markdown_render import without_github_only

NOTES = (
    "**Y2Linux 1.0 Beta 9.3.19**: update notifications.\n\n"
    "## What's new\n- **Update Notifications**: Settings → System.\n\n"
    "<!-- github-only -->\n\n"
    "## Install with SP Flash Tool\n1. Unpack both.\n\n"
    "## Files\n- `manifest.json`\n\n"
    "<!-- /github-only -->\n\n"
    "## Known issues\n- None.\n\n"
    "<!-- github-only -->\n\n"
    "## Sources unchanged since an earlier release\n- linux.tar.xz\n\n"
    "<!-- /github-only -->\n"
)


def test_marked_parts_are_left_out():
    shown = without_github_only(NOTES)
    assert "What's new" in shown and "Known issues" in shown
    for hidden in ("SP Flash Tool", "## Files", "Sources unchanged", "github-only"):
        assert hidden not in shown
    assert "\n\n\n" not in shown


def test_windows_line_endings():
    shown = without_github_only(NOTES.replace("\n", "\r\n"))
    assert "SP Flash Tool" not in shown and "Known issues" in shown


@pytest.mark.parametrize(
    "text",
    [
        "",
        "## Changes\n- Plain notes from another project.",
        "<!-- some other comment -->\ntext",
        "a\n<!-- github-only -->\nnever closed",
        "a\n<!-- /github-only -->\nb\n<!-- github-only -->\nc",
        "inline <!-- github-only --> marker\n<!-- /github-only -->",
    ],
)
def test_text_without_paired_markers_is_unchanged(text):
    assert without_github_only(text) == text


def test_none_reads_as_empty():
    assert without_github_only(None) == ""
