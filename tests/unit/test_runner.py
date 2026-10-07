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
from mtg_combo_cube.ilp.ilp_models import CandidateCard, ComboData, OptimizationResult
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer


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

        async def fake_fetch_color_identities(card_names: Any, **kwargs: Any) -> dict[str, str]:
            return {"A": "W", "B": "WU", "C": ""}

        monkeypatch.setattr(ilp_runner, "load_instance", fake_load_instance)
        monkeypatch.setattr(ilp_runner, "ILPOptimizer", RecordingOptimizer)
        monkeypatch.setattr(ilp_runner, "fetch_color_identities", fake_fetch_color_identities)

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
        )

        assert len(created) == 1
        assert created[0].util_cap == util_cap
        assert created[0].min_pair_combos == 25
        assert created[0].min_mono_combos == 8
        assert created[0].max_wide_combo_share == 0.3
        assert created[0].phase2_objective == "softcap"
        assert created[0].variant_weight == 0.5
        # The colors of every candidate card reach the optimizer, with the ratio
        assert created[0].card_colors == {"A": "W", "B": "WU", "C": ""}
        assert created[0].max_color_ratio == 0
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
        # ... and the archetype rules were not applied
        assert "min_pair_combos" not in stats["phase2"]
