"""Unit tests for ILP runner utilities."""

import json
import logging
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from mtg_combo_cube.ilp.ilp_models import (
    CandidateCard,
    ComboGroupStats,
    OptimizationResult,
    UtilizationStats,
)
from mtg_combo_cube.ilp.ilp_runner import format_combo_count, log_phase_summary, write_stats


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

            write_stats(result, output_file, 3)

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

            write_stats(result, output_file, 3)

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

            write_stats(result, output_file, 15)

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

            write_stats(result, output_file, 0)

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
            write_stats(result, output_file, 0)

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

            write_stats(result, output_file, 0)

            with open(stats_file) as f:
                stats = json.load(f)

            assert "timestamp" in stats["metadata"]
            # Should be ISO format with timezone
            assert "T" in stats["metadata"]["timestamp"]

    def test_write_stats_card_changes(self):
        """Test that card changes between phases are recorded."""
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

            # Phase 1 had: Card A, Card B, Card C
            # Phase 2 has: Card A, Card D, Card E (removed B, C; added D, E)
            phase1_cards = make_candidate_cards(["Card A", "Card B", "Card C"])
            phase2_cards = make_candidate_cards(["Card A", "Card D", "Card E"])

            result = OptimizationResult(
                selected_cards=phase2_cards,
                completable_combo_ids=["combo1", "combo2"],
                combo_count=2,
                objective_value=2.0,
                solve_time_seconds=3.0,
                phase1_status="OPTIMAL",
                utilization_per_card={"Card A": 2, "Card D": 3, "Card E": 4},
                phase1_utilization_stats=p1_stats,
                phase2_utilization_stats=p2_stats,
                phase1_solve_time=1.5,
                phase2_solve_time=1.5,
                phase2_status="OPTIMAL",
                is_multi_objective=True,
                phase1_selected_cards=phase1_cards,
            )

            write_stats(result, output_file, 3)

            with open(stats_file) as f:
                stats = json.load(f)

            assert "card_changes" in stats["improvement"]
            card_changes = stats["improvement"]["card_changes"]

            assert card_changes["cards_added"] == ["Card D", "Card E"]
            assert card_changes["cards_removed"] == ["Card B", "Card C"]
            assert card_changes["total_changed"] == 4

    def test_write_stats_card_changes_no_changes(self):
        """Test that card_changes is recorded even when no cards change."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            p1_stats = UtilizationStats(1, 5, 3.0, 1.5, 10, 3.0)
            p2_stats = UtilizationStats(2, 4, 3.0, 0.8, 6, 3.0)

            # Same cards in both phases
            phase1_cards = make_candidate_cards(["Card A", "Card B", "Card C"])
            phase2_cards = make_candidate_cards(["Card A", "Card B", "Card C"])

            result = OptimizationResult(
                selected_cards=phase2_cards,
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
                phase1_selected_cards=phase1_cards,
            )

            write_stats(result, output_file, 3)

            with open(stats_file) as f:
                stats = json.load(f)

            assert "card_changes" in stats["improvement"]
            card_changes = stats["improvement"]["card_changes"]

            assert card_changes["cards_added"] == []
            assert card_changes["cards_removed"] == []
            assert card_changes["total_changed"] == 0

    def test_write_stats_card_changes_not_present_without_phase1_cards(self):
        """Test that card_changes is not present when phase1_selected_cards is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            p1_stats = UtilizationStats(1, 5, 3.0, 1.5, 10, 3.0)
            p2_stats = UtilizationStats(2, 4, 3.0, 0.8, 6, 3.0)

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
                # phase1_selected_cards is None (not provided)
            )

            write_stats(result, output_file, 3)

            with open(stats_file) as f:
                stats = json.load(f)

            # improvement should exist but not have card_changes
            assert stats["improvement"] is not None
            assert "card_changes" not in stats["improvement"]

    def test_write_stats_phase2_fallback(self):
        """A failed Phase 2 is recorded instead of looking like a single-phase run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            result = OptimizationResult(
                selected_cards=make_candidate_cards(["Card A", "Card B", "Card C"]),
                completable_combo_ids=["combo1", "combo2"],
                combo_count=2,
                objective_value=2.0,
                solve_time_seconds=31.5,
                phase1_status="OPTIMAL",
                utilization_per_card={"Card A": 2, "Card B": 1, "Card C": 1},
                phase1_utilization_stats=UtilizationStats(1, 2, 1.33, 0.47, 1, 1.0),
                phase1_solve_time=1.5,
                phase2_solve_time=30.0,
                phase2_status="TIMEOUT",
                is_multi_objective=False,
                phase2_fell_back=True,
                profile_data={"phase1": {"counts": {}}, "phase2": {"counts": {}}},
            )

            write_stats(result, output_file, 3)

            with open(stats_file) as f:
                stats = json.load(f)

            assert stats["metadata"]["optimization_method"] == "two_phase_fallback_to_phase1"
            assert stats["metadata"]["total_solve_time_seconds"] == 31.5
            assert stats["phase1"] is not None
            assert stats["phase2"] == {
                "solve_time_seconds": 30.0,
                "status": "TIMEOUT",
                "fell_back_to_phase1": True,
            }
            assert stats["improvement"] is None
            assert set(stats["profiling"]) == {"phase1", "phase2"}

    @pytest.mark.parametrize(
        ("objective", "util_cap", "expected"),
        [
            ("softcap", 32, {"objective": "softcap", "util_cap": 32}),
            ("maxutil", None, {"objective": "maxutil"}),
            (None, None, {}),
        ],
    )
    def test_write_stats_phase2_objective_and_cap(
        self, objective: str | None, util_cap: int | None, expected: dict
    ):
        """The Phase 2 objective and the utilization cap it used are recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            result = OptimizationResult(
                selected_cards=make_candidate_cards(["Card A", "Card B", "Card C"]),
                completable_combo_ids=["combo1", "combo2"],
                combo_count=2,
                objective_value=2.0,
                solve_time_seconds=3.0,
                phase1_status="OPTIMAL",
                utilization_per_card={"Card A": 2, "Card B": 3, "Card C": 4},
                phase1_utilization_stats=UtilizationStats(1, 5, 3.0, 1.5, 10, 3.0),
                phase2_utilization_stats=UtilizationStats(2, 4, 3.0, 0.8, 6, 3.0),
                phase1_solve_time=1.5,
                phase2_solve_time=1.5,
                phase2_status="OPTIMAL",
                phase2_objective=objective,
                phase2_util_cap=util_cap,
                is_multi_objective=True,
            )

            write_stats(result, output_file, 3)

            with open(stats_file) as f:
                stats = json.load(f)

            recorded = {k: v for k, v in stats["phase2"].items() if k in ("objective", "util_cap")}
            assert recorded == expected

    def test_write_stats_phase2_fallback_records_objective_and_cap(self):
        """A fallback keeps the objective and cap of the failed Phase 2 attempt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = str(Path(tmpdir) / "data/cube.txt")
            stats_file = str(Path(tmpdir) / "data/cube_stats.json")

            result = OptimizationResult(
                selected_cards=make_candidate_cards(["Card A"]),
                completable_combo_ids=["combo1"],
                combo_count=1,
                objective_value=1.0,
                solve_time_seconds=31.5,
                phase1_status="OPTIMAL",
                phase1_solve_time=1.5,
                phase2_solve_time=30.0,
                phase2_status="INFEASIBLE",
                phase2_objective="softcap",
                phase2_util_cap=12,
                phase2_fell_back=True,
            )

            write_stats(result, output_file, 1)

            with open(stats_file) as f:
                stats = json.load(f)

            assert stats["phase2"] == {
                "solve_time_seconds": 30.0,
                "status": "INFEASIBLE",
                "fell_back_to_phase1": True,
                "objective": "softcap",
                "util_cap": 12,
            }


class TestWriteStatsCombosAndColors:
    """Per-phase combo counts and color distributions in the stats file."""

    @staticmethod
    def _two_phase_result() -> OptimizationResult:
        return OptimizationResult(
            selected_cards=make_candidate_cards(["Card A", "Card B", "Card D"]),
            completable_combo_ids=["combo1", "combo2", "combo3"],
            combo_count=3,
            objective_value=4.0,
            solve_time_seconds=3.0,
            phase1_status="OPTIMAL",
            utilization_per_card={"Card A": 2, "Card B": 2, "Card D": 2},
            phase1_utilization_stats=UtilizationStats(1, 5, 3.0, 1.5, 10, 3.0),
            phase2_utilization_stats=UtilizationStats(2, 4, 3.0, 0.8, 6, 3.0),
            phase1_solve_time=1.5,
            phase2_solve_time=1.5,
            phase2_status="OPTIMAL",
            is_multi_objective=True,
            phase1_selected_cards=make_candidate_cards(["Card A", "Card B", "Card C"]),
            phase1_combo_count=4,
        )

    @staticmethod
    def _write(result: OptimizationResult, tmp_path: Path, **kwargs) -> dict:
        write_stats(result, str(tmp_path / "cube.txt"), 3, **kwargs)
        with open(tmp_path / "cube_stats.json") as f:
            return json.load(f)

    def test_combo_counts_per_phase(self, tmp_path: Path):
        stats = self._write(self._two_phase_result(), tmp_path)

        assert stats["phase1"]["combo_count"] == 4
        assert stats["phase2"]["combo_count"] == 3
        assert stats["improvement"]["combo_count_before"] == 4
        assert stats["improvement"]["combo_count_after"] == 3
        assert stats["improvement"]["combo_count_change_percent"] == pytest.approx(-25.0)

    def test_single_phase_combo_count(self, tmp_path: Path):
        result = OptimizationResult(
            selected_cards=make_candidate_cards(["Card A"]),
            completable_combo_ids=["combo1"],
            combo_count=1,
            objective_value=1.0,
            solve_time_seconds=1.0,
            phase1_status="OPTIMAL",
            phase1_utilization_stats=UtilizationStats(1, 1, 1.0, 0.0, 0, 1.0),
        )

        stats = self._write(result, tmp_path, color_identities={"Card A": "G"})

        assert stats["phase1"]["combo_count"] == 1
        assert stats["phase1"]["colors"]["mono_colored"]["G"] == 1
        # Without grouping data the grouping keys are left out everywhere
        assert "distinct_combo_count" not in stats["phase1"]
        assert "weighted_combo_count" not in stats["phase1"]
        assert "distinct_combo_count" not in stats["metadata"]
        assert "variant_weight" not in stats["metadata"]
        assert "largest_combo_groups" not in stats

    def test_distinct_combo_counts_and_largest_groups(self, tmp_path: Path):
        result = replace(
            self._two_phase_result(),
            distinct_combo_count=2,
            weighted_combo_count=2.1,
            phase1_distinct_combo_count=3,
            phase1_weighted_combo_count=3.1,
            variant_weight=0.1,
            largest_combo_groups=[
                ComboGroupStats("6186", 2, ["Card A", "Card B"]),
                ComboGroupStats("ab", 1, ["Card D"]),
            ],
            phase2_reference_combo_count=4,
            phase2_reference_distinct_combo_count=3,
            phase2_reference_weighted_combo_count=3.1,
            phase2_combo_tolerance=0.1,
        )

        stats = self._write(result, tmp_path)

        assert stats["metadata"]["distinct_combo_count"] == 2
        assert stats["metadata"]["weighted_combo_count"] == 2.1
        assert stats["metadata"]["variant_weight"] == 0.1
        assert stats["phase1"]["distinct_combo_count"] == 3
        assert stats["phase1"]["weighted_combo_count"] == 3.1
        assert stats["phase2"]["distinct_combo_count"] == 2
        assert stats["phase2"]["weighted_combo_count"] == 2.1
        assert stats["phase2"]["reference_combo_count"] == 4
        assert stats["phase2"]["reference_distinct_combo_count"] == 3
        assert stats["phase2"]["reference_weighted_combo_count"] == 3.1
        assert stats["phase2"]["combo_tolerance"] == 0.1
        assert stats["improvement"]["distinct_combo_count_before"] == 3
        assert stats["improvement"]["distinct_combo_count_after"] == 2
        assert stats["largest_combo_groups"] == [
            {"group_key": "6186", "variant_count": 2, "cards": ["Card A", "Card B"]},
            {"group_key": "ab", "variant_count": 1, "cards": ["Card D"]},
        ]

    def test_log_phase_summary_reports_variants_and_combos(self, caplog: pytest.LogCaptureFixture):
        result = replace(
            self._two_phase_result(),
            distinct_combo_count=2,
            weighted_combo_count=2.1,
            phase1_distinct_combo_count=3,
            phase1_weighted_combo_count=3.1,
            phase2_reference_combo_count=4,
            phase2_reference_distinct_combo_count=3,
            phase2_reference_weighted_combo_count=3.1,
        )

        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_runner"):
            log_phase_summary(result, None)

        assert (
            "Combos: Phase 1 4 variants in 3 combos (weighted 3.1), best under coverage and "
            "color 4 variants in 3 combos (weighted 3.1), Phase 2 3 variants in 2 combos "
            "(weighted 2.1) (-25.0% variants from Phase 1)"
        ) in caplog.text

    def test_format_combo_count_hides_a_weighted_count_equal_to_the_variants(self):
        assert format_combo_count(4, 1, 4.0) == "4 variants in 1 combos"
        assert format_combo_count(4, 1, 1.3) == "4 variants in 1 combos (weighted 1.3)"
        assert format_combo_count(4, None) == "4 variants"

    def test_colors_per_phase(self, tmp_path: Path):
        identities = {"Card A": "W", "Card B": "WU", "Card C": "", "Card D": "U"}

        stats = self._write(self._two_phase_result(), tmp_path, color_identities=identities)

        # Phase 1 cube: Card A, Card B, Card C
        assert stats["phase1"]["colors"] == {
            "cards_per_color": {"W": 2, "U": 1, "B": 0, "R": 0, "G": 0},
            "mono_colored": {"W": 1, "U": 0, "B": 0, "R": 0, "G": 0},
            "multicolor": 1,
            "colorless": 1,
            "unknown": 0,
            "variance": pytest.approx(0.64),
            "std_deviation": pytest.approx(0.8),
        }
        # Phase 2 cube: Card A, Card B, Card D
        assert stats["phase2"]["colors"]["cards_per_color"] == {
            "W": 2,
            "U": 2,
            "B": 0,
            "R": 0,
            "G": 0,
        }
        assert stats["phase2"]["colors"]["colorless"] == 0

    def test_no_colors_without_color_data(self, tmp_path: Path):
        stats = self._write(self._two_phase_result(), tmp_path)

        assert "colors" not in stats["phase1"]
        assert "colors" not in stats["phase2"]
