"""Unit tests for ILP data models."""

from mtg_combo_cube.ilp.ilp_models import (
    ComboData,
    OptimizationResult,
    RequirementOption,
    UtilizationStats,
)


class TestComboData:
    """Test ComboData model."""

    def test_all_requirements_resolvable_with_no_requirements(self):
        """Test combo with no optional requirements."""
        combo = ComboData(
            id="combo1",
            required_cards=frozenset(["Card A", "Card B"]),
            requirement_options=[],
            popularity=100,
        )
        assert combo.all_requirements_resolvable() is True

    def test_all_requirements_resolvable_with_valid_requirements(self):
        """Test combo with valid optional requirements."""
        combo = ComboData(
            id="combo1",
            required_cards=frozenset(["Card A"]),
            requirement_options=[
                RequirementOption(
                    "Sac outlet", "scryfall:q=sac outlet", frozenset(["Card B", "Card C"])
                ),
                RequirementOption("Mana dork", "scryfall:q=mana dork", frozenset(["Card D"])),
            ],
            popularity=100,
        )
        assert combo.all_requirements_resolvable() is True

    def test_all_requirements_resolvable_with_empty_requirement(self):
        """Test combo with empty requirement option."""
        combo = ComboData(
            id="combo1",
            required_cards=frozenset(["Card A"]),
            requirement_options=[
                RequirementOption("Empty req", "name:empty req", frozenset()),
                RequirementOption("Valid req", "name:valid req", frozenset(["Card B"])),
            ],
            popularity=100,
        )
        assert combo.all_requirements_resolvable() is False


class TestUtilizationStats:
    """Test UtilizationStats model."""

    def test_creation(self):
        """Test creating utilization stats."""
        stats = UtilizationStats(
            min_utilization=1,
            max_utilization=10,
            mean_utilization=5.5,
            std_deviation=2.87,
            total_absolute_deviation=28,
            median_utilization=6.0,
        )
        assert stats.min_utilization == 1
        assert stats.max_utilization == 10
        assert stats.mean_utilization == 5.5
        assert stats.std_deviation == 2.87
        assert stats.total_absolute_deviation == 28
        assert stats.median_utilization == 6.0


class TestOptimizationResult:
    """Test OptimizationResult model."""

    def test_basic_result(self):
        """Test creating basic optimization result."""
        result = OptimizationResult(
            selected_cards=["Card A", "Card B"],
            completable_combo_ids=["combo1"],
            combo_count=1,
            objective_value=1.0,
            solve_time_seconds=1.5,
            status="OPTIMAL",
        )
        assert result.selected_cards == ["Card A", "Card B"]
        assert result.combo_count == 1
        assert result.is_multi_objective is False
        assert result.utilization_per_card is None

    def test_multi_objective_result(self):
        """Test creating multi-objective optimization result."""
        p1_stats = UtilizationStats(1, 10, 5.0, 3.0, 30, 5.0)
        p2_stats = UtilizationStats(2, 8, 5.0, 2.0, 20, 5.0)

        result = OptimizationResult(
            selected_cards=["Card A", "Card B"],
            completable_combo_ids=["combo1"],
            combo_count=1,
            objective_value=1.0,
            solve_time_seconds=3.0,
            status="OPTIMAL",
            utilization_per_card={"Card A": 1, "Card B": 1},
            phase1_utilization_stats=p1_stats,
            phase2_utilization_stats=p2_stats,
            phase1_solve_time=1.5,
            phase2_solve_time=1.5,
            phase2_status="OPTIMAL",
            is_multi_objective=True,
        )
        assert result.is_multi_objective is True
        assert result.phase1_utilization_stats == p1_stats
        assert result.phase2_utilization_stats == p2_stats
        assert result.phase1_solve_time == 1.5
        assert result.phase2_solve_time == 1.5

    def test_backward_compatibility(self):
        """Test that optional fields have defaults."""
        result = OptimizationResult(
            selected_cards=[],
            completable_combo_ids=[],
            combo_count=0,
            objective_value=0.0,
            solve_time_seconds=0.0,
            status="INFEASIBLE",
        )
        assert result.utilization_per_card is None
        assert result.phase1_utilization_stats is None
        assert result.phase2_utilization_stats is None
        assert result.is_multi_objective is False
