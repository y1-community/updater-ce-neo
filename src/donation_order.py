"""Ordering for the pairs of donation buttons shown across the app.

The two coffee pages are equally good ways to give, and a row that reads the
same on every screen stops being noticed. Each surface asks this module for
the order it should draw, once per render, and places its widgets in that
order — so what the reader sees always follows the choice that was made.

The post-install setup dialog keeps Ko-fi in the familiar first slot: the guide
has just sent the reader there, and moving the button they were told about
costs more than the variety is worth.
"""

from __future__ import annotations

import random

#: The coffee pair, in the traditional order.
COFFEE_METHODS = ("kofi", "bmc")

#: The two payment pages that alternate among themselves.
PAYMENT_METHODS = ("paypal", "revolut")


def _maybe_swapped(pair, rng=None):
    source = rng or random
    return pair if source.random() < 0.5 else tuple(reversed(pair))


def coffee_order(rng=None) -> tuple[str, str]:
    """Ko-fi and Buy Me a Coffee, in the order this render should draw them."""
    return _maybe_swapped(COFFEE_METHODS, rng)


def payment_order(rng=None) -> tuple[str, str]:
    """PayPal and Revolut, in the order this render should draw them."""
    return _maybe_swapped(PAYMENT_METHODS, rng)


def completion_coffee_order(post_setup: bool = False, rng=None) -> tuple[str, str]:
    """The completion letter's coffee pair, or the fixed post-setup order."""
    if post_setup:
        return COFFEE_METHODS
    return coffee_order(rng)


def order_for(methods, rng=None) -> list[str]:
    """Apply both swaps to a method list, keeping every method in its own slot.

    The Support page draws all its methods in one row. The coffee pair and the
    payment pair swap inside the slots they already occupy, so Patreon and any
    other method keep their position.
    """
    ordered = list(methods)
    for pair, chooser in (
        (COFFEE_METHODS, coffee_order),
        (PAYMENT_METHODS, payment_order),
    ):
        slots = [index for index, method in enumerate(ordered) if method in pair]
        if len(slots) != len(pair):
            continue
        chosen = chooser(rng)
        for slot, method in zip(slots, chosen):
            ordered[slot] = method
    return ordered


def place(layout, widgets: dict[str, object], order, start: int = 0) -> None:
    """Put each method's widget into the next slot of ``layout`` from ``start``.

    ``layout`` is any ``QBoxLayout``-like object; the widgets are only moved
    when they are already in that layout, so a hidden or absent method cannot
    shift the rest of the row.
    """
    index = start
    for method in order:
        widget = widgets.get(method)
        if widget is None or layout.indexOf(widget) < 0:
            continue
        layout.removeWidget(widget)
        layout.insertWidget(index, widget)
        index += 1
