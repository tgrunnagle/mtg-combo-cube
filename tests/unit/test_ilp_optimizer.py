"""Unit tests for ILP optimizer."""

import pytest

from mtg_combo_cube.ilp.ilp_models import ComboData
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer


class TestILPOptimizerInit:
    """Test ILPOptimizer initialization."""

    def test_empty_combos(self):
        """Test optimizer with no combos."""
        optimizer = ILPOptimizer(combos=[], cube_size=10)
        assert optimizer.combos == []
        assert optimizer.all_cards == []
        assert optimizer.card_to_combos == {}

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
                requirement_options=[frozenset(["Card D"])],
                popularity=50,
            ),
        ]
        optimizer = ILPOptimizer(combos=combos, cube_size=4)
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
                requirement_options=[frozenset(["Card C", "Card D"])],
                popularity=50,
            ),
        ]
        optimizer = ILPOptimizer(combos=combos, cube_size=4)

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
        optimizer = ILPOptimizer(combos=[], cube_size=10)

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
        optimizer = ILPOptimizer(combos=combos, cube_size=3)

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
                requirement_options=[frozenset(["Card B", "Card C"])],
                popularity=100,
            ),
        ]
        optimizer = ILPOptimizer(combos=combos, cube_size=2)

        selected = ["Card A", "Card B"]
        completed = ["combo1"]

        utilization = optimizer._calculate_utilization(selected, completed)

        assert utilization["Card A"] == 1  # Required card
        assert utilization["Card B"] == 1  # Optional but used

    def test_compute_utilization_stats_empty(self):
        """Test utilization stats with empty input."""
        optimizer = ILPOptimizer(combos=[], cube_size=10)
        stats = optimizer._compute_utilization_stats({})

        assert stats.min_utilization == 0
        assert stats.max_utilization == 0
        assert stats.mean_utilization == 0.0
        assert stats.std_deviation == 0.0

    def test_compute_utilization_stats(self):
        """Test utilization stats computation."""
        optimizer = ILPOptimizer(combos=[], cube_size=10)
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
        optimizer = ILPOptimizer(combos=[], cube_size=10)
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
        optimizer = ILPOptimizer(combos=[], cube_size=10)
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
        optimizer = ILPOptimizer(combos=[], cube_size=10, time_limit_seconds=1)
        result = optimizer.solve()

        assert result.status == "OPTIMAL"
        assert result.combo_count == 0
        assert result.selected_cards == []

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
        optimizer = ILPOptimizer(combos=combos, cube_size=10, time_limit_seconds=1)
        result = optimizer.solve()

        assert result.status == "INFEASIBLE"
        assert result.combo_count == 0

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
        optimizer = ILPOptimizer(combos=combos, cube_size=2, time_limit_seconds=5)
        result = optimizer.solve()

        assert result.status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 1
        assert set(result.selected_cards) == {"Card A", "Card B"}
        assert result.completable_combo_ids == ["combo1"]

    def test_solve_with_optional_requirements(self):
        """Test solving with optional requirements."""
        combos = [
            ComboData(
                id="combo1",
                required_cards=frozenset(["Card A"]),
                requirement_options=[frozenset(["Card B", "Card C"])],
                popularity=100,
            ),
        ]
        optimizer = ILPOptimizer(combos=combos, cube_size=2, time_limit_seconds=5)
        result = optimizer.solve()

        assert result.status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 1
        assert "Card A" in result.selected_cards
        # Should select either Card B or Card C (or both)
        assert "Card B" in result.selected_cards or "Card C" in result.selected_cards

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
        optimizer = ILPOptimizer(combos=combos, cube_size=4, time_limit_seconds=5)
        result = optimizer.solve()

        assert result.status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 2
        assert set(result.selected_cards) == {"Card A", "Card B", "Card C", "Card D"}

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
        optimizer = ILPOptimizer(combos=combos, cube_size=3, time_limit_seconds=5)
        result = optimizer.solve()

        assert result.status in ("OPTIMAL", "FEASIBLE")
        assert result.combo_count == 2  # Both combos completable
        assert set(result.selected_cards) == {"Card A", "Card B", "Card C"}

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
        optimizer = ILPOptimizer(combos=combos, cube_size=2, time_limit_seconds=5)
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
        optimizer = ILPOptimizer(combos=[], cube_size=10, time_limit_seconds=1)
        result = optimizer.solve_two_phase()

        assert result.status == "OPTIMAL"
        assert result.combo_count == 0
        assert result.is_multi_objective is False  # Phase 2 skipped

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
        optimizer = ILPOptimizer(combos=combos, cube_size=4, time_limit_seconds=10)
        result = optimizer.solve_two_phase()

        assert result.status in ("OPTIMAL", "FEASIBLE")
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
        """Test that Phase 2 preserves combo count from Phase 1."""
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
        optimizer = ILPOptimizer(combos=combos, cube_size=4, time_limit_seconds=10)

        # Run Phase 1 only
        phase1_result = optimizer.solve()
        phase1_count = phase1_result.combo_count

        # Run two-phase
        two_phase_result = optimizer.solve_two_phase()

        # Combo count should be preserved
        assert two_phase_result.combo_count == phase1_count

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
        optimizer = ILPOptimizer(combos=combos, cube_size=6, time_limit_seconds=15)
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
