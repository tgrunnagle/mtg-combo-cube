"""Ground-truth evaluation of a cube: which combos it completes and how its cards are used.

Everything here is computed from the selected cards alone, never from solver variables,
so it is the reference for every reported combo count and utilization number.
"""

from collections.abc import Collection, Mapping
from itertools import combinations

from mtg_combo_cube.ilp.ilp_models import (
    ArchetypeStats,
    ColorStats,
    ComboData,
    ComboGroupStats,
    UtilizationStats,
)

COLORS = "WUBRG"
# The ten two-color pairs, in WUBRG order
COLOR_PAIRS: tuple[str, ...] = tuple(a + b for a, b in combinations(COLORS, 2))
MONO_COLORS: tuple[str, ...] = tuple(COLORS)
COLORLESS = "C"
# The draft archetypes a cube is measured for: pairs, mono colors and colorless
ARCHETYPES: tuple[str, ...] = COLOR_PAIRS + MONO_COLORS + (COLORLESS,)


def archetype_colors(archetype: str) -> str:
    """The colors a drafter of the archetype plays: its letters, none for colorless."""
    return "" if archetype == COLORLESS else archetype


def fits_archetype(color_identity: str, archetype: str) -> bool:
    """Whether a combo of the color identity can be assembled in the archetype."""
    return set(color_identity) <= set(archetype_colors(archetype))


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


def completed_group_sizes(combo_ids: Collection[str], combos: list[ComboData]) -> dict[str, int]:
    """
    Return the number of completed variants of each combo group (distinct combo) that has
    one, given the ids of the completed variants.

    Groups are in the order of their first completed variant in `combos`.
    """
    completed = combo_ids if isinstance(combo_ids, (set, frozenset)) else set(combo_ids)
    sizes: dict[str, int] = {}
    for combo in combos:
        if combo.id in completed:
            sizes[combo.group_key] = sizes.get(combo.group_key, 0) + 1
    return sizes


def completable_group_keys(selected_cards: Collection[str], combos: list[ComboData]) -> list[str]:
    """
    Return the keys of the combo groups (distinct combos) the selected cards complete.

    A group is complete when at least one of its variants is. Keys are in the order of
    their first completed variant in `combos`.
    """
    return list(completed_group_sizes(completable_combo_ids(selected_cards, combos), combos))


def weighted_combo_count(group_sizes: Mapping[str, int], variant_weight: float) -> float:
    """
    The combo count the optimizer works with: the first completed variant of each group is
    worth 1 and every further one `variant_weight`. With a weight of 1 this is the variant
    count, with 0 the number of groups.
    """
    return sum(1 + variant_weight * (size - 1) for size in group_sizes.values())


def largest_combo_groups(
    selected_cards: Collection[str],
    combos: list[ComboData],
    limit: int = 10,
    completed_ids: Collection[str] | None = None,
) -> list[ComboGroupStats]:
    """
    Return the completed combo groups with the most completed variants, largest first.

    Each group lists the selected cards that take part in one of its completed variants,
    counted the same way as utilization (required cards and selected pool cards).
    `completed_ids` (the completed variants, when the caller already has them) saves
    recomputing them.
    """
    selected: set[str] = set(selected_cards)
    completed = set(
        completable_combo_ids(selected, combos) if completed_ids is None else completed_ids
    )
    sizes = completed_group_sizes(completed, combos)
    cards: dict[str, set[str]] = {}
    for combo in combos:
        if combo.id not in completed:
            continue
        participants = cards.setdefault(combo.group_key, set())
        participants |= combo.required_cards
        for opt in combo.requirement_options:
            participants |= opt.cards & selected

    ranked = sorted(sizes, key=lambda key: (-sizes[key], key))[:limit]
    return [ComboGroupStats(key, sizes[key], sorted(cards[key])) for key in ranked]


def _completed_variants(
    selected_cards: Collection[str],
    combos: list[ComboData],
    completed_ids: Collection[str] | None,
) -> list[ComboData]:
    """The completed variants, from `completed_ids` when the caller already has them."""
    completed = set(
        completable_combo_ids(selected_cards, combos) if completed_ids is None else completed_ids
    )
    return [combo for combo in combos if combo.id in completed]


def combos_per_archetype(
    selected_cards: Collection[str],
    combos: list[ComboData],
    completed_ids: Collection[str] | None = None,
) -> dict[str, int]:
    """
    Return the number of completed combos (groups) each draft archetype can assemble, for
    the ten color pairs, the five mono colors and "C" (colorless), in that order.

    A combo counts for an archetype when one of its completed variants has a color identity
    within the archetype's colors, so mono-colored and colorless combos count for every
    pair they fit. `completed_ids` saves recomputing the completed variants.
    """
    groups: dict[str, set[str]] = {archetype: set() for archetype in ARCHETYPES}
    for combo in _completed_variants(selected_cards, combos, completed_ids):
        for archetype, keys in groups.items():
            if fits_archetype(combo.color_identity, archetype):
                keys.add(combo.group_key)
    return {archetype: len(keys) for archetype, keys in groups.items()}


def combos_by_color_count(
    selected_cards: Collection[str],
    combos: list[ComboData],
    completed_ids: Collection[str] | None = None,
) -> dict[int, int]:
    """
    Return the number of completed combos (groups) by the number of colors they need,
    from 0 (colorless) to 5. A combo needs the colors of its completed variant with the
    fewest.
    """
    fewest: dict[str, int] = {}
    for combo in _completed_variants(selected_cards, combos, completed_ids):
        current = fewest.get(combo.group_key)
        if current is None or combo.color_count < current:
            fewest[combo.group_key] = combo.color_count
    counts = dict.fromkeys(range(len(COLORS) + 1), 0)
    for colors in fewest.values():
        counts[colors] += 1
    return counts


def compute_archetype_stats(
    selected_cards: Collection[str],
    combos: list[ComboData],
    completed_ids: Collection[str] | None = None,
) -> ArchetypeStats:
    """The distinct combos per archetype and by color count of a set of cards."""
    completed = (
        completable_combo_ids(selected_cards, combos) if completed_ids is None else completed_ids
    )
    return ArchetypeStats(
        combos_per_archetype=combos_per_archetype(selected_cards, combos, completed),
        combos_by_color_count=combos_by_color_count(selected_cards, combos, completed),
    )


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
