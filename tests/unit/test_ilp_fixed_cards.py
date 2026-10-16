"""Tests for the fixed cards: the cube's cards outside the slots the optimizer fills, in every
model with x = 1, counted by everything that counts combos and by nothing that shapes the
cube's cards."""

import logging
from collections.abc import Collection
from typing import Any

import pytest

from mtg_combo_cube.ilp.enablers import EnablerTable, parse_enabler_table, resolve_enablers
from mtg_combo_cube.ilp.ilp_models import FIXED, CandidateCard, CardMixRules, ComboData
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.models import CardAttributes
from tests.unit.test_ilp_optimizer import build_candidate_cards


def combo(combo_id: str, cards: list[str], popularity: int = 100) -> ComboData:
    return ComboData(combo_id, frozenset(cards), [], popularity)


# A hub H with three partners (three combos), a two-card combo Q, a two-card combo D and a
# combo F + X whose piece F is a fixed card. Island is fixed too and names no combo.
COMBOS = [
    *(combo(f"h{i}", ["H", f"P{i}"]) for i in range(1, 4)),
    combo("q", ["Q1", "Q2"]),
    combo("d", ["D1", "D2"], 1),
    combo("f", ["F", "X"], 50),
]
HUB = {"H", "P1", "P2", "P3"}
FIXED_CARDS = frozenset({"F", "Island"})
NO_CARD_MIX = CardMixRules(0, 0, 0, 5, 0, 0, 0)


def enabler_table(cards: dict[str, list[str]]) -> EnablerTable:
    return resolve_enablers(
        parse_enabler_table({name: {"cards": names} for name, names in cards.items()}), {}
    )


def make_optimizer(
    combos: list[ComboData] = COMBOS,
    fixed_cards: frozenset[str] = FIXED_CARDS,
    extra_cards: Collection[str] = (),
    **kwargs: Any,
) -> ILPOptimizer:
    """An optimizer over the combo cards (plus extra cards in no combo) with fixed cards."""
    candidate_cards = build_candidate_cards(combos)
    for name in sorted(extra_cards):
        candidate_cards[name] = CandidateCard(name, frozenset(), frozenset())
    settings: dict[str, Any] = {
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
        "fixed_cards": fixed_cards,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=candidate_cards, **settings)


class TestInstance:
    def test_fixed_candidates_and_free_cards(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer = make_optimizer()

        # Island names no combo: it is kept for the record but is not in the model
        assert optimizer.fixed_cards == {"F"}
        assert optimizer.fixed_card_names == ["F", "Island"]
        assert "F" not in optimizer.free_cards
        assert set(optimizer.free_cards) == set(optimizer.all_cards) - {"F"}
        assert "Fixed cards: 2 outside the 5 slots, 1 of them candidate cards" in caplog.text
        assert "1 fixed" in caplog.text

    def test_fixed_card_is_exempt_and_floor_free_but_not_payoff_only(self):
        # Ritual is a candidate in no combo; fixed, it is not a payoff-only card (which the
        # payoff-only cap would count) but exempt from the floor, the statistics and the
        # balance like one
        optimizer = make_optimizer(fixed_cards=frozenset({"F", "Ritual"}), extra_cards={"Ritual"})

        assert optimizer.payoff_only_cards == frozenset()
        assert optimizer.enabler_only_cards == frozenset()
        assert optimizer.exempt_cards == {"F", "Ritual"}
        assert {"F", "Ritual"} <= optimizer.floor_free_cards
        assert optimizer._floor_card_selectable("Ritual")

    def test_fixed_card_is_not_in_the_card_mix_sets_or_the_color_balance(self):
        attributes = {
            "F": CardAttributes("WU", "Artifact Creature", 6),
            **{card: CardAttributes("", "Artifact", 1) for card in ["H", "P1", "P2", "P3", "X"]},
        }
        optimizer = make_optimizer(card_attributes=attributes, max_color_ratio=1)

        assert "F" not in optimizer.multicolor_cards
        assert "F" not in optimizer.expensive_cards
        assert "F" not in optimizer.creature_cards
        # F is the only white and blue card: counted, it would break the balance
        assert optimizer._color_violations(HUB | {"X", "F"}) == 0
        assert optimizer._mono_color_violations(HUB | {"X", "F"}) == 0

    def test_warm_start_adds_the_fixed_cards(self):
        optimizer = make_optimizer()

        start = optimizer._warm_start_for(HUB | {"X"})

        assert start.cards == HUB | {"X", "F"}
        assert "f" in start.combo_ids
        # The fixed card's utilization is left out, as the exempt cards' are
        assert "F" not in start.utilization
        assert start.utilization["X"] == 1


class TestSolve:
    def test_fixed_card_completes_a_combo_without_a_slot(self):
        # Five slots: the hub (four cards, three combos) plus X, whose combo F completes,
        # beats the hub plus one card of Q or D
        result = make_optimizer().solve()

        assert result.phase1_status == "OPTIMAL"
        assert set(result.get_selected_card_names()) == HUB | {"X"}
        assert result.combo_count == 4
        assert "f" in result.completable_combo_ids
        assert result.fixed_card_stats is not None
        assert result.fixed_card_stats.cards == ["F", "Island"]
        assert result.fixed_card_stats.candidate_cards == ["F"]
        assert (
            result.fixed_card_stats.combo_count,
            result.fixed_card_stats.distinct_combo_count,
        ) == (
            1,
            1,
        )
        # Without the fixed card X completes nothing and the hub plus a card of Q is as good
        plain = make_optimizer(fixed_cards=frozenset()).solve()
        assert plain.combo_count == 3
        assert plain.fixed_card_stats is None

    def test_two_phase_cubes_are_the_free_cards(self):
        result = make_optimizer().solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert not result.phase2_fell_back
        assert len(result.selected_cards) == 5
        assert "F" not in result.get_selected_card_names()
        assert result.phase1_selected_cards is not None
        assert "F" not in {card.name for card in result.phase1_selected_cards}
        assert result.combo_count == 4
        # The fixed card is not among the cards the utilization statistics describe
        assert result.utilization_per_card is not None
        assert "F" not in result.utilization_per_card
        assert result.utilization_per_card["X"] == 1

    def test_card_inclusion_lists_the_fixed_card_last(self):
        result = make_optimizer().solve_two_phase()

        assert result.card_inclusion is not None
        last = result.card_inclusion[-1]
        assert (last.card, last.reason, last.combo_count) == ("F", FIXED, 1)
        assert last.essential_combos == ["f"]
        assert [card.card for card in result.card_inclusion[:-1]] == sorted(
            HUB | {"X"}, key=lambda c: (c != "H", c)
        )
        assert "Island" not in {card.card for card in result.card_inclusion}

    def test_fixed_card_in_no_combo_is_a_feasible_phase2(self):
        # A fixed card in no combo would be excluded by the utilization floor or capped as
        # payoff-only; fixed to 1 that would make Phase 2 infeasible
        optimizer = make_optimizer(fixed_cards=frozenset({"Ritual"}), extra_cards={"Ritual"})

        result = optimizer.solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert "Ritual" not in result.get_selected_card_names()
        assert result.fixed_card_stats is not None
        assert result.fixed_card_stats.combo_count == 0

    def test_fixed_enabler_counts_for_its_floor(self):
        # The ritual floor of 2 is met by the fixed Ritual plus Petal, one slot: the cube
        # keeps four combo cards and Petal
        table = enabler_table({"ritual": ["Ritual", "Petal"]})
        optimizer = make_optimizer(
            fixed_cards=frozenset({"Ritual"}),
            extra_cards={"Ritual", "Petal"},
            enablers=table,
            min_enablers=2,
        )

        assert optimizer.enabler_cards == {"ritual": frozenset({"Ritual", "Petal"})}
        assert optimizer.enabler_floors == {"ritual": 2}
        result = optimizer.solve_two_phase()
        assert result.phase2_status == "OPTIMAL"
        selected = set(result.get_selected_card_names())
        assert "Petal" in selected and "Ritual" not in selected
        assert result.phase2_enabler_stats is not None
        assert result.phase2_enabler_stats.cards_per_category == {"ritual": 2}
        assert result.fixed_card_stats is not None
        assert result.fixed_card_stats.enabler_categories == {"ritual": ["Ritual"]}
        # With a floor of 1 the fixed Ritual meets it alone and no slot goes to Petal
        alone = make_optimizer(
            fixed_cards=frozenset({"Ritual"}),
            extra_cards={"Ritual", "Petal"},
            enablers=table,
            min_enablers=1,
        ).solve_two_phase()
        assert "Petal" not in alone.get_selected_card_names()

    def test_one_card_away_report_is_over_the_cube_and_the_fixed_cards(self):
        # The cube is the hub plus X: G1 would complete X + G1, and F's combo is complete
        # through the fixed card, so F is no missing piece
        result = make_optimizer(combos=[*COMBOS, combo("g", ["X", "G1"], 10)]).solve()

        assert set(result.get_selected_card_names()) == HUB | {"X"}
        assert result.one_card_away is not None
        assert result.one_card_away.variant_count == 1
        [piece] = result.one_card_away.pieces
        assert (piece.piece, piece.kind, piece.new_combo_count) == ("G1", "candidate", 1)
        assert piece.example_cards == ["G1", "X"]
