"""ILP-based cube optimizer using OR-Tools CP-SAT solver."""

import logging
import math
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from ortools.sat.python import cp_model

from mtg_combo_cube.ilp.cube_evaluation import (
    card_utilization,
    completable_combo_ids,
    compute_utilization_stats,
)
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


@dataclass
class _BaseModel:
    """A CP-SAT model with the card and combo variables every phase shares."""

    model: cp_model.CpModel
    x: dict[str, cp_model.IntVar]  # x[card] = 1 if the card is in the cube
    y: dict[str, cp_model.IntVar]  # y[combo.id] = 1 if the combo is completable
    counts: dict[str, int]  # variable/constraint counts by kind, reported in profile data
    # Phase 2 only: z[cards] = 1 iff at least one card of that option pool is in the cube.
    # One variable per distinct pool of two or more cards, shared by every combo using it.
    option_satisfied: dict[frozenset[str], cp_model.IntVar] = field(default_factory=dict)
    # Phase 2 only: set by an objective whose optimum can be near zero, where the relative
    # gap limit is not a usable stop rule. In objective units; None = relative gap only.
    absolute_gap_limit: float | None = None
    # Phase 2 only: utilization of the warm-start cube, for hinting objective variables
    hint_utilization: dict[str, int] = field(default_factory=dict)


@dataclass
class _WarmStart:
    """The cube Phase 2 is hinted with, and its true combos and utilization."""

    cards: set[str]
    combo_ids: set[str]
    utilization: dict[str, int]


@dataclass
class _Solution:
    """Values and statistics read from a solved model."""

    selected_cards: list[CandidateCard]
    completable_combo_ids: list[str]
    utilization_per_card: dict[str, int]
    utilization_stats: UtilizationStats
    requirement_type_stats: list[RequirementTypeStats]
    requirement_coverage_stats: RequirementCoverageStats
    cross_template_stats: CrossTemplateStats


@dataclass(frozen=True)
class _Phase2Objective:
    """
    A Phase 2 objective.

    add_to_model(optimizer, base, u, phase1_result) adds the variables and constraints
    specific to the objective, sets the model objective, and records what it added in
    base.counts.
    """

    label: str  # used in log messages and as the profile phase name
    add_to_model: Callable[
        ["ILPOptimizer", _BaseModel, dict[str, cp_model.IntVar], OptimizationResult], None
    ]
    uses_util_cap: bool = False  # the objective depends on the utilization cap (util_cap)


class ILPOptimizer:
    """
    ILP-based cube optimizer using OR-Tools CP-SAT solver.

    Maximizes the number of completable combos within a fixed cube size,
    using log-scaled popularity as a tiebreaker.
    """

    DEFAULT_TIME_LIMIT = 300  # 5 minutes
    TIEBREAK_EPSILON = 0.001  # Small weight for popularity tiebreaker
    VERSATILITY_EPSILON = 0.0001  # Small bonus for multi-template cards
    WARM_START_REPAIR_FRACTION = 0.1  # Share of the Phase 2 time limit for the hint repair
    TIER_MULTIPLES = (1, 2, 4)  # "tiered" objective: overage is counted above each multiple of T
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
        phase2_objective: str = "tiered",
        min_utilization_floor: int = 2,
        num_workers: int = 8,
        util_cap: int | None = None,
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
        if phase2_objective not in self._PHASE2_OBJECTIVES:
            raise ValueError(
                f"Unknown phase2_objective: {phase2_objective!r} "
                f"(expected one of {sorted(self._PHASE2_OBJECTIVES)})"
            )
        self.phase2_objective = phase2_objective  # a key of _PHASE2_OBJECTIVES
        self.min_utilization_floor = min_utilization_floor  # Phase 2, every objective
        self.num_workers = num_workers  # CP-SAT parallel search workers
        if util_cap is not None and util_cap < 0:
            raise ValueError(f"util_cap must be >= 0, got {util_cap}")
        self.util_cap = util_cap  # "softcap" and "tiered"; None = derive from Phase 1

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
        return compute_utilization_stats(utilization)

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

    def _build_base_model(self) -> _BaseModel:
        """
        Build the model shared by every phase.

        Variables: x[c] = 1 if card c is in the cube, y[j] = 1 if combo j is completable.
        Constraints: cube size, required cards, and requirement options.
        """
        model = cp_model.CpModel()

        x: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            x[card] = model.new_bool_var(f"card_{self.card_to_idx[card]}")

        y: dict[str, cp_model.IntVar] = {}
        for combo in self.combos:
            y[combo.id] = model.new_bool_var(f"combo_{combo.id}")

        # Constraint 1: Cube size
        model.add(sum(x[card] for card in self.all_cards) == self.cube_size)

        # Constraint 2: Required cards for each combo
        # y[j] <= x[c] for all c in required_cards[j]
        required_constraint_count = 0
        for combo in self.combos:
            for card in combo.required_cards:
                model.add(y[combo.id] <= x[card])
                required_constraint_count += 1

        # Constraint 3: Optional requirements (at least one card from each set)
        # y[j] <= sum(x[c] for c in matching_cards[r])
        options_constraint_count = 0
        for combo in self.combos:
            for opt in combo.requirement_options:
                model.add(y[combo.id] <= sum(x[card] for card in opt.cards))
                options_constraint_count += 1

        counts = {
            "variables_card": len(x),
            "variables_combo": len(y),
            "cube_size": 1,
            "required_card": required_constraint_count,
            "requirement_options": options_constraint_count,
        }
        return _BaseModel(model=model, x=x, y=y, counts=counts)

    def _add_combo_count_objective(self, base: _BaseModel) -> None:
        """Phase 1 objective: maximize the popularity-weighted combo count."""
        objective_terms = []
        for combo in self.combos:
            weight = self._compute_weight(combo.popularity)
            objective_terms.append(weight * base.y[combo.id])
        base.model.maximize(sum(objective_terms))

    def _combo_count_window(self, target_combo_count: int) -> tuple[int, int]:
        """Smallest and largest combo count Phase 2 accepts for a Phase 1 count."""
        if self.combo_tolerance > 0:
            return (
                math.floor(target_combo_count * (1 - self.combo_tolerance)),
                math.ceil(target_combo_count * (1 + self.combo_tolerance)),
            )
        return target_combo_count, target_combo_count

    def _add_combo_count_window(self, base: _BaseModel, target_combo_count: int) -> None:
        """Hold the combo count at the Phase 1 target, within combo_tolerance."""
        combo_sum = sum(base.y[combo.id] for combo in self.combos)

        if self.combo_tolerance > 0:
            min_combo_count, max_combo_count = self._combo_count_window(target_combo_count)

            logger.info(
                f"Phase 2 combo tolerance: {self.combo_tolerance:.1%} "
                f"(range: {min_combo_count}-{max_combo_count})"
            )

            base.model.add(combo_sum >= min_combo_count)
            base.model.add(combo_sum <= max_combo_count)
            base.counts["combo_count"] = 2
        else:
            # No tolerance - use exact equality
            base.model.add(combo_sum == target_combo_count)
            base.counts["combo_count"] = 1

    def _add_phase2_coverage(self, base: _BaseModel) -> None:
        """Add the minimum coverage ratio constraints when they are enabled."""
        coverage_constraints = 0
        if self.min_coverage_ratio > 0:
            requirement_pool_info = self._build_requirement_pool_info()
            coverage_constraints = self._add_coverage_constraints(
                base.model, base.x, requirement_pool_info
            )
            logger.info(
                f"Phase 2: Added {coverage_constraints} coverage constraints "
                f"(min_ratio={self.min_coverage_ratio}, min_combos={self.min_combo_threshold})"
            )
        base.counts["coverage"] = coverage_constraints

    def _add_exact_combo_linking(self, base: _BaseModel) -> None:
        """
        Make y exact: y[j] = 1 if and only if the selected cards complete combo j.

        The base model only bounds y from above (y = 1 requires a complete combo), which is
        enough when y is maximized. The Phase 2 objectives reward low utilization, so
        without the other direction the solver could switch off a combo the cube completes.

        For each distinct option pool, z = 1 iff at least one of its cards is selected
        (z is the card variable itself for a single-card pool). Then, over the k required
        cards and option pools of the combo: y[j] >= (number satisfied) - (k - 1).

        Written as linear constraints. The equivalent clause form (add_bool_or /
        add_implication) was compared on rungs S and M and was not better; see
        docs/ilp-improvement-plan.md, Stage 3.
        """
        model = base.model
        x = base.x
        option_linking_count = 0

        def option_literal(cards: frozenset[str]) -> cp_model.IntVar:
            nonlocal option_linking_count
            if len(cards) == 1:
                return x[next(iter(cards))]
            if cards not in base.option_satisfied:
                if not cards:
                    base.option_satisfied[cards] = model.new_constant(0)
                    return base.option_satisfied[cards]
                z = model.new_bool_var(f"option_{len(base.option_satisfied)}")
                pool = [x[card] for card in sorted(cards)]
                model.add(z <= sum(pool))
                for card_var in pool:
                    model.add(z >= card_var)
                option_linking_count += 1 + len(pool)
                base.option_satisfied[cards] = z
            return base.option_satisfied[cards]

        for combo in self.combos:
            literals = [x[card] for card in sorted(combo.required_cards)]
            for cards in dict.fromkeys(opt.cards for opt in combo.requirement_options):
                literals.append(option_literal(cards))
            model.add(base.y[combo.id] >= sum(literals) - (len(literals) - 1))

        base.counts["variables_option"] = len(base.option_satisfied)
        base.counts["option_linking"] = option_linking_count
        base.counts["combo_exact_linking"] = len(self.combos)

    def _add_utilization_vars(self, base: _BaseModel) -> dict[str, cp_model.IntVar]:
        """
        Add u[card] = number of completed combos the card participates in (0 if unselected).

        Shared by every Phase 2 objective. Relies on y being exact
        (_add_exact_combo_linking). u[card] is bounded by the number of combos the card
        appears in.
        """
        model = base.model
        pool_cards = {
            card for combo in self.combos for opt in combo.requirement_options for card in opt.cards
        }

        u: dict[str, cp_model.IntVar] = {}
        utilization_linking_count = 0
        for card in self.all_cards:
            card_combos = self.card_to_combos[card]
            if not card_combos:
                # The card is in no combo: its utilization is always 0
                u[card] = model.new_constant(0)
                continue

            u[card] = model.new_int_var(0, len(card_combos), f"util_{self.card_to_idx[card]}")
            card_combo_sum = sum(base.y[combo.id] for combo in card_combos)
            if card in pool_cards:
                # A combo can be complete through another card of the pool, so the sum
                # only counts when the card is selected
                model.add(u[card] == card_combo_sum).only_enforce_if(base.x[card])
                model.add(u[card] == 0).only_enforce_if(base.x[card].Not())
                utilization_linking_count += 2
            else:
                # Required in every one of its combos: y <= x already makes the sum 0
                # when the card is not selected
                model.add(u[card] == card_combo_sum)
                utilization_linking_count += 1

        base.counts["variables_utilization"] = len(u)
        base.counts["utilization_linking"] = utilization_linking_count
        return u

    def _add_utilization_floor(self, base: _BaseModel, u: dict[str, cp_model.IntVar]) -> None:
        """Every selected card takes part in at least min_utilization_floor completed combos."""
        floor = self.min_utilization_floor
        if floor <= 0:
            return

        for card in self.all_cards:
            if len(self.card_to_combos[card]) < floor:
                # The card can never reach the floor, so it cannot be selected
                base.model.add(base.x[card] == 0)
            else:
                base.model.add(u[card] >= floor * base.x[card])

        logger.info(f"Phase 2: Minimum utilization floor = {floor}")
        base.counts["utilization_floor"] = len(self.all_cards)

    def _versatility_weight(self, card: str) -> int:
        """Integer tiebreak weight of a card: grows with the templates it satisfies."""
        return int(
            self.VERSATILITY_EPSILON
            * math.log1p(self.candidate_cards[card].template_count)
            * self.WEIGHT_SCALE
        )

    def _versatility_bonus(self, base: _BaseModel) -> cp_model.LinearExpr | int:
        """Small bonus for cards that satisfy more templates (subtract when minimizing)."""
        return sum(self._versatility_weight(card) * base.x[card] for card in self.all_cards)

    def _tiebreak_scale(self) -> int:
        """
        Factor for an integer-valued primary objective that keeps the versatility bonus a
        pure tiebreak: larger than the biggest bonus any cube can collect, so one unit of
        the primary objective always outweighs it. WEIGHT_SCALE unless the bonus could
        exceed it.
        """
        weights = sorted((self._versatility_weight(card) for card in self.all_cards), reverse=True)
        return max(self.WEIGHT_SCALE, sum(weights[: self.cube_size]) + 1)

    def _resolve_util_cap(self, phase1_stats: UtilizationStats) -> int:
        """
        The utilization cap T of the "softcap" and "tiered" objectives.

        util_cap when given. Otherwise twice the Phase 1 median utilization: the median is
        the typical card and, unlike the mean, is not pulled up by the hub cards the cap is
        meant to catch, so "more than twice the typical card" marks a card as over-used.
        """
        if self.util_cap is not None:
            return self.util_cap
        return max(1, math.ceil(2 * phase1_stats.median_utilization))

    def _add_mad_objective(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_result: OptimizationResult,
    ) -> None:
        """
        Phase 2 "mad" objective: minimize total absolute deviation from the
        Phase 1 mean utilization.

        MAD linearization: minimize sum(d_plus[c] + d_minus[c]).
        """
        model = base.model
        x = base.x
        phase1_stats, _ = self._require_phase1_stats(phase1_result)

        # We need integer arithmetic, so scale mean by 100 to preserve precision
        mean_scaled = int(phase1_stats.mean_utilization * 100)

        d_plus: dict[str, cp_model.IntVar] = {}
        d_minus: dict[str, cp_model.IntVar] = {}

        mad_constraint_count = 0
        for card in self.all_cards:
            # Deviations are bounded by the card's utilization range [0, combos it is in]
            max_plus = max(0, len(self.card_to_combos[card]) * 100 - mean_scaled)
            max_minus = max(0, mean_scaled)
            d_plus[card] = model.new_int_var(0, max_plus, f"dplus_{self.card_to_idx[card]}")
            d_minus[card] = model.new_int_var(0, max_minus, f"dminus_{self.card_to_idx[card]}")

            # u[card] - mean <= d_plus[card] (when x[card] = 1)
            # mean - u[card] <= d_minus[card] (when x[card] = 1)
            # When x[card] = 0, both deviations are 0
            model.add(u[card] * 100 - mean_scaled <= d_plus[card]).only_enforce_if(x[card])
            model.add(mean_scaled - u[card] * 100 <= d_minus[card]).only_enforce_if(x[card])
            model.add(d_plus[card] == 0).only_enforce_if(x[card].Not())
            model.add(d_minus[card] == 0).only_enforce_if(x[card].Not())
            mad_constraint_count += 4

        base.counts["variables_deviation"] = len(d_plus) * 2
        base.counts["mad_deviation"] = mad_constraint_count

        mad_terms = sum(d_plus[card] + d_minus[card] for card in self.all_cards)
        model.minimize(mad_terms - self._versatility_bonus(base))

    def _add_minmax_objective(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_result: OptimizationResult,
    ) -> None:
        """
        Phase 2 "minmax" objective: minimize(max_util - min_util) over selected cards.

        Needs only 2 auxiliary variables (vs 2 x cards for MAD).
        """
        model = base.model
        x = base.x
        phase1_stats, _ = self._require_phase1_stats(phase1_result)

        # max_util cannot exceed the largest per-card bound. min_util is the minimum over
        # cube_size selected cards, so it cannot exceed the cube_size-th largest bound.
        bounds = sorted((len(self.card_to_combos[card]) for card in self.all_cards), reverse=True)
        max_bound = bounds[0] if bounds else 0
        min_bound = bounds[min(self.cube_size, len(bounds)) - 1] if bounds else 0
        max_util = model.new_int_var(0, max_bound, "max_util")
        min_util = model.new_int_var(0, min_bound, "min_util")
        base.counts["variables_minmax"] = 2

        # Link max/min to the utilization of the selected cards.
        #   max_util >= u[card]: no big-M needed, because u[card] = 0 for unselected cards
        #   min_util <= u[card] + M * (1 - x[card])  =>  when x=1: min_util <= u[card]
        # M = min_bound (the upper bound of min_util) switches the constraint off when x=0.
        minmax_constraint_count = 0
        for card in self.all_cards:
            model.add(max_util >= u[card])
            model.add(min_util <= u[card] + min_bound * (1 - x[card]))
            minmax_constraint_count += 2

        base.counts["minmax_linking"] = minmax_constraint_count

        hint_max, hint_min = self._hint_utilization_extremes(base, phase1_stats)
        model.add_hint(max_util, min(hint_max, max_bound))
        model.add_hint(min_util, min(hint_min, min_bound))

        # Scale range to be comparable to bonus
        range_term = max_util - min_util
        model.minimize(range_term * self._tiebreak_scale() - self._versatility_bonus(base))

    @staticmethod
    def _hint_utilization_extremes(
        base: _BaseModel, phase1_stats: UtilizationStats
    ) -> tuple[int, int]:
        """Largest and smallest utilization in the warm-start cube (Phase 1 if there is none)."""
        values = base.hint_utilization.values()
        if not values:
            return phase1_stats.max_utilization, phase1_stats.min_utilization
        return max(values), min(values)

    def _add_max_util_var(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_stats: UtilizationStats,
    ) -> cp_model.IntVar:
        """Add max_util >= u[card] for every card, hinted with the warm-start maximum."""
        model = base.model
        max_bound = max((len(self.card_to_combos[card]) for card in self.all_cards), default=0)
        max_util = model.new_int_var(0, max_bound, "max_util")

        # No big-M needed: u[card] = 0 for unselected cards
        linking_count = 0
        for card in self.all_cards:
            if not self.card_to_combos[card]:
                continue  # u is the constant 0
            model.add(max_util >= u[card])
            linking_count += 1

        hint_max, _ = self._hint_utilization_extremes(base, phase1_stats)
        model.add_hint(max_util, min(hint_max, max_bound))
        base.counts["variables_maxutil"] = 1
        base.counts["maxutil_linking"] = linking_count
        return max_util

    def _add_maxutil_objective(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_result: OptimizationResult,
    ) -> None:
        """
        Phase 2 "maxutil" objective: minimize the largest utilization of a selected card.

        The low end is handled by the utilization floor, so unlike "minmax" there is no
        min_util variable and no linking for it.
        """
        phase1_stats, _ = self._require_phase1_stats(phase1_result)
        max_util = self._add_max_util_var(base, u, phase1_stats)
        base.model.minimize(max_util * self._tiebreak_scale() - self._versatility_bonus(base))

    def _add_overage_vars(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        cap: int,
    ) -> cp_model.LinearExpr | int:
        """
        Add over[card] >= u[card] - cap for every card that can exceed the cap.

        No conditioning on x is needed: u[card] = 0 for unselected cards. Returns the sum
        of the overage variables. Can be called once per cap for several caps.
        """
        model = base.model

        over: dict[str, cp_model.IntVar] = {}
        for card in self.all_cards:
            max_over = len(self.card_to_combos[card]) - cap
            if max_over <= 0:
                continue  # the card can never exceed the cap
            over[card] = model.new_int_var(0, max_over, f"over{cap}_{self.card_to_idx[card]}")
            model.add(over[card] >= u[card] - cap)
            model.add_hint(over[card], max(0, base.hint_utilization.get(card, 0) - cap))

        for name in ("variables_overage", "overage_linking"):
            base.counts[name] = base.counts.get(name, 0) + len(over)
        return sum(over.values())

    def _set_overage_stop_rule(self, base: _BaseModel, scale: int, phase1_overage: int) -> None:
        """
        Stop rule for objectives whose primary term is a total overage that can reach 0.

        Near 0 a relative gap is useless (and the objective itself is negative once only
        the versatility bonus is left). So the gap limit is measured against the overage
        of the Phase 1 cube instead: stop once the overage is proven to be within
        gap_limit * phase1_overage of its optimum, without proving the tiebreak optimal.
        The relative limit stays active but is never the tighter one, because the Phase 1
        overage is at least the current one.
        """
        if self.gap_limit <= 0:
            return
        allowed_overage = math.floor(self.gap_limit * phase1_overage)
        # (scale - 1) covers any difference in the tiebreak term
        base.absolute_gap_limit = allowed_overage * scale + scale - 1
        logger.info(
            f"Phase 2: stop when the overage is proven within {allowed_overage} of optimal "
            f"({self.gap_limit:.1%} of the Phase 1 overage of {phase1_overage})"
        )

    def _add_overage_objective(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_result: OptimizationResult,
        caps: list[int],
    ) -> None:
        """Minimize the total utilization above each of the caps, summed over the caps."""
        overage = sum(self._add_overage_vars(base, u, cap) for cap in caps)
        phase1_overage = sum(
            max(0, value - cap)
            for value in (phase1_result.utilization_per_card or {}).values()
            for cap in caps
        )

        scale = self._tiebreak_scale()
        base.model.minimize(overage * scale - self._versatility_bonus(base))
        self._set_overage_stop_rule(base, scale, phase1_overage)

    def _add_softcap_objective(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_result: OptimizationResult,
    ) -> None:
        """
        Phase 2 "softcap" objective: minimize the total utilization above a cap T,
        sum(max(0, u[card] - T)) over the selected cards.

        Penalizes every over-used card, not only the worst one. T is util_cap, or twice
        the Phase 1 median utilization when not given (_resolve_util_cap).
        """
        phase1_stats, _ = self._require_phase1_stats(phase1_result)
        cap = self._resolve_util_cap(phase1_stats)
        self._add_overage_objective(base, u, phase1_result, [cap])

    def _add_tiered_objective(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        phase1_result: OptimizationResult,
    ) -> None:
        """
        Phase 2 "tiered" objective: softcap with a penalty that grows faster the further
        a card is above the cap. Minimizes the total utilization above T, plus the total
        above 2T, plus the total above 4T.

        A unit of utilization costs 1 between T and 2T, 2 between 2T and 4T and 3 above
        4T: a convex piecewise-linear stand-in for the squared deviation, so that one
        card far above the cap is worse than several cards slightly above it. T is the
        same cap as for "softcap".
        """
        phase1_stats, _ = self._require_phase1_stats(phase1_result)
        cap = self._resolve_util_cap(phase1_stats)
        self._add_overage_objective(base, u, phase1_result, [cap * m for m in self.TIER_MULTIPLES])

    # Phase 2 objectives by name (the phase2_objective constructor argument). To add one,
    # write an _add_<name>_objective method with the same signature and register it here.
    _PHASE2_OBJECTIVES: dict[str, _Phase2Objective] = {
        "mad": _Phase2Objective(label="Phase 2 (mad)", add_to_model=_add_mad_objective),
        "minmax": _Phase2Objective(label="Phase 2 (minmax)", add_to_model=_add_minmax_objective),
        "maxutil": _Phase2Objective(label="Phase 2 (maxutil)", add_to_model=_add_maxutil_objective),
        "softcap": _Phase2Objective(
            label="Phase 2 (softcap)", add_to_model=_add_softcap_objective, uses_util_cap=True
        ),
        "tiered": _Phase2Objective(
            label="Phase 2 (tiered)", add_to_model=_add_tiered_objective, uses_util_cap=True
        ),
    }

    def _get_phase2_objective(self) -> _Phase2Objective:
        """Look up the configured Phase 2 objective (validated in __init__)."""
        return self._PHASE2_OBJECTIVES[self.phase2_objective]

    def _warm_start_for(self, cards: set[str]) -> _WarmStart:
        """The warm start for a cube, with its true combos and utilization."""
        return _WarmStart(
            cards=cards,
            combo_ids=set(completable_combo_ids(cards, self.combos)),
            utilization=card_utilization(cards, self.combos),
        )

    def _coverage_violations(self, cards: set[str]) -> int:
        """Number of Phase 2 coverage constraints (_add_coverage_constraints) a cube breaks."""
        if self.min_coverage_ratio <= 0:
            return 0
        violations = 0
        for info in self._build_requirement_pool_info().values():
            if info.combo_count < self.min_combo_threshold:
                continue
            required = min(
                math.ceil(self.min_coverage_ratio * info.combo_count), len(info.pool_cards)
            )
            pool = [card for card in info.pool_cards if card in self.card_to_idx]
            if len(pool) >= required and sum(card in cards for card in pool) < required:
                violations += 1
        return violations

    def _repair_warm_start(
        self, phase1_result: OptimizationResult, phase1_start: _WarmStart
    ) -> tuple[_WarmStart | None, str]:
        """
        Find a cube near the Phase 1 cube that satisfies every Phase 2 constraint.

        Solved in the small Phase 1 model (one-sided y) with the coverage constraints, the
        lower edge of the combo count window, and the utilization floor written on y:
        floor * x[c] <= sum(y over the combos of c). With one-sided y that sum never
        exceeds the true utilization, so the true floor holds. The search is hinted with
        the Phase 1 cube and stops at the first solution. Returns the cube (None if none
        was found) and the solver status.
        """
        base = self._build_base_model()
        self._add_phase2_coverage(base)
        self._add_combo_count_objective(base)
        min_combo_count, _ = self._combo_count_window(phase1_result.combo_count)
        base.model.add(sum(base.y[combo.id] for combo in self.combos) >= min_combo_count)
        if self.min_utilization_floor > 0:
            for card in self.all_cards:
                base.model.add(
                    self.min_utilization_floor * base.x[card]
                    <= sum(base.y[combo.id] for combo in self.card_to_combos[card])
                )
        for card in self.all_cards:
            base.model.add_hint(base.x[card], 1 if card in phase1_start.cards else 0)
        for combo in self.combos:
            base.model.add_hint(base.y[combo.id], 1 if combo.id in phase1_start.combo_ids else 0)

        solver = self._make_solver(time_limit=self.time_limit * self.WARM_START_REPAIR_FRACTION)
        solver.parameters.stop_after_first_solution = True
        # CpSolverStatus is int at runtime, type stubs are incomplete
        status_str = self._status_to_string(solver.solve(base.model))  # type: ignore[arg-type]
        if status_str not in ("OPTIMAL", "FEASIBLE"):
            return None, status_str
        cards = {card for card in self.all_cards if solver.value(base.x[card]) == 1}
        return self._warm_start_for(cards), status_str

    def _build_warm_start(
        self,
        phase1_result: OptimizationResult,
        profile_result: ProfileResult | None,
    ) -> _WarmStart:
        """
        Choose the cube Phase 2 is hinted with.

        The Phase 1 cube when it satisfies the Phase 2 coverage and floor constraints.
        Otherwise CP-SAT would first have to repair the hint inside the much larger
        Phase 2 model, so a feasible cube is searched for in the small Phase 1 model
        instead (_repair_warm_start). If that finds none, the Phase 1 cube is used.
        """
        warm_start = self._warm_start_for({card.name for card in phase1_result.selected_cards})
        coverage_violations = self._coverage_violations(warm_start.cards)
        below_floor = sum(
            1 for value in warm_start.utilization.values() if value < self.min_utilization_floor
        )
        if not coverage_violations and not below_floor:
            return warm_start

        problem = (
            f"Phase 1 cube breaks {coverage_violations} coverage constraints and has "
            f"{below_floor} cards below the utilization floor"
        )
        repair_start = time.perf_counter()
        repaired, status_str = self._repair_warm_start(phase1_result, warm_start)
        repair_time = time.perf_counter() - repair_start
        if profile_result:
            profile_result.timings["warm_start_repair"] = repair_time

        if repaired is None:
            logger.warning(
                f"Phase 2: {problem}; no repaired warm start found ({status_str}, "
                f"{repair_time:.1f}s), hinting with the Phase 1 cube"
            )
            return warm_start
        logger.info(
            f"Phase 2: {problem}; repaired warm start has {len(repaired.combo_ids)} combos, "
            f"{len(repaired.cards - warm_start.cards)} cards swapped ({repair_time:.1f}s)"
        )
        return repaired

    def _add_warm_start(
        self,
        base: _BaseModel,
        u: dict[str, cp_model.IntVar],
        warm_start: _WarmStart,
    ) -> None:
        """Hint the card, combo, option and utilization variables with the warm-start cube."""
        model = base.model
        hints_added = 0

        for card in self.all_cards:
            model.add_hint(base.x[card], 1 if card in warm_start.cards else 0)
            hints_added += 1

        for combo in self.combos:
            model.add_hint(base.y[combo.id], 1 if combo.id in warm_start.combo_ids else 0)
            hints_added += 1

        for cards, z in base.option_satisfied.items():
            model.add_hint(z, 0 if cards.isdisjoint(warm_start.cards) else 1)
            hints_added += 1

        for card in self.all_cards:
            if not self.card_to_combos[card]:
                continue  # u is the constant 0
            model.add_hint(u[card], warm_start.utilization.get(card, 0))
            hints_added += 1

        logger.info(f"Phase 2: Added {hints_added} warm-start hints")
        base.counts["warm_start_hints"] = hints_added

    def _make_solver(
        self,
        gap_limit: float | None = None,
        absolute_gap_limit: float | None = None,
        time_limit: float | None = None,
    ) -> cp_model.CpSolver:
        """Create a solver with the configured time limit, workers and logging."""
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = (
            self.time_limit if time_limit is None else time_limit
        )
        solver.parameters.num_workers = self.num_workers  # Parallel search
        solver.parameters.log_search_progress = logger.isEnabledFor(logging.DEBUG)

        # Early termination: stop when solution is within gap_limit of optimal
        if gap_limit is not None and gap_limit > 0:
            solver.parameters.relative_gap_limit = gap_limit
            logger.info(f"Phase 2: Early termination enabled (gap limit: {gap_limit:.1%})")
        if absolute_gap_limit is not None:
            solver.parameters.absolute_gap_limit = absolute_gap_limit

        return solver

    def _run_solver(
        self,
        solver: cp_model.CpSolver,
        base: _BaseModel,
        profile_result: ProfileResult | None,
    ) -> str:
        """Solve the model, record solver profiling, and return the status string."""
        solve_start = time.perf_counter()
        status = solver.solve(base.model)
        solver_time = time.perf_counter() - solve_start

        if profile_result:
            profile_result.timings["solver"] = solver_time
            profile_result.solver_stats = extract_solver_stats(solver)

        # CpSolverStatus is int at runtime, type stubs are incomplete
        return self._status_to_string(status)  # type: ignore[arg-type]

    def _extract_solution(
        self,
        solver: cp_model.CpSolver,
        base: _BaseModel,
        profile_result: ProfileResult | None,
    ) -> _Solution:
        """Read the solved model and compute the statistics every phase reports."""
        extract_start = time.perf_counter()
        selected_names = [card for card in self.all_cards if solver.value(base.x[card]) == 1]
        selected = [self.candidate_cards[name] for name in selected_names]

        # Everything reported is computed from the selected cards, not from the solver's y
        completed = completable_combo_ids(set(selected_names), self.combos)
        solver_completed = {
            combo.id for combo in self.combos if solver.value(base.y[combo.id]) == 1
        }
        if solver_completed != set(completed):
            logger.warning(
                f"Solver combo variables disagree with the selected cards: the solver marks "
                f"{len(solver_completed)} combos complete, the cards complete {len(completed)} "
                f"({len(solver_completed.symmetric_difference(completed))} combos differ). "
                f"Reporting the true values."
            )

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

        return _Solution(
            selected_cards=selected,
            completable_combo_ids=completed,
            utilization_per_card=utilization,
            utilization_stats=utilization_stats,
            requirement_type_stats=req_stats,
            requirement_coverage_stats=coverage_stats,
            cross_template_stats=cross_template_stats,
        )

    @staticmethod
    def _profile_block(profile_result: ProfileResult) -> dict[str, Any]:
        """Profile data for one phase, as stored in OptimizationResult.profile_data."""
        return {
            "timings": profile_result.timings,
            "counts": profile_result.counts,
            "solver_stats": profile_result.solver_stats,
        }

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
                solve_time_seconds=time.perf_counter() - start_time,
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
                solve_time_seconds=time.perf_counter() - start_time,
                phase1_status="INFEASIBLE",
            )

        build_start = time.perf_counter()
        base = self._build_base_model()
        self._add_combo_count_objective(base)

        if profile_result:
            profile_result.counts = base.counts
            profile_result.timings["model_build"] = time.perf_counter() - build_start

        solver = self._make_solver()
        status_str = self._run_solver(solver, base, profile_result)
        solve_time = time.perf_counter() - start_time

        if status_str not in ("OPTIMAL", "FEASIBLE"):
            logger.warning(f"ILP solve failed: {status_str}")
            return OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=solve_time,
                phase1_status=status_str,
            )

        objective = solver.objective_value
        solution = self._extract_solution(solver, base, profile_result)
        utilization_stats = solution.utilization_stats

        logger.info(
            f"ILP solved ({status_str}): {len(solution.selected_cards)} cards, "
            f"{len(solution.completable_combo_ids)} combos in {solve_time:.1f}s"
        )
        logger.info(
            f"Utilization stats: min={utilization_stats.min_utilization}, "
            f"max={utilization_stats.max_utilization}, "
            f"mean={utilization_stats.mean_utilization:.1f}, "
            f"median={utilization_stats.median_utilization:.1f}, "
            f"std_dev={utilization_stats.std_deviation:.2f}"
        )

        profile_data = None
        if profile_result:
            profile_data = {"phase1": self._profile_block(profile_result)}

        return OptimizationResult(
            selected_cards=solution.selected_cards,
            completable_combo_ids=solution.completable_combo_ids,
            combo_count=len(solution.completable_combo_ids),
            objective_value=objective / self.WEIGHT_SCALE,
            solve_time_seconds=solve_time,
            phase1_status=status_str,
            utilization_per_card=solution.utilization_per_card,
            phase1_utilization_stats=utilization_stats,
            phase1_solve_time=solve_time,
            is_multi_objective=False,
            requirement_type_stats=solution.requirement_type_stats,
            requirement_coverage_stats=solution.requirement_coverage_stats,
            cross_template_stats=solution.cross_template_stats,
            profile_data=profile_data,
        )

    @staticmethod
    def _require_phase1_stats(
        phase1_result: OptimizationResult,
    ) -> tuple[UtilizationStats, float]:
        """
        Return the Phase 1 utilization stats and solve time that Phase 2 builds on.

        Raises ValueError if phase1_result is not a successful Phase 1 solution.
        """
        stats = phase1_result.phase1_utilization_stats
        solve_time = phase1_result.phase1_solve_time
        if stats is None or solve_time is None:
            raise ValueError(
                "Phase 2 requires a successful Phase 1 result "
                f"(phase1_status={phase1_result.phase1_status})"
            )
        return stats, solve_time

    @staticmethod
    def _log_phase2_improvement(
        p1: UtilizationStats, p2: UtilizationStats, status_str: str, phase2_time: float
    ) -> None:
        """Log how Phase 2 changed the utilization spread relative to Phase 1."""
        std_improvement = (
            100 * (1 - p2.std_deviation / p1.std_deviation) if p1.std_deviation > 0 else 0.0
        )
        range_before = p1.max_utilization - p1.min_utilization
        range_after = p2.max_utilization - p2.min_utilization
        range_improvement = 100 * (1 - range_after / range_before) if range_before > 0 else 0.0

        logger.info(
            f"Phase 2 complete ({status_str}): "
            f"std_dev {p1.std_deviation:.2f} → {p2.std_deviation:.2f} "
            f"({std_improvement:.1f}% improvement) in {phase2_time:.1f}s"
        )
        logger.info(
            f"Utilization range: {p1.min_utilization}-{p1.max_utilization} → "
            f"{p2.min_utilization}-{p2.max_utilization} "
            f"({range_improvement:.1f}% reduction)"
        )

    def _merge_phase2_profile(
        self, phase1_result: OptimizationResult, profile_result: ProfileResult
    ) -> dict[str, Any]:
        """Add the Phase 2 profile to the Phase 1 profile data and log the comparison."""
        profile_data = dict(phase1_result.profile_data or {})
        profile_data["phase2"] = self._profile_block(profile_result)
        phase1_profile = ProfileResult(
            phase="Phase 1",
            timings=profile_data.get("phase1", {}).get("timings", {}),
            counts=profile_data.get("phase1", {}).get("counts", {}),
            solver_stats=profile_data.get("phase1", {}).get("solver_stats", {}),
        )
        log_profile_comparison(phase1_profile, profile_result)
        return profile_data

    def _solve_phase2(
        self,
        phase1_result: OptimizationResult,
        profile: bool = False,
    ) -> OptimizationResult:
        """
        Phase 2: balance card utilization using the configured phase2_objective.

        The model is the base model plus:
        - Combo count held at the Phase 1 count (within combo_tolerance)
        - Minimum coverage ratio constraints
        - Exact combo linking: y[j] = 1 iff the selected cards complete combo j
        - Utilization variables: u[c] = completed combos card c participates in
        - The utilization floor for selected cards
        - The variables, constraints and objective function of the chosen objective
        - Warm-start hints from the Phase 1 cube, repaired first if it breaks the coverage
          constraints (_build_warm_start)

        Falls back to the Phase 1 result if Phase 2 finds no solution; the returned result
        then has phase2_fell_back set and carries the Phase 2 status, time and profile.
        """
        objective = self._get_phase2_objective()
        p1, phase1_solve_time = self._require_phase1_stats(phase1_result)
        target_combo_count = phase1_result.combo_count
        logger.info(
            f"Starting {objective.label}: balancing utilization "
            f"(target: {target_combo_count} combos)"
        )
        start_time = time.perf_counter()
        profile_result = ProfileResult(phase=objective.label) if profile else None
        util_cap = self._resolve_util_cap(p1) if objective.uses_util_cap else None
        if util_cap is not None:
            source = "--util-cap" if self.util_cap is not None else "2 x Phase 1 median"
            logger.info(f"Phase 2: utilization cap T = {util_cap} ({source})")

        warm_start = self._build_warm_start(phase1_result, profile_result)

        build_start = time.perf_counter()
        base = self._build_base_model()
        base.hint_utilization = warm_start.utilization
        self._add_combo_count_window(base, target_combo_count)
        self._add_phase2_coverage(base)
        self._add_exact_combo_linking(base)
        u = self._add_utilization_vars(base)
        self._add_utilization_floor(base, u)
        objective.add_to_model(self, base, u, phase1_result)
        self._add_warm_start(base, u, warm_start)

        if profile_result:
            profile_result.counts = base.counts
            profile_result.timings["model_build"] = time.perf_counter() - build_start

        # The time limit covers all of Phase 2, including a warm-start repair
        solver = self._make_solver(
            gap_limit=self.gap_limit,
            absolute_gap_limit=base.absolute_gap_limit,
            time_limit=max(1.0, self.time_limit - (time.perf_counter() - start_time)),
        )
        status_str = self._run_solver(solver, base, profile_result)
        phase2_time = time.perf_counter() - start_time

        # If Phase 2 fails, fall back to Phase 1
        if status_str not in ("OPTIMAL", "FEASIBLE"):
            logger.warning(f"Phase 2 failed ({status_str}), falling back to Phase 1 result")
            profile_data = phase1_result.profile_data
            if profile_result:
                profile_result.log_summary()
                profile_data = self._merge_phase2_profile(phase1_result, profile_result)
            return replace(
                phase1_result,
                solve_time_seconds=phase1_solve_time + phase2_time,
                phase2_solve_time=phase2_time,
                phase2_status=status_str,
                phase2_fell_back=True,
                phase2_objective=self.phase2_objective,
                phase2_util_cap=util_cap,
                profile_data=profile_data,
            )

        solution = self._extract_solution(solver, base, profile_result)
        phase2_stats = solution.utilization_stats
        self._log_phase2_improvement(p1, phase2_stats, status_str, phase2_time)

        profile_data = None
        if profile_result:
            profile_data = self._merge_phase2_profile(phase1_result, profile_result)

        return OptimizationResult(
            selected_cards=solution.selected_cards,
            completable_combo_ids=solution.completable_combo_ids,
            combo_count=len(solution.completable_combo_ids),
            objective_value=phase1_result.objective_value,  # Preserve Phase 1 objective
            solve_time_seconds=phase1_solve_time + phase2_time,
            phase1_status=phase1_result.phase1_status,
            utilization_per_card=solution.utilization_per_card,
            phase1_utilization_stats=p1,
            phase2_utilization_stats=phase2_stats,
            phase1_solve_time=phase1_solve_time,
            phase2_solve_time=phase2_time,
            phase2_status=status_str,
            phase2_objective=self.phase2_objective,
            phase2_util_cap=util_cap,
            is_multi_objective=True,
            requirement_type_stats=solution.requirement_type_stats,
            requirement_coverage_stats=solution.requirement_coverage_stats,
            cross_template_stats=solution.cross_template_stats,
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

        # Phase 2: Balance utilization with the configured objective
        return self._solve_phase2(phase1_result=phase1_result, profile=profile)

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
