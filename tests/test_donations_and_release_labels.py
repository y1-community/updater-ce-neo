"""Unit tests for donation line formatting, alternating rotation,
compound release names (e.g. Koensayr), and duplicate status suppression.
"""

from datetime import datetime
import re
import pytest
from PySide6.QtWidgets import QApplication, QLabel

from src.i18n import translator, tr
from src.donors import format_donation_when
from src.donation_dialog import DonationStatusBar
from src.catalog import parse_clean_release_tag_label, parse_version_designations
from src.ui.flash_page import _FlowRow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def strip_html(text: str) -> str:
    """Strip HTML tags for clean text matching."""
    return re.sub(r"<[^>]+>", "", text).strip()


class TestDonationFormattingAndExamples:
    def test_user_examples_formatting(self, qapp):
        translator().set_language("en")
        # Reference date: October 27, 2026
        now = datetime(2026, 10, 27, 12, 0, 0)

        # 1. Top supporter Alexis P bought us 90 coffees last year.
        dt_alexis = datetime(2025, 9, 2)
        assert format_donation_when(dt_alexis, now=now) == "last year"

        # 2. Top supporter Zangu bought us 50 coffees in August.
        dt_zangu = datetime(2026, 8, 23)
        assert format_donation_when(dt_zangu, now=now) == "in August"

        # 3. Bryon bought us 5 coffees last month
        dt_bryon = datetime(2026, 9, 15)
        assert format_donation_when(dt_bryon, now=now) == "last month"

        # 4. Mason M donated $5.79 by Revolut in July
        dt_mason = datetime(2026, 7, 5)
        assert format_donation_when(dt_mason, now=now) == "in July"

        # 5. Lang C donated $9.07 by PayPal in June
        dt_lang = datetime(2026, 6, 7)
        assert format_donation_when(dt_lang, now=now) == "in June"

        donations = [
            {"name": "Alexis P", "amount": 90, "method": "Ko-Fi", "dt": dt_alexis, "url": "https://ko-fi.com/alexis"},
            {"name": "Zangu", "amount": 50, "method": "Ko-Fi", "dt": dt_zangu, "url": "https://ko-fi.com/zangu"},
            {"name": "Bryon", "amount": 5, "method": "Ko-Fi", "dt": dt_bryon, "url": ""},
            {"name": "Mason M", "amount": 5.79, "method": "Revolut Pay", "dt": dt_mason, "url": ""},
            {"name": "Lang C", "amount": 9.07, "method": "PayPal", "dt": dt_lang, "url": ""},
        ]

        bar = DonationStatusBar(donations=donations)
        lines = [strip_html(x) for x in bar._donor_lines]

        # Verify all 5 lines match the exact requirements
        alexis_line = next((l for l in lines if "Alexis P" in l), "")
        assert "Top supporter Alexis P bought us 90 coffees last year" in alexis_line

        zangu_line = next((l for l in lines if "Zangu" in l), "")
        assert "Top supporter Zangu bought us 50 coffees in August" in zangu_line

        bryon_line = next((l for l in lines if "Bryon" in l), "")
        assert "Bryon bought us 5 coffees last month" in bryon_line
        assert "Top supporter" not in bryon_line

        mason_line = next((l for l in lines if "Mason M" in l), "")
        assert "Mason M donated $5.79 by Revolut in July" in mason_line
        assert "Top supporter" not in mason_line

        lang_line = next((l for l in lines if "Lang C" in l), "")
        assert "Lang C donated $9.07 by PayPal in June" in lang_line
        assert "Top supporter" not in lang_line

        bar.deleteLater()

    def test_multi_donation_aggregation(self, qapp):
        translator().set_language("en")
        now = datetime(2026, 10, 27, 12, 0, 0)

        # Luis C has multiple donations totaling $19.44 across Patreon
        dt_latest = datetime(2026, 2, 27)
        donations = [
            {"name": "Luis C", "amount": 3.0, "method": "Patreon", "dt": datetime(2025, 7, 30)},
            {"name": "Luis C", "amount": 3.0, "method": "Patreon", "dt": datetime(2025, 9, 29)},
            {"name": "Luis C", "amount": 3.0, "method": "Patreon", "dt": datetime(2025, 10, 30)},
            {"name": "Luis C", "amount": 3.48, "method": "Patreon", "dt": datetime(2025, 12, 30)},
            {"name": "Luis C", "amount": 3.48, "method": "Patreon", "dt": datetime(2026, 1, 30)},
            {"name": "Luis C", "amount": 3.48, "method": "Patreon", "dt": dt_latest},
        ]

        bar = DonationStatusBar(donations=donations)
        lines = [strip_html(x) for x in bar._donor_lines]
        luis_line = next((l for l in lines if "Luis C" in l), "")

        # Multiple donations qualify as top supporter, totaled as $19.44
        assert "Top supporter Luis C" in luis_line
        assert "19.44" in luis_line
        assert "Patreon" in luis_line

        bar.deleteLater()

    def test_single_coffee_pluralization(self, qapp):
        translator().set_language("en")
        donations = [
            {"name": "Jimmy G", "amount": 1, "method": "Ko-Fi", "dt": datetime(2026, 8, 17)},
        ]
        bar = DonationStatusBar(donations=donations)
        lines = [strip_html(x) for x in bar._donor_lines]
        assert "Jimmy G bought us 1 coffee" in lines[0]
        assert "coffees" not in lines[0]
        bar.deleteLater()

    def test_alternating_rotation(self, qapp):
        translator().set_language("en")
        donations = [
            {"name": "Alexis P", "amount": 90, "method": "Ko-Fi", "dt": datetime(2025, 9, 2)},
            {"name": "Zangu", "amount": 50, "method": "Ko-Fi", "dt": datetime(2026, 8, 23)},
        ]
        bar = DonationStatusBar(donations=donations)
        bar.show()

        # Turn 0 on startup: monthly goal or initial display
        # Ticking _rotate() should strictly alternate:
        # Turn 1: Donation line
        # Turn 2: Message line (Goal or Appeal)
        # Turn 3: Donation line
        # Turn 4: Message line

        bar._cycle_turn = 0
        bar._rotate()  # cycle_turn = 1 (Donation)
        assert bar._donor_label.isVisible()
        assert not bar._goal_bar.isVisible()
        assert "Alexis P" in bar._donor_label.text() or "Zangu" in bar._donor_label.text()

        bar._rotate()  # cycle_turn = 2 (Message line)
        # Message line: either goal (goal_label + goal_bar visible) or appeal
        if bar._showing_goal:
            assert bar._goal_label.isVisible()
            assert bar._goal_bar.isVisible()
        else:
            assert bar._donor_label.isVisible()
            assert not bar._goal_bar.isVisible()

        bar._rotate()  # cycle_turn = 3 (Donation)
        assert bar._donor_label.isVisible()
        assert not bar._goal_bar.isVisible()
        assert "Alexis P" in bar._donor_label.text() or "Zangu" in bar._donor_label.text()

        bar.close()
        bar.deleteLater()


class TestCompoundReleaseLabels:
    def test_koensayr_compound_versions(self):
        translator().set_language("en")

        # Koensayr with OS 3.0.2
        rel_302 = {"tag_name": "2.4.0-koensayr-3.0.2", "name": "Koensayr 2.4.0"}
        label_302 = parse_clean_release_tag_label(rel_302)
        assert label_302 == "2.4.0 (for OS 3.0.2)"

        # Koensayr with OS 3.0.7
        rel_307 = {"tag_name": "2.4.0-koensayr-3.0.7", "name": "Koensayr 2.4.0"}
        label_307 = parse_clean_release_tag_label(rel_307)
        assert label_307 == "2.4.0 (for OS 3.0.7)"

        # Koensayr with OS 3.0.2 in designation parser
        info = parse_version_designations("2.4.0-koensayr-3.0.2")
        assert info.get("clean_version") == "2.4.0"
        assert "for OS 3.0.2" in info.get("designations", [])

    def test_compound_version_permutations(self):
        translator().set_language("en")

        # User's exact prompt example: 2.0.4 (for OS 3.0.2)
        rel1 = {"tag_name": "v2.0.4-koensayr-v3.0.2"}
        assert parse_clean_release_tag_label(rel1) == "2.0.4 (for OS 3.0.2)"

        rel2 = {"tag_name": "2.0.4-os-3.0.2"}
        assert parse_clean_release_tag_label(rel2) == "2.0.4 (for OS 3.0.2)"

        # With other verbs like ADB
        rel3 = {"tag_name": "2.4.0-koensayr-3.0.2-adb"}
        assert parse_clean_release_tag_label(rel3) == "2.4.0 (for OS 3.0.2) (ADB)"


class TestFlowRowDeduplication:
    def test_flow_row_hides_duplicate_label(self, qapp):
        lbl1 = QLabel("Installing Rockbox Themes & Fonts...")
        lbl2 = QLabel("Installing Rockbox Themes & Fonts...")

        flow = _FlowRow(lbl1, lbl2)
        flow.show()
        flow.refresh()

        # Duplicate label should be hidden automatically
        assert lbl1.isVisible()
        assert not lbl2.isVisible()

        # When second label has distinct text, it becomes visible again
        lbl2.setText("120s remaining")
        flow.refresh()
        assert lbl1.isVisible()
        assert lbl2.isVisible()

        flow.close()
        flow.deleteLater()
