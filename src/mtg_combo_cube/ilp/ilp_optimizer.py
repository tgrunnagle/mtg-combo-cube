"""ILP-based cube optimizer using OR-Tools CP-SAT solver."""

import logging
import math
import time
from collections import defaultdict

from ortools.sat.python import cp_model

from mtg_combo_cube.ilp.ilp_models import (
    CandidateCard,
    ComboData,
    CrossTemplateStats,
    OptimizationResult,
    RequirementCoverageStats,
    RequirementPool,
    RequirementTypeStats,
    TemplateOverlapPairStats,
    UtilizationStats,
)
from mtg_combo_cube.ilp.profiling import (
    ProfileResult,
    extract_solver_stats,
    log_profile_comparison,
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
    VERSATILITY_EPSILON = 0.0001  # Small bonus for multi-template cards
    WEIGHT_SCALE = 10000  # Scale for integer conversion

    def __init__(
        self,
        combos: list[ComboData],
        candidate_cards: dict[str, CandidateCard],
        cube_size: int,
        time_limit_seconds: int = DEFAULT_TIME_LIMIT,
        tiebreak_epsilon: float = TIEBREAK_EPSILON,
        combo_tolerance: float = 0.1,
        min_coverage_ratio: float = 0.1,
        min_combo_threshold: int = 10,
        gap_limit: float = 0.05,
        phase2_objective: str = "minmax",
        min_utilization_floor: int = 2,
    ):
        self.combos = combos
        self.candidate_cards = candidate_cards
        self.cube_size = cube_size
        self.time_limit = time_limit_seconds
        self.tiebreak_epsilon = tiebreak_epsilon
        self.combo_tolerance = combo_tolerance
        self.min_coverage_ratio = min_coverage_ratio
        self.min_combo_threshold = min_combo_threshold
        self.gap_limit = gap_limit
        self.phase2_objective = phase2_objective  # "mad" or "minmax"
        self.min_utilization_floor = min_utilization_floor  # for minmax objective

        # Build card universe from candidate cards
        self.all_cards: list[str] = sorted(candidate_cards.keys())
        self.card_to_idx: dict[str, int] = {card: i for i, card in enumerate(self.all_cards)}
        self.card_to_combos: dict[str, list[ComboData]] = self._build_participation_graph()

        logger.info(
            f"ILP Optimizer initialized: {len(self.combos)} combos, "
            f"{len(self.all_cards)} cards, cube size {cube_size}"
        )

    def _build_participation_graph(self) -> dict[str, list[ComboData]]:
        """Build mapping of cards to combos they participate in."""
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
        # Group by group_key, but track all display names seen
        template_stats: dict[str, dict[str, set[str]]] = {}

        for combo in self.combos:
            if combo.id not in completable_combo_ids:
                continue
            for opt in combo.requirement_options:
                key = opt.group_key
                if key not in template_stats:
                    template_stats[key] = {
                        "combos": set(),
                        "cards": set(),
                        "display_names": set(),
                    }
                template_stats[key]["combos"].add(combo.id)
                template_stats[key]["display_names"].add(opt.template_name)
                # Cards that satisfy this requirement AND are in the cube
                satisfying = opt.cards & selected_cards
                template_stats[key]["cards"].update(satisfying)

        result = []
        for _key, data in sorted(template_stats.items()):
            display_name = self._pick_display_name(data["display_names"])
            other_names = sorted(data["display_names"] - {display_name})
            result.append(
                RequirementTypeStats(
                    template_name=display_name,
                    combo_count=len(data["combos"]),
                    card_count=len(data["cards"]),
                    cards=sorted(data["cards"]),
                    coverage_ratio=(
                        len(data["cards"]) / len(data["combos"]) if data["combos"] else 0.0
                    ),
                    aliases=other_names if other_names else None,
                )
            )
        return result

    def _pick_display_name(self, names: set[str]) -> str:
        """Pick the best display name from a set of aliases.

        Strategy: Pick the shortest name (usually the most general).
        If tied, pick alphabetically first for determinism.
        """
        return min(names, key=lambda n: (len(n), n))

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

    def _build_group_key_to_name_map(
        self,
        completable_combo_ids: set[str],
    ) -> dict[str, str]:
        """Build mapping from group_key to display name for completable combos."""
        group_key_to_names: dict[str, set[str]] = defaultdict(set)

        for combo in self.combos:
            if combo.id not in completable_combo_ids:
                continue
            for opt in combo.requirement_options:
                group_key_to_names[opt.group_key].add(opt.template_name)

        return {key: self._pick_display_name(names) for key, names in group_key_to_names.items()}

    def _calculate_cross_template_stats(
        self,
        selected_cards: set[str],
        group_key_to_name: dict[str, str],
    ) -> CrossTemplateStats:
        """Calculate cross-template overlap statistics for selected cards."""
        # Get candidate cards that are selected
        selected_candidates = [
            self.candidate_cards[name] for name in selected_cards if name in self.candidate_cards
        ]

        # Count distribution
        template_counts = [c.template_count for c in selected_candidates]
        cards_by_count: dict[int, int] = {}
        for count in template_counts:
            cards_by_count[count] = cards_by_count.get(count, 0) + 1

        # Multi-template cards sorted by template_count
        multi_template = [c for c in selected_candidates if c.is_multi_template]
        top_versatile = sorted(multi_template, key=lambda c: -c.template_count)[:10]

        # Calculate pairwise template overlaps
        template_to_cards: dict[str, set[str]] = defaultdict(set)
        for card in selected_candidates:
            for key in card.requirement_group_keys:
                template_to_cards[key].add(card.name)

        pair_overlaps: list[TemplateOverlapPairStats] = []
        template_keys = sorted(template_to_cards.keys())
        for i, t1 in enumerate(template_keys):
            for t2 in template_keys[i + 1 :]:
                cards1, cards2 = template_to_cards[t1], template_to_cards[t2]
                shared = cards1 & cards2
                if shared:
                    union_size = len(cards1 | cards2)
                    pair_overlaps.append(
                        TemplateOverlapPairStats(
                            template1_name=group_key_to_name.get(t1, t1),
                            template2_name=group_key_to_name.get(t2, t2),
                            shared_cards=sorted(shared),
                            overlap_count=len(shared),
                            jaccard_similarity=len(shared) / union_size,
                        )
                    )

        top_pairs = sorted(pair_overlaps, key=lambda p: -p.overlap_count)[:10]

        return CrossTemplateStats(
            multi_template_card_count=len(multi_template),
            max_templates_per_card=max(template_counts) if template_counts else 0,
            mean_templates_per_card=(
                sum(template_counts) / len(template_counts) if template_counts else 0.0
            ),
            cards_by_template_count=cards_by_count,
            top_versatile_cards=top_versatile,
            top_overlapping_pairs=top_pairs,
        )

    def _build_requirement_pool_info(self) -> dict[str, RequirementPool]:
        """
        Build mapping of requirement group_key to pool information.

        This includes ALL cards that can satisfy each requirement (not just selected cards),
        and counts ALL combos using the requirement (not just completable ones).
        """
        requirement_info: dict[str, dict] = defaultdict(
            lambda: {"combos": set(), "pool_cards": set(), "display_names": set()}
        )

        for combo in self.combos:
            for opt in combo.requirement_options:
                key = opt.group_key
                requirement_info[key]["combos"].add(combo.id)
                requirement_info[key]["pool_cards"].update(opt.cards)
                requirement_info[key]["display_names"].add(opt.template_name)

        result = {}
        for key, data in requirement_info.items():
            display_name = self._pick_display_name(data["display_names"])
            result[key] = RequirementPool(
                group_key=key,
                display_name=display_name,
                combo_count=len(data["combos"]),
                pool_cards=frozenset(data["pool_cards"]),
            )
        return result

    def _add_coverage_constraints(
        self,
        model: cp_model.CpModel,
        x: dict[str, cp_model.IntVar],
        requirement_pool_info: dict[str, RequirementPool],
    ) -> int:
        """
        Add minimum coverage ratio constraints to the model.

        Returns the number of constraints added.
        """
        constraints_added = 0

        for info in requirement_pool_info.values():
            # Skip requirements used by few combos
            if info.combo_count < self.min_combo_threshold:
                continue

            # Calculate required minimum cards
            required_min_cards = math.ceil(self.min_coverage_ratio * info.combo_count)

            # Check if enough cards exist in the pool
            pool_card_count = len(info.pool_cards)
            if pool_card_count < required_min_cards:
                logger.warning(
                    f"Cannot meet minimum coverage for '{info.display_name}': "
                    f"need {required_min_cards} cards but only {pool_card_count} available "
                    f"(used by {info.combo_count} combos)"
                )
                # Use max available as soft constraint
                required_min_cards = pool_card_count

            if required_min_cards > 0:
                # Only add constraint for cards in our card universe
                cards_in_universe = [c for c in info.pool_cards if c in x]
                if len(cards_in_universe) >= required_min_cards:
                    model.add(sum(x[card] for card in cards_in_universe) >= required_min_cards)
                    constraints_added += 1
                    logger.debug(
                        f"Coverage constraint: '{info.display_name}' needs >= {required_min_cards} "
                        f"cards (from {len(cards_in_universe)} available)"
                    )

        return constraints_added

    def solve(self, profile: bool = False) -> OptimizationResult:
        """
        Build and solve the ILP model.

        Args:
            profile: If True, collect detailed profiling statistics

        Returns OptimizationResult with selected cards and completable combos.
        """
        start_time = time.perf_counter()
        profile_result = ProfileResult(phase="Phase 1") if profile else None

        # Handle edge case: no combos
        if not self.combos:
            logger.warning("No combos to optimize")
            return OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=time.time() - start_time,
                phase1_status="OPTIMAL",
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
                phase1_status="INFEASIBLE",
            )

        model = cp_model.CpModel()
        build_start = time.perf_counter()

        # Decision variables
        # x[c] = 1 if card c is in cube
        x: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            x[card] = model.new_bool_var(f"card_{self.card_to_idx[card]}")

        # y[j] = 1 if combo j is completable
        y: dict[str, cp_model.IntVar] = {}
        for combo in self.combos:
            y[combo.id] = model.new_bool_var(f"combo_{combo.id}")

        if profile_result:
            profile_result.counts["variables_card"] = len(x)
            profile_result.counts["variables_combo"] = len(y)

        # Constraint 1: Cube size
        model.add(sum(x[card] for card in self.all_cards) == self.cube_size)

        # Constraint 2: Required cards for each combo
        # y[j] <= x[c] for all c in required_cards[j]
        required_constraint_count = 0
        for combo in self.combos:
            for card in combo.required_cards:
                model.add(y[combo.id] <= x[card])
                required_constraint_count += 1

        if profile_result:
            profile_result.counts["required_card"] = required_constraint_count

        # Constraint 3: Optional requirements (at least one card from each set)
        # y[j] <= sum(x[c] for c in matching_cards[r])
        options_constraint_count = 0
        for combo in self.combos:
            for opt in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opt.cards))
                options_constraint_count += 1

        if profile_result:
            profile_result.counts["requirement_options"] = options_constraint_count
            profile_result.counts["cube_size"] = 1

        # Objective: Maximize weighted combo count
        objective_terms = []
        for combo in self.combos:
            weight = self._compute_weight(combo.popularity)
            objective_terms.append(weight * y[combo.id])
        model.maximize(sum(objective_terms))

        if profile_result:
            profile_result.timings["model_build"] = time.perf_counter() - build_start

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 8  # Parallel search
        solver.parameters.log_search_progress = logger.isEnabledFor(logging.DEBUG)

        solve_start = time.perf_counter()
        status = solver.solve(model)
        solver_time = time.perf_counter() - solve_start

        if profile_result:
            profile_result.timings["solver"] = solver_time
            profile_result.solver_stats = extract_solver_stats(solver)

        solve_time = time.perf_counter() - start_time

        # Extract solution
        # CpSolverStatus is int at runtime, type stubs are incomplete
        status_str = self._status_to_string(status)  # type: ignore[arg-type]

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            extract_start = time.perf_counter()
            selected_names = [card for card in self.all_cards if solver.value(x[card]) == 1]
            selected = [self.candidate_cards[name] for name in selected_names]
            completed = [combo.id for combo in self.combos if solver.value(y[combo.id]) == 1]
            objective = solver.objective_value

            # Calculate utilization for this solution
            utilization = self._calculate_utilization(selected_names, completed)
            utilization_stats = self._compute_utilization_stats(utilization)

            # Calculate requirement type stats
            selected_name_set = set(selected_names)
            completed_set = set(completed)
            req_stats = self._calculate_requirement_stats(selected_name_set, completed_set)
            coverage_stats = self._compute_coverage_stats(req_stats)

            # Calculate cross-template stats
            group_key_to_name = self._build_group_key_to_name_map(completed_set)
            cross_template_stats = self._calculate_cross_template_stats(
                selected_name_set, group_key_to_name
            )

            if profile_result:
                profile_result.timings["extraction"] = time.perf_counter() - extract_start
                profile_result.log_summary()

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

            # Build profile data for result
            profile_data = None
            if profile_result:
                profile_data = {
                    "phase1": {
                        "timings": profile_result.timings,
                        "counts": profile_result.counts,
                        "solver_stats": profile_result.solver_stats,
                    }
                }

            return OptimizationResult(
                selected_cards=selected,
                completable_combo_ids=completed,
                combo_count=len(completed),
                objective_value=objective / self.WEIGHT_SCALE,
                solve_time_seconds=solve_time,
                phase1_status=status_str,
                utilization_per_card=utilization,
                phase1_utilization_stats=utilization_stats,
                phase1_solve_time=solve_time,
                is_multi_objective=False,
                requirement_type_stats=req_stats,
                requirement_coverage_stats=coverage_stats,
                cross_template_stats=cross_template_stats,
                profile_data=profile_data,
            )
        else:
            logger.warning(f"ILP solve failed: {status_str}")
            return OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=solve_time,
                phase1_status=status_str,
            )

    def _solve_phase2(
        self,
        target_combo_count: int,
        phase1_result: OptimizationResult,
        profile: bool = False,
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
        start_time = time.perf_counter()
        profile_result = ProfileResult(phase="Phase 2") if profile else None

        model = cp_model.CpModel()
        build_start = time.perf_counter()

        # Decision variables (same as Phase 1)
        x: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            x[card] = model.new_bool_var(f"card_{self.card_to_idx[card]}")

        y: dict[str, cp_model.IntVar] = {}
        for combo in self.combos:
            y[combo.id] = model.new_bool_var(f"combo_{combo.id}")

        if profile_result:
            profile_result.counts["variables_card"] = len(x)
            profile_result.counts["variables_combo"] = len(y)

        # Base constraints (same as Phase 1)
        model.add(sum(x[card] for card in self.all_cards) == self.cube_size)

        required_constraint_count = 0
        for combo in self.combos:
            for card in combo.required_cards:
                model.add(y[combo.id] <= x[card])
                required_constraint_count += 1

        options_constraint_count = 0
        for combo in self.combos:
            for opt in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opt.cards))
                options_constraint_count += 1

        if profile_result:
            profile_result.counts["cube_size"] = 1
            profile_result.counts["required_card"] = required_constraint_count
            profile_result.counts["requirement_options"] = options_constraint_count

        # NEW: Fix combo count to Phase 1 target (with optional tolerance)
        combo_sum = sum(y[combo.id] for combo in self.combos)

        combo_count_constraints = 0
        if self.combo_tolerance > 0:
            min_combo_count = math.floor(target_combo_count * (1 - self.combo_tolerance))
            max_combo_count = math.ceil(target_combo_count * (1 + self.combo_tolerance))

            logger.info(
                f"Phase 2 combo tolerance: {self.combo_tolerance:.1%} "
                f"(range: {min_combo_count}-{max_combo_count})"
            )

            model.add(combo_sum >= min_combo_count)
            model.add(combo_sum <= max_combo_count)
            combo_count_constraints = 2
        else:
            # No tolerance - use exact equality (current behavior)
            model.add(combo_sum == target_combo_count)
            combo_count_constraints = 1

        if profile_result:
            profile_result.counts["combo_count"] = combo_count_constraints

        # Add minimum coverage ratio constraints
        coverage_constraints = 0
        if self.min_coverage_ratio > 0:
            requirement_pool_info = self._build_requirement_pool_info()
            coverage_constraints = self._add_coverage_constraints(model, x, requirement_pool_info)
            logger.info(
                f"Phase 2: Added {coverage_constraints} coverage constraints "
                f"(min_ratio={self.min_coverage_ratio}, min_combos={self.min_combo_threshold})"
            )

        if profile_result:
            profile_result.counts["coverage"] = coverage_constraints

        # Utilization variables: u[card] = count of completable combos card participates in
        u: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            # Max possible utilization is the total number of combos
            u[card] = model.new_int_var(0, len(self.combos), f"util_{self.card_to_idx[card]}")

        if profile_result:
            profile_result.counts["variables_utilization"] = len(u)

        # Link utilization to combo participation (only for selected cards)
        utilization_linking_count = 0
        for card in self.all_cards:
            # Sum of combo variables this card participates in
            combo_sum = sum(y[combo.id] for combo in self.card_to_combos[card])
            # If card is selected, u[card] = combo_sum; else u[card] = 0
            model.add(u[card] == combo_sum).only_enforce_if(x[card])
            model.add(u[card] == 0).only_enforce_if(x[card].Not())
            utilization_linking_count += 2

        if profile_result:
            profile_result.counts["utilization_linking"] = utilization_linking_count

        # Compute target mean utilization from Phase 1
        assert phase1_result.phase1_utilization_stats is not None
        target_mean = phase1_result.phase1_utilization_stats.mean_utilization

        # MAD deviation variables (only for selected cards)
        # We need integer arithmetic, so scale mean by 100 to preserve precision
        mean_scaled = int(target_mean * 100)

        d_plus: dict[str, cp_model.IntVar] = {}
        d_minus: dict[str, cp_model.IntVar] = {}

        mad_constraint_count = 0
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
            mad_constraint_count += 4

        if profile_result:
            profile_result.counts["variables_deviation"] = len(d_plus) * 2
            profile_result.counts["mad_deviation"] = mad_constraint_count

        # Objective: Minimize total absolute deviation with versatility bonus
        mad_terms = sum(d_plus[card] + d_minus[card] for card in self.all_cards)

        # Add versatility bonus (subtract because we're minimizing)
        # Cards satisfying more templates get a small bonus
        versatility_bonus = sum(
            int(
                self.VERSATILITY_EPSILON
                * math.log1p(self.candidate_cards[card].template_count)
                * self.WEIGHT_SCALE
            )
            * x[card]
            for card in self.all_cards
        )

        model.minimize(mad_terms - versatility_bonus)

        # Warm-start from Phase 1 solution (add hints to model before solving)
        # Only hint variables that we have values for from Phase 1
        phase1_cards = {c.name for c in phase1_result.selected_cards}
        phase1_combos = set(phase1_result.completable_combo_ids)
        hints_added = 0

        # Hint card selection variables
        for card in self.all_cards:
            model.add_hint(x[card], 1 if card in phase1_cards else 0)
            hints_added += 1

        # Hint combo completion variables
        for combo in self.combos:
            model.add_hint(y[combo.id], 1 if combo.id in phase1_combos else 0)
            hints_added += 1

        # Hint utilization variables based on Phase 1 solution
        if phase1_result.utilization_per_card:
            for card in self.all_cards:
                if card in phase1_cards:
                    util_value = phase1_result.utilization_per_card.get(card, 0)
                    model.add_hint(u[card], util_value)
                else:
                    model.add_hint(u[card], 0)
                hints_added += 1

        logger.info(f"Phase 2: Added {hints_added} warm-start hints from Phase 1 solution")

        if profile_result:
            profile_result.timings["model_build"] = time.perf_counter() - build_start
            profile_result.counts["warm_start_hints"] = hints_added

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 8
        solver.parameters.log_search_progress = logger.isEnabledFor(logging.DEBUG)

        # Early termination: stop when solution is within gap_limit of optimal
        if self.gap_limit > 0:
            solver.parameters.relative_gap_limit = self.gap_limit
            logger.info(f"Phase 2: Early termination enabled (gap limit: {self.gap_limit:.1%})")

        solve_start = time.perf_counter()
        status = solver.solve(model)
        solver_time = time.perf_counter() - solve_start

        if profile_result:
            profile_result.timings["solver"] = solver_time
            profile_result.solver_stats = extract_solver_stats(solver)

        phase2_time = time.perf_counter() - start_time
        status_str = self._status_to_string(status)  # type: ignore[arg-type]

        # If Phase 2 fails, fall back to Phase 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            logger.warning(f"Phase 2 failed ({status_str}), falling back to Phase 1 result")
            return phase1_result

        # Extract Phase 2 solution
        extract_start = time.perf_counter()
        selected_names = [card for card in self.all_cards if solver.value(x[card]) == 1]
        selected = [self.candidate_cards[name] for name in selected_names]
        completed = [combo.id for combo in self.combos if solver.value(y[combo.id]) == 1]

        # Calculate Phase 2 utilization stats
        utilization = self._calculate_utilization(selected_names, completed)
        phase2_stats = self._compute_utilization_stats(utilization)

        # Calculate requirement type stats for Phase 2 solution
        selected_name_set = set(selected_names)
        completed_set = set(completed)
        req_stats = self._calculate_requirement_stats(selected_name_set, completed_set)
        coverage_stats = self._compute_coverage_stats(req_stats)

        # Calculate cross-template stats
        group_key_to_name = self._build_group_key_to_name_map(completed_set)
        cross_template_stats = self._calculate_cross_template_stats(
            selected_name_set, group_key_to_name
        )

        if profile_result:
            profile_result.timings["extraction"] = time.perf_counter() - extract_start
            profile_result.log_summary()

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

        # Build combined profile data
        profile_data = None
        if profile_result:
            # Merge Phase 1 profile data with Phase 2
            profile_data = phase1_result.profile_data or {}
            profile_data["phase2"] = {
                "timings": profile_result.timings,
                "counts": profile_result.counts,
                "solver_stats": profile_result.solver_stats,
            }
            # Log comparison summary
            phase1_profile = ProfileResult(
                phase="Phase 1",
                timings=profile_data.get("phase1", {}).get("timings", {}),
                counts=profile_data.get("phase1", {}).get("counts", {}),
                solver_stats=profile_data.get("phase1", {}).get("solver_stats", {}),
            )
            log_profile_comparison(phase1_profile, profile_result)

        # Return full multi-objective result
        return OptimizationResult(
            selected_cards=selected,
            completable_combo_ids=completed,
            combo_count=len(completed),
            objective_value=phase1_result.objective_value,  # Preserve Phase 1 objective
            solve_time_seconds=phase1_result.phase1_solve_time + phase2_time,  # type: ignore
            phase1_status=phase1_result.phase1_status,
            utilization_per_card=utilization,
            phase1_utilization_stats=phase1_result.phase1_utilization_stats,
            phase2_utilization_stats=phase2_stats,
            phase1_solve_time=phase1_result.phase1_solve_time,
            phase2_solve_time=phase2_time,
            phase2_status=status_str,
            is_multi_objective=True,
            requirement_type_stats=req_stats,
            requirement_coverage_stats=coverage_stats,
            cross_template_stats=cross_template_stats,
            phase1_selected_cards=phase1_result.selected_cards,
            profile_data=profile_data,
        )

    def _solve_phase2_minmax(
        self,
        target_combo_count: int,
        phase1_result: OptimizationResult,
        profile: bool = False,
    ) -> OptimizationResult:
        """
        Phase 2 with min-max range objective: minimize(max_util - min_util).

        Much faster than MAD because:
        - Only 2 auxiliary variables (vs 2×cards for MAD)
        - Tighter LP bounds
        - Simpler constraint structure

        Args:
            target_combo_count: Target combo count from Phase 1
            phase1_result: Result from Phase 1 optimization
            profile: If True, collect detailed profiling statistics
        """
        logger.info(
            f"Starting Phase 2 (minmax): balancing utilization (target: {target_combo_count} combos)"
        )
        start_time = time.perf_counter()
        profile_result = ProfileResult(phase="Phase 2 (minmax)") if profile else None

        model = cp_model.CpModel()
        build_start = time.perf_counter()

        # Decision variables (same as Phase 1)
        x: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            x[card] = model.new_bool_var(f"card_{self.card_to_idx[card]}")

        y: dict[str, cp_model.IntVar] = {}
        for combo in self.combos:
            y[combo.id] = model.new_bool_var(f"combo_{combo.id}")

        if profile_result:
            profile_result.counts["variables_card"] = len(x)
            profile_result.counts["variables_combo"] = len(y)

        # Base constraints (same as Phase 1)
        model.add(sum(x[card] for card in self.all_cards) == self.cube_size)

        required_constraint_count = 0
        for combo in self.combos:
            for card in combo.required_cards:
                model.add(y[combo.id] <= x[card])
                required_constraint_count += 1

        options_constraint_count = 0
        for combo in self.combos:
            for opt in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opt.cards))
                options_constraint_count += 1

        if profile_result:
            profile_result.counts["cube_size"] = 1
            profile_result.counts["required_card"] = required_constraint_count
            profile_result.counts["requirement_options"] = options_constraint_count

        # Combo count constraints (with tolerance)
        combo_sum = sum(y[combo.id] for combo in self.combos)

        combo_count_constraints = 0
        if self.combo_tolerance > 0:
            min_combo_count = math.floor(target_combo_count * (1 - self.combo_tolerance))
            max_combo_count = math.ceil(target_combo_count * (1 + self.combo_tolerance))

            logger.info(
                f"Phase 2 combo tolerance: {self.combo_tolerance:.1%} "
                f"(range: {min_combo_count}-{max_combo_count})"
            )

            model.add(combo_sum >= min_combo_count)
            model.add(combo_sum <= max_combo_count)
            combo_count_constraints = 2
        else:
            model.add(combo_sum == target_combo_count)
            combo_count_constraints = 1

        if profile_result:
            profile_result.counts["combo_count"] = combo_count_constraints

        # Coverage constraints
        coverage_constraints = 0
        if self.min_coverage_ratio > 0:
            requirement_pool_info = self._build_requirement_pool_info()
            coverage_constraints = self._add_coverage_constraints(model, x, requirement_pool_info)
            logger.info(
                f"Phase 2: Added {coverage_constraints} coverage constraints "
                f"(min_ratio={self.min_coverage_ratio}, min_combos={self.min_combo_threshold})"
            )

        if profile_result:
            profile_result.counts["coverage"] = coverage_constraints

        # Utilization variables
        u: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            u[card] = model.new_int_var(0, len(self.combos), f"util_{self.card_to_idx[card]}")

        if profile_result:
            profile_result.counts["variables_utilization"] = len(u)

        # Link utilization to combo participation
        utilization_linking_count = 0
        for card in self.all_cards:
            card_combo_sum = sum(y[combo.id] for combo in self.card_to_combos[card])
            model.add(u[card] == card_combo_sum).only_enforce_if(x[card])
            model.add(u[card] == 0).only_enforce_if(x[card].Not())
            utilization_linking_count += 2

        if profile_result:
            profile_result.counts["utilization_linking"] = utilization_linking_count

        # Min-max range variables (only 2!)
        max_util = model.new_int_var(0, len(self.combos), "max_util")
        min_util = model.new_int_var(0, len(self.combos), "min_util")

        if profile_result:
            profile_result.counts["variables_minmax"] = 2

        # Link max/min to card utilizations (only for selected cards)
        minmax_constraint_count = 0
        for card in self.all_cards:
            model.add(max_util >= u[card]).only_enforce_if(x[card])
            model.add(min_util <= u[card]).only_enforce_if(x[card])
            minmax_constraint_count += 2

        if profile_result:
            profile_result.counts["minmax_linking"] = minmax_constraint_count

        # Floor constraint: minimum utilization must be at least N
        if self.min_utilization_floor > 0:
            model.add(min_util >= self.min_utilization_floor)
            logger.info(f"Phase 2: Minimum utilization floor = {self.min_utilization_floor}")
            if profile_result:
                profile_result.counts["min_floor"] = 1

        # Objective: minimize range (max - min) with versatility bonus
        range_term = max_util - min_util

        # Add versatility bonus (subtract because we're minimizing)
        versatility_bonus = sum(
            int(
                self.VERSATILITY_EPSILON
                * math.log1p(self.candidate_cards[card].template_count)
                * self.WEIGHT_SCALE
            )
            * x[card]
            for card in self.all_cards
        )

        # Scale range to be comparable to bonus
        model.minimize(range_term * self.WEIGHT_SCALE - versatility_bonus)

        if profile_result:
            profile_result.timings["model_build"] = time.perf_counter() - build_start

        # Warm-start from Phase 1 solution
        phase1_cards = {c.name for c in phase1_result.selected_cards}
        phase1_combos = set(phase1_result.completable_combo_ids)
        hints_added = 0

        for card in self.all_cards:
            model.add_hint(x[card], 1 if card in phase1_cards else 0)
            hints_added += 1

        for combo in self.combos:
            model.add_hint(y[combo.id], 1 if combo.id in phase1_combos else 0)
            hints_added += 1

        if phase1_result.utilization_per_card:
            for card in self.all_cards:
                if card in phase1_cards:
                    util_value = phase1_result.utilization_per_card.get(card, 0)
                    model.add_hint(u[card], util_value)
                else:
                    model.add_hint(u[card], 0)
                hints_added += 1

        logger.info(f"Phase 2: Added {hints_added} warm-start hints from Phase 1 solution")

        if profile_result:
            profile_result.counts["warm_start_hints"] = hints_added

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 8
        solver.parameters.log_search_progress = logger.isEnabledFor(logging.DEBUG)

        if self.gap_limit > 0:
            solver.parameters.relative_gap_limit = self.gap_limit
            logger.info(f"Phase 2: Early termination enabled (gap limit: {self.gap_limit:.1%})")

        solve_start = time.perf_counter()
        status = solver.solve(model)
        solver_time = time.perf_counter() - solve_start

        if profile_result:
            profile_result.timings["solver"] = solver_time
            profile_result.solver_stats = extract_solver_stats(solver)

        phase2_time = time.perf_counter() - start_time
        status_str = self._status_to_string(status)  # type: ignore[arg-type]

        # If Phase 2 fails, fall back to Phase 1
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            logger.warning(f"Phase 2 failed ({status_str}), falling back to Phase 1 result")
            return phase1_result

        # Extract Phase 2 solution
        extract_start = time.perf_counter()
        selected_names = [card for card in self.all_cards if solver.value(x[card]) == 1]
        selected = [self.candidate_cards[name] for name in selected_names]
        completed = [combo.id for combo in self.combos if solver.value(y[combo.id]) == 1]

        # Calculate Phase 2 utilization stats
        utilization = self._calculate_utilization(selected_names, completed)
        phase2_stats = self._compute_utilization_stats(utilization)

        # Calculate requirement type stats
        selected_name_set = set(selected_names)
        completed_set = set(completed)
        req_stats = self._calculate_requirement_stats(selected_name_set, completed_set)
        coverage_stats = self._compute_coverage_stats(req_stats)

        # Calculate cross-template stats
        group_key_to_name = self._build_group_key_to_name_map(completed_set)
        cross_template_stats = self._calculate_cross_template_stats(
            selected_name_set, group_key_to_name
        )

        if profile_result:
            profile_result.timings["extraction"] = time.perf_counter() - extract_start
            profile_result.log_summary()

        # Log improvement metrics
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

        # Build combined profile data
        profile_data = None
        if profile_result:
            profile_data = phase1_result.profile_data or {}
            profile_data["phase2"] = {
                "timings": profile_result.timings,
                "counts": profile_result.counts,
                "solver_stats": profile_result.solver_stats,
            }
            phase1_profile = ProfileResult(
                phase="Phase 1",
                timings=profile_data.get("phase1", {}).get("timings", {}),
                counts=profile_data.get("phase1", {}).get("counts", {}),
                solver_stats=profile_data.get("phase1", {}).get("solver_stats", {}),
            )
            log_profile_comparison(phase1_profile, profile_result)

        return OptimizationResult(
            selected_cards=selected,
            completable_combo_ids=completed,
            combo_count=len(completed),
            objective_value=phase1_result.objective_value,
            solve_time_seconds=phase1_result.phase1_solve_time + phase2_time,  # type: ignore
            phase1_status=phase1_result.phase1_status,
            utilization_per_card=utilization,
            phase1_utilization_stats=phase1_result.phase1_utilization_stats,
            phase2_utilization_stats=phase2_stats,
            phase1_solve_time=phase1_result.phase1_solve_time,
            phase2_solve_time=phase2_time,
            phase2_status=status_str,
            is_multi_objective=True,
            requirement_type_stats=req_stats,
            requirement_coverage_stats=coverage_stats,
            cross_template_stats=cross_template_stats,
            phase1_selected_cards=phase1_result.selected_cards,
            profile_data=profile_data,
        )

    def solve_two_phase(self, profile: bool = False) -> OptimizationResult:
        """
        Two-phase multi-objective optimization (recommended entry point).

        Args:
            profile: If True, collect detailed profiling statistics

        Returns:
            OptimizationResult with balanced utilization, or Phase 1 fallback
        """
        logger.info("Starting two-phase multi-objective optimization")

        # Phase 1: Maximize combo count
        phase1_result = self.solve(profile=profile)

        # Handle Phase 1 failure or edge cases
        if phase1_result.phase1_status not in ("OPTIMAL", "FEASIBLE"):
            logger.warning(f"Phase 1 failed: {phase1_result.phase1_status}")
            return phase1_result

        if phase1_result.combo_count == 0:
            logger.warning("Phase 1 found 0 combos. Skipping Phase 2.")
            return phase1_result

        # Phase 2: Balance utilization (choose objective based on setting)
        if self.phase2_objective == "minmax":
            return self._solve_phase2_minmax(
                target_combo_count=phase1_result.combo_count,
                phase1_result=phase1_result,
                profile=profile,
            )
        else:  # default: "mad"
            return self._solve_phase2(
                target_combo_count=phase1_result.combo_count,
                phase1_result=phase1_result,
                profile=profile,
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
