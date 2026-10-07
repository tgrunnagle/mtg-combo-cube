"""Ground-truth evaluation of a cube: which combos it completes and how its cards are used.

Everything here is computed from the selected cards alone, never from solver variables,
so it is the reference for every reported combo count and utilization number.
"""

import math
from collections.abc import Collection, Mapping
from itertools import combinations
from statistics import median

from mtg_combo_cube.ilp.ilp_models import (
    ArchetypeStats,
    CardMixStats,
    ColorStats,
    ComboData,
    ComboGroupStats,
    OutcomeStats,
    PopularityStats,
    UtilizationStats,
)
from mtg_combo_cube.ilp.outcomes import OutcomeCategories
from mtg_combo_cube.models import UNKNOWN_CARD, CardAttributes

COLORS = "WUBRG"
# The ten two-color pairs, in WUBRG order
COLOR_PAIRS: tuple[str, ...] = tuple(a + b for a, b in combinations(COLORS, 2))
MONO_COLORS: tuple[str, ...] = tuple(COLORS)
COLORLESS = "C"
# The draft archetypes a cube is measured for: pairs, mono colors and colorless
ARCHETYPES: tuple[str, ...] = COLOR_PAIRS + MONO_COLORS + (COLORLESS,)
# The card types the card mix is reported for, in the order of the report
CARD_TYPES: tuple[str, ...] = (
    "Creature",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Planeswalker",
    "Battle",
    "Land",
)
SPELL_TYPES = frozenset({"Instant", "Sorcery"})  # "spells" in the card mix: interaction
MANA_VALUE_CAP = 7  # the last mana value bucket of the card mix: 7 or more


def archetype_colors(archetype: str) -> str:
    """The colors a drafter of the archetype plays: its letters, none for colorless."""
    return "" if archetype == COLORLESS else archetype


def fits_archetype(color_identity: str, archetype: str) -> bool:
    """Whether a combo of the color identity can be assembled in the archetype."""
    return set(color_identity) <= set(archetype_colors(archetype))


def known_color_identity(combo: ComboData) -> str:
    """The combo's color identity, which the archetype counts need; raises if unknown."""
    if combo.color_identity is None:
        raise ValueError(f"combo {combo.id} has no color identity")
    return combo.color_identity


def color_identities_known(combos: list[ComboData]) -> bool:
    """Whether every combo carries a color identity (the preprocessor always sets one)."""
    return all(combo.color_identity is not None for combo in combos)


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
    pair they fit. `completed_ids` saves recomputing the completed variants. Every combo
    must carry a color identity (see compute_archetype_stats).
    """
    groups: dict[str, set[str]] = {archetype: set() for archetype in ARCHETYPES}
    for combo in _completed_variants(selected_cards, combos, completed_ids):
        identity = known_color_identity(combo)
        for archetype, keys in groups.items():
            if fits_archetype(identity, archetype):
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
        colors = len(known_color_identity(combo))
        current = fewest.get(combo.group_key)
        if current is None or colors < current:
            fewest[combo.group_key] = colors
    counts = dict.fromkeys(range(len(COLORS) + 1), 0)
    for colors in fewest.values():
        counts[colors] += 1
    return counts


def compute_archetype_stats(
    selected_cards: Collection[str],
    combos: list[ComboData],
    completed_ids: Collection[str] | None = None,
) -> ArchetypeStats | None:
    """
    The distinct combos per archetype and by color count of a set of cards, or None when
    a combo has no color identity (so nothing is reported rather than counting it as
    colorless).
    """
    if not color_identities_known(combos):
        return None
    completed = (
        completable_combo_ids(selected_cards, combos) if completed_ids is None else completed_ids
    )
    return ArchetypeStats(
        combos_per_archetype=combos_per_archetype(selected_cards, combos, completed),
        combos_by_color_count=combos_by_color_count(selected_cards, combos, completed),
    )


def group_features(combos: list[ComboData]) -> dict[str, frozenset[str]]:
    """
    The features of each combo group: the union over its variants (keys in instance order).

    A combo's outcome is judged from every variant it has, not only the completed ones, so
    that the model, the rule checks and the statistics all place a combo in the same
    categories. Variants of one combo are the same combo with a piece swapped and produce
    the same features in nearly every case.
    """
    features: dict[str, set[str]] = {}
    for combo in combos:
        features.setdefault(combo.group_key, set()).update(combo.features)
    return {key: frozenset(names) for key, names in features.items()}


def group_outcomes(
    combos: list[ComboData], categories: OutcomeCategories
) -> dict[str, frozenset[str]]:
    """The outcome categories of each combo group (see group_features)."""
    return {
        key: categories.categorize(features) for key, features in group_features(combos).items()
    }


def combos_per_outcome(
    selected_cards: Collection[str],
    combos: list[ComboData],
    categories: OutcomeCategories,
    completed_ids: Collection[str] | None = None,
    group_categories: Mapping[str, frozenset[str]] | None = None,
) -> OutcomeStats:
    """
    The number of completed combos (groups) in each outcome category, in table order, with
    the number in no category and the total. A combo counts for every category it is in.
    `completed_ids` and `group_categories` (the result of group_outcomes for the same combos
    and table) save recomputing them when the caller already has them.
    """
    completed = set(
        completable_combo_ids(selected_cards, combos) if completed_ids is None else completed_ids
    )
    outcomes = group_outcomes(combos, categories) if group_categories is None else group_categories
    completed_groups = {combo.group_key for combo in combos if combo.id in completed}
    counts = dict.fromkeys(categories.names, 0)
    uncategorized = 0
    for key in completed_groups:
        if not outcomes[key]:
            uncategorized += 1
        for name in outcomes[key]:
            counts[name] += 1
    return OutcomeStats(
        combos_per_outcome=counts, uncategorized=uncategorized, total=len(completed_groups)
    )


def group_popularity(combos: list[ComboData]) -> dict[str, int]:
    """The popularity of each combo group: that of its most popular variant."""
    popularity: dict[str, int] = {}
    for combo in combos:
        popularity[combo.group_key] = max(popularity.get(combo.group_key, 0), combo.popularity)
    return popularity


def popularity_stats(
    selected_cards: Collection[str],
    combos: list[ComboData],
    completed_ids: Collection[str] | None = None,
) -> PopularityStats:
    """
    How popular the completed combos (groups) are: the median popularity, the mean of
    log(1 + popularity), and the share below the median of every combo in the pool.
    """
    completed = set(
        completable_combo_ids(selected_cards, combos) if completed_ids is None else completed_ids
    )
    popularity = group_popularity(combos)
    pool_median = float(median(popularity.values())) if popularity else 0.0
    completed_groups = {combo.group_key for combo in combos if combo.id in completed}
    values = [popularity[key] for key in completed_groups]
    if not values:
        return PopularityStats(0, 0.0, 0.0, 0.0, pool_median)
    return PopularityStats(
        combo_count=len(values),
        median_popularity=float(median(values)),
        mean_log_popularity=sum(math.log1p(value) for value in values) / len(values),
        below_pool_median_share=sum(1 for value in values if value < pool_median) / len(values),
        pool_median_popularity=pool_median,
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


def compute_color_stats(
    cards: Collection[str], attributes: Mapping[str, CardAttributes]
) -> ColorStats:
    """
    Compute how the cards are distributed over the five colors, by color identity.

    attributes maps a card name to its Scryfall attributes. Cards missing from it are
    counted as unknown.
    """
    cards_per_color = dict.fromkeys(COLORS, 0)
    mono_colored = dict.fromkeys(COLORS, 0)
    multicolor = colorless = unknown = 0

    for card in cards:
        card_attributes = attributes.get(card)
        if card_attributes is None:
            unknown += 1
            continue
        identity = card_attributes.color_identity
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


def is_land(attributes: CardAttributes) -> bool:
    """Whether the card is a land; lands are left out of the card mix rules."""
    return "Land" in attributes.types


def is_spell(attributes: CardAttributes) -> bool:
    """Whether the card is an instant or sorcery."""
    return not attributes.types.isdisjoint(SPELL_TYPES)


def is_expensive(attributes: CardAttributes, mana_value: float) -> bool:
    """Whether the card's mana value is at least the threshold."""
    return attributes.mana_value >= mana_value


def compute_card_mix_stats(
    cards: Collection[str], attributes: Mapping[str, CardAttributes]
) -> CardMixStats:
    """
    Compute the make-up of the cards by type, color count and mana value.

    `multicolor` and `colorless` count what the multicolor and colorless caps count: nonland
    cards, so lands appear in `type_counts` only. Cards missing from attributes are counted
    as unknown and, as in the card mix rules, as colorless and typeless; they are left out
    of the mana values, which are over the nonland cards with data.
    """
    type_counts = dict.fromkeys(CARD_TYPES, 0)
    mana_value_counts = dict.fromkeys(range(MANA_VALUE_CAP + 1), 0)
    mana_values: list[float] = []
    mana_values_per_color: dict[str, list[float]] = {color: [] for color in COLORS}
    multicolor = colorless = unknown = 0

    for card in cards:
        card_attributes = attributes.get(card)
        known = card_attributes is not None
        if card_attributes is None:
            unknown += 1
            card_attributes = UNKNOWN_CARD
        types = card_attributes.types
        for card_type in types & set(CARD_TYPES):
            type_counts[card_type] += 1
        if is_land(card_attributes):
            continue
        if card_attributes.is_multicolor:
            multicolor += 1
        elif card_attributes.is_colorless:
            colorless += 1
        if not known:
            continue
        mana_value = card_attributes.mana_value
        mana_value_counts[min(int(mana_value), MANA_VALUE_CAP)] += 1
        mana_values.append(mana_value)
        for color in card_attributes.color_identity:
            mana_values_per_color[color].append(mana_value)

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return CardMixStats(
        card_count=len(cards),
        type_counts=type_counts,
        multicolor=multicolor,
        colorless=colorless,
        mana_value_counts=mana_value_counts,
        mean_mana_value=mean(mana_values),
        mean_mana_value_per_color={
            color: mean(values) for color, values in mana_values_per_color.items()
        },
        unknown=unknown,
    )
