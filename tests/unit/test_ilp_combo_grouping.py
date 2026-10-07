"""Tests for combo grouping: distinct combos in the Phase 1 objective and the Phase 2 window."""

import math
from typing import Any

import pytest

from mtg_combo_cube.ilp.ilp_models import ComboData
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
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


def make_optimizer(**kwargs: Any) -> ILPOptimizer:
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
    return ILPOptimizer(combos=COMBOS, candidate_cards=build_candidate_cards(COMBOS), **settings)


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

    def test_tolerance_is_measured_in_weighted_combos(self):
        result = make_optimizer(variant_weight=0, combo_tolerance=0.5).solve_two_phase()

        assert result.is_multi_objective
        # Window: floor(3 x 0.5) = 1 to ceil(3 x 1.5) = 5 distinct combos
        assert result.distinct_combo_count is not None
        assert 1 <= result.distinct_combo_count <= 5
        assert result.phase2_reference_weighted_combo_count == 3.0

    def test_weight_one_reference_is_the_variant_count(self):
        result = make_optimizer(variant_weight=1).solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_reference_combo_count == 4
        assert result.phase2_reference_distinct_combo_count == 1
        assert result.phase2_reference_weighted_combo_count == 4.0
        assert result.combo_count == 4

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
