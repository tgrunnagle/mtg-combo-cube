"""Tests for combo grouping: distinct combos in the Phase 1 objective and the Phase 2 window."""

import itertools
import math
from collections.abc import Collection
from typing import Any

import pytest
from ortools.sat.python import cp_model

from mtg_combo_cube.ilp.cube_evaluation import (
    completable_combo_ids,
    completed_group_sizes,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.ilp_models import ComboData, RequirementOption
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer, _WarmStart
from tests.unit.test_ilp_optimizer import build_candidate_cards

# One combo ("big") with four interchangeable partners of a hub card H, and three combos of
# one variant each. At six cards, counting variants takes H with all four partners (4
# variants of 1 combo); counting distinct combos takes H with one partner plus two of the
# single-variant combos (3 variants of 3 combos).
BIG = [ComboData(f"h{i}", frozenset(["H", f"P{i}"]), [], 100, group_key="big") for i in range(1, 5)]
SINGLES = [
    ComboData("ab", frozenset(["A", "B"]), [], 10),
    ComboData("cd", frozenset(["C", "D"]), [], 10),
    ComboData("ef", frozenset(["E", "F"]), [], 10),
]
COMBOS = BIG + SINGLES
CUBE_SIZE = 6
W = ILPOptimizer.WEIGHT_SCALE


def make_optimizer(combos: list[ComboData] = COMBOS, **kwargs: Any) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "cube_size": CUBE_SIZE,
        "time_limit_seconds": 30,
        "combo_tolerance": 0,
        "min_coverage_ratio": 0,
        "gap_limit": 0,
        "num_workers": 1,
        "min_utilization_floor": 0,
        "phase2_objective": "minmax",
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=build_candidate_cards(combos), **settings)


def fixed_cube_status(optimizer: ILPOptimizer, cube: Collection[str], reference_score: int) -> str:
    """
    Solve the Phase 2 window as a feasibility problem with the cube fixed.

    The model is the base model with exact combo linking, x fixed to the cube and the combo
    window around reference_score (tolerance 0, so the score must equal it).
    """
    base = optimizer._build_base_model()
    optimizer._add_exact_combo_linking(base)
    for card in optimizer.all_cards:
        base.model.add(base.x[card] == (1 if card in cube else 0))
    optimizer._add_combo_count_window(base, reference_score)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    return optimizer._status_to_string(solver.solve(base.model))  # type: ignore[arg-type]


class TestComboScore:
    def test_variant_weight_one_counts_variants(self):
        optimizer = make_optimizer(variant_weight=1)

        assert optimizer._grouped_keys() == []
        assert optimizer._weighted_combo_count(["h1", "h2", "h3", "ab"]) == 4.0

    def test_variant_weight_zero_counts_groups(self):
        optimizer = make_optimizer(variant_weight=0)

        assert optimizer._grouped_keys() == ["big"]
        assert optimizer._weighted_combo_count(["h1", "h2", "h3", "ab"]) == 2.0
        assert optimizer._weighted_combo_count(["ab", "cd"]) == 2.0
        assert optimizer._weighted_combo_count([]) == 0.0

    def test_further_variants_are_worth_the_variant_weight(self):
        optimizer = make_optimizer(variant_weight=0.5)

        # The first variant of "big" is worth 1, the next two 0.5 each, plus one single
        assert optimizer._weighted_combo_count(["h1", "h2", "h3", "ab"]) == 3.0
        assert optimizer._combo_score(["h1"]) == optimizer.WEIGHT_SCALE

    @pytest.mark.parametrize("weight", [-0.1, 1.5])
    def test_weight_outside_zero_to_one_is_rejected(self, weight: float):
        with pytest.raises(ValueError, match="variant_weight"):
            make_optimizer(variant_weight=weight)


class TestPhase1:
    def test_default_weight_prefers_distinct_combos(self):
        result = make_optimizer().solve()

        assert result.variant_weight == 0.1
        assert result.combo_count == 3
        assert result.distinct_combo_count == 3

    def test_weight_one_counts_variants(self):
        result = make_optimizer(variant_weight=1).solve(profile=True)

        assert result.phase1_status == "OPTIMAL"
        assert result.combo_count == 4
        assert result.distinct_combo_count == 1
        assert result.phase1_distinct_combo_count == 1
        assert result.variant_weight == 1.0
        assert {"H", "P1", "P2", "P3", "P4"} <= set(result.get_selected_card_names())
        # Objective unchanged from the popularity-weighted variant count
        weight = int((1 + optimizer_epsilon() * math.log1p(100)) * ILPOptimizer.WEIGHT_SCALE)
        assert result.objective_value == pytest.approx(4 * weight / ILPOptimizer.WEIGHT_SCALE)
        # Without group variables the model is unchanged
        assert result.profile_data is not None
        counts = result.profile_data["phase1"]["counts"]
        assert "variables_group" not in counts
        assert "group_linking" not in counts

    @pytest.mark.parametrize("weight", [0, 0.1, 0.25])
    def test_lower_weights_prefer_distinct_combos(self, weight: float):
        result = make_optimizer(variant_weight=weight).solve(profile=True)

        assert result.phase1_status == "OPTIMAL"
        assert result.combo_count == 3
        assert result.distinct_combo_count == 3
        assert result.variant_weight == weight
        # Popularity tiebreak keeps the hub combo (with one partner) over the third single
        selected = set(result.get_selected_card_names())
        assert "H" in selected
        assert len(selected & {"P1", "P2", "P3", "P4"}) == 1
        assert result.largest_combo_groups is not None
        sizes = {g.group_key: g.variant_count for g in result.largest_combo_groups}
        assert sizes["big"] == 1 and len(sizes) == 3

        # One group variable for "big", linked to its four variants
        assert result.profile_data is not None
        counts = result.profile_data["phase1"]["counts"]
        assert counts["variables_group"] == 1
        assert counts["group_linking"] == 5

    def test_weight_one_half_ties_and_popularity_decides(self):
        # Three hub variants plus one single score 1 + 2 x 0.5 + 1 = 3, the same as three
        # distinct combos. The tiebreak counts once per combo (the hub's 100 and one 10
        # against the hub's 100 and two 10s), so the three distinct combos win
        result = make_optimizer(variant_weight=0.5).solve()

        assert result.combo_count == 3
        assert result.distinct_combo_count == 3

    def test_high_weight_still_prefers_variants(self):
        # At 0.9 three hub variants plus one single score 1 + 2 x 0.9 + 1 = 3.8 against 3
        # distinct combos, and beat the four hub variants (1 + 3 x 0.9 = 3.7) as well
        result = make_optimizer(variant_weight=0.9).solve()

        assert result.combo_count == 4
        assert result.distinct_combo_count == 2


class TestPopularityTiebreak:
    def test_redundant_popular_variants_do_not_outweigh_a_distinct_combo(self):
        # 100 variants of one hub combo, all completed by the same two cards, at the highest
        # popularity on Spellbook; three unpopular single-variant combos on a triangle. At
        # weight 0 the triangle (3 distinct combos) must beat the hub plus one single (2).
        hub = [
            ComboData(f"hub{i}", frozenset(["H", "P1"]), [], 356_633, group_key="hub")
            for i in range(100)
        ]
        triangle = [
            ComboData("ab", frozenset(["A", "B"]), [], 0),
            ComboData("bc", frozenset(["B", "C"]), [], 0),
            ComboData("ca", frozenset(["C", "A"]), [], 0),
        ]
        combos = hub + triangle
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=build_candidate_cards(combos),
            cube_size=4,
            time_limit_seconds=30,
            num_workers=1,
            variant_weight=0,
        )

        result = optimizer.solve()

        assert result.phase1_status == "OPTIMAL"
        assert result.distinct_combo_count == 3
        assert result.combo_count == 3
        assert {"A", "B", "C"} <= set(result.get_selected_card_names())

    def test_tiebreak_is_counted_once_per_grouped_combo(self):
        optimizer = make_optimizer(variant_weight=0)
        base = optimizer._build_base_model()
        optimizer._add_combo_count_objective(base)
        hub_tiebreak = optimizer._tiebreak_weight(100)
        single_tiebreak = optimizer._tiebreak_weight(10)

        objective = str(base.model.proto.objective)

        # One term for the hub group's g, one per single; nothing on the hub variants' y
        assert objective.count(str(hub_tiebreak)) == 1
        assert objective.count(str(single_tiebreak)) == 3


class TestPhase2Window:
    def test_window_holds_the_weighted_count(self):
        result = make_optimizer(variant_weight=0).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_status == "OPTIMAL"
        assert result.phase1_distinct_combo_count == 3
        assert result.distinct_combo_count == 3
        assert result.combo_count == 3
        assert result.phase2_reference_combo_count == 3
        assert result.phase2_reference_distinct_combo_count == 3
        assert result.phase2_reference_weighted_combo_count == 3.0
        assert result.profile_data is not None
        counts = result.profile_data["phase2"]["counts"]
        assert counts["variables_group"] == 1
        assert counts["combo_count"] == 1
        # The group variable is hinted with the warm start too
        assert counts["warm_start_hints"] == 11 + 7 + 1 + 11

    def test_window_edges_are_whole_combos(self):
        optimizer = make_optimizer(variant_weight=0, combo_tolerance=0.5)

        # floor(3 x 0.5) = 1 to ceil(3 x 1.5) = 5 distinct combos
        assert optimizer._combo_score_window(3 * W) == (1 * W, 5 * W)

        # A fractional reference (2.1 weighted combos at weight 0.1) rounds outwards
        optimizer = make_optimizer(variant_weight=0.1, combo_tolerance=0.1)
        assert optimizer._combo_score_window(21_000) == (1 * W, 3 * W)

        # Without tolerance the score must equal the reference, fraction included
        optimizer = make_optimizer(variant_weight=0.1, combo_tolerance=0)
        assert optimizer._combo_score_window(21_000) == (21_000, 21_000)

    def test_window_counts_weighted_combos_of_a_fixed_cube(self):
        # H with three partners and one single: 4 variants in 2 groups
        optimizer = make_optimizer(variant_weight=0)
        cube = {"H", "P1", "P2", "P3", "E", "F"}

        assert fixed_cube_status(optimizer, cube, 2 * W) == "OPTIMAL"
        # 4 would be the variant count: the window is not on variants
        assert fixed_cube_status(optimizer, cube, 4 * W) == "INFEASIBLE"
        # 1 would need g = 0 while a hub variant is complete: g >= y forbids it
        assert fixed_cube_status(optimizer, cube, 1 * W) == "INFEASIBLE"

        # At weight 0.5 the same cube scores 1 + 2 x 0.5 + 1 = 3
        optimizer = make_optimizer(variant_weight=0.5)
        assert fixed_cube_status(optimizer, cube, 3 * W) == "OPTIMAL"
        assert fixed_cube_status(optimizer, cube, 2 * W) == "INFEASIBLE"

    def test_window_counts_single_variant_combos_directly(self):
        optimizer = make_optimizer(variant_weight=0)
        cube = {"A", "B", "C", "D", "E", "F"}

        assert fixed_cube_status(optimizer, cube, 3 * W) == "OPTIMAL"
        assert fixed_cube_status(optimizer, cube, 2 * W) == "INFEASIBLE"

    def test_weight_one_reference_is_the_variant_count(self):
        result = make_optimizer(variant_weight=1).solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_reference_combo_count == 4
        assert result.phase2_reference_distinct_combo_count == 1
        assert result.phase2_reference_weighted_combo_count == 4.0
        assert result.combo_count == 4

    def test_window_and_floor_check_uses_the_score(self):
        optimizer = make_optimizer(variant_weight=0, min_utilization_floor=0)
        hub_cube = _WarmStart(
            cards={"H", "P1", "P2", "P3"}, combo_ids={"h1", "h2", "h3"}, utilization={}
        )

        # 3 variants, but one combo: below a window edge of 2 combos
        assert optimizer._satisfies_window_and_floor(hub_cube, 1 * W)
        assert not optimizer._satisfies_window_and_floor(hub_cube, 2 * W)

    def test_fallback_records_the_reference(self):
        # With utilization floor 2 no six-card cube with 3 distinct combos has every card in
        # two completed combos, so Phase 2 falls back to the Phase 1 cube
        result = make_optimizer(variant_weight=0, min_utilization_floor=2).solve_two_phase()

        assert result.phase2_fell_back
        assert result.distinct_combo_count == 3
        assert result.phase2_reference_distinct_combo_count == 3
        assert result.phase2_reference_weighted_combo_count == 3.0


def optimizer_epsilon() -> float:
    return ILPOptimizer.TIEBREAK_EPSILON


SAC = RequirementOption("Sac outlet", "scryfall:q=sac", frozenset(["S1", "S2", "S3", "S4"]))
# The grouping instance plus two combos that need a sacrifice outlet from a four-card pool
COVERAGE_COMBOS = COMBOS + [
    ComboData("sx", frozenset(["X"]), [SAC], 10),
    ComboData("sy", frozenset(["Y"]), [SAC], 10),
]


class TestGroupedWarmStart:
    """The reference cube and warm start repair with group variables in the model."""

    @staticmethod
    def coverage_optimizer(**kwargs: Any) -> ILPOptimizer:
        # Both sac combos need 2 of the 4 outlets in the cube (ratio 1.0, threshold 2)
        settings: dict[str, Any] = {
            "cube_size": 7,
            "variant_weight": 0.1,
            "combo_tolerance": 0.25,
            "min_coverage_ratio": 1.0,
            "min_combo_threshold": 2,
        }
        settings.update(kwargs)
        return make_optimizer(COVERAGE_COMBOS, **settings)

    def test_reference_is_the_best_cube_under_coverage(self):
        optimizer = self.coverage_optimizer()
        phase1 = optimizer.solve()
        # Phase 1 takes one outlet at most: the coverage rule wants two
        assert optimizer._coverage_violations(set(phase1.get_selected_card_names())) == 1

        warm_start, reference = optimizer._build_warm_start(phase1, None)

        assert optimizer._coverage_violations(reference.cards) == 0
        assert optimizer._coverage_violations(warm_start.cards) == 0
        # The best cube under coverage, by enumeration of every 7-card cube
        best = max(
            weighted_combo_count(
                completed_group_sizes(
                    completable_combo_ids(cube, COVERAGE_COMBOS), COVERAGE_COMBOS
                ),
                0.1,
            )
            for cube in itertools.combinations(sorted(build_candidate_cards(COVERAGE_COMBOS)), 7)
            if optimizer._coverage_violations(set(cube)) == 0
        )
        assert best == pytest.approx(3.1)
        assert optimizer._weighted_combo_count(reference.combo_ids) == pytest.approx(best)
        # Two outlets, the two sac combos and H with two partners: 4 variants in 3 combos,
        # so the reference's variant count is not its weighted count
        assert len(reference.combo_ids) == 4
        assert len(optimizer._group_sizes_of(reference.combo_ids)) == 3

    def test_phase2_window_is_measured_from_the_grouped_reference(self):
        result = self.coverage_optimizer().solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_reference_combo_count == 4
        assert result.phase2_reference_distinct_combo_count == 3
        assert result.phase2_reference_weighted_combo_count == pytest.approx(3.1)
        # Window: floor(3.1 x 0.75) = 2 to ceil(3.1 x 1.25) = 4 weighted combos
        assert result.weighted_combo_count is not None
        assert 2 <= result.weighted_combo_count <= 4
        assert result.profile_data is not None
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]

    def test_reference_inside_the_window_is_not_repaired(self):
        # Only the hub combo: the 3-card cube completes 3 variants of 1 combo, every card
        # in 2 of them. Measured in variants the window edge would be floor(3 x 0.9) = 2
        # combos, above the cube's score of 1, and a pointless repair would run
        combos = [
            ComboData("h1", frozenset(["H", "P1"]), [], 10, group_key="big"),
            ComboData("h2", frozenset(["H", "P2"]), [], 10, group_key="big"),
            ComboData("p12", frozenset(["P1", "P2"]), [], 10, group_key="big"),
        ]
        optimizer = make_optimizer(
            combos, cube_size=3, variant_weight=0, combo_tolerance=0.1, min_utilization_floor=2
        )
        phase1 = optimizer.solve()
        assert phase1.combo_count == 3
        assert phase1.distinct_combo_count == 1

        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.profile_data is not None
        assert "warm_start_repair" not in result.profile_data["phase2"]["timings"]
        assert result.phase2_reference_weighted_combo_count == 1.0

    def test_floor_repair_succeeds_with_group_variables(self):
        # Two hub combos whose three cards complete three variants of each other (every
        # card in 2), and three single-variant combos. At weight 0 and 6 cards, Phase 1
        # takes one card pair from each hub and one single: 3 combos, every card in 1
        # variant, so the floor of 2 is broken. The repair must find a cube that keeps the
        # score inside the window (1 combo at tolerance 0.5) with every card at the floor:
        # the two complete hubs, 6 variants in 2 combos
        combos = [
            ComboData("h1", frozenset(["H", "P1"]), [], 100, group_key="big1"),
            ComboData("h2", frozenset(["H", "P2"]), [], 100, group_key="big1"),
            ComboData("p12", frozenset(["P1", "P2"]), [], 100, group_key="big1"),
            ComboData("k1", frozenset(["K", "Q1"]), [], 100, group_key="big2"),
            ComboData("k2", frozenset(["K", "Q2"]), [], 100, group_key="big2"),
            ComboData("q12", frozenset(["Q1", "Q2"]), [], 100, group_key="big2"),
            *SINGLES,
        ]
        optimizer = make_optimizer(
            combos, variant_weight=0, combo_tolerance=0.5, min_utilization_floor=2
        )
        phase1 = optimizer.solve()
        assert phase1.distinct_combo_count == 3
        assert phase1.utilization_per_card is not None
        assert min(phase1.utilization_per_card.values()) < 2

        warm_start, reference = optimizer._build_warm_start(phase1, None)

        assert reference.combo_ids == set(phase1.completable_combo_ids)
        assert warm_start is not reference
        assert warm_start.cards == {"H", "P1", "P2", "K", "Q1", "Q2"}
        assert min(warm_start.utilization.values()) >= 2
        min_score, _ = optimizer._combo_score_window(optimizer._combo_score(reference.combo_ids))
        assert optimizer._combo_score(warm_start.combo_ids) >= min_score
        assert optimizer._weighted_combo_count(warm_start.combo_ids) == 2.0
