"""Tests for the "essential" Phase 2 objective (the default), the measure it works on, and
the warm-start cube that hints the reference solve (docs/plans/balancing-plan.md).

As for the other objectives, the small fixed instance of the characterization tests is
solved to proven optimality (one worker, no gap limit) and compared with a brute-force
enumeration of every cube; a grouped variant of the instance exercises the group terms.
"""

import itertools
import logging
import math
import statistics
from collections.abc import Collection
from typing import Any

import pytest

from mtg_combo_cube.ilp.cube_evaluation import (
    card_inclusion,
    card_utilization,
    completable_combo_ids,
)
from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData, OptimizationResult
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer, _WarmStart
from tests.unit.test_cube_evaluation import make_combo
from tests.unit.test_ilp_optimizer import build_candidate_cards
from tests.unit.test_ilp_optimizer_characterization import (
    build_instance,
    make_optimizer,
    phase2_counts,
    phase_profile,
)
from tests.unit.test_ilp_phase2_objectives import overage, versatility_bonus

# (cube_size, floor, tolerance) cases with a feasible Phase 2 on each instance (with the
# groups, no cube at the Phase 1 score has every card in two variants)
CASES = [(8, 0, 0), (9, 2, 0), (10, 2, 0), (8, 2, 0.25), (9, 0, 0.25), (10, 2, 0.25)]
GROUPED_CASES = [(8, 0, 0), (8, 2, 0.25), (9, 0, 0.25), (10, 2, 0.25)]

# The groups of the grouped instance: the hub's pairs two by two, each triangle as one
GROUPS = {
    "c01": "hub1",
    "c02": "hub1",
    "c03": "hub2",
    "c04": "hub2",
    "c06": "tri1",
    "c07": "tri1",
    "c08": "tri1",
    "c09": "tri2",
    "c10": "tri2",
    "c11": "tri2",
}


def build_grouped_instance() -> list[ComboData]:
    """The characterization instance with some variants grouped into combos."""
    combos = build_instance()
    for combo in combos:
        combo.group_key = GROUPS.get(combo.id, combo.id)
    return combos


def make_grouped_optimizer(cube_size: int, **kwargs: Any) -> ILPOptimizer:
    combos = build_grouped_instance()
    settings: dict[str, Any] = {
        "staple_combos": 0,
        "time_limit_seconds": 30,
        "combo_tolerance": 0,
        "gap_limit": 0,
        "num_workers": 1,
    }
    settings.update(kwargs)
    return ILPOptimizer(
        combos=combos,
        candidate_cards=build_candidate_cards(combos),
        cube_size=cube_size,
        **settings,
    )


OPTIMIZERS = {"plain": make_optimizer, "grouped": make_grouped_optimizer}
INSTANCE_CASES = [("plain", *case) for case in CASES] + [
    ("grouped", *case) for case in GROUPED_CASES
]


def tiered_overage(values: Collection[int], cap: int) -> int:
    return sum(overage(values, cap * multiple) for multiple in (1, 2, 4))


def result_essential(optimizer: ILPOptimizer, result: OptimizationResult) -> dict[str, int]:
    """The essential counts of a result's cube, as the objective measures them."""
    return optimizer._essential_counts_of(
        result.get_selected_card_names(), result.completable_combo_ids
    )


def brute_force_minimum(
    optimizer: ILPOptimizer, cube_size: int, floor: int, cap: int
) -> int | None:
    """
    The smallest tiered essential overage over every cube Phase 2 accepts, by enumeration:
    inside the combo score window of the Phase 1 cube (the reference, as no cube rule is
    on) and meeting the utilization floor.
    """
    combos = optimizer.combos
    phase1 = optimizer.solve()
    low, high = optimizer._combo_score_window(optimizer._combo_score(phase1.completable_combo_ids))
    best: int | None = None
    for cube in itertools.combinations(sorted(optimizer.all_cards), cube_size):
        completed = completable_combo_ids(cube, combos)
        if not low <= optimizer._combo_score(completed) <= high:
            continue
        if floor and min(card_utilization(cube, combos).values()) < floor:
            continue
        value = tiered_overage(optimizer._essential_counts_of(cube, completed).values(), cap)
        if best is None or value < best:
            best = value
    return best


class TestEssentialMeasure:
    """_essential_counts_of: the completed combos whose every completed variant names the card."""

    COMBOS = [
        make_combo("h1", ["H", "P1"], group="big"),
        make_combo("h2", ["H"], [["S1", "S2"]], group="big"),
        make_combo("ab", ["A", "B"]),
        make_combo("t", ["T"], [["S1", "S2"]]),
        make_combo("x", ["A", "Z"]),  # not completed: Z is not in the cube
    ]
    CUBE = ["H", "P1", "S1", "A", "B", "T", "F"]

    def optimizer(self) -> ILPOptimizer:
        return ILPOptimizer(
            staple_combos=0,
            combos=self.COMBOS,
            candidate_cards=build_candidate_cards(self.COMBOS),
            cube_size=len(self.CUBE),
        )

    def test_counts(self):
        completed = completable_combo_ids(self.CUBE, self.COMBOS)

        counts = self.optimizer()._essential_counts_of(self.CUBE, completed)

        # H is in both variants of "big"; P1 and S1 each have a variant without them; the
        # single-variant combos need both their pieces; S1 fills t's only pool but is no piece
        assert counts == {"H": 1, "P1": 0, "S1": 0, "A": 1, "B": 1, "T": 1, "F": 0}

    def test_agrees_with_card_inclusion_except_for_a_sole_filler(self):
        completed = completable_combo_ids(self.CUBE, self.COMBOS)
        counts = self.optimizer()._essential_counts_of(self.CUBE, completed)
        inclusion = {s.card: s for s in card_inclusion(self.CUBE, self.COMBOS, completed)}

        for card in self.CUBE:
            if card == "S1":
                continue
            assert counts[card] == inclusion[card].essential_combo_count, card
        # The only selected card of t's pool: essential to t in the ground truth, not in
        # the model's measure, where another pool card could fill the slot
        assert inclusion["S1"].essential_combos == ["t"]
        assert counts["S1"] == 0

    def test_unselected_and_unknown_cards_count_zero(self):
        completed = completable_combo_ids(self.CUBE, self.COMBOS)

        counts = self.optimizer()._essential_counts_of(["H", "Z", "Nope"], completed)

        assert counts == {"H": 1, "Z": 0, "Nope": 0}

    def test_phase1_counts_leave_the_exempt_cards_out(self):
        combos = [make_combo("ab", ["A", "B"]), make_combo("bc", ["B", "C"])]
        cards = build_candidate_cards(combos)
        cards["Pay"] = CandidateCard("Pay", frozenset(), frozenset())
        optimizer = ILPOptimizer(combos=combos, candidate_cards=cards, cube_size=4)
        assert optimizer.exempt_cards == {"Pay"}
        phase1 = optimizer.solve()

        counts = optimizer._phase1_essential_counts(phase1)

        assert counts == {"A": 1, "B": 2, "C": 1}


class TestEssentialObjective:
    """Phase 2 "essential": the tiered objective on the essential counts."""

    @pytest.mark.parametrize("cap", [1, 2])
    @pytest.mark.parametrize(("instance", "cube_size", "floor", "tolerance"), INSTANCE_CASES)
    def test_matches_brute_force(
        self, instance: str, cube_size: int, floor: int, tolerance: float, cap: int
    ):
        settings: dict[str, Any] = {
            "staple_combos": 0,
            "util_cap": cap,
            "min_utilization_floor": floor,
            "combo_tolerance": tolerance,
            "min_coverage_ratio": 0,
        }
        optimizer = OPTIMIZERS[instance](cube_size, phase2_objective="essential", **settings)
        result = optimizer.solve_two_phase(profile=True)
        expected = brute_force_minimum(
            OPTIMIZERS[instance](cube_size, **settings), cube_size, floor, cap
        )

        assert expected is not None
        assert result.phase2_status == "OPTIMAL"
        assert result.is_multi_objective is True
        assert result.phase2_objective == "essential"
        assert result.phase2_util_cap == cap
        assert result.phase2_util_cap_measure == "essential_combos"
        assert len(result.selected_cards) == cube_size
        assert tiered_overage(result_essential(optimizer, result).values(), cap) == expected
        assert result.utilization_per_card is not None
        assert min(result.utilization_per_card.values()) >= floor

        # The solver's objective is the scaled overage minus the tiebreak bonus
        solver_objective = phase_profile(result, "phase2")["solver_stats"]["objective_value"]
        assert solver_objective == pytest.approx(
            expected * optimizer.WEIGHT_SCALE - versatility_bonus(optimizer, result)
        )

    def test_keeps_a_redundant_outlet_where_tiered_removes_it(self):
        # Two outlets H and O complete the same three combos with A, B and C; D has two
        # combos of its own. At seven of the eight cards and a cap of 1, the essential
        # objective keeps both outlets (neither is essential, A, B and C are essential once
        # each) and drops one of D's partners; the tiered objective sees two cards in three
        # variants each and drops an outlet instead
        combos = [
            make_combo("ha", ["H", "A"], group="gA"),
            make_combo("oa", ["O", "A"], group="gA"),
            make_combo("hb", ["H", "B"], group="gB"),
            make_combo("ob", ["O", "B"], group="gB"),
            make_combo("hc", ["H", "C"], group="gC"),
            make_combo("oc", ["O", "C"], group="gC"),
            make_combo("de", ["D", "E"]),
            make_combo("df", ["D", "F"]),
        ]
        cubes: dict[str, set[str]] = {}
        for objective in ("essential", "tiered"):
            optimizer = ILPOptimizer(
                staple_combos=0,
                combos=combos,
                candidate_cards=build_candidate_cards(combos),
                cube_size=7,
                phase2_objective=objective,
                util_cap=1,
                min_utilization_floor=0,
                min_coverage_ratio=0,
                min_pair_combos=0,
                min_mono_combos=0,
                combo_tolerance=0.25,
                gap_limit=0,
                num_workers=1,
            )
            result = optimizer.solve_two_phase()
            assert result.phase2_status == "OPTIMAL", objective
            cubes[objective] = set(result.get_selected_card_names())
            if objective == "essential":
                assert max(result_essential(optimizer, result).values()) == 1
                stats = result.phase2_utilization_stats
                assert stats is not None and stats.essential_combos is not None
                assert stats.essential_combos.max == 1
                assert stats.concentration is not None
                assert stats.concentration.max_essential_combos == 1

        assert {"H", "O"} <= cubes["essential"]
        assert not {"H", "O"} <= cubes["tiered"]

    @pytest.mark.parametrize("cube_size", [9, 10])
    def test_default_cap_is_twice_the_phase1_median_essential_count(self, cube_size: int):
        optimizer = make_grouped_optimizer(
            cube_size, phase2_objective="essential", combo_tolerance=0.25
        )
        result = optimizer.solve_two_phase()

        phase1 = make_grouped_optimizer(cube_size).solve()
        values = optimizer._phase1_essential_counts(phase1).values()
        cap = max(1, math.ceil(2 * statistics.median(values)))
        assert result.phase2_util_cap == cap
        assert result.phase2_util_cap_measure == "essential_combos"
        assert result.phase2_status == "OPTIMAL"
        expected = brute_force_minimum(
            make_grouped_optimizer(cube_size, combo_tolerance=0.25), cube_size, 2, cap
        )
        assert tiered_overage(result_essential(optimizer, result).values(), cap) == expected

    def test_cap_is_logged_with_its_measure(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO):
            make_optimizer(10, phase2_objective="essential", util_cap=3).solve_two_phase()

        assert "Phase 2: essential combos per card cap T = 3 (--util-cap)" in caplog.text

    def test_profile_counts(self):
        cap = 1
        optimizer = make_optimizer(10, phase2_objective="essential", util_cap=cap)
        result = optimizer.solve_two_phase(profile=True)

        # One e variable per card that is a piece somewhere (no group has two variants, so
        # no per-group booleans), and one overage variable per tier a card can exceed
        pieces = {card: len(optimizer._piece_groups(card)) for card in optimizer.all_cards}
        with_pieces = sum(1 for count in pieces.values() if count)
        assert 0 < with_pieces < len(optimizer.all_cards)
        can_exceed = sum(
            1 for count in pieces.values() for multiple in (1, 2, 4) if count > cap * multiple
        )
        assert phase_profile(result, "phase2")["counts"] == phase2_counts(
            {
                "variables_essential": with_pieces,
                "essential_linking": with_pieces,
                "variables_overage": can_exceed,
                "overage_linking": can_exceed,
            }
        )

    def test_grouped_profile_counts_add_the_group_terms(self):
        optimizer = make_grouped_optimizer(10, phase2_objective="essential", util_cap=1)
        result = optimizer.solve_two_phase(profile=True)

        counts = phase_profile(result, "phase2")["counts"]
        # The hub is in every variant of hub1 and hub2 (their g); the hub's four partners
        # are each in one of their group's two variants and each triangle card in two of
        # its group's three (a boolean each); the groups' g already exist
        pair_vars = 10
        with_pieces = 11
        assert counts["variables_essential"] == with_pieces + pair_vars
        assert counts["essential_linking"] == with_pieces + pair_vars
        assert counts["variables_group"] == 4

    def test_infeasible_floor_falls_back_with_cap_recorded(self):
        result = make_optimizer(
            8, phase2_objective="essential", util_cap=3, min_utilization_floor=2
        ).solve_two_phase()

        assert result.phase2_fell_back is True
        assert result.phase2_status == "INFEASIBLE"
        assert result.phase2_objective == "essential"
        assert result.phase2_util_cap == 3
        assert result.phase2_util_cap_measure == "essential_combos"

    def test_other_objectives_record_no_measure(self):
        result = make_optimizer(10, phase2_objective="maxutil").solve_two_phase()

        assert result.phase2_util_cap is None
        assert result.phase2_util_cap_measure is None

        result = make_optimizer(10, phase2_objective="tiered", util_cap=2).solve_two_phase()

        assert result.phase2_util_cap == 2
        assert result.phase2_util_cap_measure == "utilization"


class TestReportedMeasures:
    """Every result carries the distinct and essential counts and the concentration."""

    def test_phase1_and_phase2_stats_carry_the_measures(self):
        result = make_grouped_optimizer(10, combo_tolerance=0.25).solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        for stats in (result.phase1_utilization_stats, result.phase2_utilization_stats):
            assert stats is not None
            assert stats.distinct_combos is not None
            assert stats.essential_combos is not None
            assert stats.concentration is not None
            assert stats.distinct_combos.max >= stats.essential_combos.max
            assert 0 < stats.concentration.top_card_share <= stats.concentration.top_five_share <= 1
            assert len(stats.concentration.top_five_cards) == 5
            assert stats.concentration.top_card == stats.concentration.top_five_cards[0]

    def test_measures_agree_with_the_card_inclusion(self):
        result = make_grouped_optimizer(10).solve()
        assert result.card_inclusion is not None and result.phase1_utilization_stats is not None
        by_card = {s.card: s for s in result.card_inclusion}

        stats = result.phase1_utilization_stats
        assert stats.essential_combos is not None and stats.concentration is not None
        assert stats.essential_combos.max == max(s.essential_combo_count for s in by_card.values())
        assert (
            by_card[stats.concentration.most_essential_card].essential_combo_count
            == stats.concentration.max_essential_combos
        )
        assert stats.concentration.top_card == max(
            by_card, key=lambda card: (by_card[card].distinct_combo_count, card)
        )


class TestWarmStartCube:
    """A cube from an earlier build hints the reference solve (warm_start_cards)."""

    @staticmethod
    def coverage_optimizer(**kwargs: Any) -> ILPOptimizer:
        # The Phase 1 cube at 8 cards breaks coverage, so a reference solve runs
        return make_optimizer(
            8,
            combo_tolerance=0.25,
            min_coverage_ratio=0.5,
            min_combo_threshold=3,
            min_utilization_floor=0,
            **kwargs,
        )

    @staticmethod
    def best_with_coverage(optimizer: ILPOptimizer) -> tuple[set[str], int]:
        combos = build_instance()
        cubes = (
            set(cube)
            for cube in itertools.combinations(sorted(build_candidate_cards(combos)), 8)
            if optimizer._coverage_violations(set(cube)) == 0
        )
        best = max(cubes, key=lambda cube: len(completable_combo_ids(cube, combos)))
        return best, len(completable_combo_ids(best, combos))

    def test_unknown_cards_are_dropped_with_a_warning(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING):
            optimizer = make_optimizer(8, warm_start_cards=["H", "A", "Nope", "Also nope"])

        assert optimizer.warm_start_cards == {"H", "A"}
        assert "2 warm-start cards are not in the pool and are ignored" in caplog.text
        assert "['Also nope', 'Nope']" in caplog.text

    def test_a_whole_cube_satisfying_the_rules_hints_the_reference_solve(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        probe = self.coverage_optimizer()
        best, best_count = self.best_with_coverage(probe)
        optimizer = self.coverage_optimizer(warm_start_cards=best)
        phase1 = optimizer.solve()
        hints: list[set[str]] = []
        original = ILPOptimizer._best_constrained_cube

        def recording(self: ILPOptimizer, hint: _WarmStart) -> tuple[_WarmStart | None, str]:
            hints.append(set(hint.cards))
            return original(self, hint)

        monkeypatch.setattr(ILPOptimizer, "_best_constrained_cube", recording)
        with caplog.at_level(logging.INFO):
            _, reference, _ = optimizer._build_warm_start(phase1, None)

        assert hints == [best]
        assert "the reference solve is hinted with the warm-start cube" in caplog.text
        assert len(reference.combo_ids) == best_count

    def test_a_cube_breaking_the_rules_is_the_hint_but_not_the_reference(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        probe = self.coverage_optimizer()
        phase1 = probe.solve()
        # A whole cube that breaks coverage, different from the Phase 1 cube
        best, _ = self.best_with_coverage(probe)
        broken_cube = set(phase1.get_selected_card_names())
        assert probe._coverage_violations(broken_cube) == 1 and broken_cube != best
        optimizer = self.coverage_optimizer(warm_start_cards=broken_cube)
        hints: list[set[str]] = []

        def recording(self: ILPOptimizer, hint: _WarmStart) -> tuple[_WarmStart | None, str]:
            hints.append(set(hint.cards))
            return None, "TIMEOUT"  # the solve finds nothing: no cube may fall back on it

        monkeypatch.setattr(ILPOptimizer, "_best_constrained_cube", recording)
        with caplog.at_level(logging.WARNING):
            _, reference, _ = optimizer._build_warm_start(phase1, None)

        assert hints == [broken_cube]
        assert (
            "the warm-start cube breaks 1 coverage constraints; it hints the reference solve "
            "but cannot be the reference"
        ) in caplog.text
        # Nothing satisfied the rules, so the window is measured from the Phase 1 cube
        assert reference.cards == set(phase1.get_selected_card_names())
        assert "measuring the combo window from the Phase 1 count" in caplog.text

    def test_a_partial_cube_is_not_the_hint(self, caplog: pytest.LogCaptureFixture):
        probe = self.coverage_optimizer()
        best, _ = self.best_with_coverage(probe)
        optimizer = self.coverage_optimizer(warm_start_cards=sorted(best)[:5])
        phase1 = optimizer.solve()

        with caplog.at_level(logging.WARNING):
            optimizer._build_warm_start(phase1, None)

        assert "the warm-start cube has 5 candidate cards, not 8" in caplog.text

    def test_the_warm_start_cube_is_the_reference_when_the_solve_finds_nothing_better(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        probe = self.coverage_optimizer()
        best, best_count = self.best_with_coverage(probe)
        optimizer = self.coverage_optimizer(warm_start_cards=best)
        phase1 = optimizer.solve()
        monkeypatch.setattr(
            ILPOptimizer, "_best_constrained_cube", lambda self, hint: (None, "TIMEOUT")
        )

        with caplog.at_level(logging.INFO):
            warm_start, reference, _ = optimizer._build_warm_start(phase1, None)

        assert reference.cards == best
        assert len(reference.combo_ids) == best_count
        assert "found nothing better than the warm-start cube" in caplog.text
        assert optimizer._coverage_violations(warm_start.cards) == 0

    def test_no_reference_solve_when_the_phase1_cube_satisfies_the_rules(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        optimizer = make_optimizer(
            9, warm_start_cards=["H", "A", "B", "C", "D", "E", "F", "G", "I"]
        )
        phase1 = optimizer.solve()
        monkeypatch.setattr(
            ILPOptimizer,
            "_best_constrained_cube",
            lambda self, hint: pytest.fail("no reference solve expected"),
        )

        _, reference, _ = optimizer._build_warm_start(phase1, None)

        assert reference.cards == set(phase1.get_selected_card_names())

    def test_result_records_the_reference_cube(self):
        optimizer = self.coverage_optimizer()
        result = optimizer.solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert result.phase2_reference_cards is not None
        assert len(result.phase2_reference_cards) == 8
        assert result.phase2_reference_cards == sorted(result.phase2_reference_cards)
        assert optimizer._coverage_violations(set(result.phase2_reference_cards)) == 0
        assert (
            len(completable_combo_ids(result.phase2_reference_cards, build_instance()))
            == result.phase2_reference_combo_count
        )
