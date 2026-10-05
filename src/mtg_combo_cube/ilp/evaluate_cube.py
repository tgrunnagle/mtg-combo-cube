"""Evaluate an existing cube list: true combo count and utilization statistics.

Usage:
    uv run python -m mtg_combo_cube.ilp.evaluate_cube data/cube.txt -n 10000

The instance is loaded through the same path as a build (cached API data, blocklist,
preprocessing), so the numbers are comparable with a build that used the same settings.
"""

import argparse
import asyncio
import logging
from pathlib import Path

from mtg_combo_cube.blocklist import load_blocklist
from mtg_combo_cube.ilp.cube_evaluation import (
    card_utilization,
    completable_combo_ids,
    compute_utilization_stats,
)
from mtg_combo_cube.ilp.ilp_models import UtilizationStats
from mtg_combo_cube.ilp.ilp_runner import load_instance

logger = logging.getLogger(__name__)


def read_cube_file(path: str) -> list[str]:
    """Read card names from a cube list file (one name per line, blank lines ignored)."""
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


async def evaluate_cube(
    cube_file: str,
    max_variants: int = 10000,
    blocklist: frozenset[str] = frozenset(),
) -> tuple[int, int, UtilizationStats]:
    """
    Evaluate a cube list against the instance built from the cached API data.

    Returns:
        - Number of cards in the cube
        - Number of combos the cube completes
        - Utilization statistics over the cube's cards
    """
    cards = read_cube_file(cube_file)
    combos, candidate_cards = await load_instance(
        max_variants=max_variants,
        enable_cache_write=False,
        read_cache=True,
        blocklist=blocklist,
    )

    unknown = [card for card in cards if card not in candidate_cards]
    if unknown:
        logger.warning(
            f"{len(unknown)} cards in {cube_file} are not in the instance "
            f"(they count with utilization 0): {unknown[:5]}"
        )

    combo_count = len(completable_combo_ids(cards, combos))
    stats = compute_utilization_stats(card_utilization(cards, combos))
    return len(cards), combo_count, stats


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Print the true combo count and utilization statistics of a cube list"
    )
    argparser.add_argument("cube_file", type=str, help="Cube list file (one card name per line)")
    argparser.add_argument(
        "-n",
        "--max-variants",
        type=int,
        default=10000,
        help="Maximum number of combo variants, as used for the build (default: 10000)",
    )
    argparser.add_argument(
        "--blocklist",
        type=str,
        default=None,
        help="Path to blocklist file (default: data/blocklist.txt)",
    )
    args = argparser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    card_count, combo_count, stats = asyncio.run(
        evaluate_cube(
            cube_file=args.cube_file,
            max_variants=args.max_variants,
            blocklist=load_blocklist(args.blocklist),
        )
    )
    print(f"Cube: {Path(args.cube_file)} ({card_count} cards)")
    print(f"Combos completed: {combo_count}")
    print(
        f"Utilization: min={stats.min_utilization}, max={stats.max_utilization}, "
        f"range={stats.max_utilization - stats.min_utilization}, "
        f"mean={stats.mean_utilization:.2f}, median={stats.median_utilization:.1f}, "
        f"std_dev={stats.std_deviation:.2f}"
    )
