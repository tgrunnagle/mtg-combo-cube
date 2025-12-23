"""Unit tests for ILP runner utilities."""

import json
import tempfile
from pathlib import Path

import pytest

from mtg_combo_cube.ilp.ilp_models import CandidateCard, OptimizationResult, UtilizationStats
from mtg_combo_cube.ilp.ilp_runner import write_utilization_stats


def make_candidate_cards(names: list[str]) -> list[CandidateCard]:
    """Helper to create CandidateCard objects from names for testing."""
    return [
        CandidateCard(name=name, combo_ids=frozenset(), requirement_group_keys=frozenset())
        for name in names
    ]


class TestWriteUtilizationStats:
    """Test write_utilization_stats function."""

    def test_write_stats_single_phase(self):
        """Test writing stats for single-phase optimization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            result = OptimizationResult(
                selected_cards=make_candidate_cards(["Card A", "Card B", "Card C"]),
                completable_combo_ids=["combo1", "combo2"],
                combo_count=2,
                objective_value=2.0,
                solve_time_seconds=1.5,
                phase1_status="OPTIMAL",
                utilization_per_card={"Card A": 2, "Card B": 1, "Card C": 1},
                phase1_utilization_stats=UtilizationStats(
                    min_utilization=1,
                    max_utilization=2,
                    mean_utilization=1.33,
                    std_deviation=0.47,
                    total_absolute_deviation=1,
                    median_utilization=1.0,
                ),
                phase1_solve_time=1.5,
                is_multi_objective=False,
            )

            write_utilization_stats(result, output_file, 3)

            assert Path(stats_file).exists()

            with open(stats_file) as f:
                stats = json.load(f)

            assert stats["metadata"]["cube_size"] == 3
            assert stats["metadata"]["combo_count"] == 2
            assert stats["metadata"]["optimization_method"] == "single_phase"
            assert stats["metadata"]["phase1_status"] == "OPTIMAL"
            assert stats["metadata"]["total_solve_time_seconds"] == 1.5

            assert stats["phase1"] is not None
            assert stats["phase1"]["min_utilization"] == 1
            assert stats["phase1"]["max_utilization"] == 2

            assert stats["phase2"] is None
            assert stats["improvement"] is None

    def test_write_stats_two_phase(self):
        """Test writing stats for two-phase optimization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            p1_stats = UtilizationStats(
                min_utilization=1,
                max_utilization=5,
                mean_utilization=3.0,
                std_deviation=1.5,
                total_absolute_deviation=10,
                median_utilization=3.0,
            )
            p2_stats = UtilizationStats(
                min_utilization=2,
                max_utilization=4,
                mean_utilization=3.0,
                std_deviation=0.8,
                total_absolute_deviation=6,
                median_utilization=3.0,
            )

            result = OptimizationResult(
                selected_cards=make_candidate_cards(["Card A", "Card B", "Card C"]),
                completable_combo_ids=["combo1", "combo2"],
                combo_count=2,
                objective_value=2.0,
                solve_time_seconds=3.0,
                phase1_status="OPTIMAL",
                utilization_per_card={"Card A": 2, "Card B": 3, "Card C": 4},
                phase1_utilization_stats=p1_stats,
                phase2_utilization_stats=p2_stats,
                phase1_solve_time=1.5,
                phase2_solve_time=1.5,
                phase2_status="OPTIMAL",
                is_multi_objective=True,
            )

            write_utilization_stats(result, output_file, 3)

            assert Path(stats_file).exists()

            with open(stats_file) as f:
                stats = json.load(f)

            assert stats["metadata"]["optimization_method"] == "two_phase"
            assert stats["phase1"] is not None
            assert stats["phase2"] is not None
            assert stats["improvement"] is not None

            # Check improvement metrics
            improvement = stats["improvement"]
            assert "std_deviation_reduction_percent" in improvement
            assert "mad_reduction_percent" in improvement
            assert improvement["range_before"] == 4  # 5 - 1
            assert improvement["range_after"] == 2  # 4 - 2

            # std_dev improvement: (1 - 0.8/1.5) * 100 = 46.67%
            assert improvement["std_deviation_reduction_percent"] == pytest.approx(46.67, rel=0.01)

            # MAD improvement: (1 - 6/10) * 100 = 40%
            assert improvement["mad_reduction_percent"] == pytest.approx(40.0, rel=0.01)

    def test_write_stats_top_bottom_cards(self):
        """Test that top and bottom utilized cards are recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            card_names = [f"Card {chr(65 + i)}" for i in range(15)]
            utilization = {
                name: i + 1 for i, name in enumerate(card_names)
            }  # Card A=1, B=2, ..., O=15

            result = OptimizationResult(
                selected_cards=make_candidate_cards(card_names),
                completable_combo_ids=["combo1"],
                combo_count=1,
                objective_value=1.0,
                solve_time_seconds=1.0,
                phase1_status="OPTIMAL",
                utilization_per_card=utilization,
                phase1_utilization_stats=UtilizationStats(1, 15, 8.0, 4.0, 50, 8.0),
                phase1_solve_time=1.0,
                is_multi_objective=False,
            )

            write_utilization_stats(result, output_file, 15)

            with open(stats_file) as f:
                stats = json.load(f)

            assert len(stats["top_utilized_cards"]) == 10
            assert len(stats["bottom_utilized_cards"]) == 10

            # Top card should be Card O (15)
            assert stats["top_utilized_cards"][0]["card"] == "Card O"
            assert stats["top_utilized_cards"][0]["utilization"] == 15

            # Bottom card should be Card A (1)
            assert stats["bottom_utilized_cards"][-1]["card"] == "Card A"
            assert stats["bottom_utilized_cards"][-1]["utilization"] == 1

    def test_write_stats_derives_filename_correctly(self):
        """Test that stats filename is derived correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "my_cube.txt")
            stats_file = str(Path(tmpdir) / "my_cube_stats.json")

            result = OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=0.0,
                phase1_status="OPTIMAL",
            )

            write_utilization_stats(result, output_file, 0)

            assert Path(stats_file).exists()

    def test_write_stats_zero_division_protection(self):
        """Test that zero division is handled in improvement calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            p1_stats = UtilizationStats(0, 0, 0.0, 0.0, 0, 0.0)
            p2_stats = UtilizationStats(0, 0, 0.0, 0.0, 0, 0.0)

            result = OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=0.0,
                phase1_status="OPTIMAL",
                utilization_per_card={},
                phase1_utilization_stats=p1_stats,
                phase2_utilization_stats=p2_stats,
                phase1_solve_time=0.0,
                phase2_solve_time=0.0,
                phase2_status="OPTIMAL",
                is_multi_objective=True,
            )

            # Should not raise ZeroDivisionError
            write_utilization_stats(result, output_file, 0)

            with open(stats_file) as f:
                stats = json.load(f)

            assert stats["improvement"]["std_deviation_reduction_percent"] == 0.0
            assert stats["improvement"]["mad_reduction_percent"] == 0.0

    def test_write_stats_contains_timestamp(self):
        """Test that stats file contains timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            result = OptimizationResult(
                selected_cards=[],
                completable_combo_ids=[],
                combo_count=0,
                objective_value=0.0,
                solve_time_seconds=0.0,
                phase1_status="OPTIMAL",
            )

            write_utilization_stats(result, output_file, 0)

            with open(stats_file) as f:
                stats = json.load(f)

            assert "timestamp" in stats["metadata"]
            # Should be ISO format with timezone
            assert "T" in stats["metadata"]["timestamp"]
