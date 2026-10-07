"""Data structures for ILP optimization."""

import math
from dataclasses import dataclass, fields
from fractions import Fraction
from typing import Any


def hundredths(value: float) -> Fraction:
    """
    A share or ratio rounded to hundredths, as an integer fraction for the model: 0.333
    becomes 33/100 and 0.15 becomes 3/20. The value must be finite.
    """
    return Fraction(round(value * 100), 100)


# =============================================================================
# Solver Input Models - Used to build and run the ILP optimization
# =============================================================================


@dataclass
class CandidateCard:
    """A card that could be included in the cube, with all its relationships."""

    name: str
    combo_ids: frozenset[str]  # Combo IDs where this card is directly required (from 'uses')
    requirement_group_keys: frozenset[str]  # Requirement group keys this card satisfies

    @property
    def template_count(self) -> int:
        """Number of distinct requirement templates this card satisfies."""
        return len(self.requirement_group_keys)

    @property
    def is_multi_template(self) -> bool:
        """Whether this card satisfies multiple requirement templates."""
        return self.template_count >= 2


@dataclass
class RequirementOption:
    """A single template requirement for a combo, with cards that can satisfy it.

    This is a per-combo structure - each combo has its own list of RequirementOptions.
    """

    template_name: str  # Human-readable name for display
    group_key: str  # Canonical key for deduplication across combos
    cards: frozenset[str]  # Cards that satisfy this requirement


@dataclass
class ComboData:
    """Preprocessed combo data for ILP optimization.

    This is a simplified representation of a Variant that contains
    only the information needed for constraint generation.
    """

    id: str
    required_cards: frozenset[str]  # Card names from 'uses' field
    requirement_options: list[RequirementOption]  # For each 'requires', template + valid cards
    popularity: int  # For tiebreaking (higher = better)
    # The combo this variant is one way of assembling: the sorted Spellbook combo ids the
    # variant belongs to ('of'), joined with "+". Variants with the same key are the same
    # combo with a piece swapped. Defaults to the variant id (a group of one).
    group_key: str = ""
    # Color identity of the variant as given by Spellbook: WUBRG letters in that order,
    # "" for colorless, None when unknown (an instance built without identities). It covers
    # the required cards; a template requirement filled by a colored card can add a color
    # the identity does not show.
    color_identity: str | None = None
    # What the variant does: the names of the features it produces on Spellbook ("Infinite
    # colored mana", "Infinite creature ETB", ...). Empty when unknown.
    features: frozenset[str] = frozenset()
    # Spellbook's bracket tag, a single letter (power level); "" when unknown
    bracket_tag: str = ""

    def __post_init__(self) -> None:
        if not self.group_key:
            self.group_key = self.id

    def all_requirements_resolvable(self) -> bool:
        """Check if all template requirements have at least one card option."""
        return all(len(opt.cards) > 0 for opt in self.requirement_options)


class CardMixRuleError(ValueError):
    """An invalid CardMixRules setting; `field` names the setting."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field} {message}")
        self.field = field


@dataclass(frozen=True)
class CardMixRules:
    """
    Phase 2 limits on the make-up of the cube by card type, mana value and color count,
    each a share of the cube size, rounded to hundredths. A share of 0 disables its rule (a
    cap of 1 too, since every card may then count), as does a ratio of 0. Lands are left
    out of every rule: they are combo pieces, not part of the mix being shaped.
    """

    # At most this share of the cube may be multicolor cards (two or more colors)
    max_multicolor_share: float = 0.15
    # At most this share may be colorless nonland cards (cards without Scryfall data count
    # here)
    max_colorless_share: float = 0.25
    # At most this share may have a mana value of expensive_mana_value (above 0) or more
    max_expensive_share: float = 0.2
    expensive_mana_value: float = 5
    # At most this share may be creatures
    max_creature_share: float = 0.6
    # At least this share must be instants or sorceries
    min_spell_share: float = 0.05
    # No mono color may have more than this many times the mono-colored cards of another
    # (the color balance form on mono-colored cards); 0 disables, otherwise at least 1
    mono_color_ratio: float = 0

    CAP_FIELDS = (
        "max_multicolor_share",
        "max_colorless_share",
        "max_expensive_share",
        "max_creature_share",
    )
    SHARE_FIELDS = (*CAP_FIELDS, "min_spell_share")

    def __post_init__(self) -> None:
        for field in fields(self):
            if not math.isfinite(getattr(self, field.name)):
                raise CardMixRuleError(
                    field.name, f"must be a number, got {getattr(self, field.name)}"
                )
        for name in self.SHARE_FIELDS:
            share = getattr(self, name)
            if not 0 <= share <= 1:
                raise CardMixRuleError(name, f"must be between 0 and 1, got {share}")
            if share > 0 and hundredths(share) == 0:
                raise CardMixRuleError(
                    name, f"of {share} rounds to 0 in hundredths; use 0 to disable the rule"
                )
        if self.expensive_mana_value <= 0:
            raise CardMixRuleError(
                "expensive_mana_value", f"must be above 0, got {self.expensive_mana_value}"
            )
        if not (self.mono_color_ratio == 0 or self.mono_color_ratio >= 1):
            raise CardMixRuleError(
                "mono_color_ratio", f"must be 0 or at least 1, got {self.mono_color_ratio}"
            )

    def enabled(self) -> dict[str, float]:
        """The settings in force, for the stats file: every rule that is not disabled."""
        settings: dict[str, float] = {}
        for name in self.CAP_FIELDS:
            if 0 < hundredths(getattr(self, name)) < 1:
                settings[name] = getattr(self, name)
        if "max_expensive_share" in settings:
            settings["expensive_mana_value"] = self.expensive_mana_value
        if hundredths(self.min_spell_share) > 0:
            settings["min_spell_share"] = self.min_spell_share
        if self.mono_color_ratio > 0:
            settings["mono_color_ratio"] = self.mono_color_ratio
        return settings


DEFAULT_CARD_MIX = CardMixRules()  # the default rules, shared by every layer's signature


@dataclass
class RequirementPool:
    """Aggregated requirement info for coverage constraint generation.

    Unlike RequirementOption (per-combo), this aggregates across ALL combos
    that share the same group_key.
    """

    group_key: str
    display_name: str
    combo_count: int  # Total combos using this requirement
    pool_cards: frozenset[str]  # All cards that can satisfy this requirement


# =============================================================================
# Stats Models - Computed from optimization results for reporting
# =============================================================================


@dataclass
class UtilizationStats:
    """Statistics about card utilization across completable combos."""

    min_utilization: int
    max_utilization: int
    mean_utilization: float
    std_deviation: float
    total_absolute_deviation: int
    median_utilization: float


@dataclass
class ComboGroupStats:
    """A combo group (distinct combo) and how many of its variants a cube completes."""

    group_key: str
    variant_count: int  # completed variants of the group
    cards: list[str]  # selected cards taking part in a completed variant of the group


@dataclass
class ColorStats:
    """How a set of cards is distributed over the five colors, by color identity."""

    cards_per_color: dict[str, int]  # W/U/B/R/G; a card counts once per color in its identity
    mono_colored: dict[str, int]  # W/U/B/R/G; cards whose identity is exactly that color
    multicolor: int
    colorless: int
    unknown: int  # cards without color data
    variance: float  # population variance of cards_per_color
    std_deviation: float


@dataclass
class CardMixStats:
    """The make-up of a set of cards by type, color count and mana value."""

    card_count: int
    # Cards of each card type (Creature, Instant, ...); a card counts once per type it has
    type_counts: dict[str, int]
    # Nonland cards with two or more colors, and nonland cards with none (cards without
    # Scryfall data count here): what the multicolor and colorless caps count. Lands
    # appear in type_counts only.
    multicolor: int
    colorless: int
    # Nonland cards by mana value, 0 to MANA_VALUE_CAP (the last bucket is "that or more")
    mana_value_counts: dict[int, int]
    mean_mana_value: float  # of the nonland cards
    mean_mana_value_per_color: dict[str, float]  # of the nonland cards of each color
    unknown: int  # cards without Scryfall data


@dataclass
class ArchetypeStats:
    """How many distinct combos a cube offers each draft archetype."""

    # Completed combos (groups) that a drafter of each archetype can assemble: the ten
    # two-color pairs, the five mono colors and "C" (colorless). A combo counts for every
    # archetype its color identity fits in, so a mono-white combo counts for W, WU, WB, WR
    # and WG, and a colorless combo for every archetype.
    combos_per_archetype: dict[str, int]
    # Completed combos by the number of colors in their identity (0 to 5)
    combos_by_color_count: dict[int, int]

    @property
    def wide_combo_count(self) -> int:
        """Completed combos that need three or more colors."""
        return sum(count for colors, count in self.combos_by_color_count.items() if colors >= 3)


@dataclass
class OutcomeStats:
    """How many distinct combos a cube offers of each outcome (what the combos do)."""

    # Completed combos (groups) in each outcome category of the table, in table order. A
    # combo is in every category one of its features matches, so the counts overlap.
    combos_per_outcome: dict[str, int]
    uncategorized: int  # completed combos in no category: a trigger loop without a payoff
    total: int  # completed combos


@dataclass
class PopularityStats:
    """How popular (by Spellbook usage) the distinct combos a cube completes are."""

    combo_count: int  # completed combos (groups); a combo's popularity is its top variant's
    median_popularity: float
    mean_log_popularity: float  # mean of log(1 + popularity)
    # Share of the completed combos whose popularity is below the pool median
    below_pool_median_share: float
    pool_median_popularity: float  # median popularity of every combo in the pool


@dataclass
class RequirementTypeStats:
    """Statistics for a single requirement type in the final cube."""

    template_name: str
    combo_count: int  # How many completable combos use this requirement
    card_count: int  # How many cards in cube satisfy this requirement
    cards: list[str]  # Which cards satisfy it
    coverage_ratio: float  # card_count / combo_count
    aliases: list[str] | None = None  # Other names that were merged into this group


@dataclass
class RequirementCoverageStats:
    """Aggregate statistics for coverage ratios across all requirement types."""

    mean_coverage_ratio: float
    std_dev_coverage_ratio: float


@dataclass
class TemplateOverlapPairStats:
    """Statistics about overlap between two requirement templates."""

    template1_name: str
    template2_name: str
    shared_cards: list[str]
    overlap_count: int
    jaccard_similarity: float  # |intersection| / |union|


@dataclass
class CrossTemplateStats:
    """Aggregate statistics for cross-template card overlap."""

    multi_template_card_count: int
    max_templates_per_card: int
    mean_templates_per_card: float
    cards_by_template_count: dict[int, int]  # template_count -> number of cards with that count
    top_versatile_cards: list[CandidateCard]  # Top 10 by template_count
    top_overlapping_pairs: list[TemplateOverlapPairStats]  # Top 10 by overlap_count


# =============================================================================
# Result Model - Contains both solution and computed stats
# =============================================================================


@dataclass
class OptimizationResult:
    """Result from ILP optimization."""

    selected_cards: list[CandidateCard]
    completable_combo_ids: list[str]
    combo_count: int  # completed variants
    objective_value: float
    solve_time_seconds: float
    phase1_status: str  # "OPTIMAL", "FEASIBLE", "INFEASIBLE", "TIMEOUT"

    # Combo grouping: completed variants belong to this many distinct combos (groups), and
    # count this much with variant_weight (groups + variant_weight x further variants)
    distinct_combo_count: int | None = None
    weighted_combo_count: float | None = None
    phase1_distinct_combo_count: int | None = None
    phase1_weighted_combo_count: float | None = None
    largest_combo_groups: list[ComboGroupStats] | None = None  # top groups by variants
    variant_weight: float | None = None  # value of each further variant of a completed group

    # Multi-objective optimization fields (backward compatible)
    utilization_per_card: dict[str, int] | None = None
    phase1_utilization_stats: UtilizationStats | None = None
    phase2_utilization_stats: UtilizationStats | None = None
    phase1_solve_time: float | None = None
    phase2_solve_time: float | None = None
    phase2_status: str | None = None
    phase2_objective: str | None = None  # name of the Phase 2 objective that ran
    phase2_util_cap: int | None = None  # utilization cap T used ("softcap" and "tiered" only)
    phase2_max_color_ratio: float | None = None  # color balance ratio applied, if any
    # Archetype support applied in Phase 2, when the rules were enabled (None otherwise)
    phase2_min_pair_combos: int | None = None
    phase2_min_mono_combos: int | None = None
    phase2_max_wide_combo_share: float | None = None
    # The card mix rules Phase 2 applied; None when there was no (or too little) card data
    # to apply them to. The limits are the card counts each enabled rule applied at this cube
    # size, by rule name ("multicolor_cap", ..., "spell_floor").
    phase2_card_mix: CardMixRules | None = None
    phase2_card_mix_limits: dict[str, int] | None = None
    # Candidate cards without Scryfall data (None without any card data)
    phase2_unknown_candidate_cards: int | None = None
    # Distinct combos per draft archetype of each phase's cube
    phase1_archetype_stats: ArchetypeStats | None = None
    phase2_archetype_stats: ArchetypeStats | None = None
    # Distinct combos per outcome category of each phase's cube (None without a table)
    phase1_outcome_stats: OutcomeStats | None = None
    phase2_outcome_stats: OutcomeStats | None = None
    # Popularity of the distinct combos of each phase's cube
    phase1_popularity_stats: PopularityStats | None = None
    phase2_popularity_stats: PopularityStats | None = None
    # Outcome rules applied in Phase 2, when enabled (None otherwise): the minimum completed
    # combos per category actually applied (the table's own minimum or the default), and the
    # largest share of the completed combos one category may hold
    phase2_outcome_minimums: dict[str, int] | None = None
    phase2_max_outcome_share: float | None = None
    # Popularity weight: each combo's value is scaled by 1 + weight x its popularity relative
    # to the most popular combo (log scale). The combo score is the weighted combo count under
    # that scaling; it equals weighted_combo_count when the weight is 0.
    popularity_weight: float | None = None
    combo_score: float | None = None
    phase1_combo_score: float | None = None
    phase2_reference_combo_score: float | None = None
    # Combo count the Phase 2 combo window is measured from: the best cube found under the
    # Phase 2 cube rules (coverage, color balance, archetype support, card mix)
    phase2_reference_combo_count: int | None = None
    phase2_reference_distinct_combo_count: int | None = None
    # The quantity the window holds: groups + variant_weight x further variants of the
    # reference cube (equal to phase2_reference_combo_count when variant_weight is 1)
    phase2_reference_weighted_combo_count: float | None = None
    phase2_combo_tolerance: float | None = None  # the window half-width, as a fraction
    is_multi_objective: bool = False
    # True when Phase 2 ran but found no solution, so this is the Phase 1 cube. phase2_status
    # and phase2_solve_time then describe the failed Phase 2 attempt.
    phase2_fell_back: bool = False
    requirement_type_stats: list[RequirementTypeStats] | None = None
    requirement_coverage_stats: RequirementCoverageStats | None = None
    cross_template_stats: CrossTemplateStats | None = None
    phase1_selected_cards: list[CandidateCard] | None = None  # Cards from Phase 1 (before Phase 2)
    phase1_combo_count: int | None = None  # Combos the Phase 1 cube completes

    # Profiling data (populated when --profile is used)
    profile_data: dict[str, Any] | None = None

    def get_selected_card_names(self) -> list[str]:
        """Get the names of all selected cards."""
        return [card.name for card in self.selected_cards]
