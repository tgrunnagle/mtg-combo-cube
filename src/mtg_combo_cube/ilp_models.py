"""Data structures for ILP optimization."""

from dataclasses import dataclass


@dataclass
class ComboData:
    """Preprocessed combo data for ILP optimization.

    This is a simplified representation of a Variant that contains
    only the information needed for constraint generation.
    """

    id: str
    required_cards: frozenset[str]  # Card names from 'uses' field
    requirement_options: list[
        frozenset[str]
    ]  # For each 'requires', set of valid card names
    popularity: int  # For tiebreaking (higher = better)

    def all_requirements_resolvable(self) -> bool:
        """Check if all template requirements have at least one card option."""
        return all(len(opts) > 0 for opts in self.requirement_options)


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
