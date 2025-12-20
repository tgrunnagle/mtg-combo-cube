"""ILP-based cube optimizer using OR-Tools CP-SAT solver."""

import logging
import math
import time

from ortools.sat.python import cp_model

from mtg_combo_cube.ilp_models import ComboData, OptimizationResult

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
    ):
        self.combos = combos
        self.cube_size = cube_size
        self.time_limit = time_limit_seconds
        self.tiebreak_epsilon = tiebreak_epsilon

        # Build card universe
        self.all_cards: list[str] = self._collect_all_cards()
        self.card_to_idx: dict[str, int] = {
            card: i for i, card in enumerate(self.all_cards)
        }

        logger.info(
            f"ILP Optimizer initialized: {len(self.combos)} combos, "
            f"{len(self.all_cards)} cards, cube size {cube_size}"
        )

    def _collect_all_cards(self) -> list[str]:
        """Collect all unique card names from combos."""
        cards: set[str] = set()
        for combo in self.combos:
            cards.update(combo.required_cards)
            for opts in combo.requirement_options:
                cards.update(opts)
        return sorted(cards)  # Sorted for deterministic ordering

    def _compute_weight(self, popularity: int) -> int:
        """
        Compute integer weight for objective function.

        Primary: 1.0 (combo count)
        Secondary: epsilon * log(1 + popularity) for tiebreaking

        Scaled to integer for CP-SAT.
        """
        weight = 1.0 + self.tiebreak_epsilon * math.log1p(popularity)
        return int(weight * self.WEIGHT_SCALE)

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
                f"Only {len(self.all_cards)} cards available, "
                f"but cube size is {self.cube_size}"
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
            for opts in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opts))

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
            completed = [
                combo.id for combo in self.combos if solver.value(y[combo.id]) == 1
            ]
            objective = solver.objective_value

            logger.info(
                f"ILP solved ({status_str}): {len(selected)} cards, "
                f"{len(completed)} combos in {solve_time:.1f}s"
            )

            return OptimizationResult(
                selected_cards=selected,
                completable_combo_ids=completed,
                combo_count=len(completed),
                objective_value=objective / self.WEIGHT_SCALE,
                solve_time_seconds=solve_time,
                status=status_str,
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
