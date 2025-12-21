import argparse
import asyncio
import logging

from mtg_combo_cube.ilp_runner import run_ilp
from mtg_combo_cube.run import run

if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Build MTG combo cube with optimal card selection"
    )
    argparser.add_argument("-c", "--cube-size", type=int, default=300)
    argparser.add_argument("-r", "--ratio", type=float,
                          help="Golden ratio for greedy method (default: 1.2)")
    argparser.add_argument("-o", "--output-file", type=str, default='cube.txt')
    argparser.add_argument("-d", "--debug", action="store_true")
    argparser.add_argument(
        "-m", "--method",
        choices=["greedy", "ilp"],
        default="greedy",
        help="Optimization method: greedy (default) or ilp"
    )
    argparser.add_argument(
        "-t", "--time-limit",
        type=int,
        default=300,
        help="Time limit for ILP solver in seconds (default: 300)"
    )
    argparser.add_argument(
        "-n", "--max-variants",
        type=int,
        default=10000,
        help="Maximum number of combo variants to fetch (default: 10000)"
    )
    argparser.add_argument(
        "--single-phase",
        action="store_true",
        help="Use single-phase ILP (max combos only). Default: two-phase (balanced utilization)"
    )
    args = argparser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    if args.method == "ilp":
        asyncio.run(run_ilp(
            args.cube_size,
            args.output_file,
            time_limit_seconds=args.time_limit,
            max_variants=args.max_variants,
            use_multi_objective=not args.single_phase,
        ))
    else:
        asyncio.run(run(args.cube_size, args.output_file, golden_ratio=args.ratio))