"""Evaluate an existing cube list: true combo count and utilization statistics.

Usage:
    uv run python -m mtg_combo_cube.ilp.evaluate_cube data/cube.txt -n 20000

The instance is loaded through the same path as a build (cached API data, blocklist,
preprocessing), so the numbers are comparable with a build that used the same settings.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from mtg_combo_cube.blocklist import load_blocklist
from mtg_combo_cube.ilp.cube_evaluation import (
    card_utilization,
    completable_combo_ids,
    completable_group_keys,
    compute_color_stats,
    compute_utilization_stats,
    largest_combo_groups,
)
from mtg_combo_cube.ilp.ilp_models import ComboGroupStats, UtilizationStats
from mtg_combo_cube.ilp.ilp_runner import (
    fetch_color_identities,
    format_color_stats,
    format_combo_count,
    load_instance,
)

logger = logging.getLogger(__name__)


def read_cube_file(path: str) -> list[str]:
    """Read card names from a cube list file (one name per line, blank lines ignored)."""
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


@dataclass
class CubeEvaluation:
    """What a cube list completes and how its cards are used."""

    card_count: int
    combo_count: int  # completed variants
    distinct_combo_count: int  # combo groups with a completed variant
    utilization_stats: UtilizationStats
    largest_combo_groups: list[ComboGroupStats]


async def evaluate_cube(
    cube_file: str,
    max_variants: int = 20000,
    blocklist: frozenset[str] = frozenset(),
) -> CubeEvaluation:
    """Evaluate a cube list against the instance built from the cached API data."""
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

    return CubeEvaluation(
        card_count=len(cards),
        combo_count=len(completable_combo_ids(cards, combos)),
        distinct_combo_count=len(completable_group_keys(cards, combos)),
        utilization_stats=compute_utilization_stats(card_utilization(cards, combos)),
        largest_combo_groups=largest_combo_groups(cards, combos),
    )


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Print the true combo count and utilization statistics of a cube list"
    )
    argparser.add_argument("cube_file", type=str, help="Cube list file (one card name per line)")
    argparser.add_argument(
        "-n",
        "--max-variants",
        type=int,
        default=20000,
        help="Maximum number of combo variants, as used for the build (default: 20000)",
    )
    argparser.add_argument(
        "--blocklist",
        type=str,
        default=None,
        help="Path to blocklist file (default: data/blocklist.txt)",
    )
    args = argparser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    evaluation = asyncio.run(
        evaluate_cube(
            cube_file=args.cube_file,
            max_variants=args.max_variants,
            blocklist=load_blocklist(args.blocklist),
        )
    )
    stats = evaluation.utilization_stats
    print(f"Cube: {Path(args.cube_file)} ({evaluation.card_count} cards)")
    print(
        "Combos completed: "
        f"{format_combo_count(evaluation.combo_count, evaluation.distinct_combo_count)}"
    )
    for group in evaluation.largest_combo_groups:
        print(
            f"  combo {group.group_key}: {group.variant_count} variants, {len(group.cards)} cards"
        )
    print(
        f"Utilization: min={stats.min_utilization}, max={stats.max_utilization}, "
        f"range={stats.max_utilization - stats.min_utilization}, "
        f"mean={stats.mean_utilization:.2f}, median={stats.median_utilization:.1f}, "
        f"std_dev={stats.std_deviation:.2f}"
    )

    cube_cards = read_cube_file(args.cube_file)
    color_identities = asyncio.run(fetch_color_identities(cube_cards, read_cache=True))
    if color_identities is not None:
        color_stats = compute_color_stats(cube_cards, color_identities)
        print(f"Colors (by color identity): {format_color_stats(color_stats)}")
