"""Tests for the evaluate_cube command's wiring: instance loading and the evaluation fields."""

from pathlib import Path
from typing import Any

import pytest

from mtg_combo_cube.ilp import evaluate_cube as module
from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData

COMBOS = [
    ComboData("h1", frozenset(["H", "P1"]), [], 10, group_key="big", color_identity="W"),
    ComboData("h2", frozenset(["H", "P2"]), [], 10, group_key="big", color_identity="WU"),
    ComboData("ab", frozenset(["A", "B"]), [], 10, color_identity="UB"),
    ComboData("cd", frozenset(["C", "D"]), [], 10),
]
CARDS = {
    name: CandidateCard(name, frozenset(), frozenset())
    for name in ["H", "P1", "P2", "A", "B", "C", "D"]
}


class TestEvaluateCube:
    async def test_evaluates_a_cube_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        received: dict[str, Any] = {}

        async def fake_load_instance(**kwargs: Any) -> tuple[list[ComboData], dict]:
            received.update(kwargs)
            return COMBOS, CARDS

        monkeypatch.setattr(module, "load_instance", fake_load_instance)
        cube_file = tmp_path / "cube.txt"
        cube_file.write_text("H\nP1\nP2\n\nA\nB\nNot A Card\n", encoding="utf-8")

        evaluation = await module.evaluate_cube(
            str(cube_file), max_variants=4, blocklist=frozenset(["Zzz"]), variant_weight=0.5
        )

        # The instance is loaded like a build's, from the cache, without writing to it
        assert received == {
            "max_variants": 4,
            "enable_cache_write": False,
            "read_cache": True,
            "blocklist": frozenset(["Zzz"]),
        }
        assert evaluation.card_count == 6  # blank lines skipped, unknown cards kept
        assert evaluation.combo_count == 3
        assert evaluation.distinct_combo_count == 2
        assert evaluation.weighted_combo_count == 2.5  # big (1 + 0.5) and ab
        assert evaluation.utilization_stats.min_utilization == 0  # the unknown card
        assert evaluation.utilization_stats.max_utilization == 2  # H
        assert [
            (g.group_key, g.variant_count, g.cards) for g in evaluation.largest_combo_groups
        ] == [("big", 2, ["H", "P1", "P2"]), ("ab", 1, ["A", "B"])]
        counts = evaluation.archetype_stats.combos_per_archetype
        assert counts["W"] == 1 and counts["WU"] == 1 and counts["UB"] == 1 and counts["C"] == 0
        assert evaluation.archetype_stats.combos_by_color_count[1] == 1

    def test_read_cube_file_skips_blank_lines(self, tmp_path: Path):
        cube_file = tmp_path / "cube.txt"
        cube_file.write_text("A\n\n  B  \n", encoding="utf-8")

        assert module.read_cube_file(str(cube_file)) == ["A", "B"]
