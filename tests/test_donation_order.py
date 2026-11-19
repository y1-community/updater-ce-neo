"""The donation button pairs alternate, and the post-setup dialog does not.

The completion letter and the other donation surfaces draw Ko-fi and Buy Me a
Coffee in a randomly chosen order on each render; PayPal and Revolut swap the
same way on the Support page. The dialog that follows an install keeps Ko-fi
first, because the guide has just sent the reader to that slot.
"""

import random

from src import donation_order


class _FixedRandom:
    """A random source with the answers written down, for exact orders."""

    def __init__(self, *values):
        self._values = list(values)

    def random(self):
        return self._values.pop(0)


def test_both_coffee_orders_are_reachable():
    seen = {donation_order.coffee_order(random.Random(seed)) for seed in range(200)}
    assert seen == {("kofi", "bmc"), ("bmc", "kofi")}


def test_both_payment_orders_are_reachable():
    seen = {donation_order.payment_order(random.Random(seed)) for seed in range(200)}
    assert seen == {("paypal", "revolut"), ("revolut", "paypal")}


def test_completion_order_alternates_when_not_post_setup():
    seen = {
        donation_order.completion_coffee_order(
            post_setup=False, rng=random.Random(seed)
        )
        for seed in range(200)
    }
    assert seen == {("kofi", "bmc"), ("bmc", "kofi")}


def test_post_setup_order_is_always_kofi_first():
    for seed in range(100):
        assert donation_order.completion_coffee_order(
            post_setup=True, rng=random.Random(seed)
        ) == ("kofi", "bmc")


def test_order_for_swaps_each_pair_inside_its_own_slots():
    methods = ["kofi", "bmc", "paypal", "revolut", "patreon"]
    assert donation_order.order_for(methods, _FixedRandom(0.9, 0.9)) == [
        "bmc",
        "kofi",
        "revolut",
        "paypal",
        "patreon",
    ]
    assert donation_order.order_for(methods, _FixedRandom(0.1, 0.1)) == methods


def test_completion_letter_places_the_coffee_pair_in_the_chosen_order(qapp, monkeypatch):
    """The row follows the chosen order on each render, not just at build time."""
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_post_install_urls("https://example.test/steps", "")
        page.set_device_done(donations_shown=True)
        orders = [("bmc", "kofi"), ("kofi", "bmc")]
        monkeypatch.setattr(
            donation_order,
            "completion_coffee_order",
            lambda *args, **kwargs: orders.pop(0),
        )

        page.show_completion_appeal(True)
        actions = page._appeal_actions
        assert actions.indexOf(page._bmc_btn) < actions.indexOf(page._coffee_btn)

        page.show_completion_appeal(True)
        assert actions.indexOf(page._coffee_btn) < actions.indexOf(page._bmc_btn)
    finally:
        page.deleteLater()


def test_settings_row_uses_the_random_order(qapp, monkeypatch):
    from src.ui.settings_page import SettingsPage

    monkeypatch.setattr(
        donation_order, "coffee_order", lambda rng=None: ("bmc", "kofi")
    )
    page = SettingsPage()
    try:
        layout = page._donations_actions_layout
        assert layout.indexOf(page._settings_bmc_btn) < layout.indexOf(
            page._settings_coffee_btn
        )
    finally:
        page.deleteLater()


def test_support_row_swaps_both_pairs(qapp, monkeypatch):
    from src.ui.support_page import SupportPage

    monkeypatch.setattr(
        donation_order, "coffee_order", lambda rng=None: ("bmc", "kofi")
    )
    monkeypatch.setattr(
        donation_order, "payment_order", lambda rng=None: ("revolut", "paypal")
    )
    page = SupportPage()
    try:
        page._populate_method_buttons("")
        keys = [key for _button, key in page._method_buttons]
        assert keys[:4] == [
            "donate_buy_me_a_coffee",
            "donate_kofi",
            "donate_revolut",
            "donate_paypal",
        ]
        assert "donate_patreon" in keys
    finally:
        page.deleteLater()


def test_post_setup_dialog_keeps_kofi_first(qapp, monkeypatch):
    from src.donation_dialog import DonationDialog
    from src.i18n import tr

    # A random source that would swap must not move this dialog's first slot.
    monkeypatch.setattr(
        donation_order, "coffee_order", lambda rng=None: ("bmc", "kofi")
    )
    dialog = DonationDialog(context="install_success")
    try:
        labels = [button.text() for button in dialog._pay_buttons]
        assert labels and labels[0] == tr("donate_buy_coffee")
    finally:
        dialog.deleteLater()
