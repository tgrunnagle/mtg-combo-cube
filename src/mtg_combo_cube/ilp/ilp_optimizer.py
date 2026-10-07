"""ILP-based cube optimizer using OR-Tools CP-SAT solver."""

import itertools
import logging
import math
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from fractions import Fraction
from functools import partial
from typing import Any

from ortools.sat.python import cp_model

from mtg_combo_cube.ilp.cube_evaluation import (
    COLOR_PAIRS,
    COLORS,
    MONO_COLORS,
    card_utilization,
    color_identities_known,
    completable_combo_ids,
    completed_group_sizes,
    compute_archetype_stats,
    compute_utilization_stats,
    fits_archetype,
    is_expensive,
    is_spell,
    known_color_identity,
    largest_combo_groups,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.ilp_models import (
    DEFAULT_CARD_MIX,
    ArchetypeStats,
    CandidateCard,
    CardMixRules,
    ComboData,
    ComboGroupStats,
    CrossTemplateStats,
    OptimizationResult,
    RequirementCoverageStats,
    RequirementPool,
    RequirementTypeStats,
    TemplateOverlapPairStats,
    UtilizationStats,
    hundredths,
)
from mtg_combo_cube.ilp.profiling import (
    ProfileResult,
    extract_solver_stats,
    log_profile_comparison,
)
from mtg_combo_cube.models import UNKNOWN_CARD, CardAttributes

logger = logging.getLogger(__name__)


@dataclass
class _BaseModel:
    """A CP-SAT model with the card and combo variables every phase shares."""

    model: cp_model.CpModel
    x: dict[str, cp_model.IntVar]  # x[card] = 1 if the card is in the cube
    y: dict[str, cp_model.IntVar]  # y[combo.id] = 1 if the combo is completable
    counts: dict[str, int]  # variable/constraint counts by kind, reported in profile data
    # g[group_key] = 1 iff a variant of the group is completable. Only for groups of two or
    # more variants, and only when variant_weight < 1 (otherwise the score needs no g).
    g: dict[str, cp_model.IntVar] = field(default_factory=dict)
    # Phase 2 only: z[cards] = 1 iff at least one card of that option pool is in the cube.
    # One variable per distinct pool of two or more cards, shared by every combo using it.
    option_satisfied: dict[frozenset[str], cp_model.IntVar] = field(default_factory=dict)
    # Variants whose y is exact in both directions (_add_exact_combo_linking)
    exact_combo_ids: set[str] = field(default_factory=set)
    # Archetype rules: h[name, group] = 1 only if a variant of the group that fits the named
    # archetype is completable, for groups where only some variants fit (see
    # _fitting_group_count). Holds the variable and the ids of those variants.
    fit_vars: dict[tuple[str, str], tuple[cp_model.IntVar, frozenset[str]]] = field(
        default_factory=dict
    )
    # Phase 2 only: set by an objective whose optimum can be near zero, where the relative
    # gap limit is not a usable stop rule. In objective units; None = relative gap only.
    absolute_gap_limit: float | None = None
    # Phase 2 only: utilization of the warm-start cube, for hinting objective variables
    hint_utilization: dict[str, int] = field(default_factory=dict)


class _StopAtZero(cp_model.CpSolverSolutionCallback):
    """Stops the search at the first solution in which the watched expression is 0."""

    def __init__(self, expression: cp_model.LinearExprT):
        super().__init__()
        self._expression = expression

    def on_solution_callback(self) -> None:
        if self.value(self._expression) == 0:
            self.stop_search()


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
    distinct_combo_count: int
    weighted_combo_count: float
    largest_combo_groups: list[ComboGroupStats]
    utilization_per_card: dict[str, int]
    utilization_stats: UtilizationStats
    archetype_stats: ArchetypeStats | None  # None when the combos carry no color identities
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


@dataclass(frozen=True)
class _CubeRule:
    """
    A hard constraint on the Phase 2 cube, applied wherever Phase 2 feasibility is decided:
    the Phase 2 model, the warm-start repair models, and the check of whether the Phase 1
    cube already satisfies the rules.

    add(base) adds the constraints (and a base.counts entry when it added any);
    violations(cards) counts the constraints a cube breaks.
    """

    label: str  # used in log messages
    add: Callable[[_BaseModel], None]
    violations: Callable[[Collection[str]], int]


class ILPOptimizer:
    """
    ILP-based cube optimizer using OR-Tools CP-SAT solver.

    Maximizes the number of completable combos within a fixed cube size,
    using log-scaled popularity as a tiebreaker.

    Combo score: a completed variant is worth 1 for the first variant of its combo group
    (ComboData.group_key) and variant_weight for each further one, so with variant_weight 1
    the score is the variant count and with 0 the number of distinct combos. Phase 1
    maximizes the score and the Phase 2 combo window holds it. In the model the score is
    scaled by WEIGHT_SCALE to stay integer.
    """

    DEFAULT_TIME_LIMIT = 300  # 5 minutes
    TIEBREAK_EPSILON = 0.001  # Small weight for popularity tiebreaker
    VERSATILITY_EPSILON = 0.0001  # Small bonus for multi-template cards
    # Shares of the Phase 2 time limit for the two warm-start repair stages
    WARM_START_MAXIMIZE_FRACTION = 0.2
    WARM_START_FLOOR_FRACTION = 0.2
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
        card_attributes: Mapping[str, CardAttributes] | None = None,
        max_color_ratio: float = 2.0,
        variant_weight: float = 0.1,
        min_pair_combos: int = 250,
        min_mono_combos: int = 150,
        max_wide_combo_share: float = 0.25,
        card_mix: CardMixRules = DEFAULT_CARD_MIX,
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
        if 0 < max_color_ratio < 1:
            raise ValueError(f"max_color_ratio must be 0 or >= 1, got {max_color_ratio}")
        # Card name -> Scryfall attributes (color identity, types, mana value), behind the
        # Phase 2 color balance and card mix rules. Without the data (None, a failed lookup)
        # those rules are skipped; a card missing from it counts as colorless, typeless and
        # mana value 0 (UNKNOWN_CARD).
        self.card_attributes = card_attributes
        self.max_color_ratio = max_color_ratio
        self.card_mix = card_mix
        if not 0 <= variant_weight <= 1:
            raise ValueError(f"variant_weight must be between 0 and 1, got {variant_weight}")
        self.variant_weight = variant_weight
        # Phase 2 archetype support: distinct combos each two-color pair and each mono color
        # must be able to assemble, and the largest share of the completed combos that may
        # need three or more colors. 0 disables each. The rules need the combos' color
        # identities; without them (an instance built without identities) they are skipped.
        if min_pair_combos < 0 or min_mono_combos < 0:
            raise ValueError(
                f"min_pair_combos and min_mono_combos must be >= 0, got "
                f"{min_pair_combos} and {min_mono_combos}"
            )
        if not 0 <= max_wide_combo_share <= 1:
            raise ValueError(
                f"max_wide_combo_share must be between 0 and 1, got {max_wide_combo_share}"
            )
        self.min_pair_combos = min_pair_combos
        self.min_mono_combos = min_mono_combos
        self.max_wide_combo_share = max_wide_combo_share
        self.has_color_identities = color_identities_known(combos)

        # Build card universe from candidate cards
        self.all_cards: list[str] = sorted(candidate_cards.keys())
        self.card_to_idx: dict[str, int] = {card: i for i, card in enumerate(self.all_cards)}
        # The candidate cards each card mix rule counts. Lands are left out of the colorless
        # cap: it is about artifacts, and lands have an empty color identity.
        self.multicolor_cards = self._cards_where(lambda a: a.is_multicolor)
        self.colorless_cards = self._cards_where(lambda a: a.is_colorless and "Land" not in a.types)
        self.expensive_cards = self._cards_where(
            lambda a: is_expensive(a, card_mix.expensive_mana_value)
        )
        self.creature_cards = self._cards_where(lambda a: "Creature" in a.types)
        self.spell_cards = self._cards_where(is_spell)
        self.card_to_combos: dict[str, list[ComboData]] = self._build_participation_graph()

        # Combo groups (distinct combos) and the integer score weights, in WEIGHT_SCALE units:
        # a group's first completed variant scores group_scale + variant_scale = WEIGHT_SCALE,
        # each further one variant_scale
        groups: dict[str, list[ComboData]] = defaultdict(list)
        for combo in combos:
            groups[combo.group_key].append(combo)
        self.combo_groups: dict[str, list[ComboData]] = dict(groups)
        self.combo_group: dict[str, str] = {combo.id: combo.group_key for combo in combos}
        self.variant_scale: int = round(variant_weight * self.WEIGHT_SCALE)
        self.group_scale: int = self.WEIGHT_SCALE - self.variant_scale
        # Groups that get a g variable: two or more variants, when variant_weight < 1 (the
        # score needs g) or an archetype rule counts groups
        self.grouped_keys: frozenset[str] = (
            frozenset(key for key, members in self.combo_groups.items() if len(members) > 1)
            if self.group_scale or self._archetype_rules_enabled()
            else frozenset()
        )

        logger.info(
            f"ILP Optimizer initialized: {len(self.combos)} variants in "
            f"{len(self.combo_groups)} combos, {len(self.all_cards)} cards, cube size "
            f"{cube_size}, variant weight {variant_weight:g}"
        )

    def _grouped_keys(self) -> list[str]:
        """Keys of the groups that get a g variable, in instance order."""
        return [key for key in self.combo_groups if key in self.grouped_keys]

    def _group_sizes_of(self, combo_ids: Collection[str]) -> Counter[str]:
        """Completed variants per group for the given completed variant ids."""
        return Counter(self.combo_group[combo_id] for combo_id in combo_ids)

    def _combo_score(self, combo_ids: Collection[str]) -> int:
        """
        The combo score of a set of completed variants, in WEIGHT_SCALE units.

        This is the integer the model uses; it equals WEIGHT_SCALE times
        cube_evaluation.weighted_combo_count when variant_weight x WEIGHT_SCALE is a whole
        number, and differs by rounding otherwise.
        """
        score = 0
        for key, completed in self._group_sizes_of(combo_ids).items():
            if key in self.grouped_keys:
                score += self.group_scale + self.variant_scale * completed
            else:
                score += self.WEIGHT_SCALE * completed
        return score

    def _weighted_combo_count(self, combo_ids: Collection[str]) -> float:
        """The reported weighted count: groups + variant_weight x further variants."""
        return weighted_combo_count(self._group_sizes_of(combo_ids), self.variant_weight)

    def _combo_score_expr(self, base: _BaseModel) -> cp_model.LinearExpr:
        """The combo score as a linear expression over y and g, in WEIGHT_SCALE units."""
        terms: list[cp_model.LinearExpr] = []
        for key, combos in self.combo_groups.items():
            if key in base.g:
                terms.append(self.group_scale * base.g[key])
                terms.extend(self.variant_scale * base.y[combo.id] for combo in combos)
            else:
                terms.extend(self.WEIGHT_SCALE * base.y[combo.id] for combo in combos)
        return cp_model.LinearExpr.sum(terms)

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
        base = _BaseModel(model=model, x=x, y=y, counts=counts)
        self._add_group_vars(base)
        return base

    def _add_group_vars(self, base: _BaseModel) -> None:
        """
        Add g[k] = 1 iff a variant of group k is completable, for every group of two or
        more variants, when variant_weight < 1.

        g[k] <= sum(y[j] for j in k) and g[k] >= y[j] for each j, so g is exact in both
        directions and the combo score can be bounded from either side.
        """
        linking = 0
        for key in self._grouped_keys():
            g = base.model.new_bool_var(f"group_{len(base.g)}")
            variants = [base.y[combo.id] for combo in self.combo_groups[key]]
            base.model.add(g <= sum(variants))
            for y in variants:
                base.model.add(g >= y)
            linking += 1 + len(variants)
            base.g[key] = g
        if base.g:
            base.counts["variables_group"] = len(base.g)
            base.counts["group_linking"] = linking

    def _tiebreak_weight(self, popularity: int) -> int:
        """The popularity part of _compute_weight: the integer weight above WEIGHT_SCALE."""
        return self._compute_weight(popularity) - self.WEIGHT_SCALE

    def _add_combo_count_objective(self, base: _BaseModel) -> None:
        """
        Phase 1 objective: maximize the combo score, with popularity as a tiebreak.

        The tiebreak is added once per counted item: on y[j] for a variant of a group
        without a g variable (so with variant_weight 1 this is the popularity-weighted
        variant count), and on g[k], with the group's highest popularity, for a grouped
        combo. A further variant of a grouped combo earns exactly variant_scale, so the
        tiebreak can never outweigh the variant credit, and with variant_weight 0 the
        objective counts distinct combos only. With variant_weight 1 the g variables, if an
        archetype rule created them, play no part in the objective.
        """
        tiebreak_terms: list[cp_model.LinearExpr] = []
        for key, members in self.combo_groups.items():
            if key in base.g and self.group_scale:
                popularity = max(combo.popularity for combo in members)
                tiebreak_terms.append(self._tiebreak_weight(popularity) * base.g[key])
            else:
                tiebreak_terms.extend(
                    self._tiebreak_weight(combo.popularity) * base.y[combo.id] for combo in members
                )
        base.model.maximize(self._combo_score_expr(base) + cp_model.LinearExpr.sum(tiebreak_terms))

    def _combo_count_window(self, target_combo_count: float) -> tuple[int, int]:
        """Smallest and largest combo count Phase 2 accepts, in combo units (tolerance > 0)."""
        return (
            math.floor(target_combo_count * (1 - self.combo_tolerance)),
            math.ceil(target_combo_count * (1 + self.combo_tolerance)),
        )

    def _combo_score_window(self, reference_score: int) -> tuple[int, int]:
        """Smallest and largest combo score Phase 2 accepts for a reference score."""
        if self.combo_tolerance > 0:
            low, high = self._combo_count_window(reference_score / self.WEIGHT_SCALE)
            return low * self.WEIGHT_SCALE, high * self.WEIGHT_SCALE
        return reference_score, reference_score

    def _add_combo_count_window(self, base: _BaseModel, reference_score: int) -> None:
        """Hold the combo score at the reference score, within combo_tolerance."""
        score = self._combo_score_expr(base)
        min_score, max_score = self._combo_score_window(reference_score)

        if self.combo_tolerance > 0:
            logger.info(
                f"Phase 2 combo tolerance: {self.combo_tolerance:.1%} "
                f"(range: {min_score // self.WEIGHT_SCALE}-{max_score // self.WEIGHT_SCALE} "
                f"weighted combos)"
            )
            base.model.add(score >= min_score)
            base.model.add(score <= max_score)
            base.counts["combo_count"] = 2
        else:
            # No tolerance - use exact equality
            base.model.add(score == reference_score)
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

    def _add_exact_combo_linking(
        self, base: _BaseModel, combos: list[ComboData] | None = None
    ) -> None:
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
        docs/plans/ilp-improvement-plan.md, Stage 3.

        `combos` restricts the linking to those variants (every variant by default). Variants
        linked by an earlier call are skipped, so the wide combo cap can link the variants it
        needs exact in the one-sided repair models before Phase 2 links the rest.
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

        for combo in self.combos if combos is None else combos:
            if combo.id in base.exact_combo_ids:
                continue
            literals = [x[card] for card in sorted(combo.required_cards)]
            for cards in dict.fromkeys(opt.cards for opt in combo.requirement_options):
                literals.append(option_literal(cards))
            model.add(base.y[combo.id] >= sum(literals) - (len(literals) - 1))
            base.exact_combo_ids.add(combo.id)

        base.counts["variables_option"] = len(base.option_satisfied)
        base.counts["option_linking"] = base.counts.get("option_linking", 0) + option_linking_count
        base.counts["combo_exact_linking"] = len(base.exact_combo_ids)

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

    def _attributes_of(self, card: str) -> CardAttributes:
        """The card's Scryfall attributes; UNKNOWN_CARD when there are none."""
        return (self.card_attributes or {}).get(card, UNKNOWN_CARD)

    def _cards_where(self, predicate: Callable[[CardAttributes], bool]) -> frozenset[str]:
        """The candidate cards whose attributes satisfy the predicate."""
        return frozenset(card for card in self.all_cards if predicate(self._attributes_of(card)))

    def _balance_ratio(self, ratio: float) -> Fraction | None:
        """A balance ratio as an integer fraction, or None when there is no constraint."""
        if self.card_attributes is None or ratio <= 0:
            return None
        return hundredths(ratio)

    def _color_balance_ratio(self) -> Fraction | None:
        """The color balance ratio as an integer fraction, or None when there is no constraint."""
        return self._balance_ratio(self.max_color_ratio)

    def _cards_of_color(self, color: str, cards: Collection[str]) -> list[str]:
        """The given cards whose color identity includes the color."""
        return [card for card in cards if color in self._attributes_of(card).color_identity]

    def _mono_cards_of_color(self, color: str, cards: Collection[str]) -> list[str]:
        """The given cards whose color identity is exactly the color."""
        return [card for card in cards if self._attributes_of(card).color_identity == color]

    def _add_balance(
        self,
        base: _BaseModel,
        name: str,
        ratio: Fraction,
        cards_of_color: Callable[[str, Collection[str]], list[str]],
    ) -> None:
        """
        Add the balance constraints: no color has more than `ratio` times the cards of
        another color, where cards_of_color(color, cards) says which cards count for a
        color. Written as `den * count[a] <= num * count[b]` for every ordered pair.
        """
        counts = {
            color: sum(base.x[card] for card in cards_of_color(color, self.all_cards))
            for color in COLORS
        }
        constraints = 0
        for larger, smaller in itertools.permutations(COLORS, 2):
            base.model.add(ratio.denominator * counts[larger] <= ratio.numerator * counts[smaller])
            constraints += 1
        logger.info(
            f"Phase 2: Added {constraints} {name.replace('_', ' ')} constraints "
            f"(max ratio {float(ratio):g})"
        )
        base.counts[name] = constraints

    @staticmethod
    def _balance_violations(counts: Mapping[str, int], ratio: Fraction) -> int:
        """Number of balance constraints (_add_balance) the per-color counts break."""
        return sum(
            1
            for larger, smaller in itertools.permutations(COLORS, 2)
            if ratio.denominator * counts[larger] > ratio.numerator * counts[smaller]
        )

    def _add_color_balance(self, base: _BaseModel) -> None:
        """
        Add the color balance constraints: no color has more than max_color_ratio times the
        cards of another color.

        A card counts once for each color of its identity. Colorless cards, and cards
        without color data, are not constrained.
        """
        ratio = self._color_balance_ratio()
        if ratio is not None:
            self._add_balance(base, "color_balance", ratio, self._cards_of_color)

    def _color_violations(self, cards: Collection[str]) -> int:
        """Number of color balance constraints (_add_color_balance) a cube breaks."""
        ratio = self._color_balance_ratio()
        if ratio is None:
            return 0
        counts = {color: len(self._cards_of_color(color, cards)) for color in COLORS}
        return self._balance_violations(counts, ratio)

    # --- Card mix: shares of the cube by color count, mana value and type ---

    def _cap_count(self, max_share: float) -> int | None:
        """
        The most cards a share cap allows, or None when there is no cap: a share that
        rounds (to hundredths) to 0 or to 1, or no card data to apply it to.
        """
        share = hundredths(max_share)
        if self.card_attributes is None or not 0 < share < 1:
            return None
        return math.floor(share * self.cube_size)

    def _floor_count(self, min_share: float) -> int | None:
        """The fewest cards a share floor requires, or None when there is no floor."""
        share = hundredths(min_share)
        if self.card_attributes is None or share <= 0:
            return None
        return math.ceil(share * self.cube_size)

    def _add_share_cap(
        self, base: _BaseModel, label: str, cards: frozenset[str], max_share: float
    ) -> None:
        """At most floor(max_share x cube size) of the given candidate cards in the cube."""
        limit = self._cap_count(max_share)
        if limit is None or not cards:
            return
        base.model.add(sum(base.x[card] for card in sorted(cards)) <= limit)
        base.counts[label.replace(" ", "_")] = 1
        logger.info(
            f"Phase 2: Added {label} constraint (at most {limit} of {self.cube_size} cards, "
            f"share {max_share:g}; {len(cards)} candidates)"
        )

    def _share_cap_violations(
        self, cube: Collection[str], cards: frozenset[str], max_share: float
    ) -> int:
        """Whether a cube breaks a share cap (_add_share_cap): 1 or 0."""
        limit = self._cap_count(max_share)
        if limit is None:
            return 0
        return int(sum(1 for card in cube if card in cards) > limit)

    def _add_share_floor(
        self, base: _BaseModel, label: str, cards: frozenset[str], min_share: float
    ) -> None:
        """At least ceil(min_share x cube size) of the given candidate cards in the cube."""
        required = self._floor_count(min_share)
        if required is None:
            return
        if not cards:
            base.model.add_bool_or([])  # no card can count: the floor is unsatisfiable
        else:
            base.model.add(sum(base.x[card] for card in sorted(cards)) >= required)
        base.counts[label.replace(" ", "_")] = 1
        logger.info(
            f"Phase 2: Added {label} constraint (at least {required} of {self.cube_size} "
            f"cards, share {min_share:g}; {len(cards)} candidates)"
        )

    def _share_floor_violations(
        self, cube: Collection[str], cards: frozenset[str], min_share: float
    ) -> int:
        """Whether a cube breaks a share floor (_add_share_floor): 1 or 0."""
        required = self._floor_count(min_share)
        if required is None:
            return 0
        return int(sum(1 for card in cube if card in cards) < required)

    def _check_card_mix_pool(self) -> None:
        """
        Warn about card mix rules the whole pool cannot meet, before solving: a floor with
        fewer candidate cards than it requires, and a cap that leaves too few other cards to
        fill the cube.
        """
        for label, cards, share in self._share_caps():
            limit = self._cap_count(share)
            if limit is not None and len(self.all_cards) - len(cards) < self.cube_size - limit:
                logger.warning(
                    f"Phase 2: the pool has only {len(self.all_cards) - len(cards)} cards "
                    f"outside the {label}, fewer than the {self.cube_size - limit} the cube "
                    f"needs beside the {limit} it allows; Phase 2 cannot meet it"
                )
        required = self._floor_count(self.card_mix.min_spell_share)
        if required is not None and len(self.spell_cards) < required:
            logger.warning(
                f"Phase 2: the pool has only {len(self.spell_cards)} instants and sorceries, "
                f"below the spell floor of {required}; Phase 2 cannot meet it"
            )

    def _mono_color_ratio(self) -> Fraction | None:
        """The mono-colored balance ratio, or None when there is no constraint."""
        return self._balance_ratio(self.card_mix.mono_color_ratio)

    def _add_mono_color_balance(self, base: _BaseModel) -> None:
        """
        Add the mono-colored balance: no color has more than mono_color_ratio times the
        mono-colored cards of another color (the color balance form on the cards whose
        identity is exactly one color).
        """
        ratio = self._mono_color_ratio()
        if ratio is not None:
            self._add_balance(base, "mono_color_balance", ratio, self._mono_cards_of_color)

    def _mono_color_violations(self, cards: Collection[str]) -> int:
        """Number of mono-colored balance constraints a cube breaks."""
        ratio = self._mono_color_ratio()
        if ratio is None:
            return 0
        counts = {color: len(self._mono_cards_of_color(color, cards)) for color in COLORS}
        return self._balance_violations(counts, ratio)

    def _share_caps(self) -> list[tuple[str, frozenset[str], float]]:
        """The share caps: label, the candidate cards that count, the share."""
        rules = self.card_mix
        return [
            ("multicolor cap", self.multicolor_cards, rules.max_multicolor_share),
            ("colorless cap", self.colorless_cards, rules.max_colorless_share),
            ("expensive cap", self.expensive_cards, rules.max_expensive_share),
            ("creature cap", self.creature_cards, rules.max_creature_share),
        ]

    def _card_mix_rules(self) -> list[_CubeRule]:
        """The card mix rules (card_mix) as cube rules; a disabled rule adds nothing."""
        rules = self.card_mix
        return [
            *(
                _CubeRule(
                    label,
                    partial(self._add_share_cap, label=label, cards=cards, max_share=share),
                    partial(self._share_cap_violations, cards=cards, max_share=share),
                )
                for label, cards, share in self._share_caps()
            ),
            _CubeRule(
                "spell floor",
                partial(
                    self._add_share_floor,
                    label="spell floor",
                    cards=self.spell_cards,
                    min_share=rules.min_spell_share,
                ),
                partial(
                    self._share_floor_violations,
                    cards=self.spell_cards,
                    min_share=rules.min_spell_share,
                ),
            ),
            _CubeRule(
                "mono color balance", self._add_mono_color_balance, self._mono_color_violations
            ),
        ]

    def _card_mix_info(self) -> dict[str, Any]:
        """
        The card mix rules Phase 2 applies, for the result (None without card data), with
        a warning when the data is missing or incomplete.
        """
        enabled = self.card_mix.enabled()
        if self.card_attributes is None:
            if enabled:
                logger.warning(
                    "Phase 2: no card data from Scryfall; the card mix rules are not enforced"
                )
            return {"phase2_card_mix": None}
        unknown = sum(1 for card in self.all_cards if card not in self.card_attributes)
        if enabled and unknown:
            logger.warning(
                f"Phase 2: {unknown} of {len(self.all_cards)} candidate cards have no Scryfall "
                f"data; they count as colorless, typeless and mana value 0"
            )
        return {"phase2_card_mix": self.card_mix}

    def _coverage_violations(self, cards: Collection[str]) -> int:
        """Number of Phase 2 coverage constraints (_add_coverage_constraints) a cube breaks."""
        if self.min_coverage_ratio <= 0:
            return 0
        cards = set(cards)
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

    # --- Archetype support: combos per color pair and mono color, cap on wide combos ---

    def _archetype_rules_enabled(self) -> bool:
        """Whether any archetype rule is configured and the combos carry color identities."""
        return bool(self._archetype_minimums()) or self._wide_share_fraction() is not None

    def _archetype_minimums(self) -> dict[str, int]:
        """The minimum number of combos per archetype, for the archetypes with a minimum."""
        if not self.has_color_identities:
            return {}
        minimums: dict[str, int] = {}
        if self.min_pair_combos > 0:
            minimums.update(dict.fromkeys(COLOR_PAIRS, self.min_pair_combos))
        if self.min_mono_combos > 0:
            minimums.update(dict.fromkeys(MONO_COLORS, self.min_mono_combos))
        return minimums

    def _wide_share_fraction(self) -> Fraction | None:
        """
        The wide combo cap as an integer fraction, or None when there is no cap: a share
        that rounds (to hundredths) to 0, or to 1 (every combo may be wide), disables it.
        """
        if not self.has_color_identities:
            return None
        share = hundredths(self.max_wide_combo_share)
        return share if 0 < share < 1 else None

    @staticmethod
    def _is_narrow(combo: ComboData) -> bool:
        """A variant of at most two colors, which a two-color drafter can assemble."""
        return len(known_color_identity(combo)) < 3

    def _wide_variants(self) -> list[ComboData]:
        """The variants that need three or more colors."""
        return [combo for combo in self.combos if not self._is_narrow(combo)]

    def _group_indicator(self, base: _BaseModel, key: str) -> cp_model.IntVar:
        """
        The variable that is 1 iff a variant of the group is completable: g, or the y of a
        single variant. Groups of several variants have a g whenever an archetype rule is
        enabled (see grouped_keys).
        """
        if key in base.g:
            return base.g[key]
        return base.y[self.combo_groups[key][0].id]

    def _fitting_group_count(
        self, base: _BaseModel, name: str, fits: Callable[[ComboData], bool]
    ) -> cp_model.LinearExpr:
        """
        The number of completed groups with a completed variant that fits, as an
        expression the solver can only under-count, so it is exact where a constraint
        needs the count to be large (a minimum, or the narrow side of the wide cap).

        A group whose variants all fit contributes its group indicator and one where none
        fits nothing. Where only some variants fit (the same combo with a piece of another
        color swapped in), a bool h <= sum(y of the fitting variants) is added, shared by
        every rule using the same name.
        """
        terms: list[cp_model.LinearExpr] = []
        for key, members in self.combo_groups.items():
            fitting = [combo for combo in members if fits(combo)]
            if not fitting:
                continue
            if len(fitting) == len(members):
                terms.append(self._group_indicator(base, key))
                continue
            if (name, key) not in base.fit_vars:
                h = base.model.new_bool_var(f"fit_{name}_{len(base.fit_vars)}")
                base.model.add(h <= sum(base.y[combo.id] for combo in fitting))
                base.fit_vars[(name, key)] = (h, frozenset(combo.id for combo in fitting))
            terms.append(base.fit_vars[(name, key)][0])
        return cp_model.LinearExpr.sum(terms)

    def _add_archetype_minimums(self, base: _BaseModel) -> None:
        """
        Every two-color pair must be able to assemble min_pair_combos completed combos and
        every mono color min_mono_combos, counting the combos whose color identity fits
        within the archetype's colors (so mono and colorless combos count for every pair
        they fit in). Counted in distinct combos (groups), not variants.
        """
        minimums = self._archetype_minimums()
        if not minimums:
            return
        for archetype, minimum in minimums.items():
            count = self._fitting_group_count(
                base,
                archetype,
                lambda combo, archetype=archetype: fits_archetype(
                    known_color_identity(combo), archetype
                ),
            )
            base.model.add(count >= minimum)
        logger.info(
            f"Phase 2: Added {len(minimums)} archetype minimum constraints "
            f"(min_pair_combos={self.min_pair_combos}, min_mono_combos={self.min_mono_combos}; "
            f"{len(base.fit_vars)} variables for groups of mixed color identity)"
        )
        base.counts["archetype_minimum"] = len(minimums)

    def _add_wide_combo_cap(self, base: _BaseModel) -> None:
        """
        At most max_wide_combo_share of the completed combos may need three or more
        colors. Written as (den - num) * total <= den * narrow over the completed groups,
        where narrow counts the groups with a completed variant of at most two colors.

        In the Phase 1 style repair models y is one-sided, so the solver could meet the cap
        by leaving y at 0 for a wide combo the cube completes. Every wide variant is
        therefore linked exactly (_add_exact_combo_linking); with g >= y that makes total
        exact for every group completed through a wide variant, including a group of mixed
        identity whose narrow variant the cube does not complete. Under-counting a group
        completed through a narrow variant only tightens the cap.
        """
        share = self._wide_share_fraction()
        if share is None:
            return
        wide = self._wide_variants()
        self._add_exact_combo_linking(base, wide)
        total = cp_model.LinearExpr.sum(
            [self._group_indicator(base, key) for key in self.combo_groups]
        )
        narrow = self._fitting_group_count(base, "narrow", self._is_narrow)
        base.model.add((share.denominator - share.numerator) * total <= share.denominator * narrow)
        logger.info(
            f"Phase 2: Added the wide combo cap (at most {self.max_wide_combo_share:.0%} of the "
            f"completed combos may need three or more colors; {len(wide)} wide variants "
            f"linked exactly)"
        )
        base.counts["wide_combo_cap"] = 1

    def _archetype_shortfalls(self, cards: Collection[str]) -> dict[str, tuple[int, int]]:
        """
        The archetype minimums (_add_archetype_minimums) a cube breaks: archetype ->
        (combos it can assemble, minimum).
        """
        minimums = self._archetype_minimums()
        if not minimums:
            return {}
        stats = compute_archetype_stats(cards, self.combos)
        assert stats is not None  # the minimums are empty without identities
        return {
            archetype: (stats.combos_per_archetype[archetype], minimum)
            for archetype, minimum in minimums.items()
            if stats.combos_per_archetype[archetype] < minimum
        }

    def _archetype_minimum_violations(self, cards: Collection[str]) -> int:
        """Number of archetype minimum constraints a cube breaks."""
        return len(self._archetype_shortfalls(cards))

    def _wide_cap_violations(self, cards: Collection[str]) -> int:
        """1 if the cube's completed combos break the wide combo cap (_add_wide_combo_cap)."""
        share = self._wide_share_fraction()
        if share is None:
            return 0
        stats = compute_archetype_stats(cards, self.combos)
        assert stats is not None  # there is no cap without identities
        total = sum(stats.combos_by_color_count.values())
        return int(share.denominator * stats.wide_combo_count > share.numerator * total)

    def _check_archetype_pool(self) -> None:
        """Warn about archetype minimums the whole pool cannot meet, before solving."""
        for archetype, minimum in self._archetype_minimums().items():
            available = sum(
                1
                for members in self.combo_groups.values()
                if any(fits_archetype(known_color_identity(combo), archetype) for combo in members)
            )
            if available < minimum:
                logger.warning(
                    f"Phase 2: the pool has only {available} combos for {archetype}, below the "
                    f"minimum of {minimum}; Phase 2 cannot meet it"
                )

    def _describe_archetype_shortfalls(self, cards: Collection[str]) -> str:
        """'UB 10 < 20, R 3 < 5' for the archetype minimums a cube breaks, or ''."""
        return ", ".join(
            f"{archetype} {have} < {minimum}"
            for archetype, (have, minimum) in self._archetype_shortfalls(cards).items()
        )

    # --- The Phase 2 cube rules together ---

    def _cube_rules(self) -> list[_CubeRule]:
        """
        The hard constraints on the Phase 2 cube, beyond the combo window, the floor and
        the objective. Every rule is added to the Phase 2 model and the warm-start repair
        models, and checked on the Phase 1 cube to decide whether a repair is needed.
        """
        return [
            _CubeRule("coverage", self._add_phase2_coverage, self._coverage_violations),
            _CubeRule("color balance", self._add_color_balance, self._color_violations),
            _CubeRule(
                "archetype minimum",
                self._add_archetype_minimums,
                self._archetype_minimum_violations,
            ),
            _CubeRule("wide combo cap", self._add_wide_combo_cap, self._wide_cap_violations),
            *self._card_mix_rules(),
        ]

    def _add_cube_rules(self, base: _BaseModel) -> None:
        """Add every Phase 2 cube rule to the model."""
        for rule in self._cube_rules():
            rule.add(base)

    def _cube_rule_violations(self, cards: Collection[str]) -> dict[str, int]:
        """Constraints of each cube rule that a cube breaks, by rule label."""
        return {rule.label: rule.violations(cards) for rule in self._cube_rules()}

    def _describe_broken_rules(self, cards: Collection[str]) -> str:
        """'11 coverage, 1 wide combo cap' for the cube rules a cube breaks, or '' if none."""
        violations = self._cube_rule_violations(cards)
        return ", ".join(f"{count} {label}" for label, count in violations.items() if count)

    def _log_fallback_cause(self, reference: _WarmStart) -> None:
        """
        After a Phase 2 failure, say whether the cube rules are the likely cause: they are
        when no cube satisfying them was found, so the reference is still the Phase 1 cube
        and breaks some of them (named, with the archetypes below their minimum); when the
        reference satisfies every rule, the cause lies elsewhere.
        """
        broken = self._describe_broken_rules(reference.cards)
        if broken:
            shortfalls = self._describe_archetype_shortfalls(reference.cards)
            detail = f" (archetypes below their minimum: {shortfalls})" if shortfalls else ""
            logger.warning(
                f"Phase 2: no cube satisfying the cube rules was found; the reference cube "
                f"(the Phase 1 cube) still breaks {broken} constraints{detail}"
            )
        else:
            logger.warning(
                "Phase 2: the reference cube satisfies every cube rule, so the failure lies in "
                "the combo window, the utilization floor or the time limit"
            )

    def _repair_model(self, hint: _WarmStart) -> _BaseModel:
        """The Phase 1 model with the Phase 2 cube rules, hinted with a cube."""
        base = self._build_base_model()
        self._add_cube_rules(base)
        self._add_combo_count_objective(base)
        for card in self.all_cards:
            base.model.add_hint(base.x[card], 1 if card in hint.cards else 0)
        for combo in self.combos:
            base.model.add_hint(base.y[combo.id], 1 if combo.id in hint.combo_ids else 0)
        self._hint_group_vars(base, hint.combo_ids)
        return base

    def _hint_group_vars(self, base: _BaseModel, combo_ids: set[str]) -> int:
        """
        Hint every g variable with the groups the combos belong to, and every archetype
        fit variable with whether one of its variants is among the combos; returns the
        hint count.
        """
        completed_groups = self._group_sizes_of(combo_ids).keys()
        for key, g in base.g.items():
            base.model.add_hint(g, 1 if key in completed_groups else 0)
        for h, fitting_ids in base.fit_vars.values():
            base.model.add_hint(h, 0 if fitting_ids.isdisjoint(combo_ids) else 1)
        return len(base.g) + len(base.fit_vars)

    def _solve_repair(
        self,
        base: _BaseModel,
        time_fraction: float,
        callback: cp_model.CpSolverSolutionCallback | None = None,
    ) -> tuple[_WarmStart | None, str]:
        """Solve a repair model. Returns the cube found (None if none) and the solver status."""
        solver = self._make_solver(time_limit=self.time_limit * time_fraction)
        # CpSolverStatus is int at runtime, type stubs are incomplete
        status_str = self._status_to_string(solver.solve(base.model, callback))  # type: ignore[arg-type]
        if status_str not in ("OPTIMAL", "FEASIBLE"):
            return None, status_str
        cards = {card for card in self.all_cards if solver.value(base.x[card]) == 1}
        return self._warm_start_for(cards), status_str

    def _satisfies_window_and_floor(self, cube: _WarmStart, min_score: int) -> bool:
        return self._combo_score(cube.combo_ids) >= min_score and all(
            value >= self.min_utilization_floor for value in cube.utilization.values()
        )

    def _best_constrained_cube(self, phase1_start: _WarmStart) -> tuple[_WarmStart | None, str]:
        """
        Find the cube with the highest combo score that satisfies the Phase 2 cube rules.

        Solved in the small Phase 1 model (one-sided y), hinted with the Phase 1 cube, for at
        most WARM_START_MAXIMIZE_FRACTION of the time limit, so the result is the best cube
        found and not a proven maximum. Returns the cube (None if none was found) and the
        solver status.
        """
        return self._solve_repair(
            self._repair_model(phase1_start), self.WARM_START_MAXIMIZE_FRACTION
        )

    def _repair_floor(self, hint: _WarmStart, min_score: int) -> tuple[_WarmStart | None, str]:
        """
        Find a cube that satisfies every Phase 2 constraint, starting from a hint cube.

        Solved in the small Phase 1 model (one-sided y) with the Phase 2 cube rules, the
        lower edge of the combo window (min_score, in WEIGHT_SCALE units),
        and a soft floor on y:
        floor * x[c] <= sum(y over the combos of c) + shortfall[c]. The total shortfall is
        minimized and the search stops at the first cube with none. With one-sided y the sum
        never exceeds the true utilization, so zero shortfall means the true floor holds.

        As a hard constraint the floor makes even a first solution hard to find; as a penalty
        a cube that satisfies everything else is a valid start and the solver only has to
        work the shortfall down.

        Returns the cube (None if none satisfies every constraint) and the solver status.
        """
        base = self._repair_model(hint)
        base.model.add(self._combo_score_expr(base) >= min_score)
        shortfalls = []
        for card in self.all_cards:
            shortfall = base.model.new_int_var(0, self.min_utilization_floor, f"short_{card}")
            base.model.add(
                self.min_utilization_floor * base.x[card]
                <= sum(base.y[combo.id] for combo in self.card_to_combos[card]) + shortfall
            )
            hinted = self.min_utilization_floor - hint.utilization.get(card, 0)
            base.model.add_hint(shortfall, max(0, hinted) if card in hint.cards else 0)
            shortfalls.append(shortfall)
        base.model.minimize(sum(shortfalls))

        repaired, status_str = self._solve_repair(
            base, self.WARM_START_FLOOR_FRACTION, _StopAtZero(sum(shortfalls))
        )
        if repaired is None or not self._satisfies_window_and_floor(repaired, min_score):
            return None, status_str
        broken = self._describe_broken_rules(repaired.cards)
        if broken:
            logger.warning(f"Phase 2: the repaired warm start breaks {broken} constraints")
            return None, status_str
        return repaired, status_str

    def _describe_combos(self, combo_ids: Collection[str]) -> str:
        """'N variants (G combos)' for log messages, with the weighted count when it differs."""
        groups = len(self._group_sizes_of(combo_ids))
        text = f"{len(combo_ids)} variants ({groups} combos"
        if self.group_scale:
            text += f", weighted {self._weighted_combo_count(combo_ids):.1f}"
        return text + ")"

    def _build_warm_start(
        self,
        phase1_result: OptimizationResult,
        profile_result: ProfileResult | None,
    ) -> tuple[_WarmStart, _WarmStart]:
        """
        Choose the cube Phase 2 is hinted with and the reference cube its window is measured
        from. Returns (warm start, reference).

        The reference is the cube with the highest combo score found under the Phase 2 cube
        rules (coverage, color balance, archetype support, card mix). Phase 1 ignores them, so
        measuring the combo tolerance from the Phase 1 score can leave no feasible cube at
        all.

        - The Phase 1 cube satisfies every cube rule: it is the reference.
        - Otherwise the best constrained cube is searched for (_best_constrained_cube) and
          becomes the reference. If none is found, the Phase 1 count is kept.

        The hint is the reference cube when it also satisfies the combo window and the
        utilization floor. Otherwise CP-SAT would first have to repair the hint inside the
        much larger Phase 2 model, so a feasible cube is searched for in the small Phase 1
        model instead (_repair_floor). If that finds none, the reference cube is used.
        """
        phase1_start = self._warm_start_for({card.name for card in phase1_result.selected_cards})
        broken = self._describe_broken_rules(phase1_start.cards)
        repair_start = time.perf_counter()

        reference = phase1_start
        if broken:
            problem = f"Phase 1 cube breaks {broken} constraints"
            shortfalls = self._describe_archetype_shortfalls(phase1_start.cards)
            if shortfalls:
                problem += f" (archetypes below their minimum: {shortfalls})"
            best, status_str = self._best_constrained_cube(phase1_start)
            # The rules are checked on the true completions of the cube found: the repair
            # model is one-sided in y, so a rule not written to be exact there could be met
            # by the solver's y rather than by the cards
            still_broken = self._describe_broken_rules(best.cards) if best is not None else ""
            if still_broken:
                logger.warning(
                    f"Phase 2: the best cube found ({status_str}) still breaks {still_broken} "
                    f"constraints; it is not used as the reference"
                )
                best = None
            if best is None:
                logger.warning(
                    f"Phase 2: {problem}; no cube satisfying them was found ({status_str}), "
                    f"measuring the combo window from the Phase 1 count"
                )
            else:
                reference = best
                logger.info(
                    f"Phase 2: {problem}; best cube satisfying them has "
                    f"{self._describe_combos(best.combo_ids)}, "
                    f"{len(best.cards - phase1_start.cards)} cards swapped; "
                    f"the combo window is measured from it"
                )

        min_score, _ = self._combo_score_window(self._combo_score(reference.combo_ids))
        warm_start = reference
        if not self._satisfies_window_and_floor(reference, min_score):
            below_floor = sum(
                1 for value in reference.utilization.values() if value < self.min_utilization_floor
            )
            repaired, status_str = self._repair_floor(reference, min_score)
            if repaired is None:
                logger.warning(
                    f"Phase 2: warm start has {below_floor} cards below the utilization floor; "
                    f"no repaired warm start found ({status_str})"
                )
            else:
                warm_start = repaired
                logger.info(
                    f"Phase 2: warm start had {below_floor} cards below the utilization floor; "
                    f"repaired warm start has {self._describe_combos(repaired.combo_ids)}"
                )

        if warm_start is not phase1_start:
            repair_time = time.perf_counter() - repair_start
            logger.info(f"Phase 2: warm start preparation took {repair_time:.1f}s")
            if profile_result:
                profile_result.timings["warm_start_repair"] = repair_time
        return warm_start, reference

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

        hints_added += self._hint_group_vars(base, warm_start.combo_ids)

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
        group_sizes = completed_group_sizes(completed_set, self.combos)
        combo_groups = largest_combo_groups(
            selected_name_set, self.combos, completed_ids=completed_set
        )
        archetype_stats = (
            compute_archetype_stats(selected_name_set, self.combos, completed_set)
            if self.has_color_identities
            else None
        )
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
            distinct_combo_count=len(group_sizes),
            weighted_combo_count=weighted_combo_count(group_sizes, self.variant_weight),
            largest_combo_groups=combo_groups,
            utilization_per_card=utilization,
            utilization_stats=utilization_stats,
            archetype_stats=archetype_stats,
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
            f"{self._describe_combos(solution.completable_combo_ids)} in {solve_time:.1f}s"
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
            distinct_combo_count=solution.distinct_combo_count,
            weighted_combo_count=solution.weighted_combo_count,
            phase1_distinct_combo_count=solution.distinct_combo_count,
            phase1_weighted_combo_count=solution.weighted_combo_count,
            largest_combo_groups=solution.largest_combo_groups,
            variant_weight=self.variant_weight,
            utilization_per_card=solution.utilization_per_card,
            phase1_utilization_stats=utilization_stats,
            phase1_archetype_stats=solution.archetype_stats,
            phase1_solve_time=solve_time,
            phase1_combo_count=len(solution.completable_combo_ids),
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

    def _archetype_info(self) -> dict[str, Any]:
        """The archetype settings Phase 2 applies, for the result (None where disabled)."""
        if not self.has_color_identities:
            if (
                self.min_pair_combos
                or self.min_mono_combos
                or 0 < hundredths(self.max_wide_combo_share) < 1
            ):
                logger.warning(
                    "Phase 2: the combos have no color identities; archetype support is not "
                    "enforced"
                )
            return {}
        return {
            "phase2_min_pair_combos": self.min_pair_combos or None,
            "phase2_min_mono_combos": self.min_mono_combos or None,
            "phase2_max_wide_combo_share": (
                self.max_wide_combo_share if self._wide_share_fraction() is not None else None
            ),
        }

    def _solve_phase2(
        self,
        phase1_result: OptimizationResult,
        profile: bool = False,
    ) -> OptimizationResult:
        """
        Phase 2: balance card utilization using the configured phase2_objective.

        The model is the base model plus:
        - Combo score held at the reference score (within combo_tolerance): the best cube
          found under the Phase 2 cube rules (_build_warm_start)
        - The Phase 2 cube rules (_cube_rules): coverage, color balance when card data and a
          ratio are configured, the archetype minimums and wide combo cap when set, and the
          card mix rules (card_mix) when set and card data is available
        - Exact combo linking: y[j] = 1 iff the selected cards complete combo j
        - Utilization variables: u[c] = completed combos card c participates in
        - The utilization floor for selected cards
        - The variables, constraints and objective function of the chosen objective
        - Warm-start hints from the Phase 1 cube, repaired first if it breaks a cube rule
          (_build_warm_start)

        Falls back to the Phase 1 result if Phase 2 finds no solution; the returned result
        then has phase2_fell_back set and carries the Phase 2 status, time and profile.
        """
        objective = self._get_phase2_objective()
        p1, phase1_solve_time = self._require_phase1_stats(phase1_result)
        logger.info(
            f"Starting {objective.label}: balancing utilization "
            f"(Phase 1: {self._describe_combos(phase1_result.completable_combo_ids)})"
        )
        start_time = time.perf_counter()
        profile_result = ProfileResult(phase=objective.label) if profile else None
        util_cap = self._resolve_util_cap(p1) if objective.uses_util_cap else None
        color_ratio = self.max_color_ratio if self._color_balance_ratio() is not None else None
        if self.card_attributes is None and self.max_color_ratio > 0:
            logger.warning("Phase 2: no card data from Scryfall; color balance is not enforced")
        if util_cap is not None:
            source = "--util-cap" if self.util_cap is not None else "2 x Phase 1 median"
            logger.info(f"Phase 2: utilization cap T = {util_cap} ({source})")
        archetype_info = self._archetype_info()
        self._check_archetype_pool()
        card_mix_info = self._card_mix_info()
        self._check_card_mix_pool()

        warm_start, reference = self._build_warm_start(phase1_result, profile_result)
        reference_score = self._combo_score(reference.combo_ids)
        reference_info: dict[str, Any] = {
            "phase2_reference_combo_count": len(reference.combo_ids),
            "phase2_reference_distinct_combo_count": len(self._group_sizes_of(reference.combo_ids)),
            "phase2_reference_weighted_combo_count": self._weighted_combo_count(
                reference.combo_ids
            ),
            "phase2_combo_tolerance": self.combo_tolerance,
        }

        build_start = time.perf_counter()
        base = self._build_base_model()
        base.hint_utilization = warm_start.utilization
        self._add_combo_count_window(base, reference_score)
        self._add_cube_rules(base)
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
            self._log_fallback_cause(reference)
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
                phase2_max_color_ratio=color_ratio,
                **archetype_info,
                **card_mix_info,
                **reference_info,
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
            distinct_combo_count=solution.distinct_combo_count,
            weighted_combo_count=solution.weighted_combo_count,
            phase1_distinct_combo_count=phase1_result.distinct_combo_count,
            phase1_weighted_combo_count=phase1_result.weighted_combo_count,
            largest_combo_groups=solution.largest_combo_groups,
            variant_weight=self.variant_weight,
            utilization_per_card=solution.utilization_per_card,
            phase1_utilization_stats=p1,
            phase2_utilization_stats=phase2_stats,
            phase1_archetype_stats=phase1_result.phase1_archetype_stats,
            phase2_archetype_stats=solution.archetype_stats,
            phase1_solve_time=phase1_solve_time,
            phase2_solve_time=phase2_time,
            phase2_status=status_str,
            phase2_objective=self.phase2_objective,
            phase2_util_cap=util_cap,
            phase2_max_color_ratio=color_ratio,
            **archetype_info,
            **card_mix_info,
            **reference_info,
            is_multi_objective=True,
            requirement_type_stats=solution.requirement_type_stats,
            requirement_coverage_stats=solution.requirement_coverage_stats,
            cross_template_stats=solution.cross_template_stats,
            phase1_selected_cards=phase1_result.selected_cards,
            phase1_combo_count=phase1_result.combo_count,
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
