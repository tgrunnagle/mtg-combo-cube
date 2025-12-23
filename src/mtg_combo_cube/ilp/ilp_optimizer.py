"""ILP-based cube optimizer using OR-Tools CP-SAT solver."""

import logging
import math
import time

from ortools.sat.python import cp_model

from mtg_combo_cube.ilp.ilp_models import (
    ComboData,
    OptimizationResult,
    RequirementCoverageStats,
    RequirementTypeStats,
    UtilizationStats,
)

logger = logging.getLogger(__name__)


class ILPOptimizer:
    """
    ILP-based cube optimizer using OR-Tools CP-SAT solver.

    Maximizes the number of completable combos within a fixed cube size,
    using log-scaled popularity as a tiebreaker.
    """

    DEFAULT_TIME_LIMIT = 300  # 5 minutes
    TIEBREAK_EPSILON = 0.001  # Small weight for popularity tiebreaker
    WEIGHT_SCALE = 10000  # Scale for integer conversion

    def __init__(
        self,
        combos: list[ComboData],
        cube_size: int,
        time_limit_seconds: int = DEFAULT_TIME_LIMIT,
        tiebreak_epsilon: float = TIEBREAK_EPSILON,
        combo_tolerance: float = 0.1,
    ):
        self.combos = combos
        self.cube_size = cube_size
        self.time_limit = time_limit_seconds
        self.tiebreak_epsilon = tiebreak_epsilon
        self.combo_tolerance = combo_tolerance

        # Build card universe
        self.all_cards: list[str] = self._collect_all_cards()
        self.card_to_idx: dict[str, int] = {card: i for i, card in enumerate(self.all_cards)}
        self.card_to_combos: dict[str, list[ComboData]] = self._build_participation_graph()

        logger.info(
            f"ILP Optimizer initialized: {len(self.combos)} combos, "
            f"{len(self.all_cards)} cards, cube size {cube_size}"
        )

    def _collect_all_cards(self) -> list[str]:
        """Collect all unique card names from combos."""
        cards: set[str] = set()
        for combo in self.combos:
            cards.update(combo.required_cards)
            for opt in combo.requirement_options:
                cards.update(opt.cards)
        return sorted(cards)  # Sorted for deterministic ordering

    def _build_participation_graph(self) -> dict[str, list[ComboData]]:
        """Build mapping of cards to combos they participate in."""
        from collections import defaultdict

        participation: dict[str, list[ComboData]] = defaultdict(list)

        for combo in self.combos:
            for card in combo.required_cards:
                participation[card].append(combo)
            for opt in combo.requirement_options:
                for card in opt.cards:
                    if combo not in participation[card]:
                        participation[card].append(combo)

        return {card: participation.get(card, []) for card in self.all_cards}

    def _compute_weight(self, popularity: int) -> int:
        """
        Compute integer weight for objective function.

        Primary: 1.0 (combo count)
        Secondary: epsilon * log(1 + popularity) for tiebreaking

        Scaled to integer for CP-SAT.
        """
        weight = 1.0 + self.tiebreak_epsilon * math.log1p(popularity)
        return int(weight * self.WEIGHT_SCALE)

    def _calculate_utilization(
        self, selected_cards: list[str], completable_combo_ids: list[str]
    ) -> dict[str, int]:
        """Calculate utilization for each selected card."""
        completable_set = set(completable_combo_ids)
        utilization: dict[str, int] = {}

        for card in selected_cards:
            count = sum(1 for combo in self.card_to_combos[card] if combo.id in completable_set)
            utilization[card] = count

        return utilization

    def _compute_utilization_stats(self, utilization: dict[str, int]) -> UtilizationStats:
        """Compute statistical summary of card utilization."""
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

    def _calculate_requirement_stats(
        self,
        selected_cards: set[str],
        completable_combo_ids: set[str],
    ) -> list[RequirementTypeStats]:
        """Calculate stats for each requirement type in completable combos."""
        template_stats: dict[str, dict[str, set[str]]] = {}

        for combo in self.combos:
            if combo.id not in completable_combo_ids:
                continue
            for opt in combo.requirement_options:
                name = opt.template_name
                if name not in template_stats:
                    template_stats[name] = {"combos": set(), "cards": set()}
                template_stats[name]["combos"].add(combo.id)
                # Cards that satisfy this requirement AND are in the cube
                satisfying = opt.cards & selected_cards
                template_stats[name]["cards"].update(satisfying)

        return [
            RequirementTypeStats(
                template_name=name,
                combo_count=len(data["combos"]),
                card_count=len(data["cards"]),
                cards=sorted(data["cards"]),
                coverage_ratio=(
                    len(data["cards"]) / len(data["combos"]) if data["combos"] else 0.0
                ),
            )
            for name, data in sorted(template_stats.items())
        ]

    def _compute_coverage_stats(
        self,
        requirement_stats: list[RequirementTypeStats],
    ) -> RequirementCoverageStats:
        """Compute mean and std dev of coverage ratios."""
        if not requirement_stats:
            return RequirementCoverageStats(mean_coverage_ratio=0.0, std_dev_coverage_ratio=0.0)

        ratios = [r.coverage_ratio for r in requirement_stats]
        mean = sum(ratios) / len(ratios)
        variance = sum((r - mean) ** 2 for r in ratios) / len(ratios)
        std_dev = variance**0.5

        return RequirementCoverageStats(
            mean_coverage_ratio=mean,
            std_dev_coverage_ratio=std_dev,
        )

    def solve(self) -> OptimizationResult:
        """
        Build and solve the ILP model.

        Returns OptimizationResult with selected cards and completable combos.
        """
        start_time = time.time()

        # Handle edge case: no combos
        if not self.combos:
            logger.warning("No combos to optimize")
            return OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=time.time() - start_time,
                status="OPTIMAL",
            )

        # Handle edge case: not enough cards for cube size
        if len(self.all_cards) < self.cube_size:
            logger.warning(
                f"Only {len(self.all_cards)} cards available, but cube size is {self.cube_size}"
            )
            return OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=time.time() - start_time,
                status="INFEASIBLE",
            )

        model = cp_model.CpModel()

        # Decision variables
        # x[c] = 1 if card c is in cube
        x: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            x[card] = model.new_bool_var(f"card_{self.card_to_idx[card]}")

        # y[j] = 1 if combo j is completable
        y: dict[str, cp_model.IntVar] = {}
        for combo in self.combos:
            y[combo.id] = model.new_bool_var(f"combo_{combo.id}")

        # Constraint 1: Cube size
        model.add(sum(x[card] for card in self.all_cards) == self.cube_size)

        # Constraint 2: Required cards for each combo
        # y[j] <= x[c] for all c in required_cards[j]
        for combo in self.combos:
            for card in combo.required_cards:
                model.add(y[combo.id] <= x[card])

        # Constraint 3: Optional requirements (at least one card from each set)
        # y[j] <= sum(x[c] for c in matching_cards[r])
        for combo in self.combos:
            for opt in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opt.cards))

        # Objective: Maximize weighted combo count
        objective_terms = []
        for combo in self.combos:
            weight = self._compute_weight(combo.popularity)
            objective_terms.append(weight * y[combo.id])
        model.maximize(sum(objective_terms))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 8  # Parallel search
        solver.parameters.log_search_progress = logger.isEnabledFor(logging.DEBUG)

        status = solver.solve(model)
        solve_time = time.time() - start_time

        # Extract solution
        # CpSolverStatus is int at runtime, type stubs are incomplete
        status_str = self._status_to_string(status)  # type: ignore[arg-type]

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            selected = [card for card in self.all_cards if solver.value(x[card]) == 1]
            completed = [combo.id for combo in self.combos if solver.value(y[combo.id]) == 1]
            objective = solver.objective_value

            # Calculate utilization for this solution
            utilization = self._calculate_utilization(selected, completed)
            utilization_stats = self._compute_utilization_stats(utilization)

            # Calculate requirement type stats
            selected_set = set(selected)
            completed_set = set(completed)
            req_stats = self._calculate_requirement_stats(selected_set, completed_set)
            coverage_stats = self._compute_coverage_stats(req_stats)

            logger.info(
                f"ILP solved ({status_str}): {len(selected)} cards, "
                f"{len(completed)} combos in {solve_time:.1f}s"
            )
            logger.info(
                f"Utilization stats: min={utilization_stats.min_utilization}, "
                f"max={utilization_stats.max_utilization}, "
                f"mean={utilization_stats.mean_utilization:.1f}, "
                f"median={utilization_stats.median_utilization:.1f}, "
                f"std_dev={utilization_stats.std_deviation:.2f}"
            )

            return OptimizationResult(
                selected_cards=selected,
                completable_combo_ids=completed,
                combo_count=len(completed),
                objective_value=objective / self.WEIGHT_SCALE,
                solve_time_seconds=solve_time,
                status=status_str,
                utilization_per_card=utilization,
                phase1_utilization_stats=utilization_stats,
                phase1_solve_time=solve_time,
                is_multi_objective=False,
                requirement_type_stats=req_stats,
                requirement_coverage_stats=coverage_stats,
            )
        else:
            logger.warning(f"ILP solve failed: {status_str}")
            return OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=solve_time,
                status=status_str,
            )

    def _solve_phase2(
        self,
        target_combo_count: int,
        phase1_result: OptimizationResult,
    ) -> OptimizationResult:
        """
        Phase 2: Minimize utilization variance while preserving combo count.

        Key constraints:
        - Fixed combo count from Phase 1
        - Utilization variables: u[c] = sum of completed combos card c participates in
        - MAD linearization: minimize sum(d_plus[c] + d_minus[c])

        Falls back to Phase 1 result if Phase 2 fails.
        """
        logger.info(
            f"Starting Phase 2: balancing utilization (target: {target_combo_count} combos)"
        )
        start_time = time.time()

        model = cp_model.CpModel()

        # Decision variables (same as Phase 1)
        x: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            x[card] = model.new_bool_var(f"card_{self.card_to_idx[card]}")

        y: dict[str, cp_model.IntVar] = {}
        for combo in self.combos:
            y[combo.id] = model.new_bool_var(f"combo_{combo.id}")

        # Base constraints (same as Phase 1)
        model.add(sum(x[card] for card in self.all_cards) == self.cube_size)

        for combo in self.combos:
            for card in combo.required_cards:
                model.add(y[combo.id] <= x[card])

        for combo in self.combos:
            for opt in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opt.cards))

        # NEW: Fix combo count to Phase 1 target (with optional tolerance)
        combo_sum = sum(y[combo.id] for combo in self.combos)

        if self.combo_tolerance > 0:
            min_combo_count = math.floor(target_combo_count * (1 - self.combo_tolerance))
            max_combo_count = math.ceil(target_combo_count * (1 + self.combo_tolerance))

            logger.info(
                f"Phase 2 combo tolerance: {self.combo_tolerance:.1%} "
                f"(range: {min_combo_count}-{max_combo_count})"
            )

            model.add(combo_sum >= min_combo_count)
            model.add(combo_sum <= max_combo_count)
        else:
            # No tolerance - use exact equality (current behavior)
            model.add(combo_sum == target_combo_count)

        # Utilization variables: u[card] = count of completable combos card participates in
        u: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            # Max possible utilization is the total number of combos
            u[card] = model.new_int_var(0, len(self.combos), f"util_{self.card_to_idx[card]}")

        # Link utilization to combo participation (only for selected cards)
        for card in self.all_cards:
            # Sum of combo variables this card participates in
            combo_sum = sum(y[combo.id] for combo in self.card_to_combos[card])
            # If card is selected, u[card] = combo_sum; else u[card] = 0
            model.add(u[card] == combo_sum).only_enforce_if(x[card])
            model.add(u[card] == 0).only_enforce_if(x[card].Not())

        # Compute target mean utilization from Phase 1
        assert phase1_result.phase1_utilization_stats is not None
        target_mean = phase1_result.phase1_utilization_stats.mean_utilization

        # MAD deviation variables (only for selected cards)
        # We need integer arithmetic, so scale mean by 100 to preserve precision
        mean_scaled = int(target_mean * 100)

        d_plus: dict[str, cp_model.IntVar] = {}
        d_minus: dict[str, cp_model.IntVar] = {}

        for card in self.all_cards:
            # Max deviation is bounded by max possible utilization
            max_dev = len(self.combos) * 100
            d_plus[card] = model.new_int_var(0, max_dev, f"dplus_{self.card_to_idx[card]}")
            d_minus[card] = model.new_int_var(0, max_dev, f"dminus_{self.card_to_idx[card]}")

            # u[card] - mean <= d_plus[card] (when x[card] = 1)
            # mean - u[card] <= d_minus[card] (when x[card] = 1)
            # When x[card] = 0, both deviations are 0
            model.add(u[card] * 100 - mean_scaled <= d_plus[card]).only_enforce_if(x[card])
            model.add(mean_scaled - u[card] * 100 <= d_minus[card]).only_enforce_if(x[card])
            model.add(d_plus[card] == 0).only_enforce_if(x[card].Not())
            model.add(d_minus[card] == 0).only_enforce_if(x[card].Not())

        # Objective: Minimize total absolute deviation
        model.minimize(sum(d_plus[card] + d_minus[card] for card in self.all_cards))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 8
        solver.parameters.log_search_progress = logger.isEnabledFor(logging.DEBUG)

        status = solver.solve(model)
        phase2_time = time.time() - start_time
        status_str = self._status_to_string(status)  # type: ignore[arg-type]

        # If Phase 2 fails, fall back to Phase 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            logger.warning(f"Phase 2 failed ({status_str}), falling back to Phase 1 result")
            return phase1_result

        # Extract Phase 2 solution
        selected = [card for card in self.all_cards if solver.value(x[card]) == 1]
        completed = [combo.id for combo in self.combos if solver.value(y[combo.id]) == 1]

        # Calculate Phase 2 utilization stats
        utilization = self._calculate_utilization(selected, completed)
        phase2_stats = self._compute_utilization_stats(utilization)

        # Calculate requirement type stats for Phase 2 solution
        selected_set = set(selected)
        completed_set = set(completed)
        req_stats = self._calculate_requirement_stats(selected_set, completed_set)
        coverage_stats = self._compute_coverage_stats(req_stats)

        # Calculate improvement metrics
        p1 = phase1_result.phase1_utilization_stats
        std_improvement = (
            100 * (1 - phase2_stats.std_deviation / p1.std_deviation)
            if p1.std_deviation > 0
            else 0.0
        )
        range_before = p1.max_utilization - p1.min_utilization
        range_after = phase2_stats.max_utilization - phase2_stats.min_utilization
        range_improvement = 100 * (1 - range_after / range_before) if range_before > 0 else 0.0

        logger.info(
            f"Phase 2 complete ({status_str}): "
            f"std_dev {p1.std_deviation:.2f} → {phase2_stats.std_deviation:.2f} "
            f"({std_improvement:.1f}% improvement) in {phase2_time:.1f}s"
        )
        logger.info(
            f"Utilization range: {p1.min_utilization}-{p1.max_utilization} → "
            f"{phase2_stats.min_utilization}-{phase2_stats.max_utilization} "
            f"({range_improvement:.1f}% reduction)"
        )

        # Return full multi-objective result
        return OptimizationResult(
            selected_cards=selected,
            completable_combo_ids=completed,
            combo_count=len(completed),
            objective_value=phase1_result.objective_value,  # Preserve Phase 1 objective
            solve_time_seconds=phase1_result.phase1_solve_time + phase2_time,  # type: ignore
            status=status_str,
            utilization_per_card=utilization,
            phase1_utilization_stats=phase1_result.phase1_utilization_stats,
            phase2_utilization_stats=phase2_stats,
            phase1_solve_time=phase1_result.phase1_solve_time,
            phase2_solve_time=phase2_time,
            phase2_status=status_str,
            is_multi_objective=True,
            requirement_type_stats=req_stats,
            requirement_coverage_stats=coverage_stats,
        )

    def solve_two_phase(self) -> OptimizationResult:
        """
        Two-phase multi-objective optimization (recommended entry point).

        Returns:
            OptimizationResult with balanced utilization, or Phase 1 fallback
        """
        logger.info("Starting two-phase multi-objective optimization")

        # Phase 1: Maximize combo count
        phase1_result = self.solve()

        # Handle Phase 1 failure or edge cases
        if phase1_result.status not in ("OPTIMAL", "FEASIBLE"):
            logger.warning(f"Phase 1 failed: {phase1_result.status}")
            return phase1_result

        if phase1_result.combo_count == 0:
            logger.warning("Phase 1 found 0 combos. Skipping Phase 2.")
            return phase1_result

        # Phase 2: Balance utilization
        return self._solve_phase2(
            target_combo_count=phase1_result.combo_count,
            phase1_result=phase1_result,
        )

    @staticmethod
    def _status_to_string(status: int) -> str:
        """Convert CP-SAT status to human-readable string."""
        mapping: dict[int, str] = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "INVALID",
            cp_model.UNKNOWN: "TIMEOUT",
        }
        return mapping.get(status, f"UNKNOWN_{status}")
