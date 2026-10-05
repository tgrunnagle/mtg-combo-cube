"""Tests that CLI options reach the ILP optimizer (CLI -> runner -> ilp_runner -> optimizer)."""

import inspect
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

    def test_unknown_objective_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        with pytest.raises(SystemExit):
            run_cli(monkeypatch, "--phase2-objective", "maxmin")


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

        monkeypatch.setattr(ilp_runner, "load_instance", fake_load_instance)
        monkeypatch.setattr(ilp_runner, "ILPOptimizer", RecordingOptimizer)

        await ilp_runner.run_ilp(
            cube_size=3,
            output_file=str(tmp_path / "cube.txt"),
            time_limit_seconds=10,
            phase2_objective="softcap",
            util_cap=util_cap,
            num_workers=1,
        )

        assert len(created) == 1
        assert created[0].util_cap == util_cap
        assert created[0].phase2_objective == "softcap"
        # Every card of the triangle has utilization 2 in Phase 1: derived cap = 2 x 2
        assert results[0].phase2_util_cap == (4 if util_cap is None else util_cap)
        assert (tmp_path / "cube.txt").read_text(encoding="utf-8").split("\n") == ["A", "B", "C"]
