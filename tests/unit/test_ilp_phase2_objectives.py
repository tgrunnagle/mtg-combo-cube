"""Tests for the "maxutil", "softcap" and "tiered" Phase 2 objectives and the warm start.

They use the small fixed instance of the characterization tests, solved to proven
optimality (one worker, no gap limit), and compare the optimum with a brute-force
enumeration of every cube.
"""

import itertools
import math
from collections.abc import Callable, Collection

import pytest

from mtg_combo_cube.ilp.cube_evaluation import card_utilization, completable_combo_ids
from mtg_combo_cube.ilp.ilp_models import OptimizationResult, UtilizationStats
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from tests.unit.test_ilp_optimizer import build_candidate_cards
from tests.unit.test_ilp_optimizer_characterization import (
    BASE_COUNTS,
    build_instance,
    make_optimizer,
    phase2_counts,
    phase_profile,
)

MAXUTIL_COUNTS: dict[str, int] = {"variables_maxutil": 1, "maxutil_linking": 14}

# (cube_size, floor, tolerance) cases with a feasible Phase 2
FEASIBLE_CASES = [(8, 0, 0), (9, 2, 0), (10, 2, 0), (8, 2, 0.25), (9, 0, 0.25), (10, 2, 0.25)]


def brute_force_minimum(
    cube_size: int, floor: int, tolerance: float, score: Callable[[Collection[int]], int]
) -> int | None:
    """Smallest score(utilization values) over every cube Phase 2 accepts, by enumeration."""
    combos = build_instance()
    cards = sorted(build_candidate_cards(combos))
    phase1_count = make_optimizer(cube_size).solve().combo_count
    min_combos = math.floor(phase1_count * (1 - tolerance))
    max_combos = math.ceil(phase1_count * (1 + tolerance))

    best: int | None = None
    for cube in itertools.combinations(cards, cube_size):
        if not min_combos <= len(completable_combo_ids(cube, combos)) <= max_combos:
            continue
        utilization = card_utilization(cube, combos).values()
        if min(utilization) < floor:
            continue
        value = score(utilization)
        if best is None or value < best:
            best = value
    return best


def overage(utilization: Collection[int], cap: int) -> int:
    return sum(max(0, value - cap) for value in utilization)


def result_utilization(result: OptimizationResult) -> Collection[int]:
    assert result.utilization_per_card is not None
    return result.utilization_per_card.values()


def versatility_bonus(optimizer: ILPOptimizer, result: OptimizationResult) -> int:
    return sum(optimizer._versatility_weight(card.name) for card in result.selected_cards)


class TestMaxutilObjective:
    """Phase 2 "maxutil": minimize the largest utilization."""

    @pytest.mark.parametrize(("cube_size", "floor", "tolerance"), FEASIBLE_CASES)
    def test_maxutil_matches_brute_force(self, cube_size: int, floor: int, tolerance: float):
        optimizer = make_optimizer(
            cube_size,
            phase2_objective="maxutil",
            min_utilization_floor=floor,
            combo_tolerance=tolerance,
            min_coverage_ratio=0,
        )
        result = optimizer.solve_two_phase(profile=True)
        expected = brute_force_minimum(cube_size, floor, tolerance, max)

        assert expected is not None
        assert result.phase2_status == "OPTIMAL"
        assert result.is_multi_objective is True
        assert result.phase2_objective == "maxutil"
        assert result.phase2_util_cap is None
        assert len(result.selected_cards) == cube_size
        assert max(result_utilization(result)) == expected
        assert min(result_utilization(result)) >= floor

        # The solver's objective is the scaled maximum minus the tiebreak bonus
        solver_objective = phase_profile(result, "phase2")["solver_stats"]["objective_value"]
        assert solver_objective == pytest.approx(
            expected * optimizer.WEIGHT_SCALE - versatility_bonus(optimizer, result)
        )

    def test_maxutil_profile_counts(self):
        result = make_optimizer(10, phase2_objective="maxutil").solve_two_phase(profile=True)

        assert phase_profile(result, "phase1")["counts"] == BASE_COUNTS
        # No min_util variable and no linking for it: one constraint per card
        assert phase_profile(result, "phase2")["counts"] == phase2_counts(MAXUTIL_COUNTS)

    def test_maxutil_infeasible_floor_falls_back_to_phase1(self):
        # At 8 cards no 9-combo cube has every card in at least 2 combos
        result = make_optimizer(
            8, phase2_objective="maxutil", min_utilization_floor=2
        ).solve_two_phase()

        assert result.phase2_fell_back is True
        assert result.phase2_status == "INFEASIBLE"
        assert result.phase2_objective == "maxutil"
        assert result.combo_count == 9


class TestSoftcapObjective:
    """Phase 2 "softcap": minimize the total utilization above a cap."""

    @pytest.mark.parametrize("cap", [1, 2, 3])
    @pytest.mark.parametrize(("cube_size", "floor", "tolerance"), FEASIBLE_CASES)
    def test_softcap_matches_brute_force(
        self, cube_size: int, floor: int, tolerance: float, cap: int
    ):
        optimizer = make_optimizer(
            cube_size,
            phase2_objective="softcap",
            util_cap=cap,
            min_utilization_floor=floor,
            combo_tolerance=tolerance,
            min_coverage_ratio=0,
        )
        result = optimizer.solve_two_phase(profile=True)
        expected = brute_force_minimum(
            cube_size, floor, tolerance, lambda utilization: overage(utilization, cap)
        )

        assert expected is not None
        assert result.phase2_status == "OPTIMAL"
        assert result.phase2_objective == "softcap"
        assert result.phase2_util_cap == cap
        assert len(result.selected_cards) == cube_size
        assert overage(result_utilization(result), cap) == expected
        assert min(result_utilization(result)) >= floor

        solver_objective = phase_profile(result, "phase2")["solver_stats"]["objective_value"]
        assert solver_objective == pytest.approx(
            expected * optimizer.WEIGHT_SCALE - versatility_bonus(optimizer, result)
        )

    @pytest.mark.parametrize("cube_size", [9, 10])
    def test_default_cap_is_twice_the_phase1_median(self, cube_size: int):
        optimizer = make_optimizer(cube_size, phase2_objective="softcap")
        result = optimizer.solve_two_phase()

        phase1_stats = result.phase1_utilization_stats
        assert phase1_stats is not None
        cap = math.ceil(2 * phase1_stats.median_utilization)
        assert cap >= 1
        assert result.phase2_util_cap == cap
        assert result.phase2_status == "OPTIMAL"
        expected = brute_force_minimum(
            cube_size, 2, 0, lambda utilization: overage(utilization, cap)
        )
        assert overage(result_utilization(result), cap) == expected

    def test_softcap_profile_counts(self):
        cap = 2
        optimizer = make_optimizer(10, phase2_objective="softcap", util_cap=cap)
        result = optimizer.solve_two_phase(profile=True)

        # One overage variable and constraint per card that can exceed the cap
        can_exceed = sum(1 for combos in optimizer.card_to_combos.values() if len(combos) > cap)
        assert 0 < can_exceed < 14
        assert phase_profile(result, "phase2")["counts"] == phase2_counts(
            {"variables_overage": can_exceed, "overage_linking": can_exceed}
        )

    def test_cap_above_every_card_leaves_only_the_tiebreak(self):
        optimizer = make_optimizer(10, phase2_objective="softcap", util_cap=100)
        result = optimizer.solve_two_phase(profile=True)

        assert result.phase2_status == "OPTIMAL"
        assert result.combo_count == 12
        counts = phase_profile(result, "phase2")["counts"]
        assert counts["variables_overage"] == 0
        solver_objective = phase_profile(result, "phase2")["solver_stats"]["objective_value"]
        assert solver_objective == pytest.approx(-versatility_bonus(optimizer, result))

    def test_softcap_infeasible_floor_falls_back_with_cap_recorded(self):
        result = make_optimizer(
            8, phase2_objective="softcap", util_cap=3, min_utilization_floor=2
        ).solve_two_phase()

        assert result.phase2_fell_back is True
        assert result.phase2_status == "INFEASIBLE"
        assert result.phase2_objective == "softcap"
        assert result.phase2_util_cap == 3


class TestUtilCapResolution:
    """--util-cap and its default derived from Phase 1."""

    @staticmethod
    def stats(median: float) -> UtilizationStats:
        return UtilizationStats(
            min_utilization=0,
            max_utilization=300,
            mean_utilization=30.0,
            std_deviation=40.0,
            total_absolute_deviation=0,
            median_utilization=median,
        )

    @pytest.mark.parametrize(("median", "expected"), [(16.0, 32), (16.5, 33), (0.5, 1), (0.0, 1)])
    def test_default_cap(self, median: float, expected: int):
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)

        assert optimizer.util_cap is None
        assert optimizer._resolve_util_cap(self.stats(median)) == expected

    @pytest.mark.parametrize("util_cap", [0, 7, 500])
    def test_explicit_cap_wins(self, util_cap: int):
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10, util_cap=util_cap)

        assert optimizer._resolve_util_cap(self.stats(16.0)) == util_cap

    def test_negative_cap_raises(self):
        with pytest.raises(ValueError, match="util_cap must be >= 0"):
            ILPOptimizer(combos=[], candidate_cards={}, cube_size=10, util_cap=-1)

    @pytest.mark.parametrize("objective", ["minmax", "mad", "maxutil"])
    def test_cap_not_recorded_for_other_objectives(self, objective: str):
        result = make_optimizer(10, phase2_objective=objective, util_cap=3).solve_two_phase()

        assert result.phase2_objective == objective
        assert result.phase2_util_cap is None


class TestTiebreakScale:
    """The versatility bonus never outweighs one unit of the primary objective."""

    def test_default_scale_is_weight_scale(self):
        optimizer = make_optimizer(10)

        # Largest possible bonus is far below WEIGHT_SCALE on this instance
        assert optimizer._tiebreak_scale() == optimizer.WEIGHT_SCALE

    def test_scale_grows_with_the_largest_possible_bonus(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ILPOptimizer, "VERSATILITY_EPSILON", 1.0)
        optimizer = make_optimizer(10)

        weights = sorted(
            (optimizer._versatility_weight(card) for card in optimizer.all_cards), reverse=True
        )
        assert sum(weights[:10]) > optimizer.WEIGHT_SCALE
        assert optimizer._tiebreak_scale() == sum(weights[:10]) + 1

    @pytest.mark.parametrize("objective", ["maxutil", "softcap", "minmax"])
    @pytest.mark.parametrize(("cube_size", "floor", "tolerance"), [(9, 0, 0.25), (10, 2, 0.25)])
    def test_large_bonus_stays_a_tiebreak(
        self,
        monkeypatch: pytest.MonkeyPatch,
        objective: str,
        cube_size: int,
        floor: int,
        tolerance: float,
    ):
        # With a 10,000 times larger bonus the primary optimum must not change
        monkeypatch.setattr(ILPOptimizer, "VERSATILITY_EPSILON", 1.0)
        cap = 2
        scores: dict[str, Callable[[Collection[int]], int]] = {
            "maxutil": max,
            "softcap": lambda utilization: overage(utilization, cap),
            "minmax": lambda utilization: max(utilization) - min(utilization),
        }
        result = make_optimizer(
            cube_size,
            phase2_objective=objective,
            util_cap=cap,
            min_utilization_floor=floor,
            combo_tolerance=tolerance,
            min_coverage_ratio=0,
        ).solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert scores[objective](result_utilization(result)) == brute_force_minimum(
            cube_size, floor, tolerance, scores[objective]
        )


class TestSoftcapStopRule:
    """softcap measures the gap limit against the Phase 1 overage, not the objective."""

    def test_no_absolute_limit_without_gap_limit(self):
        optimizer = make_optimizer(10, phase2_objective="softcap", gap_limit=0)
        base = optimizer._build_base_model()

        optimizer._set_overage_stop_rule(base, scale=10000, phase1_overage=40)

        assert base.absolute_gap_limit is None

    @pytest.mark.parametrize(("phase1_overage", "allowed"), [(40, 2), (19, 0), (0, 0)])
    def test_absolute_limit_from_phase1_overage(self, phase1_overage: int, allowed: int):
        optimizer = make_optimizer(10, phase2_objective="softcap", gap_limit=0.05)
        base = optimizer._build_base_model()

        optimizer._set_overage_stop_rule(base, scale=10000, phase1_overage=phase1_overage)

        # Proven within `allowed` overage units, plus less than one unit for the tiebreak
        assert base.absolute_gap_limit == allowed * 10000 + 9999

    def test_other_objectives_set_no_absolute_limit(self, monkeypatch: pytest.MonkeyPatch):
        seen: list[float | None] = []
        original = ILPOptimizer._make_solver

        def recording_make_solver(self: ILPOptimizer, *args, **kwargs):
            seen.append(kwargs.get("absolute_gap_limit"))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(ILPOptimizer, "_make_solver", recording_make_solver)
        make_optimizer(10, phase2_objective="maxutil", gap_limit=0.05).solve_two_phase()
        assert len(seen) >= 2  # Phase 1, (warm-start repair,) Phase 2
        assert all(limit is None for limit in seen)

        seen.clear()
        make_optimizer(10, phase2_objective="softcap", gap_limit=0.05, util_cap=2).solve_two_phase()
        assert seen[0] is None
        assert seen[-1] is not None

    def test_gap_limit_result_is_within_the_allowed_overage(self):
        cap, gap_limit = 1, 0.25
        result = make_optimizer(
            10,
            phase2_objective="softcap",
            util_cap=cap,
            gap_limit=gap_limit,
            combo_tolerance=0.25,
        ).solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        phase1 = make_optimizer(10).solve()
        phase1_overage = overage(result_utilization(phase1), cap)
        optimum = brute_force_minimum(10, 2, 0.25, lambda utilization: overage(utilization, cap))
        assert optimum is not None
        allowed = math.floor(gap_limit * phase1_overage)
        assert overage(result_utilization(result), cap) <= optimum + allowed + 1


class TestWarmStart:
    """The Phase 2 hint is a cube that satisfies the Phase 2 constraints when one is found."""

    def test_feasible_phase1_cube_is_used_unchanged(self):
        optimizer = make_optimizer(9)  # Phase 1 at 9 cards: every card in at least 2 combos
        phase1 = optimizer.solve()

        warm_start = optimizer._build_warm_start(phase1, None)

        assert warm_start.cards == set(phase1.get_selected_card_names())
        assert warm_start.combo_ids == set(phase1.completable_combo_ids)
        assert warm_start.utilization == phase1.utilization_per_card

    def test_cube_below_floor_is_repaired(self):
        # Phase 1 at 10 cards has a card in only 1 combo; with a tolerance a cube with
        # every card in at least 2 combos exists inside the window
        optimizer = make_optimizer(10, combo_tolerance=0.25)
        phase1 = optimizer.solve()
        assert phase1.utilization_per_card is not None
        assert min(phase1.utilization_per_card.values()) < 2

        warm_start = optimizer._build_warm_start(phase1, None)

        combos = build_instance()
        assert len(warm_start.cards) == 10
        assert warm_start.utilization == card_utilization(warm_start.cards, combos)
        assert warm_start.combo_ids == set(completable_combo_ids(warm_start.cards, combos))
        assert min(warm_start.utilization.values()) >= 2
        assert len(warm_start.combo_ids) >= math.floor(phase1.combo_count * 0.75)

    def test_cube_breaking_coverage_is_repaired(self):
        optimizer = make_optimizer(
            8,
            combo_tolerance=0.25,
            min_coverage_ratio=0.5,
            min_combo_threshold=3,
            min_utilization_floor=0,
        )
        phase1 = optimizer.solve()
        assert optimizer._coverage_violations(set(phase1.get_selected_card_names())) == 1

        warm_start = optimizer._build_warm_start(phase1, None)

        assert optimizer._coverage_violations(warm_start.cards) == 0
        assert {"S1", "S2"} <= warm_start.cards
        assert len(warm_start.combo_ids) >= math.floor(phase1.combo_count * 0.75)

    def test_unrepairable_cube_falls_back_to_phase1_cube(self):
        # At 8 cards and no tolerance no cube satisfies the floor
        optimizer = make_optimizer(8, min_utilization_floor=2)
        phase1 = optimizer.solve()

        warm_start = optimizer._build_warm_start(phase1, None)

        assert warm_start.cards == set(phase1.get_selected_card_names())

    def test_coverage_violations_disabled_without_ratio(self):
        optimizer = make_optimizer(8, min_coverage_ratio=0)

        assert optimizer._coverage_violations(set()) == 0


class TestTieredObjective:
    """Phase 2 "tiered": softcap that also counts utilization above 2x and 4x the cap."""

    @staticmethod
    def tiered_overage(utilization: Collection[int], cap: int) -> int:
        return sum(overage(utilization, cap * multiple) for multiple in (1, 2, 4))

    @pytest.mark.parametrize("cap", [1, 2])
    @pytest.mark.parametrize(("cube_size", "floor", "tolerance"), FEASIBLE_CASES)
    def test_tiered_matches_brute_force(
        self, cube_size: int, floor: int, tolerance: float, cap: int
    ):
        optimizer = make_optimizer(
            cube_size,
            phase2_objective="tiered",
            util_cap=cap,
            min_utilization_floor=floor,
            combo_tolerance=tolerance,
            min_coverage_ratio=0,
        )
        result = optimizer.solve_two_phase(profile=True)
        expected = brute_force_minimum(
            cube_size, floor, tolerance, lambda utilization: self.tiered_overage(utilization, cap)
        )

        assert expected is not None
        assert result.phase2_status == "OPTIMAL"
        assert result.phase2_objective == "tiered"
        assert result.phase2_util_cap == cap
        assert self.tiered_overage(result_utilization(result), cap) == expected
        assert min(result_utilization(result)) >= floor

        solver_objective = phase_profile(result, "phase2")["solver_stats"]["objective_value"]
        assert solver_objective == pytest.approx(
            expected * optimizer.WEIGHT_SCALE - versatility_bonus(optimizer, result)
        )

    def test_tiered_profile_counts(self):
        cap = 1
        optimizer = make_optimizer(10, phase2_objective="tiered", util_cap=cap)
        result = optimizer.solve_two_phase(profile=True)

        # One overage variable per tier (T, 2T, 4T) and card that can exceed that tier
        sizes = [len(combos) for combos in optimizer.card_to_combos.values()]
        can_exceed = sum(1 for size in sizes for multiple in (1, 2, 4) if size > cap * multiple)
        assert phase_profile(result, "phase2")["counts"] == phase2_counts(
            {"variables_overage": can_exceed, "overage_linking": can_exceed}
        )
