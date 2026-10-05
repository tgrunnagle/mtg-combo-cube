"""Unit tests for profiling utilities."""

import pytest
from ortools.sat.python import cp_model

from mtg_combo_cube.ilp.profiling import extract_solver_stats, relative_gap


class TestRelativeGap:
    """Test relative_gap."""

    def test_zero_when_objective_equals_bound(self):
        assert relative_gap(0.0, 0.0) == 0.0
        assert relative_gap(9999.0, 9999.0) == 0.0
        assert relative_gap(-5.0, -5.0) == 0.0

    def test_relative_to_objective(self):
        assert relative_gap(200.0, 150.0) == pytest.approx(0.25)
        assert relative_gap(-200.0, -250.0) == pytest.approx(0.25)

    def test_objective_zero_with_different_bound(self):
        # Defined as |objective - bound| / max(1, |objective|), like the CP-SAT gap limit
        assert relative_gap(0.0, -3.0) == pytest.approx(3.0)


class TestExtractSolverStats:
    """Test extract_solver_stats against real solves."""

    @staticmethod
    def solve(model: cp_model.CpModel) -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.solve(model)
        return solver

    def test_optimal_objective_zero_reports_zero_gap(self):
        model = cp_model.CpModel()
        x = model.new_int_var(0, 5, "x")
        model.minimize(x)

        stats = extract_solver_stats(self.solve(model))

        assert stats["objective_value"] == 0
        assert stats["best_objective_bound"] == 0
        assert stats["relative_gap"] == 0.0

    def test_optimal_nonzero_objective(self):
        model = cp_model.CpModel()
        x = model.new_int_var(3, 5, "x")
        model.minimize(x)

        stats = extract_solver_stats(self.solve(model))

        assert stats["objective_value"] == 3
        assert stats["relative_gap"] == 0.0
        assert {"wall_time", "branches", "conflicts", "booleans"} <= set(stats)

    def test_infeasible_has_no_objective_or_gap(self):
        model = cp_model.CpModel()
        x = model.new_int_var(0, 5, "x")
        model.add(x >= 6)
        model.minimize(x)

        stats = extract_solver_stats(self.solve(model))

        assert "objective_value" not in stats
        assert "relative_gap" not in stats
