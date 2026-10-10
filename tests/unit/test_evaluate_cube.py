"""Tests for the evaluate_cube command's wiring: instance loading and the evaluation fields."""

from pathlib import Path
from typing import Any

import pytest

from mtg_combo_cube.ilp import evaluate_cube as module
from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData
from mtg_combo_cube.ilp.outcomes import parse_outcome_categories
from mtg_combo_cube.ilp.payoffs import parse_payoff_table

MANA = frozenset(["Infinite colored mana"])
COMBOS = [
    ComboData(
        "h1", frozenset(["H", "P1"]), [], 10, group_key="big", color_identity="W", features=MANA
    ),
    ComboData("h2", frozenset(["H", "P2"]), [], 30, group_key="big", color_identity="WU"),
    ComboData("ab", frozenset(["A", "B"]), [], 10, color_identity="UB"),
    ComboData("cd", frozenset(["C", "D"]), [], 10, color_identity=""),
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
        assert evaluation.archetype_stats is not None
        counts = evaluation.archetype_stats.combos_per_archetype
        assert counts["W"] == 1 and counts["WU"] == 1 and counts["UB"] == 1 and counts["C"] == 0
        assert evaluation.archetype_stats.combos_by_color_count[1] == 1
        # Without a table there are no outcome counts; the popularity is always reported
        assert evaluation.outcome_stats is None
        assert evaluation.popularity_stats.combo_count == 2
        assert evaluation.popularity_stats.median_popularity == 20  # big 30, ab 10
        assert evaluation.popularity_stats.pool_median_popularity == 10

    async def test_outcome_table(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        async def fake_load_instance(**kwargs: Any) -> tuple[list[ComboData], dict]:
            return COMBOS, CARDS

        monkeypatch.setattr(module, "load_instance", fake_load_instance)
        cube_file = tmp_path / "cube.txt"
        cube_file.write_text("H\nP2\nA\nB\n", encoding="utf-8")
        table = parse_outcome_categories({"mana": ["infinite colored mana"]})

        evaluation = await module.evaluate_cube(str(cube_file), outcome_categories=table)

        assert evaluation.outcome_stats is not None
        # The group's features are the union over its variants, so big is a mana combo
        assert evaluation.outcome_stats.combos_per_outcome == {"mana": 1}
        assert evaluation.outcome_stats.uncategorized == 1
        assert evaluation.outcome_stats.total == 2

    async def test_payoff_table(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        received: dict[str, Any] = {}

        async def fake_load_instance(**kwargs: Any) -> tuple[list[ComboData], dict]:
            return COMBOS, dict(CARDS)

        async def fake_fetch_payoff_queries(queries: Any, **kwargs: Any) -> dict[str, list[str]]:
            received["queries"] = list(queries)
            received.update(kwargs)
            return {"o:storm": ["Grapeshot"]}

        monkeypatch.setattr(module, "load_instance", fake_load_instance)
        monkeypatch.setattr(module, "fetch_payoff_queries", fake_fetch_payoff_queries)
        cube_file = tmp_path / "cube.txt"
        cube_file.write_text("H\nP1\nGrapeshot\n", encoding="utf-8")
        table = parse_outcome_categories({"mana": ["infinite colored mana"]})
        payoffs = parse_payoff_table({"mana": {"queries": ["o:storm"], "cards": ["A"]}})

        with caplog.at_level("WARNING"):
            evaluation = await module.evaluate_cube(
                str(cube_file), outcome_categories=table, payoff_definitions=payoffs
            )

        # The queries come from the cache only, and a payoff-only card in the cube is known
        assert received == {"queries": ["o:storm"], "enable_cache_write": False, "read_cache": True}
        assert "not in the instance" not in caplog.text
        assert evaluation.payoffs is not None
        assert evaluation.payoffs.cards("mana") == {"A", "Grapeshot"}
        assert evaluation.payoff_stats is not None
        assert evaluation.payoff_stats.cards == {"mana": {"Grapeshot": ["query"]}}
        assert evaluation.payoff_stats.payoff_only == ["Grapeshot"]

    async def test_payoffs_need_the_outcome_table(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        async def fake_load_instance(**kwargs: Any) -> tuple[list[ComboData], dict]:
            return COMBOS, dict(CARDS)

        monkeypatch.setattr(module, "load_instance", fake_load_instance)
        cube_file = tmp_path / "cube.txt"
        cube_file.write_text("H\nP1\n", encoding="utf-8")
        payoffs = parse_payoff_table({"mana": {"cards": ["A"]}})

        evaluation = await module.evaluate_cube(str(cube_file), payoff_definitions=payoffs)

        assert evaluation.payoffs is None
        assert evaluation.payoff_stats is None

    def test_read_cube_file_skips_blank_lines(self, tmp_path: Path):
        cube_file = tmp_path / "cube.txt"
        cube_file.write_text("A\n\n  B  \n", encoding="utf-8")

        assert module.read_cube_file(str(cube_file)) == ["A", "B"]
