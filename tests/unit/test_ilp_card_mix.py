"""Tests for the Phase 2 card mix rules: share caps on multicolor, colorless, expensive and
creature cards, the floor on instants and sorceries, and the mono-colored balance."""

import logging
from fractions import Fraction
from typing import Any

import pytest

from mtg_combo_cube.ilp.ilp_models import (
    CardMixRuleError,
    CardMixRules,
    ComboData,
    OptimizationResult,
    hundredths,
)
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.models import CardAttributes
from tests.unit.test_ilp_optimizer import build_candidate_cards

# Every card mix rule off, for tests of other Phase 2 rules that pass card attributes
NO_CARD_MIX = CardMixRules(
    max_multicolor_share=0,
    max_colorless_share=0,
    max_expensive_share=0,
    max_creature_share=0,
    min_spell_share=0,
    mono_color_ratio=0,
)

# Four two-card combos on disjoint cards; a six-card cube holds three of them, and Phase 1
# takes the three most popular: the gold creatures, the artifacts and the big creatures,
# leaving the two spells out.
COMBOS = [
    ComboData("gold", frozenset(["X1", "X2"]), [], 100),
    ComboData("rocks", frozenset(["R1", "R2"]), [], 90),
    ComboData("big", frozenset(["B1", "B2"]), [], 80),
    ComboData("spells", frozenset(["S1", "S2"]), [], 10),
]
ATTRIBUTES = {
    "X1": CardAttributes("WU", "Creature — Human", 2),
    "X2": CardAttributes("WU", "Legendary Creature — Human", 2),
    "R1": CardAttributes("", "Artifact", 1),
    "R2": CardAttributes("", "Artifact", 1),
    "B1": CardAttributes("W", "Creature — Giant", 6),
    "B2": CardAttributes("W", "Creature — Giant", 7),
    "S1": CardAttributes("U", "Instant", 2),
    "S2": CardAttributes("U", "Sorcery", 3),
}
PHASE1_CUBE = {"X1", "X2", "R1", "R2", "B1", "B2"}
# Twelve more white creatures in unpopular pairs, to make a pool of twenty cards
FILLER = [ComboData(f"f{i}", frozenset([f"F{2 * i - 1}", f"F{2 * i}"]), [], 1) for i in range(1, 7)]
FILLER_ATTRIBUTES = {
    card: CardAttributes("W", "Creature \u2014 Soldier", 2)
    for combo in FILLER
    for card in combo.required_cards
}


def make_optimizer(combos: list[ComboData] = COMBOS, **kwargs: Any) -> ILPOptimizer:
    settings: dict[str, Any] = {
        "cube_size": 6,
        "time_limit_seconds": 30,
        "combo_tolerance": 0.5,
        "gap_limit": 0,
        "num_workers": 1,
        "min_utilization_floor": 0,
        "max_color_ratio": 0,
        "min_pair_combos": 0,
        "min_mono_combos": 0,
        "max_wide_combo_share": 0,
        "phase2_objective": "minmax",
        "card_attributes": ATTRIBUTES,
        "card_mix": NO_CARD_MIX,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=build_candidate_cards(combos), **settings)


def rules(**kwargs: Any) -> CardMixRules:
    """NO_CARD_MIX with the given rules switched on."""
    settings = {
        "max_multicolor_share": 0,
        "max_colorless_share": 0,
        "max_expensive_share": 0,
        "max_creature_share": 0,
        "min_spell_share": 0,
        "mono_color_ratio": 0,
    }
    settings.update(kwargs)
    return CardMixRules(**settings)


def cube(result: OptimizationResult) -> set[str]:
    return set(result.get_selected_card_names())


class TestCardMixRules:
    def test_defaults_enable_every_share_rule_but_the_mono_balance(self):
        assert CardMixRules().enabled() == {
            "max_multicolor_share": 0.15,
            "max_colorless_share": 0.25,
            "max_expensive_share": 0.2,
            "expensive_mana_value": 5,
            "max_creature_share": 0.6,
            "min_spell_share": 0.05,
        }

    def test_zero_and_a_cap_of_one_are_disabled(self):
        assert NO_CARD_MIX.enabled() == {}
        assert rules(max_creature_share=1, max_colorless_share=0.999).enabled() == {}
        # A floor of 1 is a rule: every card must be a spell
        assert rules(min_spell_share=1).enabled() == {"min_spell_share": 1}
        assert rules(mono_color_ratio=1.5).enabled() == {"mono_color_ratio": 1.5}

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_multicolor_share": -0.1},
            {"max_colorless_share": 1.5},
            {"max_expensive_share": 2},
            {"max_creature_share": -1},
            {"min_spell_share": 1.01},
            {"expensive_mana_value": -1},
            {"expensive_mana_value": 0},
            {"mono_color_ratio": 0.5},
            {"max_creature_share": float("nan")},
            {"mono_color_ratio": float("nan")},
            {"expensive_mana_value": float("inf")},
            {"min_spell_share": 0.004},  # rounds to 0 in hundredths
        ],
    )
    def test_invalid_values_are_rejected(self, kwargs: dict[str, float]):
        name = next(iter(kwargs))
        with pytest.raises(CardMixRuleError, match=name) as error:
            CardMixRules(**kwargs)
        assert error.value.field == name

    def test_hundredths_rounds(self):
        assert hundredths(0.333) == Fraction(33, 100)
        assert hundredths(0.15) == Fraction(3, 20)
        assert hundredths(0.125) == Fraction(12, 100)  # round half to even
        assert hundredths(2.0) == 2
        assert hundredths(0.004) == 0


class TestShareCaps:
    def test_phase1_takes_the_most_popular_combos(self):
        result = make_optimizer().solve()

        assert cube(result) == PHASE1_CUBE

    def test_rules_off_keep_the_phase1_cube(self):
        result = make_optimizer().solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert cube(result) == PHASE1_CUBE
        assert result.phase2_card_mix == NO_CARD_MIX
        assert result.profile_data is not None
        assert "multicolor_cap" not in result.profile_data["phase2"]["counts"]

    def test_multicolor_cap_swaps_out_the_gold_combo(self):
        # 0.2 of six cards: at most one multicolor card, so the gold pair cannot stay
        optimizer = make_optimizer(card_mix=rules(max_multicolor_share=0.2))

        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert cube(result) == {"R1", "R2", "B1", "B2", "S1", "S2"}
        assert result.combo_count == 3
        assert result.phase2_card_mix == rules(max_multicolor_share=0.2)
        # The Phase 1 cube breaks the cap, so the warm start was repaired
        assert result.profile_data is not None
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]
        assert result.profile_data["phase2"]["counts"]["multicolor_cap"] == 1

    def test_colorless_cap_swaps_out_the_artifacts(self):
        result = make_optimizer(card_mix=rules(max_colorless_share=0.2)).solve_two_phase()

        assert result.is_multi_objective
        assert cube(result) == {"X1", "X2", "B1", "B2", "S1", "S2"}

    def test_lands_do_not_count_toward_the_colorless_cap(self):
        attributes = {**ATTRIBUTES, "R1": CardAttributes("", "Land", 0)}
        optimizer = make_optimizer(
            card_attributes=attributes, card_mix=rules(max_colorless_share=0.2)
        )

        result = optimizer.solve_two_phase()

        # Only R2 counts, and one colorless card is allowed: the artifact pair stays
        assert optimizer.colorless_cards == {"R2"}
        assert result.is_multi_objective
        assert cube(result) == PHASE1_CUBE

    def test_expensive_cap_swaps_out_the_big_creatures(self):
        result = make_optimizer(card_mix=rules(max_expensive_share=0.2)).solve_two_phase()

        assert result.is_multi_objective
        assert cube(result) == {"X1", "X2", "R1", "R2", "S1", "S2"}

    def test_expensive_threshold_decides_which_cards_count(self):
        # Only B2 reaches a mana value of 7, and one expensive card is allowed
        optimizer = make_optimizer(card_mix=rules(max_expensive_share=0.2, expensive_mana_value=7))

        result = optimizer.solve_two_phase()

        assert optimizer.expensive_cards == {"B2"}
        assert result.is_multi_objective
        assert cube(result) == PHASE1_CUBE

    def test_creature_cap_leaves_at_most_three_creatures(self):
        # Four of the six Phase 1 cards are creatures; 0.5 allows three
        optimizer = make_optimizer(card_mix=rules(max_creature_share=0.5))

        result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert {"R1", "R2", "S1", "S2"} <= cube(result)
        assert len(cube(result) & set(optimizer.creature_cards)) <= 3
        assert result.combo_count == 3

    def test_spell_floor_brings_the_spells_in(self):
        # 0.3 of six cards, rounded up: two instants or sorceries
        result = make_optimizer(card_mix=rules(min_spell_share=0.3)).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert {"S1", "S2"} <= cube(result)
        assert result.combo_count == 3
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["spell_floor"] == 1

    def test_a_cap_of_one_is_no_cap(self):
        optimizer = make_optimizer(card_mix=rules(max_creature_share=1))

        assert optimizer._cap_count(1) is None
        result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert cube(result) == PHASE1_CUBE

    def test_share_rounds_to_hundredths(self):
        optimizer = make_optimizer()

        # 0.333 rounds to 0.33: 1.98 of six cards, one for a cap and two for a floor
        assert optimizer._cap_count(0.333) == 1
        assert optimizer._cap_count(0.5) == 3
        assert optimizer._floor_count(0.333) == 2
        assert optimizer._floor_count(0.34) == 3
        assert optimizer._floor_count(0) is None

    def test_limits_are_recorded(self):
        optimizer = make_optimizer(
            card_mix=rules(max_multicolor_share=0.2, max_creature_share=0.5, min_spell_share=0.3)
        )

        result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_card_mix_limits == {
            "multicolor_cap": 1,
            "creature_cap": 3,
            "spell_floor": 2,
        }
        assert result.phase2_unknown_candidate_cards == 0

    def test_colored_lands_are_left_out_of_every_rule(self):
        attributes = {
            **ATTRIBUTES,
            "X1": CardAttributes("WU", "Land", 0),
            "B2": CardAttributes("W", "Land \u2014 Forest", 0),
        }
        optimizer = make_optimizer(card_attributes=attributes)

        assert optimizer.multicolor_cards == {"X2"}
        assert optimizer.expensive_cards == {"B1"}
        assert optimizer.creature_cards == {"X2", "B1"}


class TestMissingCardData:
    def test_cards_without_attributes_count_as_colorless_and_typeless(
        self, caplog: pytest.LogCaptureFixture
    ):
        # One of twenty cards (5%, the limit) loses its data: B2 no longer counts as a
        # creature or as expensive, and counts as colorless alongside the artifacts
        combos = [*COMBOS, *FILLER]
        attributes = {**ATTRIBUTES, **FILLER_ATTRIBUTES}
        del attributes["B2"]
        optimizer = make_optimizer(
            combos,
            card_attributes=attributes,
            card_mix=rules(max_colorless_share=0.4, max_creature_share=0.5),
        )

        assert optimizer.card_mix_active
        assert optimizer.colorless_cards == {"B2", "R1", "R2"}
        assert "B2" not in optimizer.creature_cards
        assert optimizer.expensive_cards == {"B1"}
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        # 0.4 of six cards: two colorless, so the artifacts or the big creatures go
        assert result.is_multi_objective
        assert not ({"R1", "R2", "B2"} <= cube(result))
        assert result.phase2_unknown_candidate_cards == 1
        assert result.phase2_card_mix_limits == {"colorless_cap": 2, "creature_cap": 3}
        assert (
            "1 of 20 candidate cards have no Scryfall data; they count as colorless, typeless "
            "and mana value 0"
        ) in caplog.text

    def test_too_many_unknown_cards_disable_the_rules(self, caplog: pytest.LogCaptureFixture):
        # Two of eight cards (25%) without data: the pool is too uncertain for the rules
        attributes = {card: value for card, value in ATTRIBUTES.items() if card[0] != "B"}
        optimizer = make_optimizer(
            card_attributes=attributes, card_mix=rules(max_colorless_share=0.4)
        )

        assert not optimizer.card_mix_active
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert cube(result) == PHASE1_CUBE
        assert result.phase2_card_mix is None
        assert result.phase2_card_mix_limits is None
        assert result.phase2_unknown_candidate_cards == 2
        assert (
            "2 of 8 candidate cards (25%) have no Scryfall data, more than 5%; the card mix "
            "rules are not enforced"
        ) in caplog.text
        assert result.profile_data is not None
        assert "colorless_cap" not in result.profile_data["phase2"]["counts"]

    def test_no_card_data_disables_the_rules(self, caplog: pytest.LogCaptureFixture):
        optimizer = make_optimizer(card_attributes=None, card_mix=CardMixRules())

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert cube(result) == PHASE1_CUBE
        assert result.phase2_card_mix is None
        assert result.phase2_unknown_candidate_cards is None
        assert "no card data from Scryfall; the card mix rules are not enforced" in caplog.text
        assert result.profile_data is not None
        assert "creature_cap" not in result.profile_data["phase2"]["counts"]

    def test_no_warning_when_every_rule_is_off(self, caplog: pytest.LogCaptureFixture):
        optimizer = make_optimizer(card_attributes=None)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer.solve_two_phase()

        assert "card mix rules" not in caplog.text


class TestFallback:
    def test_unmeetable_floor_falls_back_to_phase1(self, caplog: pytest.LogCaptureFixture):
        # 0.6 of six cards, rounded up: four spells, but the pool has two
        optimizer = make_optimizer(card_mix=rules(min_spell_share=0.6))

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert cube(result) == PHASE1_CUBE
        assert result.phase2_card_mix == rules(min_spell_share=0.6)
        assert "still breaks 1 spell floor constraints" in caplog.text

    def test_floor_without_candidates_is_named(self, caplog: pytest.LogCaptureFixture):
        attributes = {**ATTRIBUTES, "S1": CardAttributes("U", "Enchantment", 2)}
        attributes["S2"] = CardAttributes("U", "Enchantment", 3)
        optimizer = make_optimizer(card_attributes=attributes, card_mix=rules(min_spell_share=0.1))

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert (
            "the pool has only 0 instants and sorceries, below the spell floor of 1; Phase 2 "
            "cannot meet it"
        ) in caplog.text

    def test_pool_check_names_a_floor_and_a_cap_the_pool_cannot_meet(
        self, caplog: pytest.LogCaptureFixture
    ):
        # Two spells in the pool against a floor of four; six of the eight cards are
        # creatures or artifacts, so a creature cap of one leaves too few other cards
        optimizer = make_optimizer(
            card_mix=rules(min_spell_share=0.6, max_creature_share=0.2),
        )

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer._check_card_mix_pool()

        assert (
            "the pool has only 2 instants and sorceries, below the spell floor of 4"
        ) in caplog.text
        assert (
            "the pool has only 4 cards outside the creature cap, fewer than the 5 the cube "
            "needs beside the 1 it allows"
        ) in caplog.text

    def test_pool_check_is_quiet_when_the_pool_suffices(self, caplog: pytest.LogCaptureFixture):
        optimizer = make_optimizer(card_mix=rules(min_spell_share=0.3, max_creature_share=0.5))

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer._check_card_mix_pool()

        assert caplog.text == ""


class TestViolations:
    def test_share_cap_and_floor_violations(self):
        optimizer = make_optimizer(
            card_mix=rules(max_multicolor_share=0.2, max_colorless_share=0.5, min_spell_share=0.3)
        )

        violations = optimizer._cube_rule_violations(PHASE1_CUBE)

        assert violations["multicolor cap"] == 1  # two multicolor cards, one allowed
        assert violations["colorless cap"] == 0  # two colorless cards, three allowed
        assert violations["spell floor"] == 1  # no spell, two required
        assert violations["expensive cap"] == 0  # the rule is off
        assert optimizer._cube_rule_violations(
            {"R1", "R2", "B1", "B2", "S1", "S2"}
        ) == dict.fromkeys(violations, 0)

    def test_rules_off_have_no_violations(self):
        optimizer = make_optimizer()

        assert not any(optimizer._cube_rule_violations(PHASE1_CUBE).values())

    def test_every_card_mix_rule_is_a_cube_rule(self):
        labels = [rule.label for rule in make_optimizer()._cube_rules()]

        assert labels[-6:] == [
            "multicolor cap",
            "colorless cap",
            "expensive cap",
            "creature cap",
            "spell floor",
            "mono color balance",
        ]


# One combo per mono color on mono-colored cards, and a white triangle that makes white the
# largest color. W4 is a five-color card: it counts for the color balance but not as a
# mono-colored white card.
MONO_COMBOS = [
    ComboData("w12", frozenset(["W1", "W2"]), [], 10),
    ComboData("w23", frozenset(["W2", "W3"]), [], 10),
    ComboData("w13", frozenset(["W1", "W3"]), [], 10),
    ComboData("w4", frozenset(["W4", "W1"]), [], 10),
    *(ComboData(c.lower(), frozenset([f"{c}1", f"{c}2"]), [], 10) for c in "UBRG"),
]
MONO_ATTRIBUTES = {
    card: CardAttributes("WUBRG" if card == "W4" else card[0])
    for combo in MONO_COMBOS
    for card in combo.required_cards
}


class TestMonoColorBalance:
    def test_mono_counts_leave_out_multicolor_cards(self):
        optimizer = make_optimizer(
            MONO_COMBOS, card_attributes=MONO_ATTRIBUTES, card_mix=rules(mono_color_ratio=2)
        )
        cards = ["W1", "W2", "W3", "W4", "U1", "B1", "R1", "G1"]

        assert optimizer._mono_cards_of_color("W", cards) == ["W1", "W2", "W3"]
        # A white land is not a mono-colored white card for the balance
        with_land = make_optimizer(
            MONO_COMBOS,
            card_attributes={**MONO_ATTRIBUTES, "W3": CardAttributes("W", "Land")},
            card_mix=rules(mono_color_ratio=2),
        )
        assert with_land._mono_cards_of_color("W", cards) == ["W1", "W2"]
        # Three mono-white cards against one of each other color: four pairs over 2 x 1
        assert optimizer._mono_color_violations(cards) == 4
        assert optimizer._mono_color_violations(["W1", "W2", "U1", "B1", "R1", "G1"]) == 0

    def test_ratio_limits_the_mono_colored_cards_of_a_color(self):
        optimizer = make_optimizer(
            MONO_COMBOS,
            cube_size=10,
            card_attributes=MONO_ATTRIBUTES,
            card_mix=rules(mono_color_ratio=2),
        )

        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        # Phase 1 takes all four white cards (mono-white 3 against 1 for some other color)
        assert len({"W1", "W2", "W3"} & cube(result)) <= 2
        assert optimizer._mono_color_violations(cube(result)) == 0
        assert result.profile_data is not None
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]
        assert result.profile_data["phase2"]["counts"]["mono_color_balance"] == 20

    def test_ratio_zero_disables_the_balance(self):
        # Holding the Phase 1 count (seven combos in ten cards) needs all four white cards
        optimizer = make_optimizer(
            MONO_COMBOS, cube_size=10, combo_tolerance=0, card_attributes=MONO_ATTRIBUTES
        )

        result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert optimizer._mono_color_ratio() is None
        assert {"W1", "W2", "W3", "W4"} <= cube(result)
        assert optimizer._mono_color_violations(cube(result)) == 0
