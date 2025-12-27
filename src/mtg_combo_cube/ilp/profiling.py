"""Profiling utilities for ILP optimization."""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ortools.sat.python import cp_model

logger = logging.getLogger(__name__)


@dataclass
class ProfileResult:
    """Container for profiling results from a single optimization phase."""

    phase: str
    timings: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    solver_stats: dict[str, Any] = field(default_factory=dict)

    def log_summary(self) -> None:
        """Log a formatted summary of profiling results."""
        total_constraints = sum(self.counts.values())
        total_time = sum(self.timings.values())

        logger.info(f"=== {self.phase} Profile ===")

        # Variables and constraints
        if self.counts:
            logger.info(f"Constraints: {total_constraints:,} total")
            for name, count in sorted(self.counts.items()):
                logger.info(f"  - {name}: {count:,}")

        # Timings
        if self.timings:
            build_time = self.timings.get("model_build", 0)
            solve_time = self.timings.get("solver", 0)
            extract_time = self.timings.get("extraction", 0)
            logger.info(
                f"Time: build={build_time:.1f}s | solve={solve_time:.1f}s | "
                f"extract={extract_time:.1f}s | total={total_time:.1f}s"
            )

        # Solver stats
        if self.solver_stats:
            branches = self.solver_stats.get("branches", 0)
            conflicts = self.solver_stats.get("conflicts", 0)
            booleans = self.solver_stats.get("booleans", 0)
            wall_time = self.solver_stats.get("wall_time", 0)
            logger.info(
                f"Solver: {branches:,} branches, {conflicts:,} conflicts, "
                f"{booleans:,} booleans, {wall_time:.1f}s wall time"
            )


@contextmanager
def timed_section(name: str, result: ProfileResult | None = None):
    """Context manager for timing code sections.

    Usage:
        with timed_section("model_build", profile):
            # code to time

    Args:
        name: Name of the section being timed
        result: ProfileResult to store timing in (optional)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        if result is not None:
            result.timings[name] = elapsed


def extract_solver_stats(solver: cp_model.CpSolver) -> dict[str, Any]:
    """Extract statistics from CP-SAT solver after solving.

    Args:
        solver: The CpSolver instance after solve() has been called

    Returns:
        Dictionary with solver statistics
    """
    stats = {
        "wall_time": solver.WallTime(),
        "branches": solver.NumBranches(),
        "conflicts": solver.NumConflicts(),
        "booleans": solver.NumBooleans(),
    }

    # Add objective value and bound if available
    try:
        stats["objective_value"] = solver.ObjectiveValue()
        stats["best_objective_bound"] = solver.BestObjectiveBound()
        # Calculate relative gap
        obj = solver.ObjectiveValue()
        bound = solver.BestObjectiveBound()
        if obj != 0:
            stats["relative_gap"] = abs(obj - bound) / abs(obj)
    except Exception:
        # Objective stats not available (e.g., for satisfaction problems)
        pass

    return stats


def log_profile_comparison(phase1: ProfileResult, phase2: ProfileResult | None) -> None:
    """Log a comparison summary of Phase 1 and Phase 2 profiling results.

    Args:
        phase1: ProfileResult from Phase 1
        phase2: ProfileResult from Phase 2 (optional)
    """
    logger.info("=== Profile Summary ===")

    p1_solve = phase1.timings.get("solver", 0)
    p1_branches = phase1.solver_stats.get("branches", 0)

    if phase2:
        p2_solve = phase2.timings.get("solver", 0)
        p2_branches = phase2.solver_stats.get("branches", 0)
        total = p1_solve + p2_solve

        p1_pct = 100 * p1_solve / total if total > 0 else 0
        p2_pct = 100 * p2_solve / total if total > 0 else 0

        logger.info(f"Phase 1: {p1_solve:.1f}s ({p1_pct:.0f}%) - {p1_branches:,} branches")
        logger.info(f"Phase 2: {p2_solve:.1f}s ({p2_pct:.0f}%) - {p2_branches:,} branches")

        if p1_branches > 0 and p2_branches > 0:
            branch_ratio = p2_branches / p1_branches
            logger.info(f"Phase 2 explores {branch_ratio:.1f}x more branches than Phase 1")
    else:
        logger.info(f"Phase 1: {p1_solve:.1f}s - {p1_branches:,} branches")
