"""Algorithm selection and execution for cube building."""

import logging

from mtg_combo_cube.greedy.greedy_runner import run_greedy
from mtg_combo_cube.ilp.ilp_runner import run_ilp

logger = logging.getLogger(__name__)


async def run(
    method: str,
    cube_size: int,
    output_file: str,
    golden_ratio: float | None = None,
    time_limit_seconds: int = 300,
    max_variants: int = 10000,
    use_multi_objective: bool = True,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    combo_tolerance: float = 0.1,
    min_coverage_ratio: float = 0.1,
):
    """
    Run the cube building algorithm.

    Args:
        method: "greedy" or "ilp"
        cube_size: Target number of cards in the cube
        output_file: Path to write the cube list
        golden_ratio: Ratio for greedy method (default: 1.2)
        time_limit_seconds: Time limit for ILP solver
        max_variants: Maximum combo variants to fetch
        use_multi_objective: Use two-phase ILP optimization
        enable_cache_write: Write API responses to cache
        read_cache: Read API responses from cache only
        combo_tolerance: Tolerance for combo count deviation in phase 2
        min_coverage_ratio: Minimum coverage ratio for requirement templates in phase 2
    """
    if method == "ilp":
        await run_ilp(
            cube_size=cube_size,
            output_file=output_file,
            time_limit_seconds=time_limit_seconds,
            max_variants=max_variants,
            use_multi_objective=use_multi_objective,
            enable_cache_write=enable_cache_write,
            read_cache=read_cache,
            combo_tolerance=combo_tolerance,
            min_coverage_ratio=min_coverage_ratio,
        )
    elif method == "greedy":
        await run_greedy(
            cube_size=cube_size,
            output_file=output_file,
            golden_ratio=golden_ratio,
        )
    else:
        raise ValueError(f"Unknown method: {method}")
