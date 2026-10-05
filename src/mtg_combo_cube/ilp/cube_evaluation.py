"""Ground-truth evaluation of a cube: which combos it completes and how its cards are used.

Everything here is computed from the selected cards alone, never from solver variables,
so it is the reference for every reported combo count and utilization number.
"""

from collections.abc import Collection

from mtg_combo_cube.ilp.ilp_models import ComboData, UtilizationStats


def completable_combo_ids(selected_cards: Collection[str], combos: list[ComboData]) -> list[str]:
    """
    Return the ids of the combos the selected cards complete, in the order of `combos`.

    A combo is complete when all of its required cards are selected and every one of its
    requirement options has at least one selected card.
    """
    selected = (
        selected_cards if isinstance(selected_cards, (set, frozenset)) else set(selected_cards)
    )
    return [
        combo.id
        for combo in combos
        if combo.required_cards <= selected
        and all(not opt.cards.isdisjoint(selected) for opt in combo.requirement_options)
    ]


def card_utilization(selected_cards: Collection[str], combos: list[ComboData]) -> dict[str, int]:
    """
    Return the utilization of each selected card.

    A card's utilization is the number of completed combos in which it is a required card
    or appears in one of the combo's requirement option pools.
    """
    utilization = dict.fromkeys(selected_cards, 0)
    completed = set(completable_combo_ids(utilization.keys(), combos))

    for combo in combos:
        if combo.id not in completed:
            continue
        participants = set(combo.required_cards)
        for opt in combo.requirement_options:
            participants |= opt.cards
        for card in participants:
            if card in utilization:
                utilization[card] += 1

    return utilization


def compute_utilization_stats(utilization: dict[str, int]) -> UtilizationStats:
    """Compute the statistical summary of per-card utilization."""
    if not utilization:
        return UtilizationStats(0, 0, 0.0, 0.0, 0, 0.0)

    values = list(utilization.values())
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    std_dev = variance**0.5
    total_abs_dev = sum(abs(x - mean) for x in values)

    sorted_values = sorted(values)
    median = (
        (sorted_values[n // 2 - 1] + sorted_values[n // 2]) / 2
        if n % 2 == 0
        else float(sorted_values[n // 2])
    )

    return UtilizationStats(
        min_utilization=min(values),
        max_utilization=max(values),
        mean_utilization=mean,
        std_deviation=std_dev,
        total_absolute_deviation=int(total_abs_dev),
        median_utilization=median,
    )
