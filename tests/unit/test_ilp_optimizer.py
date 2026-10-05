"""Unit tests for ILP optimizer."""

import pytest

from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData, RequirementOption
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer


def build_candidate_cards(combos: list[ComboData]) -> dict[str, CandidateCard]:
    """Build candidate cards from combos for testing."""
    from collections import defaultdict

    card_combo_ids: dict[str, set[str]] = defaultdict(set)
    card_requirement_keys: dict[str, set[str]] = defaultdict(set)

    for combo in combos:
        for card in combo.required_cards:
            card_combo_ids[card].add(combo.id)
        for opt in combo.requirement_options:
            for card in opt.cards:
                card_requirement_keys[card].add(opt.group_key)

    all_card_names = set(card_combo_ids.keys()) | set(card_requirement_keys.keys())
    return {
        name: CandidateCard(
            name=name,
            combo_ids=frozenset(card_combo_ids.get(name, set())),
            requirement_group_keys=frozenset(card_requirement_keys.get(name, set())),
        )
        for name in all_card_names
    }


class TestILPOptimizerInit:
    """Test ILPOptimizer initialization."""

    def test_empty_combos(self):
        """Test optimizer with no combos."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        assert optimizer.combos == []
        assert optimizer.all_cards == []
        assert optimizer.card_to_combos == {}

    def test_num_workers_default(self):
        """Test default worker count."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        assert optimizer.num_workers == 8

    def test_num_workers_stored(self):
        """Test that the worker count parameter is accepted and stored."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10, num_workers=4)
        assert optimizer.num_workers == 4

    def test_phase2_objective_default(self):
        """The default Phase 2 objective is "tiered" (Stage 4 decision)."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        assert optimizer.phase2_objective == "tiered"
        assert optimizer.util_cap is None

    def test_unknown_phase2_objective_raises(self):
        """An unknown Phase 2 objective is an error, not a silent fallback."""
        with pytest.raises(ValueError, match="Unknown phase2_objective: 'maxmin'"):
            ILPOptimizer(combos=[], candidate_cards={}, cube_size=10, phase2_objective="maxmin")

    @pytest.mark.parametrize("objective", ["mad", "minmax", "maxutil", "softcap", "tiered"])
    def test_known_phase2_objectives_accepted(self, objective: str):
        optimizer = ILPOptimizer(
            combos=[], candidate_cards={}, cube_size=10, phase2_objective=objective
        )
        assert optimizer.phase2_objective == objective

    def test_collect_all_cards(self):
        """Test card collection from combos."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B", "Card C"]),
                requirement_options=[
                    RequirementOption("Sac outlet", "scryfall:q=sac outlet", frozenset(["Card D"]))
                ],
                popularity=50,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(combos=combos, candidate_cards=candidate_cards, cube_size=4)
        assert set(optimizer.all_cards) == {"Card A", "Card B", "Card C", "Card D"}

    def test_build_participation_graph(self):
        """Test participation graph construction."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B"]),
                requirement_options=[
                    RequirementOption(
                        "Sac outlet", "scryfall:q=sac outlet", frozenset(["Card C", "Card D"])
                    )
                ],
                popularity=50,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(combos=combos, candidate_cards=candidate_cards, cube_size=4)

        # Card A appears in combo1 only
        assert len(optimizer.card_to_combos["Card A"]) == 1
        assert optimizer.card_to_combos["Card A"][0].id == "combo1"

        # Card B appears in both combos
        assert len(optimizer.card_to_combos["Card B"]) == 2
        combo_ids = {c.id for c in optimizer.card_to_combos["Card B"]}
        assert combo_ids == {"combo1", "combo2"}

        # Card C and D appear in combo2 (optional requirement)
        assert len(optimizer.card_to_combos["Card C"]) == 1
        assert optimizer.card_to_combos["Card C"][0].id == "combo2"
        assert len(optimizer.card_to_combos["Card D"]) == 1
        assert optimizer.card_to_combos["Card D"][0].id == "combo2"


class TestILPOptimizerHelpers:
    """Test ILPOptimizer helper methods."""

    def test_compute_weight(self):
        """Test weight computation with popularity."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)

        # Base weight (popularity=0) should be ~1.0 * WEIGHT_SCALE
        weight_0 = optimizer._compute_weight(0)
        assert weight_0 == 10000  # 1.0 * 10000

        # Higher popularity should increase weight slightly
        weight_100 = optimizer._compute_weight(100)
        assert weight_100 > weight_0
        assert weight_100 < 10100  # Should be small increase due to epsilon

    def test_calculate_utilization(self):
        """Test utilization calculation."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B", "Card C"]),
                requirement_options=[],
                popularity=50,
            ),
            ComboData(
                id="combo3",
                required_cards=frozenset(["Card A", "Card C"]),
                requirement_options=[],
                popularity=25,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(combos=combos, candidate_cards=candidate_cards, cube_size=3)

        selected = ["Card A", "Card B", "Card C"]
        completed = ["combo1", "combo2"]  # combo3 not completed

        utilization = optimizer._calculate_utilization(selected, completed)

        assert utilization["Card A"] == 1  # In combo1 only (combo3 not completed)
        assert utilization["Card B"] == 2  # In combo1 and combo2
        assert utilization["Card C"] == 1  # In combo2 only (combo3 not completed)

    def test_calculate_utilization_with_optional_requirements(self):
        """Test utilization with optional requirements."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[
                    RequirementOption(
                        "Sac outlet", "scryfall:q=sac outlet", frozenset(["Card B", "Card C"])
                    )
                ],
                popularity=100,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(combos=combos, candidate_cards=candidate_cards, cube_size=2)

        selected = ["Card A", "Card B"]
        completed = ["combo1"]

        utilization = optimizer._calculate_utilization(selected, completed)

        assert utilization["Card A"] == 1  # Required card
        assert utilization["Card B"] == 1  # Optional but used

    def test_compute_utilization_stats_empty(self):
        """Test utilization stats with empty input."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        stats = optimizer._compute_utilization_stats({})

        assert stats.min_utilization == 0
        assert stats.max_utilization == 0
        assert stats.mean_utilization == 0.0
        assert stats.std_deviation == 0.0

    def test_compute_utilization_stats(self):
        """Test utilization stats computation."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        utilization = {
            "Card A": 1,
            "Card B": 3,
            "Card C": 5,
            "Card D": 7,
            "Card E": 9,
        }
        stats = optimizer._compute_utilization_stats(utilization)

        assert stats.min_utilization == 1
        assert stats.max_utilization == 9
        assert stats.mean_utilization == 5.0
        assert stats.median_utilization == 5.0
        assert stats.std_deviation == pytest.approx(2.828, rel=0.01)
        # MAD = |1-5| + |3-5| + |5-5| + |7-5| + |9-5| = 4+2+0+2+4 = 12
        assert stats.total_absolute_deviation == 12

    def test_compute_utilization_stats_even_count(self):
        """Test median calculation with even number of values."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        utilization = {
            "Card A": 2,
            "Card B": 4,
            "Card C": 6,
            "Card D": 8,
        }
        stats = optimizer._compute_utilization_stats(utilization)

        # Median of [2, 4, 6, 8] is (4 + 6) / 2 = 5.0
        assert stats.median_utilization == 5.0

    def test_compute_utilization_stats_odd_count(self):
        """Test median calculation with odd number of values."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10)
        utilization = {
            "Card A": 1,
            "Card B": 5,
            "Card C": 9,
        }
        stats = optimizer._compute_utilization_stats(utilization)

        # Median of [1, 5, 9] is 5.0
        assert stats.median_utilization == 5.0


class TestILPOptimizerSolve:
    """Test ILPOptimizer solve methods."""

    def test_solve_empty_combos(self):
        """Test solving with no combos."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10, time_limit_seconds=1)
        result = optimizer.solve()

        assert result.phase1_status == "OPTIMAL"
        assert result.combo_count == 0
        assert result.selected_cards == []
        # Early return: elapsed time must come from a single clock
        assert 0 <= result.solve_time_seconds < 1

    def test_solve_insufficient_cards(self):
        """Test solving when not enough cards available."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[],
                popularity=100,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=10, time_limit_seconds=1
        )
        result = optimizer.solve()

        assert result.phase1_status == "INFEASIBLE"
        assert result.combo_count == 0
        # Early return: elapsed time must come from a single clock
        assert 0 <= result.solve_time_seconds < 1

    def test_solve_simple_combo(self):
        """Test solving with a simple combo."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=2, time_limit_seconds=5
        )
        result = optimizer.solve()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 1
        assert set(result.get_selected_card_names()) == {"Card A", "Card B"}
        assert result.completable_combo_ids == ["combo1"]

    def test_solve_with_optional_requirements(self):
        """Test solving with optional requirements."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[
                    RequirementOption(
                        "Sac outlet", "scryfall:q=sac outlet", frozenset(["Card B", "Card C"])
                    )
                ],
                popularity=100,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=2, time_limit_seconds=5
        )
        result = optimizer.solve()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 1
        selected_names = result.get_selected_card_names()
        assert "Card A" in selected_names
        # Should select either Card B or Card C (or both)
        assert "Card B" in selected_names or "Card C" in selected_names

    def test_solve_multiple_combos(self):
        """Test solving with multiple combos."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card C", "Card D"]),
                requirement_options=[],
                popularity=50,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=4, time_limit_seconds=5
        )
        result = optimizer.solve()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 2
        assert set(result.get_selected_card_names()) == {"Card A", "Card B", "Card C", "Card D"}

    def test_solve_overlapping_combos(self):
        """Test solving with combos sharing cards."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B", "Card C"]),
                requirement_options=[],
                popularity=50,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=3, time_limit_seconds=5
        )
        result = optimizer.solve()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 2  # Both combos completable
        assert set(result.get_selected_card_names()) == {"Card A", "Card B", "Card C"}

    def test_solve_populates_utilization(self):
        """Test that solve() populates utilization data."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=2, time_limit_seconds=5
        )
        result = optimizer.solve()

        assert result.utilization_per_card is not None
        assert result.phase1_utilization_stats is not None
        assert result.phase1_solve_time is not None
        assert result.is_multi_objective is False

        # Both cards should have utilization of 1
        assert result.utilization_per_card["Card A"] == 1
        assert result.utilization_per_card["Card B"] == 1


class TestILPOptimizerTwoPhase:
    """Test two-phase optimization."""

    def test_solve_two_phase_empty_combos(self):
        """Test two-phase with no combos."""
        optimizer = ILPOptimizer(combos=[], candidate_cards={}, cube_size=10, time_limit_seconds=1)
        result = optimizer.solve_two_phase()

        assert result.phase1_status == "OPTIMAL"
        assert result.combo_count == 0
        assert result.is_multi_objective is False  # Phase 2 skipped

    @pytest.mark.parametrize("objective", ["mad", "minmax", "maxutil", "softcap", "tiered"])
    def test_phase2_rejects_result_without_phase1_stats(self, objective: str):
        """Every Phase 2 objective refuses a Phase 1 result that has no stats."""
        optimizer = ILPOptimizer(
            combos=[],
            candidate_cards={},
            cube_size=10,
            time_limit_seconds=1,
            phase2_objective=objective,
        )
        phase1_result = optimizer.solve()  # early return: no utilization stats
        assert phase1_result.phase1_utilization_stats is None

        with pytest.raises(ValueError, match="successful Phase 1 result"):
            optimizer._solve_phase2(phase1_result=phase1_result)

    def test_solve_two_phase_simple(self):
        """Test two-phase with simple combos."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card C", "Card D"]),
                requirement_options=[],
                popularity=50,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=4,
            time_limit_seconds=10,
            min_utilization_floor=0,  # Disable floor since cards only in 1 combo each
        )
        result = optimizer.solve_two_phase()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 2
        assert result.is_multi_objective is True

        # Phase 1 and Phase 2 stats should both exist
        assert result.phase1_utilization_stats is not None
        assert result.phase2_utilization_stats is not None
        assert result.phase1_solve_time is not None
        assert result.phase2_solve_time is not None

        # Combo count preserved
        assert result.combo_count == 2

    def test_solve_two_phase_preserves_combo_count(self):
        """Test that Phase 2 preserves combo count from Phase 1 with zero tolerance."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B", "Card C"]),
                requirement_options=[],
                popularity=80,
            ),
            ComboData(
                id="combo3",
                required_cards=frozenset(["Card C", "Card D"]),
                requirement_options=[],
                popularity=60,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        # Use combo_tolerance=0 to enforce strict combo count preservation
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=4,
            time_limit_seconds=10,
            combo_tolerance=0,
        )

        # Run Phase 1 only
        phase1_result = optimizer.solve()
        phase1_count = phase1_result.combo_count

        # Run two-phase
        two_phase_result = optimizer.solve_two_phase()

        # Combo count should be preserved exactly
        assert two_phase_result.combo_count == phase1_count

    def test_solve_two_phase_with_tolerance(self):
        """Test that Phase 2 allows combo count deviation within tolerance."""
        import math

        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B", "Card C"]),
                requirement_options=[],
                popularity=80,
            ),
            ComboData(
                id="combo3",
                required_cards=frozenset(["Card C", "Card D"]),
                requirement_options=[],
                popularity=60,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        # Use 10% tolerance (default)
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=4,
            time_limit_seconds=10,
            combo_tolerance=0.1,
        )

        # Run Phase 1 only
        phase1_result = optimizer.solve()
        phase1_count = phase1_result.combo_count

        # Run two-phase
        two_phase_result = optimizer.solve_two_phase()

        # Combo count should be within tolerance range
        min_expected = math.floor(phase1_count * 0.9)
        max_expected = math.ceil(phase1_count * 1.1)
        assert min_expected <= two_phase_result.combo_count <= max_expected

    def test_solve_two_phase_improves_utilization(self):
        """Test that Phase 2 improves utilization balance."""
        # Create combos where some cards are heavily utilized
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card A", "Card C"]),
                requirement_options=[],
                popularity=90,
            ),
            ComboData(
                id="combo3",
                required_cards=frozenset(["Card A", "Card D"]),
                requirement_options=[],
                popularity=80,
            ),
            ComboData(
                id="combo4",
                required_cards=frozenset(["Card E", "Card F"]),
                requirement_options=[],
                popularity=70,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=6, time_limit_seconds=15
        )
        result = optimizer.solve_two_phase()

        if result.is_multi_objective and result.phase2_utilization_stats:
            p1 = result.phase1_utilization_stats
            p2 = result.phase2_utilization_stats

            assert p1 is not None
            # Phase 2 should have equal or better balance
            # (std_dev or total_absolute_deviation should be <= Phase 1)
            assert (
                p2.std_deviation <= p1.std_deviation + 0.1  # Allow small tolerance
                or p2.total_absolute_deviation <= p1.total_absolute_deviation
            )


class TestUtilizationFloor:
    """The utilization floor applies to every Phase 2 objective."""

    @staticmethod
    def build_optimizer(objective: str, floor: int) -> ILPOptimizer:
        # Phase 1 (5 cards) takes A, B, C, D and T: 5 combos, T alone completes three.
        # Phase 2 may drop to 2 combos. For "mad", T (utilization 3, far above the mean of
        # 1.4) is worse than an unused card of the 4-card combo (utilization 0), so without
        # a floor it swaps T for a dead card.
        combos = [
            ComboData("ab", frozenset(["A", "B"]), [], 100),
            ComboData("cd", frozenset(["C", "D"]), [], 100),
            ComboData("t1", frozenset(["T"]), [], 100),
            ComboData("t2", frozenset(["T"]), [], 100),
            ComboData("t3", frozenset(["T"]), [], 100),
            ComboData("big", frozenset(["W", "X", "Y", "Z"]), [], 100),
        ]
        return ILPOptimizer(
            combos=combos,
            candidate_cards=build_candidate_cards(combos),
            cube_size=5,
            time_limit_seconds=30,
            combo_tolerance=0.6,
            min_coverage_ratio=0,
            gap_limit=0,
            phase2_objective=objective,
            min_utilization_floor=floor,
            num_workers=1,
        )

    def test_mad_without_floor_selects_dead_card(self):
        result = self.build_optimizer("mad", floor=0).solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert result.phase2_utilization_stats is not None
        assert result.phase2_utilization_stats.min_utilization == 0
        assert "T" not in result.get_selected_card_names()

    @pytest.mark.parametrize("objective", ["mad", "minmax", "maxutil", "softcap", "tiered"])
    def test_floor_respected(self, objective: str):
        result = self.build_optimizer(objective, floor=1).solve_two_phase()

        assert result.phase2_status == "OPTIMAL"
        assert result.utilization_per_card is not None
        assert min(result.utilization_per_card.values()) >= 1
        assert "T" in result.get_selected_card_names()  # every floor-1 cube needs T

    @pytest.mark.parametrize("objective", ["mad", "minmax", "maxutil", "softcap", "tiered"])
    def test_unreachable_floor_falls_back_with_trace(self, objective: str):
        # Only T can reach utilization 2, so no 5-card cube satisfies the floor
        result = self.build_optimizer(objective, floor=2).solve_two_phase()

        assert result.phase2_fell_back is True
        assert result.phase2_status == "INFEASIBLE"
        assert result.phase2_solve_time is not None
        assert result.is_multi_objective is False
        assert result.combo_count == 5


class TestCoverageConstraints:
    """Test minimum coverage ratio constraints."""

    def test_coverage_constraint_enforces_minimum(self):
        """Test that coverage constraint forces multiple cards for popular requirements."""
        # Create combos where many share the same requirement
        combos = []
        # 20 combos all requiring a "sac outlet" (Card B, C, or D)
        for i in range(20):
            combos.append(
                ComboData(
                    id=f"combo{i}",
                    required_cards=frozenset([f"Card A{i}"]),  # Unique required card
                    requirement_options=[
                        RequirementOption(
                            "Sac outlet",
                            "scryfall:q=sac outlet",
                            frozenset(["Card B", "Card C", "Card D"]),
                        )
                    ],
                    popularity=100 - i,
                )
            )

        candidate_cards = build_candidate_cards(combos)
        # With min_coverage_ratio=0.1 and 20 combos, need at least 2 sac outlets
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=22,  # 20 unique + 2 sac outlets
            time_limit_seconds=30,
            min_coverage_ratio=0.1,
            min_combo_threshold=5,
        )

        result = optimizer.solve_two_phase()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        # Should have at least 2 of the sac outlet cards
        selected_names = set(result.get_selected_card_names())
        sac_outlets = {"Card B", "Card C", "Card D"} & selected_names
        assert len(sac_outlets) >= 2

    def test_coverage_constraint_soft_when_insufficient_cards(self):
        """Test that constraint is soft when not enough cards exist."""
        # Only 1 card can satisfy the requirement
        combos = []
        for i in range(20):
            combos.append(
                ComboData(
                    id=f"combo{i}",
                    required_cards=frozenset([f"Card A{i}"]),
                    requirement_options=[
                        RequirementOption(
                            "Rare ability",
                            "scryfall:q=rare ability",
                            frozenset(["Only Option"]),  # Only 1 card
                        )
                    ],
                    popularity=100 - i,
                )
            )

        candidate_cards = build_candidate_cards(combos)
        # Would need 2 cards but only 1 exists
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=21,
            time_limit_seconds=30,
            min_coverage_ratio=0.1,
            min_combo_threshold=5,
        )

        # Should still succeed (soft constraint)
        result = optimizer.solve_two_phase()
        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert "Only Option" in result.get_selected_card_names()

    def test_coverage_constraint_respects_threshold(self):
        """Test that small combo counts don't trigger constraint."""
        combos = []
        # Only 5 combos with same requirement
        for i in range(5):
            combos.append(
                ComboData(
                    id=f"combo{i}",
                    required_cards=frozenset([f"Card A{i}"]),
                    requirement_options=[
                        RequirementOption(
                            "Sac outlet",
                            "scryfall:q=sac outlet",
                            frozenset(["Card B", "Card C"]),
                        )
                    ],
                    popularity=100,
                )
            )

        candidate_cards = build_candidate_cards(combos)
        # min_combo_threshold=10, so 5 combos won't trigger constraint
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=6,
            time_limit_seconds=30,
            min_coverage_ratio=0.5,  # Would require 3 cards if threshold met
            min_combo_threshold=10,
        )

        result = optimizer.solve_two_phase()
        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        # Can get away with just 1 sac outlet since threshold not met
        selected_names = set(result.get_selected_card_names())
        sac_outlets = {"Card B", "Card C"} & selected_names
        assert len(sac_outlets) >= 1  # At least 1, but not forced to be more

    def test_build_requirement_pool_info(self):
        """Test requirement pool info building."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[
                    RequirementOption(
                        "Sac outlet",
                        "scryfall:q=sac outlet",
                        frozenset(["Card B", "Card C"]),
                    )
                ],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card D"]),
                requirement_options=[
                    RequirementOption(
                        "Sacrifice outlet",  # Different display name, same group_key
                        "scryfall:q=sac outlet",
                        frozenset(["Card B", "Card E"]),  # Overlapping cards
                    )
                ],
                popularity=50,
            ),
        ]

        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(combos=combos, candidate_cards=candidate_cards, cube_size=4)
        pool_info = optimizer._build_requirement_pool_info()

        assert "scryfall:q=sac outlet" in pool_info
        info = pool_info["scryfall:q=sac outlet"]
        assert info.combo_count == 2
        assert info.pool_cards == frozenset(["Card B", "Card C", "Card E"])

    def test_coverage_constraint_disabled_when_zero(self):
        """Test that coverage constraints are disabled when min_coverage_ratio=0."""
        combos = []
        for i in range(20):
            combos.append(
                ComboData(
                    id=f"combo{i}",
                    required_cards=frozenset([f"Card A{i}"]),
                    requirement_options=[
                        RequirementOption(
                            "Sac outlet",
                            "scryfall:q=sac outlet",
                            frozenset(["Card B", "Card C", "Card D"]),
                        )
                    ],
                    popularity=100 - i,
                )
            )

        candidate_cards = build_candidate_cards(combos)
        # Disable coverage constraints
        optimizer = ILPOptimizer(
            combos=combos,
            candidate_cards=candidate_cards,
            cube_size=21,  # Only room for 1 sac outlet
            time_limit_seconds=30,
            min_coverage_ratio=0,  # Disabled
            min_combo_threshold=5,
        )

        result = optimizer.solve_two_phase()
        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        # With constraint disabled, optimizer can use just 1 sac outlet
        selected_names = set(result.get_selected_card_names())
        sac_outlets = {"Card B", "Card C", "Card D"} & selected_names
        assert len(sac_outlets) >= 1


class TestCandidateCard:
    """Test CandidateCard dataclass properties."""

    def test_template_count(self):
        """Test template_count property."""
        card = CandidateCard(
            name="Test Card",
            combo_ids=frozenset(["combo1"]),
            requirement_group_keys=frozenset(["key1", "key2", "key3"]),
        )
        assert card.template_count == 3

    def test_template_count_empty(self):
        """Test template_count with no requirements."""
        card = CandidateCard(
            name="Test Card",
            combo_ids=frozenset(["combo1"]),
            requirement_group_keys=frozenset(),
        )
        assert card.template_count == 0

    def test_is_multi_template_true(self):
        """Test is_multi_template when card satisfies 2+ templates."""
        card = CandidateCard(
            name="Test Card",
            combo_ids=frozenset(),
            requirement_group_keys=frozenset(["key1", "key2"]),
        )
        assert card.is_multi_template is True

    def test_is_multi_template_false(self):
        """Test is_multi_template when card satisfies 0-1 templates."""
        card1 = CandidateCard(
            name="Card 1",
            combo_ids=frozenset(),
            requirement_group_keys=frozenset(["key1"]),
        )
        card2 = CandidateCard(
            name="Card 2",
            combo_ids=frozenset(),
            requirement_group_keys=frozenset(),
        )
        assert card1.is_multi_template is False
        assert card2.is_multi_template is False


class TestCrossTemplateStats:
    """Test cross-template overlap statistics calculation."""

    def test_cross_template_stats_basic(self):
        """Test cross-template stats with multi-template cards."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[
                    RequirementOption(
                        "Persist Creature",
                        "scryfall:q=keyword:persist",
                        frozenset(["Kitchen Finks", "Murderous Redcap"]),
                    )
                ],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B"]),
                requirement_options=[
                    RequirementOption(
                        "Green Persist",
                        "scryfall:q=c:g keyword:persist",
                        frozenset(["Kitchen Finks"]),  # Overlaps with first requirement
                    )
                ],
                popularity=80,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=4, time_limit_seconds=10
        )
        result = optimizer.solve()

        assert result.cross_template_stats is not None
        stats = result.cross_template_stats

        # Kitchen Finks satisfies 2 templates
        if "Kitchen Finks" in result.selected_cards:
            assert stats.multi_template_card_count >= 1
            assert stats.max_templates_per_card >= 2

    def test_cross_template_stats_no_overlap(self):
        """Test cross-template stats when no cards satisfy multiple templates."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A", "Card B"]),
                requirement_options=[],
                popularity=100,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=2, time_limit_seconds=5
        )
        result = optimizer.solve()

        assert result.cross_template_stats is not None
        stats = result.cross_template_stats

        # No requirement options, so no templates satisfied
        assert stats.multi_template_card_count == 0
        assert stats.max_templates_per_card == 0

    def test_cross_template_stats_template_pair_overlap(self):
        """Test template pair overlap calculation."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[
                    RequirementOption(
                        "Template 1",
                        "key1",
                        frozenset(["Shared Card", "Unique 1"]),
                    )
                ],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Card B"]),
                requirement_options=[
                    RequirementOption(
                        "Template 2",
                        "key2",
                        frozenset(["Shared Card", "Unique 2"]),
                    )
                ],
                popularity=80,
            ),
        ]
        candidate_cards = build_candidate_cards(combos)
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=4, time_limit_seconds=10
        )
        result = optimizer.solve()

        assert result.cross_template_stats is not None
        stats = result.cross_template_stats

        # If both combos completed and Shared Card selected, should see overlap
        selected_names = result.get_selected_card_names()
        if "Shared Card" in selected_names:
            # Shared Card satisfies both templates
            assert stats.multi_template_card_count >= 1

    def test_multi_template_card_satisfies_multiple_combos(self):
        """Test that a single multi-template card can satisfy requirements for multiple combos.

        This verifies that the ILP constraint logic correctly handles cards that appear
        in multiple requirement option sets with different group_keys.
        """
        # Setup: "Versatile Card" satisfies both "Persist Creature" and "ETB Creature"
        # requirements, allowing it to complete two different combos with just one card.
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Combo Piece A"]),
                requirement_options=[
                    RequirementOption(
                        "Persist Creature",
                        "scryfall:q=keyword:persist",
                        frozenset(["Versatile Card", "Persist Only"]),
                    )
                ],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Combo Piece B"]),
                requirement_options=[
                    RequirementOption(
                        "ETB Creature",
                        "scryfall:q=keyword:etb",
                        frozenset(["Versatile Card", "ETB Only"]),
                    )
                ],
                popularity=100,
            ),
        ]

        candidate_cards = build_candidate_cards(combos)

        # Verify CandidateCard tracking: Versatile Card should have 2 requirement_group_keys
        assert "Versatile Card" in candidate_cards
        versatile = candidate_cards["Versatile Card"]
        assert versatile.template_count == 2
        assert versatile.is_multi_template is True
        assert "scryfall:q=keyword:persist" in versatile.requirement_group_keys
        assert "scryfall:q=keyword:etb" in versatile.requirement_group_keys

        # With cube_size=3, solver can complete both combos using Versatile Card
        # for both requirements instead of needing separate cards
        optimizer = ILPOptimizer(
            combos=combos, candidate_cards=candidate_cards, cube_size=3, time_limit_seconds=10
        )
        result = optimizer.solve()

        assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 2  # Both combos should be completable
        assert set(result.completable_combo_ids) == {"combo1", "combo2"}

        # The Versatile Card should be selected since it enables completing both combos
        selected_names = result.get_selected_card_names()
        assert "Versatile Card" in selected_names
        assert "Combo Piece A" in selected_names
        assert "Combo Piece B" in selected_names

        # Cross-template stats should reflect the multi-template card
        assert result.cross_template_stats is not None
        assert result.cross_template_stats.multi_template_card_count >= 1

    def test_multi_template_card_tracked_in_candidate_cards(self):
        """Test that cards satisfying multiple different requirement group_keys are tracked."""
        # Create combos where the same card appears in different requirement options
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Base A"]),
                requirement_options=[
                    RequirementOption("Type 1", "group_key_1", frozenset(["Multi Card", "Card X"]))
                ],
                popularity=100,
            ),
            ComboData(
                id="combo2",
                required_cards=frozenset(["Base B"]),
                requirement_options=[
                    RequirementOption("Type 2", "group_key_2", frozenset(["Multi Card", "Card Y"]))
                ],
                popularity=100,
            ),
            ComboData(
                id="combo3",
                required_cards=frozenset(["Base C"]),
                requirement_options=[
                    RequirementOption("Type 3", "group_key_3", frozenset(["Multi Card", "Card Z"]))
                ],
                popularity=100,
            ),
        ]

        candidate_cards = build_candidate_cards(combos)

        # Multi Card should track all 3 group keys
        assert "Multi Card" in candidate_cards
        multi_card = candidate_cards["Multi Card"]
        assert multi_card.template_count == 3
        assert multi_card.is_multi_template is True
        assert multi_card.requirement_group_keys == frozenset(
            ["group_key_1", "group_key_2", "group_key_3"]
        )

        # Single-template cards should only have 1 key
        assert candidate_cards["Card X"].template_count == 1
        assert candidate_cards["Card Y"].template_count == 1
        assert candidate_cards["Card Z"].template_count == 1
