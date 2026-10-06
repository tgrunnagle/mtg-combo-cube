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
        default=20000,
        help="Maximum number of combo variants to fetch (default: 20000)",
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
        help="Tolerance for combo count deviation in phase 2 (default: 0.1 = 10%%), measured "
        "from the most combos found for a cube that satisfies the coverage and color balance "
        "constraints. Set to 0 for strict equality constraint.",
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
        help="Read API responses from cache, fall back to live API if missing",
    )
    argparser.add_argument(
        "--blocklist",
        type=str,
        default=None,
        help="Path to blocklist file (default: data/blocklist.txt)",
    )
    argparser.add_argument(
        "--profile",
        action="store_true",
        help="Enable detailed profiling of ILP optimization (constraint counts, solver stats)",
    )
    argparser.add_argument(
        "--gap-limit",
        type=float,
        default=0.05,
        help="Gap limit for Phase 2 early termination (default: 0.05 = 5%%). "
        "Solver stops when solution is within this %% of optimal ('softcap' and 'tiered': "
        "within this %% of the Phase 1 cube's overage). Set to 0 for exact optimal.",
    )
    argparser.add_argument(
        "--phase2-objective",
        type=str,
        choices=["mad", "minmax", "maxutil", "softcap", "tiered"],
        default="tiered",
        help="Phase 2 objective function: 'tiered' (default; minimize total utilization "
        "above --util-cap, counted again above 2x and 4x the cap), 'softcap' (minimize total "
        "utilization above --util-cap), 'maxutil' (minimize the maximum utilization), "
        "'minmax' (minimize max-min range) or 'mad' (minimize mean absolute deviation).",
    )
    argparser.add_argument(
        "--util-cap",
        type=int,
        default=None,
        help="Utilization cap for the 'softcap' and 'tiered' phase 2 objectives: utilization "
        "above this is penalized (default: 2 x the Phase 1 median utilization).",
    )
    argparser.add_argument(
        "--min-util-floor",
        type=int,
        default=2,
        help="Minimum utilization floor for phase 2, any objective (default: 2). "
        "Cards must participate in at least this many combos. Set to 0 to disable.",
    )
    argparser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel search workers for the ILP solver (default: 8). "
        "Lower this to reduce CPU load.",
    )
    argparser.add_argument(
        "--max-color-ratio",
        type=float,
        default=2.0,
        help="Color balance for phase 2: no color may have more than this many times the "
        "cards of another color (default: 2.0). A card counts once for each color of its "
        "color identity. Must be 0 or at least 1. Set to 0 to disable.",
    )
    argparser.add_argument(
        "--variant-weight",
        type=float,
        default=0.1,
        help="Value of each further completed variant of a combo the cube already completes, "
        "relative to the first, in the Phase 1 objective and the Phase 2 combo window "
        "(default: 0.1; 1 counts every variant as a combo, 0 counts distinct combos only). "
        "Between 0 and 1.",
    )
    args = argparser.parse_args()
    if 0 < args.max_color_ratio < 1:
        argparser.error("--max-color-ratio must be 0 or at least 1")
    if not 0 <= args.variant_weight <= 1:
        argparser.error("--variant-weight must be between 0 and 1")
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
            blocklist_path=args.blocklist,
            profile=args.profile,
            gap_limit=args.gap_limit,
            phase2_objective=args.phase2_objective,
            min_utilization_floor=args.min_util_floor,
            num_workers=args.workers,
            util_cap=args.util_cap,
            max_color_ratio=args.max_color_ratio,
            variant_weight=args.variant_weight,
        )
    )
