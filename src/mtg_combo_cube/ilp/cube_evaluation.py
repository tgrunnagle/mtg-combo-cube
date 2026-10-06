"""Ground-truth evaluation of a cube: which combos it completes and how its cards are used.

Everything here is computed from the selected cards alone, never from solver variables,
so it is the reference for every reported combo count and utilization number.
"""

from collections.abc import Collection, Mapping

from mtg_combo_cube.ilp.ilp_models import (
    ColorStats,
    ComboData,
    ComboGroupStats,
    UtilizationStats,
)

COLORS = "WUBRG"


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


def completable_group_keys(selected_cards: Collection[str], combos: list[ComboData]) -> list[str]:
    """
    Return the keys of the combo groups (distinct combos) the selected cards complete.

    A group is complete when at least one of its variants is. Keys are in the order of
    their first completed variant in `combos`.
    """
    completed = set(completable_combo_ids(selected_cards, combos))
    return list(dict.fromkeys(combo.group_key for combo in combos if combo.id in completed))


def combo_group_sizes(selected_cards: Collection[str], combos: list[ComboData]) -> dict[str, int]:
    """Return the number of completed variants of each completed combo group."""
    completed = set(completable_combo_ids(selected_cards, combos))
    sizes: dict[str, int] = {}
    for combo in combos:
        if combo.id in completed:
            sizes[combo.group_key] = sizes.get(combo.group_key, 0) + 1
    return sizes


def largest_combo_groups(
    selected_cards: Collection[str], combos: list[ComboData], limit: int = 10
) -> list[ComboGroupStats]:
    """
    Return the completed combo groups with the most completed variants, largest first.

    Each group lists the selected cards that take part in one of its completed variants,
    counted the same way as utilization (required cards and selected pool cards).
    """
    selected: set[str] = set(selected_cards)
    completed = set(completable_combo_ids(selected, combos))
    sizes: dict[str, int] = {}
    cards: dict[str, set[str]] = {}
    for combo in combos:
        if combo.id not in completed:
            continue
        sizes[combo.group_key] = sizes.get(combo.group_key, 0) + 1
        participants = cards.setdefault(combo.group_key, set())
        participants |= combo.required_cards
        for opt in combo.requirement_options:
            participants |= opt.cards & selected

    ranked = sorted(sizes, key=lambda key: (-sizes[key], key))[:limit]
    return [ComboGroupStats(key, sizes[key], sorted(cards[key])) for key in ranked]


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


def compute_color_stats(cards: Collection[str], color_identities: Mapping[str, str]) -> ColorStats:
    """
    Compute how the cards are distributed over the five colors.

    color_identities maps a card name to its color identity as a string of WUBRG letters
    (empty for colorless). Cards missing from it are counted as unknown.
    """
    cards_per_color = dict.fromkeys(COLORS, 0)
    mono_colored = dict.fromkeys(COLORS, 0)
    multicolor = colorless = unknown = 0

    for card in cards:
        identity = color_identities.get(card)
        if identity is None:
            unknown += 1
            continue
        for color in identity:
            cards_per_color[color] += 1
        if not identity:
            colorless += 1
        elif len(identity) == 1:
            mono_colored[identity] += 1
        else:
            multicolor += 1

    mean = sum(cards_per_color.values()) / len(COLORS)
    variance = sum((count - mean) ** 2 for count in cards_per_color.values()) / len(COLORS)
    return ColorStats(
        cards_per_color=cards_per_color,
        mono_colored=mono_colored,
        multicolor=multicolor,
        colorless=colorless,
        unknown=unknown,
        variance=variance,
        std_deviation=variance**0.5,
    )
