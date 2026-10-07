"""Tests for the Phase 2 outcome rules: distinct combos per outcome category and the cap on
one category's share of the completed combos."""

import logging
from typing import Any

import pytest

from mtg_combo_cube.ilp.ilp_models import ComboData, OutcomeStats
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.outcomes import OutcomeCategories, parse_outcome_categories
from tests.unit.test_ilp_optimizer import build_candidate_cards

MANA = frozenset(["Infinite colored mana", "Infinite creature ETB"])
DAMAGE = frozenset(["Infinite damage"])
TABLE = parse_outcome_categories({"mana": ["infinite colored mana"], "damage": ["infinite damage"]})


def combo(
    combo_id: str, cards: list[str], features: frozenset[str], popularity: int, group: str = ""
) -> ComboData:
    return ComboData(combo_id, frozenset(cards), [], popularity, group_key=group, features=features)


# A mana hub H with four partners (five cards, four mana combos), a two-card mana combo Q
# and a two-card damage combo D of low popularity. A seven-card Phase 1 cube takes the hub
# and Q (five mana combos); the damage combo only gets in when a rule asks for it.
COMBOS = [
    *(combo(f"h{i}", ["H", f"P{i}"], MANA, 100) for i in range(1, 5)),
    combo("q", ["Q1", "Q2"], MANA, 100),
    combo("d", ["D1", "D2"], DAMAGE, 1),
]
HUB = {"H", "P1", "P2", "P3", "P4"}


def make_optimizer(
    combos: list[ComboData] = COMBOS,
    categories: OutcomeCategories | None = TABLE,
    **kwargs: Any,
) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "cube_size": 7,
        "time_limit_seconds": 30,
        # The window holds the score exactly, so the objective cannot trade combos away
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
        "outcome_categories": categories,
        # The default minimum is sized for a full build; each test enables the rule it exercises
        "min_outcome_combos": 0,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=build_candidate_cards(combos), **settings)


def outcomes(stats: OutcomeStats | None) -> dict[str, int]:
    assert stats is not None
    return stats.combos_per_outcome


class TestOutcomeMinimum:
    def test_phase1_ignores_outcomes_but_reports_them(self):
        result = make_optimizer().solve()

        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}
        assert outcomes(result.phase1_outcome_stats) == {"mana": 5, "damage": 0}
        assert result.phase1_outcome_stats is not None
        assert result.phase1_outcome_stats.uncategorized == 0
        assert result.phase1_outcome_stats.total == 5
        assert result.phase1_popularity_stats is not None
        assert result.phase1_popularity_stats.combo_count == 5

    def test_minimum_pulls_the_damage_combo_into_a_cube_of_mana_combos(self):
        result = make_optimizer(min_outcome_combos=1).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == HUB | {"D1", "D2"}
        assert outcomes(result.phase2_outcome_stats) == {"mana": 4, "damage": 1}
        assert result.phase2_outcome_minimums == {"mana": 1, "damage": 1}
        assert result.phase2_max_outcome_share is None
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["outcome_minimum"] == 2
        assert "outcome_share_cap" not in result.profile_data["phase2"]["counts"]
        # The Phase 1 cube breaks the rule, so the warm start was repaired
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]

    def test_table_minimum_overrides_the_default(self):
        table = parse_outcome_categories(
            {"mana": ["infinite colored mana"], "damage": {"patterns": ["damage"], "min_combos": 1}}
        )
        result = make_optimizer(categories=table, min_outcome_combos=0).solve_two_phase(
            profile=True
        )

        assert result.is_multi_objective
        assert result.phase2_outcome_minimums == {"damage": 1}
        assert outcomes(result.phase2_outcome_stats)["damage"] == 1
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["outcome_minimum"] == 1

    def test_zero_disables_the_minimum(self):
        result = make_optimizer(min_outcome_combos=0).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_outcome_minimums is None
        assert result.phase2_max_outcome_share is None
        assert outcomes(result.phase2_outcome_stats) == {"mana": 5, "damage": 0}
        assert result.profile_data is not None
        assert "outcome_minimum" not in result.profile_data["phase2"]["counts"]

    def test_minimum_above_the_pool_is_lowered_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        # The pool has no mill combo and one damage combo, so neither can reach 2
        table = parse_outcome_categories(
            {"mana": ["infinite colored mana"], "damage": ["damage"], "mill": ["mill"]}
        )
        optimizer = make_optimizer(categories=table, min_outcome_combos=2)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_outcome_minimums == {"mana": 2, "damage": 1}
        assert outcomes(result.phase2_outcome_stats) == {"mana": 4, "damage": 1, "mill": 0}
        assert (
            "the pool has only 0 combos with outcome mill, below the minimum of 2; the "
            "minimum is lowered to 0 (every such combo must be completed)"
        ) in caplog.text
        assert (
            "the pool has only 1 combos with outcome damage, below the minimum of 2; the "
            "minimum is lowered to 1 (every such combo must be completed)"
        ) in caplog.text
        assert "falling back" not in caplog.text

    def test_infeasible_minimum_falls_back_to_phase1(self, caplog: pytest.LogCaptureFixture):
        # The pool has a mill combo, but its four cards do not fit beside a mana and a
        # damage combo in seven
        mill = frozenset(["Infinite mill"])
        combos = COMBOS + [combo("mill", ["L1", "L2", "L3", "L4"], mill, 1)]
        table = parse_outcome_categories(
            {"mana": ["infinite colored mana"], "damage": ["damage"], "mill": ["mill"]}
        )
        optimizer = make_optimizer(combos, categories=table, min_outcome_combos=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert result.phase2_outcome_minimums == {"mana": 1, "damage": 1, "mill": 1}
        assert "the pool has only" not in caplog.text
        assert (
            "no cube satisfying the cube rules was found; the reference cube (the Phase 1 "
            "cube) still breaks 2 outcome minimum constraints (outcomes below their minimum: "
            "damage 0 < 1, mill 0 < 1)"
        ) in caplog.text

    def test_without_a_table_a_requested_rule_warns(self, caplog: pytest.LogCaptureFixture):
        optimizer = make_optimizer(categories=None, min_outcome_combos=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_outcome_minimums is None
        assert result.phase1_outcome_stats is None
        assert result.phase2_outcome_stats is None
        assert "no outcome category table; the outcome rules are not enforced" in caplog.text

    @pytest.mark.parametrize("minimum", [0, ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS])
    def test_without_a_table_the_default_or_zero_minimum_is_quiet(
        self, caplog: pytest.LogCaptureFixture, minimum: int
    ):
        # The optimizer's own default, or 0, asks for no rule: an info line only
        optimizer = make_optimizer(categories=None, min_outcome_combos=minimum)

        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer.solve_two_phase()

        records = [r for r in caplog.records if "no outcome category table" in r.getMessage()]
        assert records and all(r.levelno == logging.INFO for r in records)

    def test_negative_minimum_is_rejected(self):
        with pytest.raises(ValueError, match="min_outcome_combos"):
            make_optimizer(min_outcome_combos=-1)

    def test_outcome_rules_create_group_variables_at_variant_weight_one(self):
        grouped = [
            combo("g1", ["G", "X1"], DAMAGE, 1, group="g"),
            combo("g2", ["G", "X2"], DAMAGE, 1, group="g"),
            combo("q", ["Q1", "Q2"], MANA, 100),
        ]

        assert make_optimizer(grouped, variant_weight=1).grouped_keys == frozenset()
        assert make_optimizer(grouped, variant_weight=1, min_outcome_combos=1).grouped_keys == {"g"}
        assert make_optimizer(grouped, variant_weight=1, max_outcome_share=0.5).grouped_keys == {
            "g"
        }


class TestGroupedCombos:
    """A combo of several variants is one combo to both outcome rules."""

    # A damage combo of two variants on a hub G, a second damage combo D and a popular mana
    # combo Q. Five cards hold G with both partners and Q (two combos in three variants,
    # the Phase 1 choice by popularity) or G with one partner and D (two damage combos)
    GROUPED = [
        combo("g1", ["G", "X1"], DAMAGE, 1, group="g"),
        combo("g2", ["G", "X2"], DAMAGE, 1, group="g"),
        combo("d", ["D1", "D2"], DAMAGE, 1),
        combo("q", ["Q1", "Q2"], MANA, 100),
    ]

    def test_two_variants_of_one_combo_count_once_toward_a_minimum(self):
        table = parse_outcome_categories(
            {"damage": {"patterns": ["infinite damage"], "min_combos": 2}}
        )
        result = make_optimizer(self.GROUPED, categories=table, cube_size=5).solve_two_phase(
            profile=True
        )

        assert result.is_multi_objective
        # Both variants of G are one damage combo: the second must be D
        assert {"G", "D1", "D2"} <= set(result.get_selected_card_names())
        assert outcomes(result.phase2_outcome_stats) == {"damage": 2}
        assert result.profile_data is not None
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]

    def test_two_variants_of_one_combo_count_once_toward_the_cap(self):
        # The Phase 1 cube is one damage and one mana combo, 50% each, although two of its
        # three variants are damage: it meets a 50% cap as it is
        result = make_optimizer(self.GROUPED, max_outcome_share=0.5, cube_size=5).solve_two_phase(
            profile=True
        )

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == {"G", "X1", "X2", "Q1", "Q2"}
        assert outcomes(result.phase2_outcome_stats) == {"mana": 1, "damage": 1}
        assert result.profile_data is not None
        assert "warm_start_repair" not in result.profile_data["phase2"]["timings"]


class TestOutcomeShareCap:
    def test_cap_forces_a_second_outcome_in(self):
        # Five mana combos of five is 100%; four of five is 80%
        result = make_optimizer(max_outcome_share=0.8).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == HUB | {"D1", "D2"}
        assert outcomes(result.phase2_outcome_stats) == {"mana": 4, "damage": 1}
        assert result.phase2_max_outcome_share == 0.8
        assert result.phase2_outcome_minimums is None
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["outcome_share_cap"] == 2

    def test_cap_met_by_the_phase1_cube_needs_no_repair(self):
        # Nine cards complete everything: six combos, five of them mana (83%), under a 90% cap
        result = make_optimizer(max_outcome_share=0.9, cube_size=9).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert outcomes(result.phase2_outcome_stats) == {"mana": 5, "damage": 1}
        assert result.phase2_max_outcome_share == 0.9
        assert result.profile_data is not None
        assert "warm_start_repair" not in result.profile_data["phase2"]["timings"]

    def test_cap_can_shrink_the_cube_to_what_it_allows(self):
        # At 50% a cube may hold one mana and one damage combo and nothing more
        result = make_optimizer(max_outcome_share=0.5).solve_two_phase()

        assert result.is_multi_objective
        assert outcomes(result.phase2_outcome_stats) == {"mana": 1, "damage": 1}
        assert result.phase2_reference_distinct_combo_count == 2

    def test_impossible_cap_falls_back(self, caplog: pytest.LogCaptureFixture):
        # Two categories at most 40% each cannot both be present, and both must be
        optimizer = make_optimizer(max_outcome_share=0.4, min_outcome_combos=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert result.phase2_max_outcome_share == 0.4
        assert (
            "no cube satisfying the cube rules was found; the reference cube (the Phase 1 "
            "cube) still breaks 1 outcome minimum, 1 outcome share cap constraints (outcomes "
            "below their minimum: damage 0 < 1)"
        ) in caplog.text

    @pytest.mark.parametrize("share", [0, 1])
    def test_zero_and_one_disable_the_cap(self, share: float):
        result = make_optimizer(max_outcome_share=share).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_max_outcome_share is None
        assert result.profile_data is not None
        assert "outcome_share_cap" not in result.profile_data["phase2"]["counts"]

    @pytest.mark.parametrize("share", [-0.1, 1.5])
    def test_share_outside_zero_to_one_is_rejected(self, share: float):
        with pytest.raises(ValueError, match="max_outcome_share"):
            make_optimizer(max_outcome_share=share)


class TestViolations:
    def test_minimum_violations_and_shortfalls(self):
        optimizer = make_optimizer(min_outcome_combos=1)

        # A cached categorization backs every check
        assert optimizer.group_categories["d"] == {"damage"}
        assert optimizer.outcome_groups == {"mana": ["h1", "h2", "h3", "h4", "q"], "damage": ["d"]}

        assert optimizer._outcome_minimum_violations(HUB | {"Q1", "Q2"}) == 1
        assert optimizer._outcome_shortfalls(HUB | {"Q1", "Q2"}) == {"damage": (0, 1)}
        assert optimizer._outcome_minimum_violations(HUB | {"D1", "D2"}) == 0
        assert optimizer._describe_outcome_shortfalls({"D1", "D2"}) == "mana 0 < 1"

    def test_cap_violations(self):
        optimizer = make_optimizer(max_outcome_share=0.8)

        assert optimizer._outcome_cap_violations(HUB | {"Q1", "Q2"}) == 1
        assert optimizer._outcome_cap_violations(HUB | {"D1", "D2"}) == 0
        assert optimizer._outcome_cap_violations(set()) == 0

    def test_rules_off_have_no_violations(self):
        optimizer = make_optimizer()

        assert optimizer._outcome_minimum_violations(HUB | {"Q1", "Q2"}) == 0
        assert optimizer._outcome_cap_violations(HUB | {"Q1", "Q2"}) == 0
        assert (
            make_optimizer(categories=None, max_outcome_share=0.5)._outcome_cap_violations(HUB) == 0
        )

    def test_both_rules_are_cube_rules(self):
        labels = [rule.label for rule in make_optimizer()._cube_rules()]

        assert labels[-2:] == ["outcome minimum", "outcome share cap"]
        violations = make_optimizer(
            min_outcome_combos=1, max_outcome_share=0.8
        )._cube_rule_violations(HUB | {"Q1", "Q2"})
        assert violations["outcome minimum"] == 1
        assert violations["outcome share cap"] == 1
