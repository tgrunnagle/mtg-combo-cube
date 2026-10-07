"""Tests for the Phase 2 color balance constraint."""

from typing import Any

import pytest

from mtg_combo_cube.ilp.cube_evaluation import compute_color_stats
from mtg_combo_cube.ilp.ilp_models import ComboData, OptimizationResult
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.scryfall.card_attribute_fetcher import CardAttributes
from tests.unit.test_ilp_card_mix import NO_CARD_MIX
from tests.unit.test_ilp_optimizer import build_candidate_cards

# Three white cards that form three combos, and one two-card combo in each other color.
# Combos per card favor white, so an unconstrained cube takes all of white first.
COMBOS = [
    ComboData("w12", frozenset(["W1", "W2"]), [], 10),
    ComboData("w23", frozenset(["W2", "W3"]), [], 10),
    ComboData("w13", frozenset(["W1", "W3"]), [], 10),
    ComboData("u", frozenset(["U1", "U2"]), [], 10),
    ComboData("b", frozenset(["B1", "B2"]), [], 10),
    ComboData("r", frozenset(["R1", "R2"]), [], 10),
    ComboData("g", frozenset(["G1", "G2"]), [], 10),
]
CARD_ATTRIBUTES = {
    card: CardAttributes(card[0]) for combo in COMBOS for card in combo.required_cards
}


def make_optimizer(**kwargs: Any) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "cube_size": 9,
        "time_limit_seconds": 30,
        "combo_tolerance": 0.5,
        "gap_limit": 0,
        "num_workers": 1,
        "min_utilization_floor": 0,
        "phase2_objective": "minmax",
        "card_attributes": CARD_ATTRIBUTES,
        "card_mix": NO_CARD_MIX,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=COMBOS, candidate_cards=build_candidate_cards(COMBOS), **settings)


def color_counts(result: OptimizationResult) -> dict[str, int]:
    return compute_color_stats(result.get_selected_card_names(), CARD_ATTRIBUTES).cards_per_color


class TestColorBalance:
    def test_phase1_ignores_colors(self):
        result = make_optimizer().solve()

        # All of white and three other pairs: one color is left out entirely
        assert result.combo_count == 6
        counts = color_counts(result)
        assert counts["W"] == 3
        assert min(counts.values()) == 0

    def test_phase2_enforces_the_ratio(self):
        result = make_optimizer(max_color_ratio=2.0).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_max_color_ratio == 2.0
        counts = color_counts(result)
        assert min(counts.values()) >= 1
        assert max(counts.values()) <= 2 * min(counts.values())
        # Nine cards over five colors: white cannot keep its third card
        assert counts["W"] <= 2
        # The Phase 1 cube breaks the rule, so the warm start was repaired
        assert result.profile_data is not None
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]
        assert result.profile_data["phase2"]["counts"]["color_balance"] == 20

    def test_ratio_one_requires_equal_colors(self):
        result = make_optimizer(cube_size=10, max_color_ratio=1.0).solve_two_phase()

        assert result.is_multi_objective
        assert set(color_counts(result).values()) == {2}

    def test_ratio_zero_disables_the_constraint(self):
        result = make_optimizer(max_color_ratio=0, combo_tolerance=0).solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_max_color_ratio is None
        assert result.combo_count == 6
        assert color_counts(result)["W"] == 3

    def test_no_color_data_disables_the_constraint(self):
        result = make_optimizer(card_attributes=None, combo_tolerance=0).solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_max_color_ratio is None
        assert result.combo_count == 6

    def test_infeasible_balance_falls_back_to_phase1(self):
        # No green card exists, so no cube with a colored card can be balanced, and the
        # combo window rules out the empty-of-color cube
        combos = [combo for combo in COMBOS if combo.id != "g"]
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=build_candidate_cards(combos),
            cube_size=6,
            time_limit_seconds=30,
            num_workers=1,
            min_utilization_floor=0,
            card_attributes=CARD_ATTRIBUTES,
            card_mix=NO_CARD_MIX,
        )

        result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert result.phase2_max_color_ratio == 2.0

    @pytest.mark.parametrize("ratio", [0.5, 0.99])
    def test_ratio_between_zero_and_one_is_rejected(self, ratio: float):
        with pytest.raises(ValueError, match="max_color_ratio"):
            make_optimizer(max_color_ratio=ratio)


class TestColorViolations:
    def test_balanced_cube_has_no_violations(self):
        optimizer = make_optimizer()

        assert optimizer._color_violations(["W1", "W2", "U1", "B1", "R1", "G1"]) == 0

    def test_counts_each_broken_pair(self):
        optimizer = make_optimizer()

        # White 3 against four colors with 1 card each
        assert optimizer._color_violations(["W1", "W2", "W3", "U1", "B1", "R1", "G1"]) == 4

    def test_missing_color_breaks_every_pair_with_it(self):
        optimizer = make_optimizer()

        # Green has no cards: each of the four other colors exceeds 2 x 0
        assert optimizer._color_violations(["W1", "U1", "B1", "R1"]) == 4

    def test_multicolor_card_counts_for_each_color(self):
        optimizer = make_optimizer(
            card_attributes={
                **CARD_ATTRIBUTES,
                "W2": CardAttributes("WUBRG"),
                "W3": CardAttributes("WUBRG"),
            }
        )

        # W1 alone would leave four colors empty; the two five-color cards fill them
        assert optimizer._color_violations(["W1", "W2", "W3"]) == 0

    def test_fractional_ratio(self):
        optimizer = make_optimizer(max_color_ratio=1.5)

        # 2 against 1 exceeds 1.5
        assert optimizer._color_violations(["W1", "W2", "U1", "B1", "R1", "G1"]) == 4
        assert optimizer._color_violations(["W1", "W2", "U1", "U2", "B1", "B2", "R1", "R2"]) == 4
