"""Data structures for ILP optimization."""

from dataclasses import dataclass


@dataclass
class RequirementOption:
    """A single template requirement with its matching cards."""

    template_name: str
    cards: frozenset[str]


@dataclass
class ComboData:
    """Preprocessed combo data for ILP optimization.

    This is a simplified representation of a Variant that contains
    only the information needed for constraint generation.
    """

    id: str
    required_cards: frozenset[str]  # Card names from 'uses' field
    requirement_options: list[RequirementOption]  # For each 'requires', template name + valid cards
    popularity: int  # For tiebreaking (higher = better)

    def all_requirements_resolvable(self) -> bool:
        """Check if all template requirements have at least one card option."""
        return all(len(opt.cards) > 0 for opt in self.requirement_options)


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
    """Statistics for a single requirement type."""

    template_name: str
    combo_count: int  # How many completable combos use this requirement
    card_count: int  # How many cards in cube satisfy this requirement
    cards: list[str]  # Which cards satisfy it
    coverage_ratio: float  # card_count / combo_count


@dataclass
class RequirementCoverageStats:
    """Aggregate statistics for coverage ratios across all requirement types."""

    mean_coverage_ratio: float
    std_dev_coverage_ratio: float


@dataclass
class OptimizationResult:
    """Result from ILP optimization."""

    selected_cards: list[str]
    completable_combo_ids: list[str]
    combo_count: int
    objective_value: float
    solve_time_seconds: float
    status: str  # "OPTIMAL", "FEASIBLE", "INFEASIBLE", "TIMEOUT"

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
