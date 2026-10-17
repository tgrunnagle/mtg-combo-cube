"""Tests for the Phase 2 staples rule: the named cards of the pool's most popular variants
are staples, and the cube must complete a minimum of them."""

import logging
from typing import Any

import pytest

from mtg_combo_cube.ilp.cube_evaluation import staples
from mtg_combo_cube.ilp.ilp_models import CardMixRules, ComboData, RequirementOption
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from tests.unit.test_ilp_optimizer import build_candidate_cards


def combo(
    combo_id: str,
    cards: list[str],
    popularity: int = 100,
    options: list[list[str]] | None = None,
    group: str = "",
) -> ComboData:
    return ComboData(
        combo_id,
        frozenset(cards),
        [
            RequirementOption(f"Template {i}", f"key:{sorted(pool)}", frozenset(pool))
            for i, pool in enumerate(options or [])
        ],
        popularity,
        group_key=group,
    )


# A hub H with four partners (four combos, the densest cards), the most popular combo of
# the pool S1 + S2 (one variant), a combo T1 + T2 plus a template filled by Z1 or Z2 (two
# variants of one group), a variant naming S1, S2 and U (a superset of the first staple),
# and a filler combo D
COMBOS = [
    *(combo(f"h{i}", ["H", f"P{i}"]) for i in range(1, 5)),
    combo("s", ["S1", "S2"], 1000),
    combo("t1", ["T1", "T2"], 500, [["Z1"]], group="t"),
    combo("t2", ["T1", "T2"], 400, [["Z2"]], group="t"),
    combo("u", ["S1", "S2", "U"], 300),
    combo("d", ["D1", "D2"], 1),
]
HUB = {"H", "P1", "P2", "P3", "P4"}
NO_CARD_MIX = CardMixRules(0, 0, 0, 5, 0, 0, 0)


def make_optimizer(combos: list[ComboData] = COMBOS, **kwargs: Any) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "staple_combos": 0,
        "cube_size": 5,
        "time_limit_seconds": 30,
        "combo_tolerance": 0,
        "min_coverage_ratio": 0,
        "gap_limit": 0,
        "num_workers": 1,
        "min_utilization_floor": 1,
        "max_color_ratio": 0,
        "phase2_objective": "minmax",
        "min_pair_combos": 0,
        "min_mono_combos": 0,
        "max_wide_combo_share": 0,
        "card_mix": NO_CARD_MIX,
        "min_outcome_combos": 0,
        "min_payoffs": 0,
        "payoff_share": 0,
        "min_enablers": 0,
        "enabler_share": 0,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=build_candidate_cards(combos), **settings)


class TestStaples:
    def test_variants_with_the_same_named_cards_are_one_staple(self):
        found = staples(COMBOS, 4)

        # The four most popular variants are s, t1, t2 and u: three staples, most popular
        # first; the two template variants of T are one, and u is its own despite naming
        # S1 and S2
        assert [(s.cards, s.popularity, s.variant_ids) for s in found] == [
            (["S1", "S2"], 1000, ["s"]),
            (["T1", "T2"], 500, ["t1", "t2"]),
            (["S1", "S2", "U"], 300, ["u"]),
        ]
        assert [s.cards for s in staples(COMBOS, 1)] == [["S1", "S2"]]
        assert staples(COMBOS, 0) == []

    def test_variants_without_named_cards_are_left_out(self):
        found = staples([combo("only", [], 5000, [["A", "B"]]), *COMBOS], 1)

        assert [s.cards for s in found] == [["S1", "S2"]]


class TestSettings:
    def test_minimum_defaults_to_every_staple(self):
        optimizer = make_optimizer(staple_combos=4)

        assert len(optimizer.staples) == 3
        assert optimizer.staple_minimum == 3
        assert make_optimizer(staple_combos=4, min_staple_combos=2).staple_minimum == 2
        assert make_optimizer().staple_minimum == 0
        assert make_optimizer().staples == []

    def test_minimum_above_the_staples_is_lowered_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        optimizer = make_optimizer(staple_combos=1, min_staple_combos=5)

        assert optimizer.staple_minimum == 1
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer._check_staple_pool()
        assert "1 staples, fewer than the minimum of 5; the minimum is lowered to 1" in caplog.text

    def test_more_staples_than_variants_warns(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            make_optimizer(staple_combos=50)._check_staple_pool()

        assert "fewer than the 50 the staples are taken from" in caplog.text

    def test_negative_settings_are_rejected(self):
        with pytest.raises(ValueError, match="staple_combos and min_staple_combos"):
            make_optimizer(staple_combos=-1)

    def test_staple_pieces_are_floor_free_while_the_rule_is_on(self):
        on = make_optimizer(staple_combos=1)
        off = make_optimizer()

        assert {"S1", "S2"} <= on.floor_free_cards
        assert "T1" not in on.floor_free_cards
        assert "S1" not in off.floor_free_cards
        # Floor-free is not exempt: the pieces still count in the statistics
        assert "S1" not in on.exempt_cards


class TestViolations:
    def test_shortfall_against_the_minimum(self):
        optimizer = make_optimizer(staple_combos=4, min_staple_combos=2)

        assert optimizer._staple_violations(HUB) == 2
        assert optimizer._staple_violations(HUB | {"S1", "S2"}) == 1
        assert optimizer._staple_violations({"S1", "S2", "T1", "T2", "Z2"}) == 0
        assert optimizer._staple_violations({"S1", "S2", "U"}) == 0  # two staples
        assert make_optimizer()._staple_violations(HUB) == 0

    def test_is_a_cube_rule_with_a_shortfall_description(self):
        optimizer = make_optimizer(staple_combos=1)

        assert [rule.label for rule in optimizer._cube_rules()][-1] == "staples"
        assert optimizer._cube_rule_violations(HUB)["staples"] == 1
        assert optimizer._describe_shortfalls(HUB) == " (staples completed 0 < 1)"
        assert optimizer._describe_shortfalls(HUB | {"S1", "S2"}) == ""


class TestModel:
    def test_whole_groups_use_their_indicator_and_partial_ones_a_variable(self):
        # s is a group of one (its y), t a whole group with a g at variant weight 0.1, u a
        # group of one: no staple variable
        optimizer = make_optimizer(staple_combos=4)
        base = optimizer._build_base_model()
        optimizer._add_staple_minimum(base)
        assert base.staple_vars == []
        assert base.counts["staple_minimum"] == 1

        # At variant weight 1 without a rule that counts groups there is no g, so the two
        # variants of t get a staple variable
        plain = make_optimizer(staple_combos=4, variant_weight=1)
        base = plain._build_base_model()
        plain._add_staple_minimum(base)
        assert [ids for _, ids in base.staple_vars] == [frozenset({"t1", "t2"})]

    def test_rule_off_adds_nothing(self):
        optimizer = make_optimizer()
        base = optimizer._build_base_model()
        optimizer._add_staple_minimum(base)

        assert "staple_minimum" not in base.counts


class TestSolve:
    def test_rule_pulls_the_popular_combo_into_a_cube_of_denser_combos(self):
        # Phase 1 takes the hub (four combos in five cards); the rule costs two slots for
        # one combo, and the reference cube (hub minus two partners plus S1 and S2) sets
        # the window
        result = make_optimizer(staple_combos=1).solve_two_phase()

        assert result.phase1_selected_cards is not None
        assert {card.name for card in result.phase1_selected_cards} == HUB
        assert result.phase1_staples_completed == 0
        assert result.phase2_status == "OPTIMAL"
        assert not result.phase2_fell_back
        assert {"S1", "S2"} <= set(result.get_selected_card_names())
        assert result.combo_count == 3
        assert result.phase2_staples_completed == 1
        assert (result.phase2_staple_combos, result.phase2_min_staple_combos) == (1, 1)
        assert result.staple_stats is not None
        assert [(s.cards, s.completed, s.missing_cards) for s in result.staple_stats] == [
            (["S1", "S2"], True, [])
        ]

    def test_rule_off_reports_nothing(self):
        result = make_optimizer().solve_two_phase()

        assert result.staple_stats is None
        assert result.phase1_staples_completed is None
        assert result.phase2_staples_completed is None
        assert result.phase2_staple_combos is None
        assert set(result.get_selected_card_names()) == HUB

    def test_repair_model_completes_the_staple_for_real(self):
        # The Phase 1 style repair model has one-sided y, so the staple variable can only
        # be 1 when the cards truly complete a variant
        optimizer = make_optimizer(staple_combos=2, min_staple_combos=2)

        best, status = optimizer._best_constrained_cube(optimizer._warm_start_for(HUB))

        assert status == "OPTIMAL"
        assert best is not None
        assert {"S1", "S2", "T1", "T2"} <= best.cards
        assert best.cards & {"Z1", "Z2"}
        assert optimizer._staple_violations(best.cards) == 0

    def test_staple_status_in_a_single_phase_result(self):
        result = make_optimizer(staple_combos=4).solve()

        assert result.staple_stats is not None
        assert [(s.cards, s.completed) for s in result.staple_stats] == [
            (["S1", "S2"], False),
            (["T1", "T2"], False),
            (["S1", "S2", "U"], False),
        ]
        assert result.staple_stats[0].missing_cards == ["S1", "S2"]
        assert result.phase1_staples_completed == 0


class TestDefaults:
    def test_the_rule_is_on_by_default_for_every_staple_of_the_top_twenty_five(self):
        """Chosen in Step 4 of the potential-combos plan; the CLI plumbing test checks that
        every layer declares the same values."""
        optimizer = ILPOptimizer(
            combos=COMBOS, candidate_cards=build_candidate_cards(COMBOS), cube_size=5
        )

        assert (ILPOptimizer.DEFAULT_STAPLE_COMBOS, ILPOptimizer.DEFAULT_MIN_STAPLE_COMBOS) == (
            25,
            25,
        )
        assert optimizer.staple_combos == 25
        # The pool has fewer staples than the minimum asks for, so every one is required
        assert optimizer.staple_minimum == len(optimizer.staples) == 8
