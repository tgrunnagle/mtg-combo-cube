"""Tests for the popularity weight: popular combos count for more in the Phase 1 objective
and the Phase 2 combo window."""

import math
from typing import Any

import pytest

from mtg_combo_cube.ilp.ilp_models import ComboData
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from tests.unit.test_ilp_combo_grouping import fixed_cube_status
from tests.unit.test_ilp_optimizer import build_candidate_cards

W = ILPOptimizer.WEIGHT_SCALE

# Two popular two-card combos and a triangle of three obscure ones on three cards. Four
# cards hold the two popular combos (2 combos) or the triangle plus a spare card (3 combos).
POPULAR = 1000
COMBOS = [
    ComboData("a", frozenset(["A1", "A2"]), [], POPULAR),
    ComboData("b", frozenset(["B1", "B2"]), [], POPULAR),
    ComboData("xy", frozenset(["X", "Y"]), [], 0),
    ComboData("yz", frozenset(["Y", "Z"]), [], 0),
    ComboData("xz", frozenset(["X", "Z"]), [], 0),
]
POPULAR_CUBE = {"A1", "A2", "B1", "B2"}
TRIANGLE = {"X", "Y", "Z"}


def make_optimizer(combos: list[ComboData] = COMBOS, **kwargs: Any) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "cube_size": 4,
        "time_limit_seconds": 30,
        "combo_tolerance": 0,
        "min_coverage_ratio": 0,
        "gap_limit": 0,
        "num_workers": 1,
        "min_utilization_floor": 0,
        "max_color_ratio": 0,
        "phase2_objective": "minmax",
        "min_pair_combos": 0,
        "min_mono_combos": 0,
        "max_wide_combo_share": 0,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=build_candidate_cards(combos), **settings)


class TestPhase1:
    def test_weight_zero_counts_combos_and_popularity_is_a_tiebreak(self):
        result = make_optimizer().solve()

        assert TRIANGLE <= set(result.get_selected_card_names())
        assert result.combo_count == 3
        assert result.popularity_weight == 0
        assert result.combo_score == 3.0
        assert result.phase1_combo_score == 3.0

    def test_weight_one_flips_the_choice_to_the_popular_combos(self):
        # The most popular combo is worth 2, so two of them (4) beat three obscure ones (3)
        result = make_optimizer(popularity_weight=1).solve()

        assert set(result.get_selected_card_names()) == POPULAR_CUBE
        assert result.combo_count == 2
        assert result.combo_score == 4.0
        assert result.weighted_combo_count == 2.0  # the plain count is unchanged

    def test_half_weight_ties_and_the_tiebreak_decides(self):
        # Each popular combo is worth 1.5: 3 against 3, and the popularity tiebreak, which
        # stacks with the factor, decides for the popular cube
        result = make_optimizer(popularity_weight=0.5).solve()

        assert set(result.get_selected_card_names()) == POPULAR_CUBE
        assert result.combo_score == 3.0

    def test_below_half_weight_the_triangle_wins_outright(self):
        # Each popular combo is worth 1.4: 2.8 against 3
        result = make_optimizer(popularity_weight=0.4).solve()

        assert TRIANGLE <= set(result.get_selected_card_names())
        assert result.combo_score == 3.0

    def test_factor_is_log_scaled_relative_to_the_most_popular_combo(self):
        combos = [
            ComboData("top", frozenset(["T1", "T2"]), [], POPULAR),
            ComboData("mid", frozenset(["M1", "M2"]), [], 30),
            ComboData("none", frozenset(["N1", "N2"]), [], 0),
        ]
        optimizer = make_optimizer(combos, popularity_weight=1)

        assert optimizer.group_factor["top"] == 2.0
        assert optimizer.group_factor["none"] == 1.0
        assert optimizer.group_factor["mid"] == pytest.approx(
            1 + math.log1p(30) / math.log1p(POPULAR)
        )
        # Each group's weight is rounded to an integer in WEIGHT_SCALE units
        assert optimizer._combo_score({"top", "mid", "none"}) == pytest.approx(
            W * (2 + 1 + math.log1p(30) / math.log1p(POPULAR) + 1), abs=len(combos)
        )

    def test_all_zero_popularity_leaves_every_factor_at_one(self):
        combos = [ComboData("ab", frozenset(["A", "B"]), [], 0)]

        assert make_optimizer(combos, popularity_weight=1).group_factor == {"ab": 1.0}

    def test_grouped_combo_takes_its_most_popular_variant(self):
        combos = [
            ComboData("h1", frozenset(["H", "P1"]), [], 0, group_key="hub"),
            ComboData("h2", frozenset(["H", "P2"]), [], POPULAR, group_key="hub"),
            ComboData("ab", frozenset(["A", "B"]), [], 10),
        ]
        optimizer = make_optimizer(combos, popularity_weight=1, variant_weight=0.5)

        assert optimizer.group_factor["hub"] == 2.0
        # One completed variant: 2 x (0.5 + 0.5); two: 2 x (0.5 + 2 x 0.5)
        assert optimizer._combo_score({"h1"}) == 2 * W
        assert optimizer._combo_score({"h1", "h2"}) == 3 * W

    def test_popular_grouped_combo_is_scaled_in_the_objective_and_the_window(self):
        # A popular combo of two variants on a hub (three cards) against the obscure
        # triangle (three combos on three cards). Unweighted the hub scores 1.6 to the
        # triangle's 3; with the most popular combo worth 2, both its g and y weights double
        # and it scores 3.2
        combos = [
            ComboData("h1", frozenset(["H", "P1"]), [], POPULAR, group_key="hub"),
            ComboData("h2", frozenset(["H", "P2"]), [], POPULAR, group_key="hub"),
            *COMBOS[2:],
        ]
        result = make_optimizer(combos, variant_weight=0.6).solve()

        assert TRIANGLE <= set(result.get_selected_card_names())
        assert result.combo_score == 3.0

        result = make_optimizer(combos, variant_weight=0.6, popularity_weight=1).solve_two_phase()

        assert result.is_multi_objective
        assert {"H", "P1", "P2"} <= set(result.get_selected_card_names())
        assert result.combo_score == pytest.approx(3.2)
        assert result.phase2_reference_combo_score == pytest.approx(3.2)
        assert result.weighted_combo_count == pytest.approx(1.6)

    @pytest.mark.parametrize("weight", [-0.5, math.inf, math.nan])
    def test_invalid_weight_is_rejected(self, weight: float):
        with pytest.raises(ValueError, match="popularity_weight"):
            make_optimizer(popularity_weight=weight)


class TestPhase2Window:
    def test_window_is_in_score_units(self):
        optimizer = make_optimizer(popularity_weight=1)

        # The two popular combos score 4, not 2
        assert fixed_cube_status(optimizer, POPULAR_CUBE, 4 * W) == "OPTIMAL"
        assert fixed_cube_status(optimizer, POPULAR_CUBE, 2 * W) == "INFEASIBLE"
        # The triangle scores 3 with any weight
        assert fixed_cube_status(optimizer, TRIANGLE | {"A1"}, 3 * W) == "OPTIMAL"

    def test_window_holds_the_score_of_the_reference(self):
        result = make_optimizer(popularity_weight=1).solve_two_phase()

        assert result.is_multi_objective
        assert result.popularity_weight == 1
        assert result.phase2_reference_combo_count == 2
        assert result.phase2_reference_weighted_combo_count == 2.0
        assert result.phase2_reference_combo_score == 4.0
        assert result.combo_score == 4.0
        assert result.phase1_combo_score == 4.0
        assert set(result.get_selected_card_names()) == POPULAR_CUBE

    def test_window_edges_are_whole_score_units(self):
        optimizer = make_optimizer(popularity_weight=1, combo_tolerance=0.1)

        # floor(4 x 0.9) = 3 to ceil(4 x 1.1) = 5, in popularity-weighted combos
        assert optimizer._combo_score_window(4 * W) == (3 * W, 5 * W)

    def test_weight_zero_score_equals_the_weighted_count(self):
        result = make_optimizer().solve_two_phase()

        assert result.is_multi_objective
        assert result.combo_score == result.weighted_combo_count == 3.0
        assert result.phase2_reference_combo_score == 3.0

    def test_popularity_stats_per_phase(self):
        result = make_optimizer(popularity_weight=1).solve_two_phase()

        assert result.phase1_popularity_stats is not None
        assert result.phase2_popularity_stats is not None
        stats = result.phase2_popularity_stats
        assert stats.combo_count == 2
        assert stats.median_popularity == POPULAR
        assert stats.pool_median_popularity == 0  # three of five pool combos have none
        assert stats.below_pool_median_share == 0
