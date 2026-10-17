"""Tests for the Phase 2 enabler floor: enabler-only cards in the pool and below the
utilization floor, and the minimum number of enablers per category."""

import logging
from typing import Any

import pytest

from mtg_combo_cube.ilp.enablers import EnablerTable, parse_enabler_table, resolve_enablers
from mtg_combo_cube.ilp.ilp_models import ENABLER_ONLY, CandidateCard, CardMixRules, ComboData
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.outcomes import parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import PayoffTable, parse_payoff_table, resolve_payoffs
from mtg_combo_cube.models import CardAttributes
from tests.unit.test_ilp_optimizer import build_candidate_cards

MANA = frozenset(["Infinite colored mana"])
OUTCOMES = parse_outcome_categories({"mana": ["infinite colored mana"]})


def combo(combo_id: str, cards: list[str], popularity: int = 100) -> ComboData:
    return ComboData(combo_id, frozenset(cards), [], popularity, features=MANA)


# A hub H with three partners (four cards, three combos), a two-card combo Q and a two-card
# combo D. The enablers Ritual and Petal are in no combo at all.
COMBOS = [
    *(combo(f"h{i}", ["H", f"P{i}"]) for i in range(1, 4)),
    combo("q", ["Q1", "Q2"]),
    combo("d", ["D1", "D2"], 1),
]
HUB = {"H", "P1", "P2", "P3"}
ENABLERS = {"Ritual", "Petal"}
NO_CARD_MIX = CardMixRules(0, 0, 0, 5, 0, 0, 0)


def enabler_table(cards: dict[str, list[str]], **bounds: dict[str, int]) -> EnablerTable:
    """A resolved enabler table naming the given cards per category, with optional bounds."""
    table: dict[str, dict[str, Any]] = {name: {"cards": names} for name, names in cards.items()}
    for key, by_category in bounds.items():
        for name, value in by_category.items():
            table[name][key] = value
    return resolve_enablers(parse_enabler_table(table), {})


def payoff_table(cards: dict[str, list[str]]) -> PayoffTable:
    return resolve_payoffs(
        parse_payoff_table({name: {"cards": names} for name, names in cards.items()}), {}, {}
    )


TABLE = enabler_table({"ritual": sorted(ENABLERS)})


def make_optimizer(
    combos: list[ComboData] = COMBOS,
    enablers: EnablerTable | None = TABLE,
    extra_cards: set[str] = ENABLERS,
    **kwargs: Any,
) -> ILPOptimizer:
    """An optimizer whose candidates are the combo cards plus the enabler-only extra cards."""
    candidate_cards = build_candidate_cards(combos)
    for name in sorted(extra_cards):
        candidate_cards[name] = CandidateCard(name, frozenset(), frozenset())
    settings: dict[str, Any] = {
        "staple_combos": 0,
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
        "outcome_categories": OUTCOMES,
        "min_outcome_combos": 0,
        "min_payoffs": 0,
        "payoff_share": 0,
        "enablers": enablers,
        # The default floor is sized for a full build; each test sets the one it exercises
        "min_enablers": 0,
        "enabler_share": 0,
    }
    settings.update(kwargs)
    return ILPOptimizer(combos=combos, candidate_cards=candidate_cards, **settings)


class TestEnablerShare:
    """The floors add up to a share of the cube, split evenly among the categories."""

    CARDS = {"a": ["A1"], "b": [f"B{i}" for i in range(1, 6)], "c": [f"C{i}" for i in range(1, 6)]}
    EXTRA = {card for names in CARDS.values() for card in names}

    def test_floors_split_evenly_up_to_what_a_category_has(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer = make_optimizer(
                enablers=enabler_table(self.CARDS),
                extra_cards=self.EXTRA,
                cube_size=30,
                min_enablers=1,
                enabler_share=0.3,
            )

        # 9 cards: 1 each, then one at a time to the category with the fewest (a is full
        # with its one card), up to the even cap of ceil(2 * 9 / 3) = 6
        assert optimizer.enabler_targets == {"a": 1, "b": 4, "c": 4}
        assert optimizer.enabler_floors == {"a": 1, "b": 4, "c": 4}
        assert (
            "enabler share 30% of 30 cards is 9 enabler cards, split evenly among the "
            "categories: a 1, b 4, c 4"
        ) in caplog.text

    def test_table_bounds_override_the_range(self):
        optimizer = make_optimizer(
            enablers=enabler_table(self.CARDS, max_enablers={"b": 2}, min_enablers={"c": 2}),
            extra_cards=self.EXTRA,
            cube_size=30,
            min_enablers=1,
            enabler_share=0.3,
        )

        # b stops at its own cap, c starts at its own minimum and takes the rest up to
        # the five cards it has; the one card left over is not forced anywhere
        assert optimizer.enabler_floors == {"a": 1, "b": 2, "c": 5}

    def test_a_category_short_of_its_minimum_is_lowered_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ):
        optimizer = make_optimizer(cube_size=20, min_enablers=5, enabler_share=0.3)

        assert optimizer.enabler_targets == {"ritual": 5}
        assert optimizer.enabler_floors == {"ritual": 2}
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer._check_enabler_pool()
        assert (
            "the pool has only 2 selectable enabler cards for ritual, below the minimum of 5; "
            "the floor is lowered to 2 (every such card must be in the cube)"
        ) in caplog.text

    def test_share_zero_keeps_the_minimum_alone(self):
        optimizer = make_optimizer(
            enablers=enabler_table({"b": self.CARDS["b"]}),
            extra_cards=set(self.CARDS["b"]),
            cube_size=30,
            min_enablers=2,
            enabler_share=0,
        )

        assert optimizer.enabler_floors == {"b": 2}
        assert optimizer.enabler_targets == {"b": 2}

    def test_the_share_is_enforced_and_reported(self):
        combos = [*COMBOS, combo("e", ["E1", "E2", "E3"], 1)]
        result = make_optimizer(
            combos, cube_size=10, min_utilization_floor=0, min_enablers=1, enabler_share=0.2
        ).solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert result.phase2_enabler_floors == {"ritual": 2}
        assert result.phase2_enabler_share == 0.2
        assert result.phase2_min_enablers == 1
        assert ENABLERS <= set(result.get_selected_card_names())

    def test_share_off_or_floor_off_reports_no_share(self):
        off = make_optimizer(min_enablers=1, enabler_share=0).solve_two_phase()
        assert off.phase2_enabler_share is None
        assert off.phase2_enabler_floors == {"ritual": 1}

        no_floor = make_optimizer(min_enablers=0, enabler_share=0.5).solve_two_phase()
        assert no_floor.phase2_enabler_share is None
        assert no_floor.phase2_enabler_floors is None

    @pytest.mark.parametrize("share", [1, 1.5, -0.1, float("nan")])
    def test_share_outside_zero_to_one_is_rejected(self, share: float):
        with pytest.raises(ValueError, match="enabler_share"):
            make_optimizer(enabler_share=share)

    def test_negative_floor_is_rejected(self):
        with pytest.raises(ValueError, match="min_enablers"):
            make_optimizer(min_enablers=-1)


class TestEnablerFloor:
    def test_floor_off_leaves_the_enablers_out(self):
        optimizer = make_optimizer()

        # With the floor off the cards in no combo are plain payoff-only cards, capped at 0
        assert optimizer.enabler_only_cards == frozenset()
        assert optimizer.payoff_only_cards == ENABLERS
        assert optimizer.enabler_cards == {"ritual": frozenset(ENABLERS)}
        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}
        assert result.phase2_min_enablers is None
        assert result.phase2_enabler_floors is None
        assert result.phase2_enabler_stats is not None
        assert result.phase2_enabler_stats.cards_per_category == {"ritual": 0}
        assert result.profile_data is not None
        assert "enabler_floor" not in result.profile_data["phase2"]["counts"]

    def test_floor_pulls_an_enabler_into_a_cube_of_combos(self):
        optimizer = make_optimizer(min_enablers=1)

        assert optimizer.enabler_only_cards == ENABLERS
        assert optimizer.payoff_only_cards == frozenset()
        assert optimizer.exempt_cards == ENABLERS
        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        selected = set(result.get_selected_card_names())
        # The enabler takes a slot from the Phase 1 cube, which costs one combo; the cap
        # allows one enabler-only card for the one floor
        enablers = selected & ENABLERS
        assert len(enablers) == 1
        assert "H" in selected
        assert result.combo_count == 3
        assert result.phase2_min_enablers == 1
        assert result.phase2_enabler_floors == {"ritual": 1}
        assert result.phase2_enabler_stats is not None
        assert result.phase2_enabler_stats.cards_per_category == {"ritual": 1}
        assert result.phase2_enabler_stats.cards == {
            "ritual": {enabler: ["card"] for enabler in enablers}
        }
        assert result.phase2_enabler_stats.enabler_only == sorted(enablers)
        # The utilization statistics describe the combo cards
        assert result.utilization_per_card is not None
        assert result.utilization_per_card[next(iter(enablers))] == 0
        assert result.phase2_utilization_stats is not None
        assert result.phase2_utilization_stats.min_utilization >= 1
        assert result.profile_data is not None
        assert result.profile_data["phase2"]["counts"]["enabler_floor"] == 1
        assert result.profile_data["phase2"]["counts"]["enabler_only_cap"] == 1
        assert result.profile_data["phase2"]["counts"]["utilization_floor"] == 8
        assert "warm_start_repair" in result.profile_data["phase2"]["timings"]
        # The card data names the reason
        assert result.card_inclusion is not None
        by_card = {card.card: card for card in result.card_inclusion}
        enabler = next(iter(enablers))
        assert by_card[enabler].reason == ENABLER_ONLY
        assert by_card[enabler].enabler_categories == ["ritual"]
        assert by_card["H"].enabler_categories == []

    def test_enabler_below_the_utilization_floor_is_selectable_and_its_combo_scores(self):
        # Two triangles (every card in two combos) and Q1, in one combo with A: under a
        # utilization floor of 2 Q1 could never be selected; as an enabler it is exempt,
        # and the combo it completes still counts
        combos = [
            *(combo(key, [key[0].upper(), key[1].upper()]) for key in ("ab", "bc", "ca")),
            *(combo(key, [key[0].upper(), key[1].upper()]) for key in ("de", "ef", "fd")),
            combo("q", ["Q1", "A"], 1),
        ]
        optimizer = make_optimizer(
            combos,
            enablers=enabler_table({"ritual": ["Q1", "Petal"]}),
            extra_cards={"Petal"},
            cube_size=7,
            min_utilization_floor=2,
            min_enablers=1,
        )

        assert optimizer.enabler_only_cards == {"Q1", "Petal"}
        assert optimizer.enabler_cards == {"ritual": frozenset(["Q1", "Petal"])}
        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == {"A", "B", "C", "D", "E", "F", "Q1"}
        assert result.combo_count == 7
        assert result.utilization_per_card is not None
        assert result.utilization_per_card["Q1"] == 1
        assert result.phase2_utilization_stats is not None
        assert result.phase2_utilization_stats.min_utilization == 2  # Q1 is left out
        assert result.phase2_enabler_stats is not None
        assert result.phase2_enabler_stats.enabler_only == ["Q1"]
        assert result.card_inclusion is not None
        by_card = {card.card: card for card in result.card_inclusion}
        assert by_card["Q1"].reason == "essential"
        assert by_card["Q1"].enabler_categories == ["ritual"]
        assert result.profile_data is not None
        # The floor constraint covers the six triangle cards
        assert result.profile_data["phase2"]["counts"]["utilization_floor"] == 6

        # With the floor off Q1 is excluded by the utilization floor as any other card
        off = make_optimizer(
            combos,
            enablers=enabler_table({"ritual": ["Q1", "Petal"]}),
            extra_cards={"Petal"},
            cube_size=6,
            min_utilization_floor=2,
        )
        assert off.enabler_only_cards == frozenset()
        # Petal, in no combo, is a plain payoff-only card then (exempt, capped at 0)
        assert off.payoff_only_cards == {"Petal"}
        assert off.enabler_cards == {"ritual": frozenset(["Petal"])}
        assert "Q1" not in off.solve_two_phase().get_selected_card_names()

    def test_an_enabler_at_the_utilization_floor_is_an_ordinary_combo_piece(self):
        # Q1 is an enabler too: the Phase 1 cube already holds one and needs no repair
        optimizer = make_optimizer(
            enablers=enabler_table({"ritual": ["Q1", "Ritual"]}), min_enablers=1
        )

        assert optimizer.enabler_only_cards == {"Ritual"}
        assert optimizer.enabler_cards == {"ritual": frozenset(["Q1", "Ritual"])}
        result = optimizer.solve_two_phase(profile=True)

        assert result.is_multi_objective
        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}
        assert result.phase2_enabler_stats is not None
        assert result.phase2_enabler_stats.cards == {"ritual": {"Q1": ["card"]}}
        assert result.phase2_enabler_stats.enabler_only == []
        assert result.profile_data is not None
        assert "warm_start_repair" not in result.profile_data["phase2"]["timings"]

    @pytest.mark.parametrize("objective", ["mad", "minmax"])
    def test_enabler_only_cards_are_capped_at_the_floors(self, objective: str):
        # Ten cards: the eight combo cards hold the score; "mad" and "minmax" would fill
        # the two spare slots with exempt cards, so without a floor none may be selected
        # and with a floor of 1 one
        combos = [*COMBOS, combo("e", ["E1", "E2", "E3"], 1)]
        off = make_optimizer(
            combos, cube_size=10, min_utilization_floor=0, phase2_objective=objective
        ).solve_two_phase(profile=True)

        assert off.is_multi_objective
        assert not ENABLERS & set(off.get_selected_card_names())

        one = make_optimizer(
            combos,
            cube_size=10,
            min_utilization_floor=0,
            phase2_objective=objective,
            min_enablers=1,
        ).solve_two_phase(profile=True)

        assert one.is_multi_objective
        assert len(ENABLERS & set(one.get_selected_card_names())) == 1
        assert one.profile_data is not None
        assert one.profile_data["phase2"]["counts"]["enabler_only_cap"] == 1

    @pytest.mark.parametrize("objective", sorted(ILPOptimizer._PHASE2_OBJECTIVES))
    def test_every_objective_accepts_enabler_only_cards(self, objective: str):
        result = make_optimizer(min_enablers=1, phase2_objective=objective).solve_two_phase()

        assert result.is_multi_objective
        assert ENABLERS & set(result.get_selected_card_names())
        assert result.phase2_enabler_floors == {"ritual": 1}

    def test_enabler_only_cards_count_for_the_card_mix(self):
        # Ritual is colorless; with a colorless cap of 0 cards the enabler must be Petal
        attributes = {
            card: CardAttributes("W", "Creature", 2) for card in build_candidate_cards(COMBOS)
        }
        attributes["Ritual"] = CardAttributes("", "Artifact", 0)
        attributes["Petal"] = CardAttributes("B", "Instant", 1)
        result = make_optimizer(
            min_enablers=1,
            card_attributes=attributes,
            card_mix=CardMixRules(0, 0.1, 0, 5, 0, 0, 0),  # floor(0.1 x 6) = 0 colorless cards
        ).solve_two_phase()

        assert result.is_multi_objective
        assert "Petal" in result.get_selected_card_names()
        assert "Ritual" not in result.get_selected_card_names()

    def test_a_card_in_both_tables_is_payoff_only(self):
        payoffs = payoff_table({"mana": ["Ballista"]})
        enablers = enabler_table({"ritual": ["Ballista", "Ritual"]})
        optimizer = make_optimizer(
            payoffs=payoffs,
            enablers=enablers,
            extra_cards={"Ballista", "Ritual"},
            min_payoffs=1,
            min_enablers=1,
        )

        assert optimizer.payoff_only_cards == {"Ballista"}
        assert optimizer.enabler_only_cards == {"Ritual"}
        assert optimizer.exempt_cards == {"Ballista", "Ritual"}
        # Ballista counts for both floors, so one card can satisfy them
        assert optimizer.payoff_cards == {"mana": frozenset(["Ballista"])}
        assert optimizer.enabler_cards == {"ritual": frozenset(["Ballista", "Ritual"])}
        result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        selected = set(result.get_selected_card_names())
        assert "Ballista" in selected
        assert result.combo_count == 3
        assert result.card_inclusion is not None
        by_card = {card.card: card for card in result.card_inclusion}
        assert by_card["Ballista"].reason == "payoff_only"
        assert by_card["Ballista"].payoff_categories == ["mana"]
        assert by_card["Ballista"].enabler_categories == ["ritual"]

    def test_category_without_selectable_cards_gets_no_floor(
        self, caplog: pytest.LogCaptureFixture
    ):
        enablers = enabler_table({"ritual": ["Ritual"], "wheel": ["Not A Card"]})
        optimizer = make_optimizer(enablers=enablers, extra_cards={"Ritual"}, min_enablers=1)

        assert optimizer.enabler_cards == {"ritual": frozenset(["Ritual"]), "wheel": frozenset()}
        assert optimizer.enabler_floors == {"ritual": 1}
        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert "Ritual" in result.get_selected_card_names()
        assert "the pool has no selectable enabler cards for wheel; no enabler floor" in caplog.text
        assert result.phase2_enabler_stats is not None
        assert result.phase2_enabler_stats.cards_per_category == {"ritual": 1, "wheel": 0}

    def test_min_enablers_is_recorded_only_with_a_floor(self):
        result = make_optimizer(
            enablers=enabler_table({"wheel": ["Not A Card"]}), extra_cards=set(), min_enablers=2
        ).solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_min_enablers is None
        assert result.phase2_enabler_floors is None

    def test_impossible_floor_falls_back_with_the_shortfall_named(
        self, caplog: pytest.LogCaptureFixture
    ):
        # The only enabler is colorless and the colorless cap allows none
        attributes = {
            card: CardAttributes("W", "Creature", 2) for card in build_candidate_cards(COMBOS)
        }
        attributes["Ritual"] = CardAttributes("", "Artifact", 0)
        optimizer = make_optimizer(
            enablers=enabler_table({"ritual": ["Ritual"]}),
            extra_cards={"Ritual"},
            min_enablers=1,
            card_attributes=attributes,
            card_mix=CardMixRules(0, 0.1, 0, 5, 0, 0, 0),
        )

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.phase2_fell_back
        assert result.phase2_enabler_floors == {"ritual": 1}
        assert (
            "still breaks 1 enabler floor constraints (enablers below their floor: ritual 0 < 1)"
        ) in caplog.text

    def test_without_a_table_a_requested_floor_warns(self, caplog: pytest.LogCaptureFixture):
        optimizer = make_optimizer(enablers=None, extra_cards=set(), min_enablers=1)

        with caplog.at_level(logging.WARNING, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            result = optimizer.solve_two_phase()

        assert result.is_multi_objective
        assert result.phase2_enabler_floors is None
        assert result.phase1_enabler_stats is None
        assert result.phase2_enabler_stats is None
        assert "no enabler table; the enabler floor is not enforced" in caplog.text

    @pytest.mark.parametrize("minimum", [0, ILPOptimizer.DEFAULT_MIN_ENABLERS])
    def test_without_a_table_the_default_or_zero_floor_is_quiet(
        self, caplog: pytest.LogCaptureFixture, minimum: int
    ):
        optimizer = make_optimizer(enablers=None, extra_cards=set(), min_enablers=minimum)

        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_optimizer"):
            optimizer.solve_two_phase()

        records = [r for r in caplog.records if "no enabler table" in r.getMessage()]
        assert records and all(r.levelno == logging.INFO for r in records)

    def test_phase1_reports_the_enabler_statistics(self):
        result = make_optimizer(min_enablers=1).solve()

        assert result.phase1_enabler_stats is not None
        assert result.phase1_enabler_stats.cards_per_category == {"ritual": 0}
        assert set(result.get_selected_card_names()) == HUB | {"Q1", "Q2"}


class TestWarmStart:
    def test_enabler_only_cards_are_exempt_from_the_floor_checks(self):
        optimizer = make_optimizer(min_enablers=1)
        cube = optimizer._warm_start_for(HUB | ENABLERS)

        assert set(cube.utilization) == HUB
        assert optimizer._satisfies_window_and_floor(cube, optimizer._combo_score(cube.combo_ids))
        below = optimizer._warm_start_for(HUB | {"Q1", "Ritual"})
        assert not optimizer._satisfies_window_and_floor(
            below, optimizer._combo_score(below.combo_ids)
        )


class TestViolations:
    def test_floor_violations_and_shortfalls(self):
        optimizer = make_optimizer(min_enablers=2)

        assert optimizer.enabler_floors == {"ritual": 2}
        assert optimizer._enabler_only_cap() == 2
        assert optimizer._enabler_violations(HUB | {"Q1", "Q2"}) == 1
        assert optimizer._enabler_shortfalls(HUB | {"Ritual"}) == {"ritual": (1, 2)}
        assert optimizer._describe_enabler_shortfalls(HUB | {"Ritual"}) == "ritual 1 < 2"
        assert optimizer._enabler_violations(HUB | ENABLERS) == 0
        assert optimizer._describe_shortfalls(HUB) == " (enablers below their floor: ritual 0 < 2)"

    def test_the_cap_is_a_violation(self):
        optimizer = make_optimizer(min_enablers=1)

        assert optimizer._enabler_only_cap() == 1
        assert optimizer._enabler_violations(HUB | {"Ritual"}) == 0
        assert optimizer._enabler_violations(HUB | ENABLERS) == 1

    def test_rule_off_has_no_violations(self):
        assert make_optimizer()._enabler_violations(HUB) == 0
        assert make_optimizer(enablers=None, min_enablers=2)._enabler_violations(HUB) == 0

    def test_floor_is_a_cube_rule(self):
        labels = [rule.label for rule in make_optimizer()._cube_rules()]

        assert labels[-3] == "enabler floor"
        violations = make_optimizer(min_enablers=1)._cube_rule_violations(HUB | {"Q1", "Q2"})
        assert violations["enabler floor"] == 1
