"""Unit tests for ILP runner utilities."""

import json
import logging
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from mtg_combo_cube.ilp.cube_evaluation import ARCHETYPES, compute_card_mix_stats
from mtg_combo_cube.ilp.ilp_models import (
    ArchetypeStats,
    CandidateCard,
    CardMixRules,
    ComboGroupStats,
    OptimizationResult,
    OutcomeStats,
    PayoffStats,
    PopularityStats,
    UtilizationStats,
)
from mtg_combo_cube.ilp.ilp_runner import (
    format_archetype_stats,
    format_card_mix_stats,
    format_combo_count,
    format_outcome_stats,
    format_payoff_stats,
    format_payoff_table,
    log_phase_summary,
    write_stats,
)
from mtg_combo_cube.ilp.payoffs import PayoffTable
from mtg_combo_cube.models import CardAttributes


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

        stats = self._write(result, tmp_path, attributes={"Card A": CardAttributes("G")})

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
            "Combos: Phase 1 4 variants in 3 combos (weighted 3.1), best under the cube rules "
            "4 variants in 3 combos (weighted 3.1), Phase 2 3 variants in 2 combos "
            "(weighted 2.1) (-25.0% variants from Phase 1)"
        ) in caplog.text
        # No archetype data: no archetype lines
        assert "Archetypes" not in caplog.text

    def test_format_combo_count_hides_a_weighted_count_equal_to_the_variants(self):
        assert format_combo_count(4, 1, 4.0) == "4 variants in 1 combos"
        assert format_combo_count(4, 1, 1.3) == "4 variants in 1 combos (weighted 1.3)"
        assert format_combo_count(4, None) == "4 variants"

    def test_colors_per_phase(self, tmp_path: Path):
        attributes = {
            "Card A": CardAttributes("W"),
            "Card B": CardAttributes("WU"),
            "Card C": CardAttributes(""),
            "Card D": CardAttributes("U"),
        }

        stats = self._write(self._two_phase_result(), tmp_path, attributes=attributes)

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
        assert "card_mix" not in stats["phase1"]
        assert "card_mix" not in stats["phase2"]
        assert "card_mix_rules" not in stats["phase2"]


CARD_MIX_ATTRIBUTES = {
    "Card A": CardAttributes("W", "Creature \u2014 Human", 2),
    "Card B": CardAttributes("WU", "Instant", 1),
    "Card C": CardAttributes("", "Artifact Creature \u2014 Golem", 7),
    "Card D": CardAttributes("", "Land", 0),
}


class TestWriteStatsCardMix:
    """Per-phase card mix and the card mix rules in the stats file, and the log line."""

    _write = staticmethod(TestWriteStatsCombosAndColors._write)
    _two_phase_result = staticmethod(TestWriteStatsCombosAndColors._two_phase_result)

    def test_card_mix_per_phase(self, tmp_path: Path):
        stats = self._write(self._two_phase_result(), tmp_path, attributes=CARD_MIX_ATTRIBUTES)

        # Phase 1 cube: Card A, Card B, Card C
        phase1 = stats["phase1"]["card_mix"]
        assert phase1["card_count"] == 3
        assert phase1["type_counts"] == {
            "Creature": 2,
            "Instant": 1,
            "Sorcery": 0,
            "Artifact": 1,
            "Enchantment": 0,
            "Planeswalker": 0,
            "Battle": 0,
            "Land": 0,
        }
        assert phase1["multicolor"] == 1
        assert phase1["colorless"] == 1
        assert phase1["mana_value_counts"] == {
            "0": 0,
            "1": 1,
            "2": 1,
            "3": 0,
            "4": 0,
            "5": 0,
            "6": 0,
            "7": 1,
        }
        assert phase1["mean_mana_value"] == pytest.approx(10 / 3)
        assert phase1["mean_mana_value_per_color"]["W"] == pytest.approx(1.5)
        assert phase1["mean_mana_value_per_color"]["G"] == 0.0
        assert phase1["unknown"] == 0
        # Phase 2 cube: Card A, Card B, Card D (a land: left out of the mana values and
        # not colorless, as for the colorless cap)
        phase2 = stats["phase2"]["card_mix"]
        assert phase2["type_counts"]["Land"] == 1
        assert phase2["colorless"] == 0
        assert phase2["mean_mana_value"] == pytest.approx(1.5)

    def test_card_mix_rules_applied(self, tmp_path: Path):
        result = replace(
            self._two_phase_result(),
            phase2_card_mix=CardMixRules(max_creature_share=0.5, mono_color_ratio=1.5),
        )

        stats = self._write(result, tmp_path, attributes=CARD_MIX_ATTRIBUTES)

        assert stats["phase2"]["card_mix_rules"] == {
            "max_multicolor_share": 0.15,
            "max_colorless_share": 0.25,
            "max_expensive_share": 0.2,
            "expensive_mana_value": 5,
            "max_creature_share": 0.5,
            "min_spell_share": 0.05,
            "mono_color_ratio": 1.5,
        }
        assert "card_mix_limits" not in stats["phase2"]
        assert "unknown_candidate_cards" not in stats["phase2"]

    def test_card_mix_limits_and_unknown_cards(self, tmp_path: Path):
        result = replace(
            self._two_phase_result(),
            phase2_card_mix=CardMixRules(),
            phase2_card_mix_limits={"creature_cap": 180, "spell_floor": 15},
            phase2_unknown_candidate_cards=3,
        )

        stats = self._write(result, tmp_path, attributes=CARD_MIX_ATTRIBUTES)

        assert stats["phase2"]["card_mix_limits"] == {"creature_cap": 180, "spell_floor": 15}
        assert stats["phase2"]["unknown_candidate_cards"] == 3

    def test_rules_all_off_are_left_out(self, tmp_path: Path):
        off = CardMixRules(0, 0, 0, 5, 0, 0, 0)
        result = replace(self._two_phase_result(), phase2_card_mix=off)

        stats = self._write(result, tmp_path, attributes=CARD_MIX_ATTRIBUTES)

        assert "card_mix_rules" not in stats["phase2"]

    def test_log_line(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_runner"):
            log_phase_summary(self._two_phase_result(), CARD_MIX_ATTRIBUTES)

        assert (
            "Card mix, Phase 1: types Creature=2 (67%), Instant=1, Sorcery=0, Artifact=1, "
            "Enchantment=0, Planeswalker=0, Battle=0, Land=0; multicolor 1 (33%), "
            "colorless 1 (33%); mana value (nonland) mean 3.33, 0:0 1:1 2:1 3:0 4:0 5:0 6:0 "
            "7+:1, per color W=1.5, U=1.0, B=0.0, R=0.0, G=0.0"
        ) in caplog.text
        assert "Card mix, Phase 2: types Creature=1 (33%)" in caplog.text

    def test_format_reports_unknown_cards(self):
        stats = compute_card_mix_stats(["Card A", "Mystery"], CARD_MIX_ATTRIBUTES)

        assert format_card_mix_stats(stats).endswith("; unknown=1")


class TestWriteStatsOutcomesAndPopularity:
    """Per-phase outcome counts, popularity and the outcome settings in the stats file."""

    def _result(self) -> OptimizationResult:
        return replace(
            TestWriteStatsCombosAndColors._two_phase_result(),
            phase1_outcome_stats=OutcomeStats({"mana": 3, "damage": 0}, 1, 4),
            phase2_outcome_stats=OutcomeStats({"mana": 2, "damage": 1}, 0, 3),
            phase1_popularity_stats=PopularityStats(4, 50.0, 3.5, 0.5, 60.0),
            phase2_popularity_stats=PopularityStats(3, 80.0, 4.1, 0.0, 60.0),
            phase2_outcome_minimums={"mana": 1, "damage": 1},
            phase2_max_outcome_share=0.6,
        )

    def test_outcomes_and_popularity_per_phase(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(self._result(), tmp_path)

        assert stats["phase1"]["outcomes"] == {
            "combos_per_outcome": {"mana": 3, "damage": 0},
            "uncategorized": 1,
            "total": 4,
        }
        assert stats["phase2"]["outcomes"]["combos_per_outcome"] == {"mana": 2, "damage": 1}
        assert stats["phase1"]["popularity"] == {
            "combo_count": 4,
            "median_popularity": 50.0,
            "mean_log_popularity": 3.5,
            "below_pool_median_share": 0.5,
            "pool_median_popularity": 60.0,
        }
        assert stats["phase2"]["popularity"]["median_popularity"] == 80.0
        assert stats["phase2"]["outcome_minimums"] == {"mana": 1, "damage": 1}
        assert stats["phase2"]["max_outcome_share"] == 0.6

    def test_left_out_without_data(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(
            TestWriteStatsCombosAndColors._two_phase_result(), tmp_path
        )

        for phase in ("phase1", "phase2"):
            assert "outcomes" not in stats[phase]
            assert "popularity" not in stats[phase]
        assert "outcome_minimums" not in stats["phase2"]
        assert "max_outcome_share" not in stats["phase2"]
        assert "popularity_weight" not in stats["metadata"]
        assert "combo_score" not in stats["metadata"]

    def test_fallback_records_the_settings(self, tmp_path: Path):
        result = replace(
            self._result(),
            is_multi_objective=False,
            phase2_fell_back=True,
            phase2_status="INFEASIBLE",
        )

        stats = TestWriteStatsCombosAndColors._write(result, tmp_path)

        assert stats["phase2"]["fell_back_to_phase1"]
        assert stats["phase2"]["outcome_minimums"] == {"mana": 1, "damage": 1}
        assert stats["phase2"]["max_outcome_share"] == 0.6

    def test_combo_score_only_with_a_popularity_weight(self, tmp_path: Path):
        scored = replace(
            self._result(),
            popularity_weight=0.5,
            combo_score=4.2,
            phase1_combo_score=5.1,
            phase2_reference_combo_score=4.5,
            weighted_combo_count=3.0,
        )
        stats = TestWriteStatsCombosAndColors._write(scored, tmp_path)

        assert stats["metadata"]["popularity_weight"] == 0.5
        assert stats["metadata"]["combo_score"] == 4.2
        assert stats["phase1"]["combo_score"] == 5.1
        assert stats["phase2"]["combo_score"] == 4.2
        assert stats["phase2"]["reference_combo_score"] == 4.5

        # With a weight of 0 the score is the weighted count, so it is not repeated
        plain = replace(scored, popularity_weight=0)
        stats = TestWriteStatsCombosAndColors._write(plain, tmp_path)

        assert stats["metadata"]["popularity_weight"] == 0
        assert "combo_score" not in stats["metadata"]
        assert "combo_score" not in stats["phase1"]
        assert "combo_score" not in stats["phase2"]
        assert "reference_combo_score" not in stats["phase2"]

    def test_log_lines(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_runner"):
            log_phase_summary(self._result(), None)

        assert "Outcomes, Phase 1: mana=3, damage=0; no category 1 of 4 (25%)" in caplog.text
        assert "Outcomes, Phase 2: mana=2, damage=1; no category 0 of 3 (0%)" in caplog.text
        assert (
            "Popularity, Phase 1: median 50 (pool median 60), mean log 3.50, 50% of 4 combos "
            "below the pool median"
        ) in caplog.text
        assert "Popularity, Phase 2: median 80 (pool median 60)" in caplog.text

    def test_format_without_combos(self):
        assert format_outcome_stats(OutcomeStats({"mana": 0}, 0, 0)) == (
            "mana=0; no category 0 of 0"
        )


class TestWriteStatsPayoffs:
    """Per-phase payoff counts, the payoff floor settings and the resolved table."""

    TABLE = PayoffTable(
        sources={
            "mana": {
                "Comet Storm": frozenset(["query"]),
                "Walking Ballista": frozenset(["inferred", "query"]),
            },
            "storm": {"Grapeshot": frozenset(["card", "query"])},
        },
        inferred={"mana": {"Walking Ballista": 3, "Chromatic Orrery": 1}, "storm": {}},
        inference_threshold=2,
    )

    def _result(self) -> OptimizationResult:
        return replace(
            TestWriteStatsCombosAndColors._two_phase_result(),
            phase1_payoff_stats=PayoffStats(
                {"mana": 0, "storm": 0}, {"mana": {}, "storm": {}}, payoff_only=[]
            ),
            phase2_payoff_stats=PayoffStats(
                {"mana": 2, "storm": 1},
                {
                    "mana": {"Comet Storm": ["query"], "Walking Ballista": ["inferred", "query"]},
                    "storm": {"Grapeshot": ["card", "query"]},
                },
                payoff_only=["Comet Storm"],
            ),
            phase2_min_payoffs=3,
            phase2_payoff_floors={"mana": 3, "storm": 1},
        )

    def test_payoffs_per_phase_and_the_settings(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(self._result(), tmp_path)

        assert stats["phase1"]["payoffs"] == {
            "cards_per_category": {"mana": 0, "storm": 0},
            "cards": {"mana": {}, "storm": {}},
            "payoff_only": [],
        }
        assert stats["phase2"]["payoffs"]["cards_per_category"] == {"mana": 2, "storm": 1}
        assert stats["phase2"]["payoffs"]["payoff_only"] == ["Comet Storm"]
        assert stats["phase2"]["payoffs"]["cards"]["mana"]["Walking Ballista"] == [
            "inferred",
            "query",
        ]
        assert stats["phase2"]["min_payoffs"] == 3
        assert stats["phase2"]["payoff_floors"] == {"mana": 3, "storm": 1}
        assert "payoffs" not in stats

    def test_resolved_table_is_a_top_level_block(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(self._result(), tmp_path, payoffs=self.TABLE)

        assert stats["payoffs"] == {
            "inference_threshold": 2,
            "cards_per_category": {"mana": 2, "storm": 1},
            "source_counts": {
                "mana": {"inferred": 1, "card": 0, "query": 2},
                "storm": {"inferred": 0, "card": 1, "query": 1},
            },
            "cards": {
                "mana": {"Comet Storm": ["query"], "Walking Ballista": ["inferred", "query"]},
                "storm": {"Grapeshot": ["card", "query"]},
            },
            "inferred": {"mana": {"Walking Ballista": 3, "Chromatic Orrery": 1}, "storm": {}},
        }

    def test_left_out_without_data(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(
            TestWriteStatsCombosAndColors._two_phase_result(), tmp_path
        )

        for phase in ("phase1", "phase2"):
            assert "payoffs" not in stats[phase]
        assert "min_payoffs" not in stats["phase2"]
        assert "payoff_floors" not in stats["phase2"]
        assert "payoffs" not in stats

    def test_fallback_records_the_settings(self, tmp_path: Path):
        result = replace(
            self._result(), is_multi_objective=False, phase2_fell_back=True, phase2_status="TIMEOUT"
        )

        stats = TestWriteStatsCombosAndColors._write(result, tmp_path)

        assert stats["phase2"]["fell_back_to_phase1"]
        assert stats["phase2"]["payoff_floors"] == {"mana": 3, "storm": 1}

    def test_log_lines(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_runner"):
            log_phase_summary(self._result(), None)

        assert "Payoffs, Phase 1: mana=0, storm=0" in caplog.text
        assert "Payoffs, Phase 2: mana=2, storm=1" in caplog.text

    def test_formats(self):
        assert format_payoff_stats(PayoffStats({"mana": 2}, {"mana": {}}, [])) == "mana=2"
        assert format_payoff_table(self.TABLE) == (
            "mana 2 (inferred 1, cards 0, queries 2); storm 1 (inferred 0, cards 1, queries 1)"
        )


def archetype_stats(**counts: int) -> ArchetypeStats:
    """Archetype stats with the given per-archetype counts (0 elsewhere) and 3 narrow combos."""
    return ArchetypeStats(
        combos_per_archetype={archetype: counts.get(archetype, 0) for archetype in ARCHETYPES},
        combos_by_color_count={0: 1, 1: 2, 2: 0, 3: 1, 4: 0, 5: 0},
    )


class TestWriteStatsArchetypes:
    """Per-phase archetype counts in the stats file and the log."""

    def _result(self) -> OptimizationResult:
        return replace(
            TestWriteStatsCombosAndColors._two_phase_result(),
            phase1_archetype_stats=archetype_stats(WU=4, W=3, C=1),
            phase2_archetype_stats=archetype_stats(WU=2, UB=1, U=1, C=1),
            phase2_min_pair_combos=20,
            phase2_min_mono_combos=5,
            phase2_max_wide_combo_share=0.25,
        )

    def test_archetypes_per_phase(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(self._result(), tmp_path)

        phase1 = stats["phase1"]["archetypes"]
        assert phase1["combos_per_archetype"]["WU"] == 4
        assert phase1["combos_per_archetype"]["RG"] == 0
        assert set(phase1["combos_per_archetype"]) == set(ARCHETYPES)
        assert phase1["combos_by_color_count"] == {
            "0": 1,
            "1": 2,
            "2": 0,
            "3": 1,
            "4": 0,
            "5": 0,
        }
        assert stats["phase2"]["archetypes"]["combos_per_archetype"]["UB"] == 1
        assert stats["phase2"]["min_pair_combos"] == 20
        assert stats["phase2"]["min_mono_combos"] == 5
        assert stats["phase2"]["max_wide_combo_share"] == 0.25

    def test_settings_left_out_when_not_applied(self, tmp_path: Path):
        stats = TestWriteStatsCombosAndColors._write(
            TestWriteStatsCombosAndColors._two_phase_result(), tmp_path
        )

        assert "archetypes" not in stats["phase1"]
        assert "archetypes" not in stats["phase2"]
        for key in ("min_pair_combos", "min_mono_combos", "max_wide_combo_share"):
            assert key not in stats["phase2"]

    def test_fallback_records_the_settings(self, tmp_path: Path):
        result = replace(
            self._result(),
            is_multi_objective=False,
            phase2_fell_back=True,
            phase2_status="INFEASIBLE",
        )

        stats = TestWriteStatsCombosAndColors._write(result, tmp_path)

        assert stats["phase2"]["fell_back_to_phase1"]
        assert stats["phase2"]["min_pair_combos"] == 20

    def test_log_phase_summary_prints_the_pair_counts(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.INFO, logger="mtg_combo_cube.ilp.ilp_runner"):
            log_phase_summary(self._result(), None)

        assert (
            "Archetypes, Phase 1: pairs WU=4, WB=0, WR=0, WG=0, UB=0, UR=0, UG=0, BR=0, BG=0, "
            "RG=0; mono W=3, U=0, B=0, R=0, G=0; colorless 1; 3+ colors 1 of 4 (25%)"
        ) in caplog.text
        assert "Archetypes, Phase 2: pairs WU=2, WB=0, WR=0, WG=0, UB=1," in caplog.text

    def test_format_archetype_stats_without_combos(self):
        stats = ArchetypeStats(
            combos_per_archetype=dict.fromkeys(ARCHETYPES, 0),
            combos_by_color_count=dict.fromkeys(range(6), 0),
        )

        assert format_archetype_stats(stats).endswith("colorless 0; 3+ colors 0 of 0")
