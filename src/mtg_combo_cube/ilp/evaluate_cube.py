"""Evaluate an existing cube list: true combo count and utilization statistics.

Usage:
    uv run python -m mtg_combo_cube.ilp.evaluate_cube data/cube.txt -n 20000

The instance is loaded through the same path as a build (cached API data, blocklist,
preprocessing), so the numbers are comparable with a build that used the same settings.
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from mtg_combo_cube.blocklist import BlocklistFetchError, BlocklistQueryError, fetch_blocklist
from mtg_combo_cube.config import ConfigError, load_config
from mtg_combo_cube.ilp.card_table import CardTableError
from mtg_combo_cube.ilp.cube_evaluation import (
    card_combo_groups,
    card_inclusion,
    card_utilization,
    combos_per_outcome,
    completable_combo_ids,
    completed_group_sizes,
    compute_archetype_stats,
    compute_card_mix_stats,
    compute_color_stats,
    compute_concentration_stats,
    compute_utilization_stats,
    group_outcomes,
    largest_combo_groups,
    popularity_stats,
    weighted_combo_count,
)
from mtg_combo_cube.ilp.enablers import EnablerDefinitions, EnablerTable
from mtg_combo_cube.ilp.ilp_models import (
    ArchetypeStats,
    CardInclusionStats,
    ComboGroupStats,
    EnablerStats,
    OutcomeStats,
    PayoffStats,
    PopularityStats,
    UtilizationStats,
)
from mtg_combo_cube.ilp.ilp_runner import (
    add_enabler_cards,
    add_payoff_cards,
    build_enabler_table,
    build_payoff_table,
    fetch_card_attributes,
    fetch_enabler_queries,
    fetch_payoff_queries,
    format_archetype_stats,
    format_card_inclusion,
    format_card_mix_stats,
    format_color_stats,
    format_combo_count,
    format_concentration,
    format_enabler_stats,
    format_enabler_table,
    format_outcome_stats,
    format_payoff_stats,
    format_payoff_table,
    format_popularity_stats,
    load_instance,
    read_cube_file,
    write_card_data,
)
from mtg_combo_cube.ilp.outcomes import OutcomeCategories, OutcomeCategoryError
from mtg_combo_cube.ilp.payoffs import (
    DEFAULT_INFERENCE_THRESHOLD,
    PayoffDefinitions,
    PayoffTable,
)

logger = logging.getLogger(__name__)


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
    card_inclusion: list[CardInclusionStats]  # why each card is in the cube, most-used first
    payoff_stats: PayoffStats | None = None  # payoff cards per category, with a payoff table
    payoffs: PayoffTable | None = None  # the resolved payoff table, with one
    enabler_stats: EnablerStats | None = None  # enabler cards per category, with a table
    enablers: EnablerTable | None = None  # the resolved enabler table, with one


async def evaluate_cube(
    cube_file: str,
    max_variants: int = 20000,
    blocklist: frozenset[str] = frozenset(),
    variant_weight: float = 0.1,
    outcome_categories: OutcomeCategories | None = None,
    payoff_definitions: PayoffDefinitions | None = None,
    payoff_inference_min: int = DEFAULT_INFERENCE_THRESHOLD,
    enabler_definitions: EnablerDefinitions | None = None,
    min_utilization_floor: int = 2,
) -> CubeEvaluation:
    """
    Evaluate a cube list against the instance built from the cached API data.

    variant_weight is the value of each further completed variant of a combo, as in a
    build's --variant-weight; it only affects weighted_combo_count. outcome_categories is
    the table the combos are categorized by; without one no outcome counts are reported.
    payoff_definitions is the payoff table, resolved as a build resolves it (its queries
    from the cache); it needs the outcome table, and without one no payoff counts are
    reported. enabler_definitions is the enabler table, resolved the same way; its
    enabler-only cards are the enabler cards in fewer combos of the pool than
    min_utilization_floor (a build's --min-util-floor), as a build reports them.
    """
    cards = read_cube_file(cube_file)
    query_results: dict[str, list[str]] = {}
    if payoff_definitions is not None and outcome_categories is not None:
        payoff_definitions.check_categories(outcome_categories)
        query_results = await fetch_payoff_queries(
            payoff_definitions.queries, enable_cache_write=False, read_cache=True
        )
    enabler_query_results: dict[str, list[str]] = {}
    if enabler_definitions is not None:
        enabler_query_results = await fetch_enabler_queries(
            enabler_definitions.queries, enable_cache_write=False, read_cache=True
        )
    combos, candidate_cards = await load_instance(
        max_variants=max_variants,
        enable_cache_write=False,
        read_cache=True,
        blocklist=blocklist,
    )
    payoffs: PayoffTable | None = None
    payoff_only: list[str] = []
    if payoff_definitions is not None and outcome_categories is not None:
        payoffs = build_payoff_table(
            payoff_definitions,
            combos,
            outcome_categories,
            query_results,
            blocklist,
            inference_threshold=payoff_inference_min,
        )
        add_payoff_cards(candidate_cards, payoffs)
    enablers: EnablerTable | None = None
    enabler_only: list[str] = []
    if enabler_definitions is not None:
        enablers = build_enabler_table(enabler_definitions, enabler_query_results, blocklist)
        add_enabler_cards(candidate_cards, enablers)
    # As the optimizer defines them with the floors on: a payoff card in fewer combos than
    # the utilization floor (or in none) is payoff-only, and an enabler card below it that
    # is no payoff card is enabler-only
    payoff_names = payoffs.all_cards if payoffs is not None else frozenset()
    enabler_names = enablers.all_cards if enablers is not None else frozenset()
    combos_of = dict.fromkeys(payoff_names | enabler_names, 0)
    for combo in combos:
        for card in combo.required_cards:
            if card in combos_of:
                combos_of[card] += 1
        for option in combo.requirement_options:
            for card in option.cards:
                if card in combos_of:
                    combos_of[card] += 1
    floor = max(min_utilization_floor, 1)
    payoff_only = sorted(card for card in payoff_names if combos_of[card] < floor)
    enabler_only = sorted(
        card for card in enabler_names if card not in payoff_names and combos_of[card] < floor
    )

    unknown = [card for card in cards if card not in candidate_cards]
    if unknown:
        logger.warning(
            f"{len(unknown)} cards in {cube_file} are not in the instance "
            f"(they count with utilization 0): {unknown[:5]}"
        )

    completed = completable_combo_ids(cards, combos)
    group_sizes = completed_group_sizes(completed, combos)
    inclusion = card_inclusion(
        cards,
        combos,
        completed,
        group_categories=(
            group_outcomes(combos, outcome_categories) if outcome_categories is not None else None
        ),
        payoff_cards=payoffs.cards_by_category() if payoffs is not None else None,
        enabler_cards=enablers.cards_by_category() if enablers is not None else None,
    )
    # The statistics describe the combo cards, as a build's do
    exempt = set(payoff_only) | set(enabler_only)
    utilization_stats = compute_utilization_stats(
        {
            card: value
            for card, value in card_utilization(cards, combos).items()
            if card not in exempt
        },
        inclusion,
        compute_concentration_stats(
            card_combo_groups(cards, combos, completed),
            {stats.card: stats.essential_combo_count for stats in inclusion},
        ),
    )
    return CubeEvaluation(
        card_count=len(cards),
        combo_count=len(completed),
        distinct_combo_count=len(group_sizes),
        weighted_combo_count=weighted_combo_count(group_sizes, variant_weight),
        utilization_stats=utilization_stats,
        largest_combo_groups=largest_combo_groups(cards, combos, completed_ids=completed),
        archetype_stats=compute_archetype_stats(cards, combos, completed),
        popularity_stats=popularity_stats(cards, combos, completed),
        outcome_stats=(
            combos_per_outcome(cards, combos, outcome_categories, completed)
            if outcome_categories is not None
            else None
        ),
        card_inclusion=inclusion,
        payoff_stats=payoffs.stats(cards, payoff_only) if payoffs is not None else None,
        payoffs=payoffs,
        enabler_stats=enablers.stats(cards, enabler_only) if enablers is not None else None,
        enablers=enablers,
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
        "--config",
        type=str,
        default=None,
        help="Path to the configuration file with the blocklist, the outcome category table, "
        "the payoff table and the enabler table, as used for the build (default: config.yaml)",
    )
    argparser.add_argument(
        "--variant-weight",
        type=float,
        default=0.1,
        help="Value of each further completed variant of a combo, as used for the build "
        "(default: 0.1); sets the weighted combo count",
    )
    argparser.add_argument(
        "--payoff-inference-min",
        type=int,
        default=DEFAULT_INFERENCE_THRESHOLD,
        help="Bundled variants a card must be the outlet of to count as an inferred payoff, as "
        f"used for the build (default: {DEFAULT_INFERENCE_THRESHOLD})",
    )
    argparser.add_argument(
        "--min-util-floor",
        type=int,
        default=2,
        help="Phase 2 utilization floor of the build (default: 2); an enabler card below it "
        "is reported as enabler-only, as the build reports it",
    )
    argparser.add_argument(
        "--card-data",
        action="store_true",
        help="Also write why each card is in the cube to <cube>_card_data.json beside the "
        "cube list",
    )
    args = argparser.parse_args()
    if args.payoff_inference_min < 1:
        argparser.error("--payoff-inference-min must be at least 1")
    if args.min_util_floor < 0:
        argparser.error("--min-util-floor must be 0 or more")
    # A missing or invalid configuration is a usage error; without an outcome, payoff or
    # enabler table the outcome, payoff and enabler counts are left out
    try:
        config = load_config(args.config)
    except (FileNotFoundError, ConfigError, OutcomeCategoryError, CardTableError) as e:
        argparser.error(str(e))
    logging.basicConfig(level=logging.WARNING)

    try:
        blocklist = asyncio.run(
            fetch_blocklist(
                config.blocklist,
                config.blocklist_queries,
                enable_cache_write=False,
                read_cache=True,
            )
        )
        evaluation = asyncio.run(
            evaluate_cube(
                cube_file=args.cube_file,
                max_variants=args.max_variants,
                blocklist=blocklist,
                variant_weight=args.variant_weight,
                outcome_categories=config.outcome_categories,
                payoff_definitions=config.payoffs,
                payoff_inference_min=args.payoff_inference_min,
                enabler_definitions=config.enablers,
                min_utilization_floor=args.min_util_floor,
            )
        )
    except (CardTableError, BlocklistQueryError) as e:
        argparser.error(str(e))  # a table query that matches no card, a rejected query
    except BlocklistFetchError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
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
    for label, measure in (
        ("Distinct combos per card", stats.distinct_combos),
        ("Essential combos per card", stats.essential_combos),
    ):
        if measure is not None:
            print(
                f"{label}: min={measure.min}, max={measure.max}, mean={measure.mean:.2f}, "
                f"median={measure.median:.1f}, std_dev={measure.std_deviation:.2f}"
            )
    if stats.concentration is not None:
        print(f"Concentration: {format_concentration(stats.concentration)}")
    if evaluation.archetype_stats is not None:
        print("Archetypes (distinct combos): " + format_archetype_stats(evaluation.archetype_stats))
    if evaluation.outcome_stats is not None:
        print("Outcomes (distinct combos): " + format_outcome_stats(evaluation.outcome_stats))
    print("Popularity (distinct combos): " + format_popularity_stats(evaluation.popularity_stats))
    print("Cards by reason: " + format_card_inclusion(evaluation.card_inclusion))
    if evaluation.payoff_stats is not None and evaluation.payoffs is not None:
        print("Payoff cards in the cube: " + format_payoff_stats(evaluation.payoff_stats))
        print("Payoff table: " + format_payoff_table(evaluation.payoffs))
        for name, found in evaluation.payoff_stats.cards.items():
            print(f"  {name}: {', '.join(found) if found else '(none)'}")
    if evaluation.enabler_stats is not None and evaluation.enablers is not None:
        print("Enabler cards in the cube: " + format_enabler_stats(evaluation.enabler_stats))
        print("Enabler table: " + format_enabler_table(evaluation.enablers))
        for name, found in evaluation.enabler_stats.cards.items():
            print(f"  {name}: {', '.join(found) if found else '(none)'}")
        if evaluation.enabler_stats.enabler_only:
            print(f"  enabler-only: {', '.join(evaluation.enabler_stats.enabler_only)}")

    cube_cards = read_cube_file(args.cube_file)
    attributes = asyncio.run(fetch_card_attributes(cube_cards, read_cache=True))
    if attributes is not None:
        color_stats = compute_color_stats(cube_cards, attributes)
        print(f"Colors (by color identity): {format_color_stats(color_stats)}")
        card_mix = compute_card_mix_stats(cube_cards, attributes)
        print(f"Card mix: {format_card_mix_stats(card_mix)}")
    if args.card_data:
        print(f"Per-card data: {write_card_data(evaluation.card_inclusion, args.cube_file)}")
