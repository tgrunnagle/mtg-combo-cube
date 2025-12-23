"""Data structures for ILP optimization."""

from dataclasses import dataclass

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

    def all_requirements_resolvable(self) -> bool:
        """Check if all template requirements have at least one card option."""
        return all(len(opt.cards) > 0 for opt in self.requirement_options)


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
    combo_count: int
    objective_value: float
    solve_time_seconds: float
    phase1_status: str  # "OPTIMAL", "FEASIBLE", "INFEASIBLE", "TIMEOUT"

    # Multi-objective optimization fields (backward compatible)
    utilization_per_card: dict[str, int] | None = None
    phase1_utilization_stats: UtilizationStats | None = None
    phase2_utilization_stats: UtilizationStats | None = None
    phase1_solve_time: float | None = None
    phase2_solve_time: float | None = None
    phase2_status: str | None = None
    is_multi_objective: bool = False
    requirement_type_stats: list[RequirementTypeStats] | None = None
    requirement_coverage_stats: RequirementCoverageStats | None = None
    cross_template_stats: CrossTemplateStats | None = None
    phase1_selected_cards: list[CandidateCard] | None = None  # Cards from Phase 1 (before Phase 2)

    def get_selected_card_names(self) -> list[str]:
        """Get the names of all selected cards."""
        return [card.name for card in self.selected_cards]
