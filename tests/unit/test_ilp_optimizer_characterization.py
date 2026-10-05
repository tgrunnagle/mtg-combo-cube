"""Characterization tests pinning the ILP optimizer's behavior on a small fixed instance.

The instance is small enough to solve to proven optimality, so the tests assert on optimal
objective quality (which is unique) rather than on which cards were chosen (which may tie).
All solves use one worker and no gap limit to stay deterministic.

Stage 3 (2026-10-05) changed the Phase 2 model on purpose: y is exact (a combo variable is
1 iff the cube completes the combo), utilization bounds are per card, and the utilization
floor applies to every objective. The pinned values that changed because of that:
- Profile counts: new exact-linking entries, fewer utilization linking constraints,
  `utilization_floor` (one constraint per card) instead of `min_floor`, more hints.
- "mad" now honors the floor (default 2): infeasible at 8 cards, like "minmax".
- "mad" with tolerance: 321 -> 347. The old optimum switched off a combo the cube completes.
- A Phase 2 fallback now records the Phase 2 status, time and profile.

Stage 4 (2026-10-05) added the "maxutil", "softcap" and "tiered" objectives (tested in
test_ilp_phase2_objectives.py) and a warm-start repair: when the Phase 1 cube breaks a
Phase 2 constraint, the Phase 2 profile has an extra `warm_start_repair` timing.
"""

import itertools
import logging
import math
from typing import Any

import pytest

from mtg_combo_cube.ilp.cube_evaluation import card_utilization, completable_combo_ids
from mtg_combo_cube.ilp.ilp_models import ComboData, OptimizationResult, RequirementOption
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from tests.unit.test_ilp_optimizer import build_candidate_cards

BASE_COUNTS: dict[str, int] = {
    "variables_card": 14,
    "variables_combo": 15,
    "cube_size": 1,
    "required_card": 27,
    "requirement_options": 5,
}


def build_instance() -> list[ComboData]:
    """A hub card (H), two triangles (E-F-G, I-J-K) and a few template requirements."""

    def combo(
        index: int, cards: list[str], popularity: int, options: list[RequirementOption]
    ) -> ComboData:
        return ComboData(
            id=f"c{index:02d}",
            required_cards=frozenset(cards),
            requirement_options=options,
            popularity=popularity,
        )

    def sac(name: str = "Sac outlet") -> RequirementOption:
        return RequirementOption(name, "scryfall:q=sac", frozenset(["S1", "S2"]))

    def rock() -> RequirementOption:
        return RequirementOption("Mana rock", "scryfall:q=rock", frozenset(["S2", "M1"]))

    return [
        combo(1, ["H", "A"], 900, []),
        combo(2, ["H", "B"], 800, []),
        combo(3, ["H", "C"], 700, []),
        combo(4, ["H", "D"], 600, []),
        combo(5, ["A", "B"], 500, []),
        combo(6, ["E", "F"], 50, []),
        combo(7, ["F", "G"], 40, []),
        combo(8, ["G", "E"], 30, []),
        combo(9, ["I", "J"], 25, []),
        combo(10, ["J", "K"], 20, []),
        combo(11, ["K", "I"], 15, []),
        combo(12, ["H"], 400, [sac()]),
        combo(13, ["E"], 300, [sac("Sacrifice outlet"), rock()]),
        combo(14, ["I"], 10, [rock()]),
        combo(15, ["C", "D"], 5, [sac()]),
    ]


def make_optimizer(cube_size: int, **kwargs: Any) -> ILPOptimizer:
    combos = build_instance()
    settings: dict[str, Any] = {
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


def phase_profile(result: OptimizationResult, phase: str) -> dict[str, Any]:
    assert result.profile_data is not None
    return result.profile_data[phase]


def phase2_timing_names(profile: dict[str, Any]) -> set[str]:
    """Timing names of a Phase 2 profile, without the optional warm-start repair (Stage 4)."""
    return set(profile["timings"]) - {"warm_start_repair"}


def phase2_counts(
    objective_counts: dict[str, int], floor: bool = True, **overrides: int
) -> dict[str, int]:
    counts = {
        **BASE_COUNTS,
        "combo_count": 1,
        "coverage": 0,
        "variables_option": 2,  # the sac outlet pool and the mana rock pool
        "option_linking": 6,
        "combo_exact_linking": 15,
        "variables_utilization": 14,
        "utilization_linking": 17,  # 2 per pool card (S1, S2, M1), 1 per required-only card
        "warm_start_hints": 45,
        **objective_counts,
    }
    if floor:
        counts["utilization_floor"] = 14
    counts.update(overrides)
    return counts


MINMAX_COUNTS: dict[str, int] = {"variables_minmax": 2, "minmax_linking": 28}
MAD_COUNTS: dict[str, int] = {"variables_deviation": 28, "mad_deviation": 56}


def utilization_range(result: OptimizationResult) -> int:
    stats = result.phase2_utilization_stats
    assert stats is not None
    return stats.max_utilization - stats.min_utilization


def mad_objective_from_result(optimizer: ILPOptimizer, result: OptimizationResult) -> int:
    """Recompute the MAD objective (deviation from the Phase 1 mean, minus bonus)."""
    assert result.phase1_utilization_stats is not None
    assert result.utilization_per_card is not None
    mean_scaled = int(result.phase1_utilization_stats.mean_utilization * 100)
    deviation = sum(abs(100 * u - mean_scaled) for u in result.utilization_per_card.values())
    bonus = sum(
        int(
            optimizer.VERSATILITY_EPSILON * math.log1p(card.template_count) * optimizer.WEIGHT_SCALE
        )
        for card in result.selected_cards
    )
    return deviation - bonus


def assert_fell_back_to_phase1(
    result: OptimizationResult, phase1: OptimizationResult, objective_counts: dict[str, int]
) -> None:
    """The result is the Phase 1 cube and carries a trace of the failed Phase 2."""
    assert result.phase1_status == "OPTIMAL"
    assert result.is_multi_objective is False
    assert result.phase2_fell_back is True
    assert result.phase2_status == "INFEASIBLE"
    assert result.phase2_utilization_stats is None
    assert result.phase1_selected_cards is None
    assert result.combo_count == 9
    assert result.selected_cards == phase1.selected_cards
    assert result.completable_combo_ids == phase1.completable_combo_ids
    assert result.utilization_per_card == phase1.utilization_per_card

    assert result.phase1_solve_time is not None
    assert result.phase2_solve_time is not None
    assert result.phase2_solve_time > 0
    assert result.solve_time_seconds == pytest.approx(
        result.phase1_solve_time + result.phase2_solve_time
    )

    assert result.profile_data is not None
    assert set(result.profile_data) == {"phase1", "phase2"}
    assert phase_profile(result, "phase1")["counts"] == BASE_COUNTS
    profile = phase_profile(result, "phase2")
    assert profile["counts"] == phase2_counts(objective_counts)
    assert phase2_timing_names(profile) == {"model_build", "solver"}
    assert "objective_value" not in profile["solver_stats"]
    assert "relative_gap" not in profile["solver_stats"]


class TestPhase1Characterization:
    """Pin Phase 1 results."""

    @pytest.mark.parametrize(
        ("cube_size", "combo_count", "objective", "util_min", "util_max"),
        [
            (8, 9, 9.0496, 1, 5),
            (9, 11, 11.0567, 2, 5),
            (10, 12, 12.059, 1, 5),
        ],
    )
    def test_phase1_optimum(
        self, cube_size: int, combo_count: int, objective: float, util_min: int, util_max: int
    ):
        result = make_optimizer(cube_size).solve(profile=True)

        assert result.phase1_status == "OPTIMAL"
        assert result.combo_count == combo_count
        assert len(result.completable_combo_ids) == combo_count
        assert len(result.selected_cards) == cube_size
        assert result.objective_value == pytest.approx(objective, abs=1e-9)
        assert result.is_multi_objective is False
        assert result.phase2_status is None
        assert result.phase2_utilization_stats is None
        assert result.phase1_solve_time == result.solve_time_seconds

        stats = result.phase1_utilization_stats
        assert stats is not None
        assert (stats.min_utilization, stats.max_utilization) == (util_min, util_max)
        assert result.requirement_type_stats is not None
        assert result.requirement_coverage_stats is not None
        assert result.cross_template_stats is not None

        assert result.profile_data is not None
        assert set(result.profile_data) == {"phase1"}
        profile = phase_profile(result, "phase1")
        assert set(profile) == {"timings", "counts", "solver_stats"}
        assert profile["counts"] == BASE_COUNTS
        assert set(profile["timings"]) == {"model_build", "solver", "extraction"}
        assert profile["solver_stats"]["objective_value"] == pytest.approx(objective * 10000)

    def test_phase1_without_profile_has_no_profile_data(self):
        result = make_optimizer(10).solve()

        assert result.combo_count == 12
        assert result.profile_data is None


class TestPhase2MinmaxCharacterization:
    """Pin Phase 2 results for the range ("minmax") objective."""

    @pytest.mark.parametrize(
        ("cube_size", "floor", "combo_count", "expected_range", "objective", "counts"),
        [
            (10, 2, 12, 1, 9999, phase2_counts(MINMAX_COUNTS)),
            (10, 0, 12, 1, 9999, phase2_counts(MINMAX_COUNTS, floor=False)),
            (9, 2, 11, 3, 29999, phase2_counts(MINMAX_COUNTS)),
            (8, 0, 9, 2, 19999, phase2_counts(MINMAX_COUNTS, floor=False)),
        ],
    )
    def test_minmax_optimum(
        self,
        cube_size: int,
        floor: int,
        combo_count: int,
        expected_range: int,
        objective: int,
        counts: dict[str, int],
    ):
        optimizer = make_optimizer(
            cube_size, phase2_objective="minmax", min_utilization_floor=floor
        )
        result = optimizer.solve_two_phase(profile=True)

        assert result.phase1_status == "OPTIMAL"
        assert result.phase2_status == "OPTIMAL"
        assert result.is_multi_objective is True
        assert result.combo_count == combo_count
        assert len(result.selected_cards) == cube_size
        assert utilization_range(result) == expected_range
        stats = result.phase2_utilization_stats
        assert stats is not None
        assert stats.min_utilization >= floor

        # Phase 1 values are carried through unchanged
        phase1 = make_optimizer(cube_size).solve()
        assert result.objective_value == pytest.approx(phase1.objective_value, abs=1e-9)
        assert result.phase1_utilization_stats == phase1.phase1_utilization_stats
        assert result.phase1_selected_cards == phase1.selected_cards
        assert result.phase1_solve_time is not None
        assert result.phase2_solve_time is not None
        assert result.solve_time_seconds == pytest.approx(
            result.phase1_solve_time + result.phase2_solve_time
        )

        assert result.profile_data is not None
        assert set(result.profile_data) == {"phase1", "phase2"}
        assert phase_profile(result, "phase1")["counts"] == BASE_COUNTS
        profile = phase_profile(result, "phase2")
        assert set(profile) == {"timings", "counts", "solver_stats"}
        assert profile["counts"] == counts
        assert phase2_timing_names(profile) == {"model_build", "solver", "extraction"}
        assert profile["solver_stats"]["objective_value"] == pytest.approx(objective)

    def test_minmax_with_tolerance_and_coverage(self):
        optimizer = make_optimizer(
            8,
            phase2_objective="minmax",
            combo_tolerance=0.25,
            min_coverage_ratio=0.5,
            min_combo_threshold=3,
        )
        result = optimizer.solve_two_phase(profile=True)

        assert result.phase2_status == "OPTIMAL"
        # Phase 1 finds 9 combos; the window is floor(9 * 0.75) .. ceil(9 * 1.25)
        assert 6 <= result.combo_count <= 12
        assert utilization_range(result) == 1
        selected = set(result.get_selected_card_names())
        assert {"S1", "S2"} <= selected  # coverage: 3 sac combos * 0.5 -> 2 sac outlets

        profile = phase_profile(result, "phase2")
        assert profile["counts"] == phase2_counts(MINMAX_COUNTS, combo_count=2, coverage=1)
        assert profile["solver_stats"]["objective_value"] == pytest.approx(9999)

    def test_minmax_infeasible_falls_back_to_phase1(self):
        # At 8 cards no 9-combo cube has every card in at least 2 combos.
        optimizer = make_optimizer(8, phase2_objective="minmax", min_utilization_floor=2)
        result = optimizer.solve_two_phase(profile=True)
        phase1 = make_optimizer(8).solve()

        assert_fell_back_to_phase1(result, phase1, MINMAX_COUNTS)


class TestPhase2MadCharacterization:
    """Pin Phase 2 results for the mean-absolute-deviation ("mad") objective."""

    @pytest.mark.parametrize(
        ("cube_size", "floor", "combo_count", "objective", "total_abs_dev"),
        [
            (10, 2, 12, 499, 4),
            (9, 2, 11, 664, 6),
            (8, 0, 9, 473, 4),  # floor 2 is infeasible at 8 cards
        ],
    )
    def test_mad_optimum(
        self, cube_size: int, floor: int, combo_count: int, objective: int, total_abs_dev: int
    ):
        optimizer = make_optimizer(cube_size, phase2_objective="mad", min_utilization_floor=floor)
        result = optimizer.solve_two_phase(profile=True)

        assert result.phase1_status == "OPTIMAL"
        assert result.phase2_status == "OPTIMAL"
        assert result.is_multi_objective is True
        assert result.combo_count == combo_count
        assert len(result.selected_cards) == cube_size
        assert mad_objective_from_result(optimizer, result) == objective
        stats = result.phase2_utilization_stats
        assert stats is not None
        assert stats.total_absolute_deviation == total_abs_dev
        assert stats.min_utilization >= floor

        phase1 = make_optimizer(cube_size).solve()
        assert result.objective_value == pytest.approx(phase1.objective_value, abs=1e-9)
        assert result.phase1_utilization_stats == phase1.phase1_utilization_stats
        assert result.phase1_selected_cards == phase1.selected_cards

        assert result.profile_data is not None
        assert set(result.profile_data) == {"phase1", "phase2"}
        assert phase_profile(result, "phase1")["counts"] == BASE_COUNTS
        profile = phase_profile(result, "phase2")
        assert profile["counts"] == phase2_counts(MAD_COUNTS, floor=floor > 0)
        assert phase2_timing_names(profile) == {"model_build", "solver", "extraction"}
        assert profile["solver_stats"]["objective_value"] == pytest.approx(objective)

    def test_mad_infeasible_floor_falls_back_to_phase1(self):
        # The floor applies to every objective: as for minmax, no 9-combo cube of 8 cards
        # has every card in at least 2 combos. (Before Stage 3 mad ignored the floor.)
        optimizer = make_optimizer(8, phase2_objective="mad", min_utilization_floor=2)
        result = optimizer.solve_two_phase(profile=True)
        phase1 = make_optimizer(8).solve()

        assert_fell_back_to_phase1(result, phase1, MAD_COUNTS)

    def test_mad_with_tolerance_and_coverage(self):
        optimizer = make_optimizer(
            8,
            phase2_objective="mad",
            combo_tolerance=0.25,
            min_coverage_ratio=0.5,
            min_combo_threshold=3,
        )
        result = optimizer.solve_two_phase(profile=True)

        assert result.phase2_status == "OPTIMAL"
        assert 6 <= result.combo_count <= 12
        # 321 before Stage 3: that optimum switched off a combo the cube completes
        assert mad_objective_from_result(optimizer, result) == 347
        assert {"S1", "S2"} <= set(result.get_selected_card_names())

        profile = phase_profile(result, "phase2")
        assert profile["counts"] == phase2_counts(MAD_COUNTS, combo_count=2, coverage=1)
        assert profile["solver_stats"]["objective_value"] == pytest.approx(347)


class TestTwoPhaseWithoutProfile:
    """Profile data stays off unless requested."""

    @pytest.mark.parametrize("objective", ["minmax", "mad", "maxutil", "softcap", "tiered"])
    def test_no_profile_data(self, objective: str):
        result = make_optimizer(10, phase2_objective=objective).solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert result.combo_count == 12
        assert result.profile_data is None


class TestPhase2Exactness:
    """Phase 2 reports, and optimizes over, the combos the cube really completes."""

    @staticmethod
    def brute_force_min_range(
        cube_size: int, min_combos: int, max_combos: int, floor: int
    ) -> int | None:
        """Smallest utilization range over every cube of the instance, by enumeration."""
        combos = build_instance()
        cards = sorted(build_candidate_cards(combos))
        best: int | None = None
        for cube in itertools.combinations(cards, cube_size):
            if not min_combos <= len(completable_combo_ids(cube, combos)) <= max_combos:
                continue
            utilization = card_utilization(cube, combos).values()
            if min(utilization) < floor:
                continue
            spread = max(utilization) - min(utilization)
            if best is None or spread < best:
                best = spread
        return best

    @pytest.mark.parametrize(
        ("cube_size", "floor", "tolerance"),
        [(8, 0, 0), (9, 2, 0), (10, 2, 0), (8, 2, 0.25), (9, 0, 0.25), (10, 2, 0.25)],
    )
    def test_minmax_matches_brute_force(self, cube_size: int, floor: int, tolerance: float):
        # The per-card bounds and the exact linking must not cut off the true optimum
        optimizer = make_optimizer(
            cube_size,
            phase2_objective="minmax",
            min_utilization_floor=floor,
            combo_tolerance=tolerance,
            min_coverage_ratio=0,
        )
        result = optimizer.solve_two_phase()
        phase1_count = make_optimizer(cube_size).solve().combo_count
        expected = self.brute_force_min_range(
            cube_size,
            math.floor(phase1_count * (1 - tolerance)),
            math.ceil(phase1_count * (1 + tolerance)),
            floor,
        )

        assert expected is not None
        assert result.phase2_status == "OPTIMAL"
        assert utilization_range(result) == expected

    @pytest.mark.parametrize("objective", ["minmax", "mad", "maxutil", "softcap", "tiered"])
    def test_reported_values_are_true_values(
        self, objective: str, caplog: pytest.LogCaptureFixture
    ):
        combos = build_instance()
        optimizer = make_optimizer(
            8,
            phase2_objective=objective,
            combo_tolerance=0.25,
            min_coverage_ratio=0.5,
            min_combo_threshold=3,
        )
        with caplog.at_level(logging.WARNING):
            result = optimizer.solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        selected = result.get_selected_card_names()
        assert result.completable_combo_ids == completable_combo_ids(selected, combos)
        assert result.combo_count == len(result.completable_combo_ids)
        assert result.utilization_per_card == card_utilization(selected, combos)
        # The solver's combo variables agree with the truth
        assert "disagree" not in caplog.text

    def test_one_sided_model_under_reports_and_is_corrected(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        # Regression test for the pre-Stage 3 model, where y was only bounded from above:
        # with a tolerance window, mad reaches a lower objective by switching off a combo
        # the cube completes. Reported numbers must still come from the selected cards.
        combos = build_instance()
        optimizer = make_optimizer(
            8,
            phase2_objective="mad",
            combo_tolerance=0.25,
            min_coverage_ratio=0.5,
            min_combo_threshold=3,
            min_utilization_floor=0,
        )
        monkeypatch.setattr(ILPOptimizer, "_add_exact_combo_linking", lambda self, base: None)
        with caplog.at_level(logging.WARNING):
            result = optimizer.solve_two_phase(profile=True)

        assert result.phase2_status == "OPTIMAL"
        solver_objective = phase_profile(result, "phase2")["solver_stats"]["objective_value"]
        assert solver_objective == pytest.approx(321)  # the exact model's optimum is 347
        assert "disagree" in caplog.text

        selected = result.get_selected_card_names()
        assert result.completable_combo_ids == completable_combo_ids(selected, combos)
        assert result.combo_count == len(result.completable_combo_ids)
        assert result.utilization_per_card == card_utilization(selected, combos)
        # The true objective of that cube is worse than what the solver believed
        assert mad_objective_from_result(optimizer, result) > 321
