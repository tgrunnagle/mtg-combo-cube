"""Tests for the Phase 2 archetype support rules: combos per color pair and mono color, and
the cap on combos that need three or more colors."""

import logging
from typing import Any

import pytest

from mtg_combo_cube.ilp.cube_evaluation import (
    COLOR_PAIRS,
    MONO_COLORS,
    completable_combo_ids,
    fits_archetype,
)
from mtg_combo_cube.ilp.ilp_models import ComboData, OptimizationResult
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from tests.unit.test_ilp_optimizer import build_candidate_cards


def pair_combo(pair: str, popularity: int) -> ComboData:
    """A two-card gold combo of the pair's color identity."""
    return ComboData(
        pair.lower(), frozenset([f"{pair}1", f"{pair}2"]), [], popularity, color_identity=pair
    )


# A white triangle (three combos on three cards) and one gold combo per color pair. The
# pairs with white, UB and UR are the most popular, so a 15-card Phase 1 cube takes the
# triangle and those six pairs and leaves UG, BR, BG and RG without a combo.
PAIRS = [
    ComboData("w12", frozenset(["W1", "W2"]), [], 10, color_identity="W"),
    ComboData("w23", frozenset(["W2", "W3"]), [], 10, color_identity="W"),
    ComboData("w13", frozenset(["W1", "W3"]), [], 10, color_identity="W"),
    *(pair_combo(pair, 100 if "W" in pair or pair in ("UB", "UR") else 1) for pair in COLOR_PAIRS),
]

# One mono combo per color and a three-card gold triangle whose cards also form a
# three-color combo: A, B, C complete four combos, one of them wide (WUB).
MONO = [
    ComboData("abc", frozenset(["A", "B", "C"]), [], 50, color_identity="WUB"),
    ComboData("ab", frozenset(["A", "B"]), [], 50, color_identity="WU"),
    ComboData("bc", frozenset(["B", "C"]), [], 50, color_identity="UB"),
    ComboData("ac", frozenset(["A", "C"]), [], 50, color_identity="WB"),
    *(
        ComboData(
            color.lower(), frozenset([f"{color}1", f"{color}2"]), [], 10, color_identity=color
        )
        for color in MONO_COLORS
    ),
]


def make_optimizer(combos: list[ComboData], **kwargs: Any) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "time_limit_seconds": 30,
        "combo_tolerance": 0.5,
        "gap_limit": 0,
        "num_workers": 1,
        "min_utilization_floor": 0,
        "max_color_ratio": 0,
        "phase2_objective": "minmax",
        # The defaults are sized for a full build; each test enables the rule it exercises
        "min_pair_combos": 0,
        "min_mono_combos": 0,
        "max_wide_combo_share": 0,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=build_candidate_cards(combos), **settings)


def per_archetype(result: OptimizationResult, phase: str = "phase2") -> dict[str, int]:
    stats = getattr(result, f"{phase}_archetype_stats")
    assert stats is not None
    return stats.combos_per_archetype


class TestPairMinimum:
    def test_phase1_ignores_archetypes(self):
        result = make_optimizer(PAIRS, cube_size=15).solve()

        assert result.combo_count == 9
        counts = per_archetype(result, "phase1")
        # The three white combos count for every pair with white, plus the pair's own
        assert counts["WU"] == 4 and counts["W"] == 3
        assert counts["UG"] == counts["BR"] == counts["BG"] == counts["RG"] == 0
        assert result.phase1_archetype_stats is not None
        assert result.phase1_archetype_stats.combos_by_color_count == {
            0: 0,
            1: 3,
            2: 6,
            3: 0,
            4: 0,
            5: 0,
        }

    def test_minimum_forces_combos_into_the_ignored_pairs(self):
        # The floor rules out two white cards plus a card in no combo (14 + 1 cards)
        result = make_optimizer(
            PAIRS, cube_size=15, min_pair_combos=1, min_utilization_floor=1
        ).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_min_pair_combos == 1
        assert result.phase2_min_mono_combos is None
        assert result.phase2_max_wide_combo_share is None
        counts = per_archetype(result)
        assert all(counts[pair] >= 1 for pair in COLOR_PAIRS)
        # The only 15-card cube with a combo in every pair: the triangle (which serves the
        # four pairs with white) and the six gold combos without white
        assert set(result.get_selected_card_names()) == {"W1", "W2", "W3"} | {
            f"{pair}{n}" for pair in COLOR_PAIRS if "W" not in pair for n in (1, 2)
        }
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["archetype_minimum"] == 10
        # The Phase 1 cube breaks the rule, so the warm start was repaired
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]

    def test_zero_disables_the_minimum(self):
        result = make_optimizer(PAIRS, cube_size=15, min_pair_combos=0).solve_two_phase(
            profile=True
        )

        assert result.is_multi_objective
        assert result.phase2_min_pair_combos is None
        assert result.profile_data is not None
        assert "archetype_minimum" not in result.profile_data["phase2"]["counts"]

    def test_infeasible_minimum_falls_back_to_phase1(self, caplog: pytest.LogCaptureFixture):
        # No combo of the instance fits mono blue, black, red or green
        optimizer = make_optimizer(PAIRS, cube_size=15, min_mono_combos=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert result.phase2_min_mono_combos == 1
        assert "the pool has only 0 combos for U, below the minimum of 1" in caplog.text
        assert (
            "no cube meeting the archetype minimums was found; the reference cube is below "
            "them for U 0 < 1, B 0 < 1, R 0 < 1, G 0 < 1"
        ) in caplog.text

    def test_fallback_for_another_reason_says_the_minimums_were_met(
        self, caplog: pytest.LogCaptureFixture
    ):
        # The minimum is met by the triangle plus six gold pairs, but a floor of 2 is not:
        # gold cards take part in one combo each
        optimizer = make_optimizer(PAIRS, cube_size=15, min_pair_combos=1, min_utilization_floor=2)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert "the reference cube meets the archetype minimums" in caplog.text
        assert "no cube meeting the archetype minimums" not in caplog.text

    @pytest.mark.parametrize("name", ["min_pair_combos", "min_mono_combos"])
    def test_negative_minimum_is_rejected(self, name: str):
        with pytest.raises(ValueError, match=name):
            make_optimizer(PAIRS, cube_size=15, **{name: -1})


class TestMonoMinimum:
    def test_phase1_prefers_the_gold_triangle(self):
        result = make_optimizer(MONO, cube_size=10).solve()

        # A, B, C (four combos) and three mono pairs
        assert result.combo_count == 7
        assert min(per_archetype(result, "phase1")[color] for color in MONO_COLORS) == 0

    def test_minimum_needs_a_mono_combo_in_every_color(self):
        result = make_optimizer(MONO, cube_size=10, min_mono_combos=1).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_min_mono_combos == 1
        assert set(result.get_selected_card_names()) == {
            f"{color}{n}" for color in MONO_COLORS for n in (1, 2)
        }
        assert all(per_archetype(result)[color] == 1 for color in MONO_COLORS)
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["archetype_minimum"] == 5


class TestWideComboCap:
    def test_cap_excludes_the_wide_combo(self):
        # Tolerance 0: the window is exactly the reference, so a reference cube that broke
        # the cap (six combos) would leave Phase 2 nothing to find
        result = make_optimizer(
            MONO, cube_size=7, max_wide_combo_share=0.1, combo_tolerance=0
        ).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_max_wide_combo_share == 0.1
        assert result.phase2_archetype_stats is not None
        assert result.phase2_archetype_stats.wide_combo_count == 0
        # A, B and C can no longer all be in the cube
        assert not {"A", "B", "C"} <= set(result.get_selected_card_names())
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["wide_combo_cap"] == 1
        # The best cube under the cap has three combos, and Phase 2 keeps them
        assert result.combo_count == 3
        assert result.phase2_reference_combo_count == 3
        # The cap's own exact linking and Phase 2's cover every variant once
        assert result.profile_data["phase2"]["counts"]["combo_exact_linking"] == len(MONO)

    def test_repair_model_cannot_meet_the_cap_through_y(self):
        """In the one-sided repair model a wide combo the cube completes counts as wide."""
        optimizer = make_optimizer(MONO, cube_size=7, max_wide_combo_share=0.1)
        phase1 = optimizer._warm_start_for({"A", "B", "C", "G1", "G2", "R1", "R2"})
        assert optimizer._wide_cap_violations(phase1.cards) == 1

        best, status = optimizer._best_constrained_cube(phase1)

        assert best is not None, status
        assert optimizer._wide_cap_violations(best.cards) == 0
        assert not {"A", "B", "C"} <= best.cards
        assert len(completable_combo_ids(best.cards, MONO)) == 3

    def test_repair_model_counts_a_mixed_group_completed_only_through_its_wide_variant(self):
        """A group with a wide and a narrow variant is wide when only the wide one is
        complete; the one-sided repair model cannot hide that by zeroing its y."""
        combos = [
            ComboData("abc", frozenset(["A", "B", "C"]), [], 50, "mixed", color_identity="WUB"),
            ComboData("abd", frozenset(["A", "B", "D"]), [], 50, "mixed", color_identity="WU"),
            ComboData("ab", frozenset(["A", "B"]), [], 50, color_identity="WU"),
            ComboData("bc", frozenset(["B", "C"]), [], 50, color_identity="UB"),
            ComboData("ac", frozenset(["A", "C"]), [], 50, color_identity="WB"),
            *(
                ComboData(
                    color.lower(),
                    frozenset([f"{color}1", f"{color}2"]),
                    [],
                    10,
                    color_identity=color,
                )
                for color in MONO_COLORS
            ),
        ]
        optimizer = make_optimizer(combos, cube_size=7, max_wide_combo_share=0.05)
        assert [combo.id for combo in optimizer._wide_variants()] == ["abc"]
        # A, B, C and two mono pairs: six combos, the mixed one only through abc
        phase1 = optimizer._warm_start_for({"A", "B", "C", "W1", "W2", "U1", "U2"})
        assert optimizer._wide_cap_violations(phase1.cards) == 1

        best, status = optimizer._best_constrained_cube(phase1)

        assert best is not None, status
        assert optimizer._wide_cap_violations(best.cards) == 0
        # The best cube under the cap completes the narrow variant too: A, B, C, D (five
        # combos, none wide) and one mono pair
        assert {"A", "B", "C", "D"} <= best.cards
        assert len(completable_combo_ids(best.cards, combos)) == 6

    def test_share_of_one_disables_the_cap(self):
        optimizer = make_optimizer(MONO, cube_size=7, max_wide_combo_share=1, combo_tolerance=0)

        assert not optimizer._archetype_rules_enabled()
        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_max_wide_combo_share is None
        assert result.combo_count == 6
        assert result.profile_data is not None
        assert "wide_combo_cap" not in result.profile_data["phase2"]["counts"]

    def test_cap_at_the_current_share_keeps_the_wide_combo(self):
        # One wide combo in six is under 25%
        result = make_optimizer(
            MONO, cube_size=7, max_wide_combo_share=0.25, combo_tolerance=0
        ).solve_two_phase()

        assert result.is_multi_objective
        assert result.combo_count == 6
        assert result.phase2_archetype_stats is not None
        assert result.phase2_archetype_stats.wide_combo_count == 1

    def test_zero_disables_the_cap(self):
        result = make_optimizer(MONO, cube_size=7, combo_tolerance=0).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_max_wide_combo_share is None
        assert result.combo_count == 6
        assert result.profile_data is not None
        assert "wide_combo_cap" not in result.profile_data["phase2"]["counts"]

    @pytest.mark.parametrize("share", [-0.1, 1.5])
    def test_share_outside_zero_to_one_is_rejected(self, share: float):
        with pytest.raises(ValueError, match="max_wide_combo_share"):
            make_optimizer(MONO, cube_size=7, max_wide_combo_share=share)


class TestWithoutColorIdentities:
    def test_rules_are_skipped(self, caplog: pytest.LogCaptureFixture):
        # ComboData built without identities, as the older tests do
        combos = [
            ComboData("ab", frozenset(["A", "B"]), [], 10),
            ComboData("bc", frozenset(["B", "C"]), [], 10),
            ComboData("ca", frozenset(["C", "A"]), [], 10),
        ]
        optimizer = make_optimizer(
            combos, cube_size=3, min_pair_combos=5, min_mono_combos=5, max_wide_combo_share=0.1
        )

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.combo_count == 3
        assert result.phase2_min_pair_combos is None
        assert result.phase2_max_wide_combo_share is None
        assert "no color identities; archetype support is not enforced" in caplog.text
        assert result.profile_data is not None
        assert "archetype_minimum" not in result.profile_data["phase2"]["counts"]
        # Unknown identities are not reported as colorless: there are no archetype counts
        assert result.phase1_archetype_stats is None
        assert result.phase2_archetype_stats is None

    def test_colorless_pool_is_not_unknown(self):
        combos = [
            ComboData("ab", frozenset(["A", "B"]), [], 10, color_identity=""),
            ComboData("bc", frozenset(["B", "C"]), [], 10, color_identity=""),
        ]
        optimizer = make_optimizer(combos, cube_size=3, min_pair_combos=2)

        assert optimizer.has_color_identities
        result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_min_pair_combos == 2
        assert per_archetype(result)["RG"] == 2
        assert per_archetype(result)["C"] == 2


class TestViolations:
    def test_minimum_violations_count_the_archetypes_below_it(self):
        optimizer = make_optimizer(PAIRS, cube_size=15, min_pair_combos=1, min_mono_combos=1)

        # The triangle serves the four pairs with white and mono white
        assert optimizer._archetype_minimum_violations(["W1", "W2", "W3"]) == 6 + 4
        assert optimizer._archetype_shortfalls(["W1", "W2", "W3", "UB1", "UB2"]) == dict.fromkeys(
            ["UR", "UG", "BR", "BG", "RG", "U", "B", "R", "G"], (0, 1)
        )
        every_card = [card for combo in PAIRS for card in combo.required_cards]
        assert optimizer._archetype_minimum_violations(every_card) == 4  # U, B, R, G

    def test_wide_cap_violations(self):
        cube = ["A", "B", "C", "W1", "W2"]  # five combos, one of them wide: 20%

        assert (
            make_optimizer(MONO, cube_size=5, max_wide_combo_share=0.1)._wide_cap_violations(cube)
            == 1
        )
        assert (
            make_optimizer(MONO, cube_size=5, max_wide_combo_share=0.2)._wide_cap_violations(cube)
            == 0
        )
        assert make_optimizer(MONO, cube_size=5)._wide_cap_violations(cube) == 0

    def test_every_cube_rule_is_checked(self):
        optimizer = make_optimizer(PAIRS, cube_size=15, min_pair_combos=1, max_wide_combo_share=0.5)

        assert optimizer._cube_rule_violations(["W1", "W2", "W3"]) == {
            "coverage": 0,
            "color balance": 0,
            "archetype minimum": 6,
            "wide combo cap": 0,
        }


class TestMixedIdentityGroups:
    """A combo whose variants differ in color identity counts for an archetype only through
    a variant that fits it."""

    COMBOS = [
        ComboData("g1", frozenset(["X", "Y"]), [], 10, group_key="g", color_identity="W"),
        ComboData("g2", frozenset(["X", "Z"]), [], 10, group_key="g", color_identity="WU"),
    ]

    def solve_fitting(self, archetype: str, forbid: str) -> set[str]:
        optimizer = make_optimizer(self.COMBOS, cube_size=2, min_pair_combos=1)
        base = optimizer._build_base_model()
        count = optimizer._fitting_group_count(
            base, archetype, lambda combo: fits_archetype(combo.color_identity, archetype)
        )
        base.model.add(count >= 1)
        base.model.add(base.x[forbid] == 0)
        optimizer._add_combo_count_objective(base)
        solver = optimizer._make_solver()
        status = optimizer._status_to_string(solver.solve(base.model))  # type: ignore[arg-type]
        assert status == "OPTIMAL", status
        return {card for card in optimizer.all_cards if solver.value(base.x[card]) == 1}

    def test_group_counts_for_mono_white_only_through_its_white_variant(self):
        assert self.solve_fitting("W", forbid="Z") == {"X", "Y"}
        assert self.solve_fitting("WU", forbid="Y") == {"X", "Z"}

    def test_group_cannot_count_for_mono_white_through_the_gold_variant(self):
        optimizer = make_optimizer(self.COMBOS, cube_size=2, min_pair_combos=1)
        base = optimizer._build_base_model()
        count = optimizer._fitting_group_count(
            base, "W", lambda combo: fits_archetype(combo.color_identity, "W")
        )
        base.model.add(count >= 1)
        base.model.add(base.x["Y"] == 0)
        solver = optimizer._make_solver()

        assert optimizer._status_to_string(solver.solve(base.model)) == "INFEASIBLE"  # type: ignore[arg-type]
        # One fit variable, for the group that only partly fits mono white
        assert set(base.fit_vars) == {("W", "g")}

    def test_variant_weight_one_keeps_variant_scoring(self):
        """At weight 1 a rule creates the g variables, which play no part in the score."""
        optimizer = make_optimizer(self.COMBOS, cube_size=2, variant_weight=1, min_pair_combos=1)
        plain = make_optimizer(self.COMBOS, cube_size=2, variant_weight=1)
        base = optimizer._build_base_model()

        assert set(base.g) == {"g"}
        assert not plain._build_base_model().g
        assert optimizer._combo_score({"g1", "g2"}) == 2 * ILPOptimizer.WEIGHT_SCALE
        assert optimizer._combo_score({"g1"}) == ILPOptimizer.WEIGHT_SCALE
        # Both score a cube the same way and Phase 1 finds the same optimum
        assert optimizer.solve().objective_value == plain.solve().objective_value
        assert optimizer.solve().combo_count == 1

    def test_fit_variables_are_hinted_and_counted(self):
        optimizer = make_optimizer(self.COMBOS, cube_size=2, min_pair_combos=1)
        base = optimizer._build_base_model()
        optimizer._add_archetype_minimums(base)

        # WU fits both variants (the group indicator is used); WB, WR and WG only g1
        assert set(base.fit_vars) == {(pair, "g") for pair in ["WB", "WR", "WG"]}
        assert len(base.g) == 1
        assert optimizer._hint_group_vars(base, {"g2"}) == 1 + 3
