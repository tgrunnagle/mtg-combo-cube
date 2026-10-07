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
    combos_per_outcome,
    completable_combo_ids,
    completed_group_sizes,
    compute_archetype_stats,
    compute_card_mix_stats,
    compute_color_stats,
    compute_utilization_stats,
    largest_combo_groups,
    popularity_stats,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.ilp_models import (
    ArchetypeStats,
    ComboGroupStats,
    OutcomeStats,
    PopularityStats,
    UtilizationStats,
)
from mtg_combo_cube.ilp.ilp_runner import (
    fetch_card_attributes,
    format_archetype_stats,
    format_card_mix_stats,
    format_color_stats,
    format_combo_count,
    format_outcome_stats,
    format_popularity_stats,
    load_instance,
)
from mtg_combo_cube.ilp.outcomes import OutcomeCategories, load_outcome_categories

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
    weighted_combo_count: float  # groups + variant_weight x further variants
    utilization_stats: UtilizationStats
    largest_combo_groups: list[ComboGroupStats]
    archetype_stats: ArchetypeStats | None  # distinct combos per draft archetype, if known
    popularity_stats: PopularityStats  # how popular the distinct combos are
    outcome_stats: OutcomeStats | None  # distinct combos per outcome category, with a table


async def evaluate_cube(
    cube_file: str,
    max_variants: int = 20000,
    blocklist: frozenset[str] = frozenset(),
    variant_weight: float = 0.1,
    outcome_categories: OutcomeCategories | None = None,
) -> CubeEvaluation:
    """
    Evaluate a cube list against the instance built from the cached API data.

    variant_weight is the value of each further completed variant of a combo, as in a
    build's --variant-weight; it only affects weighted_combo_count. outcome_categories is
    the table the combos are categorized by; without one no outcome counts are reported.
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

    completed = completable_combo_ids(cards, combos)
    group_sizes = completed_group_sizes(completed, combos)
    return CubeEvaluation(
        card_count=len(cards),
        combo_count=len(completed),
        distinct_combo_count=len(group_sizes),
        weighted_combo_count=weighted_combo_count(group_sizes, variant_weight),
        utilization_stats=compute_utilization_stats(card_utilization(cards, combos)),
        largest_combo_groups=largest_combo_groups(cards, combos, completed_ids=completed),
        archetype_stats=compute_archetype_stats(cards, combos, completed),
        popularity_stats=popularity_stats(cards, combos, completed),
        outcome_stats=(
            combos_per_outcome(cards, combos, outcome_categories, completed)
            if outcome_categories is not None
            else None
        ),
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
    argparser.add_argument(
        "--variant-weight",
        type=float,
        default=0.1,
        help="Value of each further completed variant of a combo, as used for the build "
        "(default: 0.1); sets the weighted combo count",
    )
    argparser.add_argument(
        "--outcome-categories",
        type=str,
        default=None,
        help="Path to the outcome category table (default: data/outcome_categories.json)",
    )
    args = argparser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    evaluation = asyncio.run(
        evaluate_cube(
            cube_file=args.cube_file,
            max_variants=args.max_variants,
            blocklist=load_blocklist(args.blocklist),
            variant_weight=args.variant_weight,
            outcome_categories=load_outcome_categories(args.outcome_categories),
        )
    )
    stats = evaluation.utilization_stats
    print(f"Cube: {Path(args.cube_file)} ({evaluation.card_count} cards)")
    print(
        "Combos completed: "
        + format_combo_count(
            evaluation.combo_count,
            evaluation.distinct_combo_count,
            evaluation.weighted_combo_count,
        )
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
    if evaluation.archetype_stats is not None:
        print("Archetypes (distinct combos): " + format_archetype_stats(evaluation.archetype_stats))
    if evaluation.outcome_stats is not None:
        print("Outcomes (distinct combos): " + format_outcome_stats(evaluation.outcome_stats))
    print("Popularity (distinct combos): " + format_popularity_stats(evaluation.popularity_stats))

    cube_cards = read_cube_file(args.cube_file)
    attributes = asyncio.run(fetch_card_attributes(cube_cards, read_cache=True))
    if attributes is not None:
        color_stats = compute_color_stats(cube_cards, attributes)
        print(f"Colors (by color identity): {format_color_stats(color_stats)}")
        card_mix = compute_card_mix_stats(cube_cards, attributes)
        print(f"Card mix: {format_card_mix_stats(card_mix)}")
