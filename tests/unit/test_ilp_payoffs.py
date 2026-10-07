"""Tests for the Phase 2 payoff floor: payoff-only cards in the pool and the minimum number
of outlets per outcome category."""

import logging
from typing import Any

import pytest

from mtg_combo_cube.ilp.ilp_models import CandidateCard, CardMixRules, ComboData
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.outcomes import OutcomeCategories, parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import PayoffTable, parse_payoff_table, resolve_payoffs
from mtg_combo_cube.models import CardAttributes
from tests.unit.test_ilp_optimizer import build_candidate_cards

MANA = frozenset(["Infinite colored mana"])
DAMAGE = frozenset(["Infinite damage"])
OUTCOMES = parse_outcome_categories(
    {"mana": ["infinite colored mana"], "damage": ["infinite damage"], "tokens": ["tokens"]}
)


def combo(combo_id: str, cards: list[str], features: frozenset[str], popularity: int) -> ComboData:
    return ComboData(combo_id, frozenset(cards), [], popularity, features=features)


# A mana hub H with three partners (four cards, three mana combos), a two-card mana combo Q
# and a two-card damage combo D. The outlets Ballista and Comet are in no combo at all.
COMBOS = [
    *(combo(f"h{i}", ["H", f"P{i}"], MANA, 100) for i in range(1, 4)),
    combo("q", ["Q1", "Q2"], MANA, 100),
    combo("d", ["D1", "D2"], DAMAGE, 1),
]
HUB = {"H", "P1", "P2", "P3"}
OUTLETS = {"Ballista", "Comet"}
NO_CARD_MIX = CardMixRules(0, 0, 0, 5, 0, 0, 0)


def payoff_table(cards: dict[str, list[str]]) -> PayoffTable:
    """A resolved payoff table naming the given cards per category (as table cards)."""
    return resolve_payoffs(
        parse_payoff_table({name: {"cards": names} for name, names in cards.items()}), {}, {}
    )


PAYOFFS = payoff_table({"mana": sorted(OUTLETS)})


def make_optimizer(
    combos: list[ComboData] = COMBOS,
    payoffs: PayoffTable | None = PAYOFFS,
    categories: OutcomeCategories | None = OUTCOMES,
    extra_cards: set[str] = OUTLETS,
    **kwargs: Any,
) -> ILPOptimizer:
    """An optimizer whose candidates are the combo cards plus the payoff-only extra cards."""
    candidate_cards = build_candidate_cards(combos)
    for name in sorted(extra_cards):
        candidate_cards[name] = CandidateCard(name, frozenset(), frozenset())
    settings: dict[str, Any] = {
        "cube_size": 6,
        "time_limit_seconds": 30,
        # The window holds the score exactly, so the objective cannot trade combos away
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
        "outcome_categories": categories,
        "min_outcome_combos": 0,
        "payoffs": payoffs,
        # The default floor is sized for a full build; each test sets the one it exercises
        "min_payoffs": 0,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=candidate_cards, **settings)


class TestPayoffFloor:
    def test_payoff_only_cards_are_candidates_that_phase1_never_picks(self):
        optimizer = make_optimizer()

        assert optimizer.payoff_only_cards == OUTLETS
        assert optimizer.payoff_cards == {"mana": frozenset(OUTLETS)}
        result = optimizer.solve()

        # Six cards hold the hub and Q: four mana combos, no slot for an outlet
        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}
        assert result.phase1_payoff_stats is not None
        assert result.phase1_payoff_stats.cards_per_category == {"mana": 0}

    def test_floor_off_leaves_the_outlets_out(self):
        result = make_optimizer().solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}
        assert result.phase2_min_payoffs is None
        assert result.phase2_payoff_floors is None
        assert result.phase2_payoff_stats is not None
        assert result.phase2_payoff_stats.cards_per_category == {"mana": 0}
        assert result.profile_data is not None
        assert "payoff_floor" not in result.profile_data["phase2"]["counts"]

    def test_floor_pulls_an_outlet_into_a_cube_of_engines(self):
        result = make_optimizer(min_payoffs=1).solve_two_phase(profile=True)

        assert result.is_multi_objective
        selected = set(result.get_selected_card_names())
        # The outlet takes a slot from the Phase 1 cube, which costs one combo: three combos
        # remain, and "minmax" prefers the hub with two partners beside Q (range 1) to the
        # whole hub beside both outlets (range 2)
        outlets = selected & OUTLETS
        assert len(outlets) == 1
        assert {"H", "Q1", "Q2"} <= selected
        assert result.combo_count == 3
        assert result.phase2_min_payoffs == 1
        assert result.phase2_payoff_floors == {"mana": 1}
        assert result.phase2_payoff_stats is not None
        assert result.phase2_payoff_stats.cards_per_category == {"mana": 1}
        assert result.phase2_payoff_stats.cards == {
            "mana": {outlet: ["card"] for outlet in outlets}
        }
        # Payoff-only cards have utilization 0 by definition
        assert result.utilization_per_card is not None
        assert result.utilization_per_card[next(iter(outlets))] == 0
        assert result.phase2_utilization_stats is not None
        assert result.phase2_utilization_stats.min_utilization == 0
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["payoff_floor"] == 1
        # The floor constraint covers the combo cards only
        assert result.profile_data["phase2"]["counts"]["utilization_floor"] == 8
        # The Phase 1 cube breaks the rule, so the warm start was repaired
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]

    @pytest.mark.parametrize("objective", sorted(ILPOptimizer._PHASE2_OBJECTIVES))
    def test_every_objective_accepts_payoff_only_cards(self, objective: str):
        result = make_optimizer(min_payoffs=1, phase2_objective=objective).solve_two_phase()

        assert result.is_multi_objective
        assert OUTLETS & set(result.get_selected_card_names())
        assert result.phase2_payoff_floors == {"mana": 1}

    def test_a_combo_piece_can_be_the_payoff(self):
        # Q1 is an outlet too: the Phase 1 cube already holds one and needs no repair
        payoffs = payoff_table({"mana": ["Q1", "Ballista"]})
        result = make_optimizer(payoffs=payoffs, min_payoffs=1).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}
        assert result.phase2_payoff_stats is not None
        assert result.phase2_payoff_stats.cards == {"mana": {"Q1": ["card"]}}
        assert result.profile_data is not None
        assert "warm_start_repair" not in result.profile_data["phase2"]["timings"]

    def test_category_without_combos_in_the_pool_gets_no_floor(
        self, caplog: pytest.LogCaptureFixture
    ):
        payoffs = payoff_table({"mana": ["Ballista"], "tokens": ["Comet"]})
        optimizer = make_optimizer(payoffs=payoffs, min_payoffs=1)

        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_payoff_floors == {"mana": 1}
        assert "Ballista" in result.get_selected_card_names()
        assert "the pool has no combos with outcome tokens; no payoff floor for it" in caplog.text
        assert result.phase2_payoff_stats is not None
        assert result.phase2_payoff_stats.cards_per_category == {"mana": 1, "tokens": 0}

    def test_floor_above_the_payoff_count_is_lowered_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        optimizer = make_optimizer(min_payoffs=5)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_min_payoffs == 5
        assert result.phase2_payoff_floors == {"mana": 2}
        assert OUTLETS <= set(result.get_selected_card_names())
        assert (
            "the pool has only 2 selectable payoff cards for mana, below the minimum of 5; "
            "the floor is lowered to 2 (every such card must be in the cube)"
        ) in caplog.text

    def test_table_cards_outside_the_pool_are_ignored(self):
        optimizer = make_optimizer(payoffs=payoff_table({"mana": ["Ballista", "Not A Card"]}))

        assert optimizer.payoff_cards == {"mana": frozenset(["Ballista"])}

    def test_combo_pieces_below_the_utilization_floor_do_not_count_toward_the_floor(
        self, caplog: pytest.LogCaptureFixture
    ):
        # Q1 is in one combo: with a utilization floor of 2 it can never be selected, so a
        # floor it is the only payoff card for would be unsatisfiable; a payoff-only card
        # is exempt from the utilization floor and still counts
        optimizer = make_optimizer(
            payoffs=payoff_table({"mana": ["Q1", "Ballista"], "damage": ["Q2"]}),
            min_payoffs=2,
            min_utilization_floor=2,
        )

        assert optimizer.payoff_cards == {"mana": frozenset(["Ballista"]), "damage": frozenset()}
        assert optimizer._payoff_floors() == {"mana": 1}
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer._check_payoff_pool()
        assert (
            "the pool has only 1 selectable payoff cards for mana, below the minimum of 2"
        ) in caplog.text
        assert "the pool has no selectable payoff cards for damage; no payoff floor" in caplog.text
        # Without a utilization floor the combo piece counts
        assert make_optimizer(
            payoffs=payoff_table({"mana": ["Q1", "Ballista"]}), min_utilization_floor=0
        ).payoff_cards == {"mana": frozenset(["Q1", "Ballista"])}

    def test_min_payoffs_is_recorded_only_with_a_floor(self):
        # No category of the table has combos in the pool, so no floor applies
        result = make_optimizer(
            payoffs=payoff_table({"tokens": ["Ballista"]}), min_payoffs=2
        ).solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_min_payoffs is None
        assert result.phase2_payoff_floors is None

    def test_payoff_only_cards_count_for_the_card_mix(self):
        # Ballista is colorless; with a colorless cap of 0 cards the outlet must be Comet
        attributes = {
            card: CardAttributes("W", "Creature", 2) for card in build_candidate_cards(COMBOS)
        }
        attributes["Ballista"] = CardAttributes("", "Artifact Creature", 0)
        attributes["Comet"] = CardAttributes("R", "Instant", 2)
        result = make_optimizer(
            min_payoffs=1,
            card_attributes=attributes,
            card_mix=CardMixRules(0, 0.1, 0, 5, 0, 0, 0),  # floor(0.1 x 6) = 0 colorless cards
        ).solve_two_phase()

        assert result.is_multi_objective
        assert "Comet" in result.get_selected_card_names()
        assert "Ballista" not in result.get_selected_card_names()

    def test_impossible_floor_falls_back_with_the_shortfall_named(
        self, caplog: pytest.LogCaptureFixture
    ):
        # The only outlet is colorless and the colorless cap allows none
        attributes = {
            card: CardAttributes("W", "Creature", 2) for card in build_candidate_cards(COMBOS)
        }
        attributes["Ballista"] = CardAttributes("", "Artifact Creature", 0)
        optimizer = make_optimizer(
            payoffs=payoff_table({"mana": ["Ballista"]}),
            extra_cards={"Ballista"},
            min_payoffs=1,
            card_attributes=attributes,
            card_mix=CardMixRules(0, 0.1, 0, 5, 0, 0, 0),
        )

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert result.phase2_payoff_floors == {"mana": 1}
        assert (
            "still breaks 1 payoff floor constraints (payoffs below their floor: mana 0 < 1)"
        ) in caplog.text

    def test_without_a_table_a_requested_floor_warns(self, caplog: pytest.LogCaptureFixture):
        optimizer = make_optimizer(payoffs=None, extra_cards=set(), min_payoffs=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_payoff_floors is None
        assert result.phase1_payoff_stats is None
        assert result.phase2_payoff_stats is None
        assert "no payoff table; the payoff floor is not enforced" in caplog.text

    @pytest.mark.parametrize("minimum", [0, ILPOptimizer.DEFAULT_MIN_PAYOFFS])
    def test_without_a_table_the_default_or_zero_floor_is_quiet(
        self, caplog: pytest.LogCaptureFixture, minimum: int
    ):
        optimizer = make_optimizer(payoffs=None, extra_cards=set(), min_payoffs=minimum)

        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer.solve_two_phase()

        records = [r for r in caplog.records if "no payoff table" in r.getMessage()]
        assert records and all(r.levelno == logging.INFO for r in records)

    def test_without_an_outcome_table_the_floor_is_not_enforced(
        self, caplog: pytest.LogCaptureFixture
    ):
        optimizer = make_optimizer(categories=None, min_payoffs=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_payoff_floors is None
        assert not OUTLETS & set(result.get_selected_card_names())
        # The payoff statistics need no outcome table
        assert result.phase2_payoff_stats is not None
        assert "the payoff floor cannot tell which categories have combos" in caplog.text

    def test_negative_floor_is_rejected(self):
        with pytest.raises(ValueError, match="min_payoffs"):
            make_optimizer(min_payoffs=-1)


class TestWarmStart:
    def test_payoff_only_cards_are_exempt_from_the_floor_checks(self):
        optimizer = make_optimizer(min_payoffs=1)
        cube = optimizer._warm_start_for(HUB | OUTLETS)

        assert cube.utilization["Ballista"] == 0
        assert optimizer._satisfies_window_and_floor(cube, optimizer._combo_score(cube.combo_ids))
        # A combo card below the floor still fails the check
        below = optimizer._warm_start_for(HUB | {"Q1", "Ballista"})
        assert not optimizer._satisfies_window_and_floor(
            below, optimizer._combo_score(below.combo_ids)
        )


class TestViolations:
    def test_floor_violations_and_shortfalls(self):
        optimizer = make_optimizer(min_payoffs=2)

        assert optimizer._payoff_floors() == {"mana": 2}
        assert optimizer._payoff_violations(HUB | {"Q1", "Q2"}) == 1
        assert optimizer._payoff_shortfalls(HUB | {"Ballista"}) == {"mana": (1, 2)}
        assert optimizer._describe_payoff_shortfalls(HUB | {"Ballista"}) == "mana 1 < 2"
        assert optimizer._payoff_violations(HUB | OUTLETS) == 0
        assert optimizer._describe_shortfalls(HUB) == " (payoffs below their floor: mana 0 < 2)"

    def test_rule_off_has_no_violations(self):
        assert make_optimizer()._payoff_violations(HUB) == 0
        assert make_optimizer(payoffs=None, min_payoffs=2)._payoff_violations(HUB) == 0
        assert make_optimizer(categories=None, min_payoffs=2)._payoff_violations(HUB) == 0

    def test_floor_is_a_cube_rule(self):
        labels = [rule.label for rule in make_optimizer()._cube_rules()]

        assert labels[-1] == "payoff floor"
        violations = make_optimizer(min_payoffs=1)._cube_rule_violations(HUB | {"Q1", "Q2"})
        assert violations["payoff floor"] == 1
