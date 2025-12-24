"""CLI entry point for MTG Combo Cube builder."""

import argparse
import asyncio
import logging

from mtg_combo_cube.runner import run

if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Build MTG combo cube with optimal card selection"
    )
    argparser.add_argument("-c", "--cube-size", type=int, default=300)
    argparser.add_argument(
        "-r", "--ratio", type=float, help="Golden ratio for greedy method (default: 1.2)"
    )
    argparser.add_argument("-o", "--output-file", type=str, default="data/cube.txt")
    argparser.add_argument("-d", "--debug", action="store_true")
    argparser.add_argument(
        "-m",
        "--method",
        choices=["greedy", "ilp"],
        default="ilp",
        help="Optimization method: greedy or ilp (default)",
    )
    argparser.add_argument(
        "-t",
        "--time-limit",
        type=int,
        default=300,
        help="Time limit for ILP solver in seconds (default: 300)",
    )
    argparser.add_argument(
        "-n",
        "--max-variants",
        type=int,
        default=10000,
        help="Maximum number of combo variants to fetch (default: 10000)",
    )
    argparser.add_argument(
        "--single-phase",
        action="store_true",
        help="Use single-phase ILP (max combos only). Default: two-phase (balanced utilization)",
    )
    argparser.add_argument(
        "--combo-tolerance",
        type=float,
        default=0.1,
        help="Tolerance for combo count deviation in phase 2 (default: 0.1 = 10%%). "
        "Set to 0 for strict equality constraint.",
    )
    argparser.add_argument(
        "--min-coverage-ratio",
        type=float,
        default=0.1,
        help="Minimum coverage ratio for requirement templates in phase 2 (default: 0.1 = 10%%). "
        "Set to 0 to disable coverage constraints.",
    )
    argparser.add_argument(
        "--skip-api-caching", action="store_true", help="Skip writing API responses to cache files"
    )
    argparser.add_argument(
        "--read-api-cache",
        action="store_true",
        help="Attempt to read API responses from cache, fall back to live API calls if cache missing",
    )
    args = argparser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    asyncio.run(
        run(
            method=args.method,
            cube_size=args.cube_size,
            output_file=args.output_file,
            golden_ratio=args.ratio,
            time_limit_seconds=args.time_limit,
            max_variants=args.max_variants,
            use_multi_objective=not args.single_phase,
            enable_cache_write=not args.skip_api_caching,
            read_cache=args.read_api_cache,
            combo_tolerance=args.combo_tolerance,
            min_coverage_ratio=args.min_coverage_ratio,
        )
    )
