"""Tests that CLI options reach the ILP optimizer (CLI -> runner -> ilp_runner -> optimizer)."""

import inspect
import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from mtg_combo_cube import runner
from mtg_combo_cube.ilp import ilp_runner
from mtg_combo_cube.ilp.ilp_models import (
    CandidateCard,
    CardMixRules,
    ComboData,
    OptimizationResult,
)
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.models import CardAttributes


def run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> dict[str, Any]:
    """Run the CLI entry point with `args` and return the keyword arguments run() received."""
    received: dict[str, Any] = {}

    async def fake_run(**kwargs: Any) -> None:
        received.update(kwargs)

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["mtg_combo_cube", *args])
    runpy.run_module("mtg_combo_cube", run_name="__main__")
    return received


class TestCliPlumbing:
    """Phase 2 objective options on the command line."""

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(monkeypatch)

        # The CLI default is the optimizer default (Stage 4 decision: "tiered")
        assert received["phase2_objective"] == "tiered"
        assert ILPOptimizer(combos=[], candidate_cards={}, cube_size=1).phase2_objective == "tiered"
        assert received["util_cap"] is None
        assert received["combo_tolerance"] == 0.1

    def test_defaults_agree_on_every_layer(self, monkeypatch: pytest.MonkeyPatch):
        """The CLI, runner, ILP runner and optimizer all declare the same default values."""
        cli_defaults = run_cli(monkeypatch)
        layers = [runner.run, ilp_runner.run_ilp, ilp_runner.build_cube_ilp, ILPOptimizer.__init__]

        compared = 0
        for function in layers:
            for name, parameter in inspect.signature(function).parameters.items():
                if name in cli_defaults and parameter.default is not inspect.Parameter.empty:
                    assert parameter.default == cli_defaults[name], (function.__qualname__, name)
                    compared += 1

        # 8 solver options on 4 layers, plus max_variants and the cache/profile switches
        assert compared >= 32

    def test_util_cap(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(monkeypatch, "--phase2-objective", "softcap", "--util-cap", "40")

        assert received["phase2_objective"] == "softcap"
        assert received["util_cap"] == 40

    @pytest.mark.parametrize("objective", sorted(ILPOptimizer._PHASE2_OBJECTIVES))
    def test_every_registered_objective_is_a_cli_choice(
        self, monkeypatch: pytest.MonkeyPatch, objective: str
    ):
        received = run_cli(monkeypatch, "--phase2-objective", objective)

        assert received["phase2_objective"] == objective

    def test_max_color_ratio(self, monkeypatch: pytest.MonkeyPatch):
        assert run_cli(monkeypatch)["max_color_ratio"] == 2.0
        assert run_cli(monkeypatch, "--max-color-ratio", "1.5")["max_color_ratio"] == 1.5
        assert run_cli(monkeypatch, "--max-color-ratio", "0")["max_color_ratio"] == 0

    def test_max_color_ratio_below_one_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "--max-color-ratio", "0.5")

    def test_variant_weight(self, monkeypatch: pytest.MonkeyPatch):
        assert run_cli(monkeypatch)["variant_weight"] == 0.1
        assert run_cli(monkeypatch, "--variant-weight", "1")["variant_weight"] == 1.0
        assert run_cli(monkeypatch, "--variant-weight", "0.1")["variant_weight"] == 0.1
        assert run_cli(monkeypatch, "--variant-weight", "0")["variant_weight"] == 0

    @pytest.mark.parametrize("weight", ["-0.5", "1.5"])
    def test_variant_weight_outside_zero_to_one_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, weight: str
    ):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "--variant-weight", weight)

    def test_unknown_objective_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "--phase2-objective", "maxmin")

    def test_archetype_defaults(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(monkeypatch)

        assert received["min_pair_combos"] == 250
        assert received["min_mono_combos"] == 150
        assert received["max_wide_combo_share"] == 0.25

    def test_archetype_options(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(
            monkeypatch,
            "--min-pair-combos",
            "25",
            "--min-mono-combos",
            "8",
            "--max-wide-combo-share",
            "0.3",
        )

        assert received["min_pair_combos"] == 25
        assert received["min_mono_combos"] == 8
        assert received["max_wide_combo_share"] == 0.3

    @pytest.mark.parametrize(
        "args",
        [
            ("--min-pair-combos", "-1"),
            ("--min-mono-combos", "-3"),
            ("--max-wide-combo-share", "1.5"),
            ("--max-wide-combo-share", "-0.1"),
        ],
    )
    def test_invalid_archetype_options_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, args: tuple[str, str]
    ):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, *args)

    def test_card_mix_defaults(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(monkeypatch)

        assert received["card_mix"] == CardMixRules()
        assert received["card_mix"].max_multicolor_share == 0.15
        assert received["card_mix"].mono_color_ratio == 0

    def test_card_mix_options(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(
            monkeypatch,
            "--max-multicolor-share",
            "0.2",
            "--max-colorless-share",
            "0",
            "--max-expensive-share",
            "0.12",
            "--expensive-mana-value",
            "6",
            "--max-creature-share",
            "0.55",
            "--min-spell-share",
            "0.08",
            "--mono-color-ratio",
            "1.5",
        )

        assert received["card_mix"] == CardMixRules(
            max_multicolor_share=0.2,
            max_colorless_share=0,
            max_expensive_share=0.12,
            expensive_mana_value=6,
            max_creature_share=0.55,
            min_spell_share=0.08,
            mono_color_ratio=1.5,
        )

    @pytest.mark.parametrize(
        "args",
        [
            ("--max-multicolor-share", "1.5"),
            ("--max-colorless-share", "-0.1"),
            ("--max-colorless-share", "x"),
            ("--max-expensive-share", "2"),
            ("--expensive-mana-value", "-1"),
            ("--expensive-mana-value", "0"),
            ("--max-creature-share", "-0.5"),
            ("--max-creature-share", "nan"),
            ("--min-spell-share", "1.1"),
            ("--min-spell-share", "0.001"),
            ("--mono-color-ratio", "0.5"),
            ("--mono-color-ratio", "inf"),
            ("--max-color-ratio", "nan"),
        ],
    )
    def test_invalid_card_mix_options_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, args: tuple[str, str]
    ):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, *args)

    def test_outcome_and_popularity_defaults(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(monkeypatch)

        assert received["min_outcome_combos"] == 40
        assert received["max_outcome_share"] == 0
        assert received["outcome_categories_path"] is None
        assert received["popularity_weight"] == 0

    def test_outcome_and_popularity_options(self, monkeypatch: pytest.MonkeyPatch):
        received = run_cli(
            monkeypatch,
            "--min-outcome-combos",
            "40",
            "--max-outcome-share",
            "0.5",
            "--outcome-categories",
            "my/outcomes.json",
            "--popularity-weight",
            "0.5",
        )

        assert received["min_outcome_combos"] == 40
        assert received["max_outcome_share"] == 0.5
        assert received["outcome_categories_path"] == "my/outcomes.json"
        assert received["popularity_weight"] == 0.5

    @pytest.mark.parametrize(
        "args",
        [
            ("--min-outcome-combos", "-1"),
            ("--max-outcome-share", "1.5"),
            ("--max-outcome-share", "-0.1"),
            ("--popularity-weight", "-1"),
            ("--popularity-weight", "nan"),
            ("--popularity-weight", "inf"),
        ],
    )
    def test_invalid_outcome_and_popularity_options_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch, args: tuple[str, str]
    ):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, *args)

    def test_card_mix_error_names_the_flag(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "--max-creature-share", "1.5")

        assert "--max-creature-share must be between 0 and 1, got 1.5" in capsys.readouterr().err


class TestRunnerPlumbing:
    """runner.run and ilp_runner pass util_cap down to the optimizer."""

    async def test_run_passes_util_cap_to_run_ilp(self, monkeypatch: pytest.MonkeyPatch):
        received: dict[str, Any] = {}

        async def fake_run_ilp(**kwargs: Any) -> None:
            received.update(kwargs)

        monkeypatch.setattr(runner, "run_ilp", fake_run_ilp)

        await runner.run(method="ilp", cube_size=10, output_file="unused.txt", util_cap=25)
        assert received["util_cap"] == 25

        await runner.run(method="ilp", cube_size=10, output_file="unused.txt")
        assert received["util_cap"] is None
        assert received["variant_weight"] == 0.1

        await runner.run(method="ilp", cube_size=10, output_file="unused.txt", variant_weight=0.25)
        assert received["variant_weight"] == 0.25

        await runner.run(
            method="ilp",
            cube_size=10,
            output_file="unused.txt",
            min_pair_combos=25,
            min_mono_combos=8,
            max_wide_combo_share=0.3,
        )
        assert received["min_pair_combos"] == 25
        assert received["min_mono_combos"] == 8
        assert received["max_wide_combo_share"] == 0.3
        assert received["card_mix"] == CardMixRules()

        card_mix = CardMixRules(max_creature_share=0.5)
        await runner.run(method="ilp", cube_size=10, output_file="unused.txt", card_mix=card_mix)
        assert received["card_mix"] == card_mix
        assert received["min_outcome_combos"] == 40
        assert received["max_outcome_share"] == 0
        assert received["outcome_categories_path"] is None
        assert received["popularity_weight"] == 0

        await runner.run(
            method="ilp",
            cube_size=10,
            output_file="unused.txt",
            outcome_categories_path="my/outcomes.json",
            min_outcome_combos=40,
            max_outcome_share=0.5,
            popularity_weight=0.5,
        )
        assert received["outcome_categories_path"] == "my/outcomes.json"
        assert received["min_outcome_combos"] == 40
        assert received["max_outcome_share"] == 0.5
        assert received["popularity_weight"] == 0.5

    @pytest.mark.parametrize("util_cap", [None, 3])
    async def test_run_ilp_passes_util_cap_to_optimizer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, util_cap: int | None
    ):
        combos = [
            ComboData("ab", frozenset(["A", "B"]), [], 10),
            ComboData("bc", frozenset(["B", "C"]), [], 10),
            ComboData("ca", frozenset(["C", "A"]), [], 10),
        ]
        cards = {
            name: CandidateCard(name, frozenset(), frozenset()) for name in ["A", "B", "C", "D"]
        }

        async def fake_load_instance(**kwargs: Any) -> tuple[list[ComboData], dict]:
            return combos, cards

        created: list[ILPOptimizer] = []
        results: list[OptimizationResult] = []

        class RecordingOptimizer(ILPOptimizer):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                created.append(self)

            def solve_two_phase(self, profile: bool = False) -> OptimizationResult:
                results.append(super().solve_two_phase(profile=profile))
                return results[-1]

        attributes = {
            "A": CardAttributes("W", "Creature \u2014 Human", 2),
            "B": CardAttributes("WU", "Instant", 1),
            "C": CardAttributes("", "Artifact", 0),
            "D": CardAttributes("G", "Creature \u2014 Elf", 3),
        }

        async def fake_fetch_card_attributes(
            card_names: Any, **kwargs: Any
        ) -> dict[str, CardAttributes]:
            return attributes

        monkeypatch.setattr(ilp_runner, "load_instance", fake_load_instance)
        monkeypatch.setattr(ilp_runner, "ILPOptimizer", RecordingOptimizer)
        monkeypatch.setattr(ilp_runner, "fetch_card_attributes", fake_fetch_card_attributes)
        # A three-card cube: at most two creatures, at least one instant or sorcery
        card_mix = CardMixRules(
            max_multicolor_share=0,
            max_colorless_share=0,
            max_expensive_share=0,
            max_creature_share=0.7,
            min_spell_share=0.3,
        )

        await ilp_runner.run_ilp(
            cube_size=3,
            output_file=str(tmp_path / "cube.txt"),
            time_limit_seconds=10,
            phase2_objective="softcap",
            util_cap=util_cap,
            num_workers=1,
            max_color_ratio=0,
            variant_weight=0.5,
            min_pair_combos=25,
            min_mono_combos=8,
            max_wide_combo_share=0.3,
            card_mix=card_mix,
            min_outcome_combos=0,
        )

        assert len(created) == 1
        assert created[0].util_cap == util_cap
        assert created[0].min_pair_combos == 25
        assert created[0].min_mono_combos == 8
        assert created[0].max_wide_combo_share == 0.3
        assert created[0].phase2_objective == "softcap"
        assert created[0].variant_weight == 0.5
        # The attributes of every candidate card reach the optimizer, with the rules
        assert created[0].card_attributes == attributes
        assert created[0].max_color_ratio == 0
        assert created[0].card_mix == card_mix
        # Every card of the triangle has utilization 2 in Phase 1: derived cap = 2 x 2
        assert results[0].phase2_util_cap == (4 if util_cap is None else util_cap)
        assert (tmp_path / "cube.txt").read_text(encoding="utf-8").split("\n") == ["A", "B", "C"]

        # The stats file reports the combo count and color distribution of both phases
        stats = json.loads((tmp_path / "cube_stats.json").read_text(encoding="utf-8"))
        assert stats["metadata"]["variant_weight"] == 0.5
        assert stats["metadata"]["distinct_combo_count"] == 3
        assert [g["variant_count"] for g in stats["largest_combo_groups"]] == [1, 1, 1]
        for phase in ("phase1", "phase2"):
            assert stats[phase]["combo_count"] == 3
            assert stats[phase]["distinct_combo_count"] == 3
            assert stats[phase]["colors"]["cards_per_color"] == {
                "W": 2,
                "U": 1,
                "B": 0,
                "R": 0,
                "G": 0,
            }
            # The combos carry no color identity: no archetype counts are reported ...
            assert "archetypes" not in stats[phase]
            assert stats[phase]["card_mix"]["type_counts"]["Creature"] == 1
            assert stats[phase]["card_mix"]["type_counts"]["Instant"] == 1
            assert stats[phase]["card_mix"]["multicolor"] == 1
        # ... and the archetype rules were not applied; the card mix rules were
        assert "min_pair_combos" not in stats["phase2"]
        assert stats["phase2"]["card_mix_rules"] == {
            "max_creature_share": 0.7,
            "min_spell_share": 0.3,
        }
        assert stats["phase2"]["card_mix_limits"] == {"creature_cap": 2, "spell_floor": 1}
        assert stats["phase2"]["unknown_candidate_cards"] == 0
        # The default outcome table was loaded: the combos have no features, so every
        # completed combo is uncategorized; no outcome rule was asked for
        assert created[0].outcome_categories is not None
        assert created[0].min_outcome_combos == 0
        assert created[0].popularity_weight == 0
        assert stats["phase1"]["outcomes"]["uncategorized"] == 3
        assert stats["phase2"]["outcomes"]["total"] == 3
        assert stats["phase2"]["popularity"]["combo_count"] == 3
        assert "outcome_minimums" not in stats["phase2"]
        assert stats["metadata"]["popularity_weight"] == 0
        assert "combo_score" not in stats["metadata"]

    async def test_run_ilp_passes_the_outcome_table_and_popularity_weight(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        mana = frozenset(["Infinite colored mana"])
        damage = frozenset(["Infinite damage"])
        combos = [
            ComboData("ab", frozenset(["A", "B"]), [], 100, features=mana),
            ComboData("bc", frozenset(["B", "C"]), [], 100, features=mana),
            ComboData("ca", frozenset(["C", "A"]), [], 100, features=mana),
            ComboData("de", frozenset(["D", "E"]), [], 1, features=damage),
        ]
        cards = {
            name: CandidateCard(name, frozenset(), frozenset())
            for name in ["A", "B", "C", "D", "E"]
        }
        table = tmp_path / "outcomes.json"
        table.write_text(
            json.dumps({"mana": ["infinite colored mana"], "damage": ["infinite damage"]}),
            encoding="utf-8",
        )

        async def fake_load_instance(**kwargs: Any) -> tuple[list[ComboData], dict]:
            return combos, cards

        async def fake_fetch_card_attributes(card_names: Any, **kwargs: Any) -> None:
            return None

        created: list[ILPOptimizer] = []

        class RecordingOptimizer(ILPOptimizer):
            def __init__(self, *args: Any, **kwargs: Any):
                super().__init__(*args, **kwargs)
                created.append(self)

        monkeypatch.setattr(ilp_runner, "load_instance", fake_load_instance)
        monkeypatch.setattr(ilp_runner, "ILPOptimizer", RecordingOptimizer)
        monkeypatch.setattr(ilp_runner, "fetch_card_attributes", fake_fetch_card_attributes)

        # Five cards hold the triangle and the damage combo; the minimum asks for one of each
        await ilp_runner.run_ilp(
            cube_size=5,
            output_file=str(tmp_path / "cube.txt"),
            time_limit_seconds=10,
            num_workers=1,
            min_utilization_floor=0,
            max_color_ratio=0,
            min_pair_combos=0,
            min_mono_combos=0,
            max_wide_combo_share=0,
            card_mix=CardMixRules(
                max_multicolor_share=0,
                max_colorless_share=0,
                max_expensive_share=0,
                max_creature_share=0,
                min_spell_share=0,
            ),
            outcome_categories_path=str(table),
            min_outcome_combos=1,
            max_outcome_share=0.9,
            popularity_weight=0.5,
        )

        assert len(created) == 1
        assert created[0].outcome_categories is not None
        assert created[0].outcome_categories.names == ("mana", "damage")
        assert created[0].min_outcome_combos == 1
        assert created[0].max_outcome_share == 0.9
        assert created[0].popularity_weight == 0.5

        stats = json.loads((tmp_path / "cube_stats.json").read_text(encoding="utf-8"))
        assert stats["metadata"]["popularity_weight"] == 0.5
        assert stats["metadata"]["combo_score"] == pytest.approx(stats["phase2"]["combo_score"])
        assert stats["phase2"]["outcomes"]["combos_per_outcome"] == {"mana": 3, "damage": 1}
        assert stats["phase2"]["outcomes"]["uncategorized"] == 0
        assert stats["phase2"]["outcome_minimums"] == {"mana": 1, "damage": 1}
        assert stats["phase2"]["max_outcome_share"] == 0.9
        assert stats["phase2"]["popularity"]["median_popularity"] == 100
        assert (
            stats["phase2"]["reference_combo_score"]
            > stats["phase2"]["reference_weighted_combo_count"]
        )
