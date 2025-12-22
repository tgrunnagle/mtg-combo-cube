"""High-level async interface for ILP-based cube building."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from mtg_combo_cube.spellbook.api_cache import SpellbookCache
from mtg_combo_cube.spellbook.commander_spellbook import CommanderSpellbook
from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor
from mtg_combo_cube.models import Variant
from mtg_combo_cube.ilp.ilp_models import OptimizationResult
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer

logger = logging.getLogger(__name__)


async def collect_variants(
    spellbook: CommanderSpellbook,
    max_cards_in_combo: int = 4,
    max_variants: int = 10000,
    cache: SpellbookCache | None = None,
) -> list[Variant]:
    """Collect variants from Commander Spellbook API with optional caching."""
    variants: list[Variant] = []

    if cache is not None:
        # Use cached getter
        async for variant in cache.get_variants_cached(
            spellbook,
            max_cards_in_combo=max_cards_in_combo,
            max_variants=max_variants,
        ):
            variants.append(variant)
    else:
        # Direct API call (legacy behavior)
        async for variant in spellbook.get_variants(
            max_cards_in_combo=max_cards_in_combo,
            max_variants=max_variants,
        ):
            variants.append(variant)

    logger.info(f"Collected {len(variants)} variants")
    return variants


def write_utilization_stats(
    result: OptimizationResult,
    output_file: str,
    cube_size: int,
) -> None:
    """Write utilization statistics to JSON file."""
    # Derive stats filename: data/cube.txt -> data/cube_stats.json
    output_path = Path(output_file)
    stats_file = output_path.with_stem(f"{output_path.stem}_stats").with_suffix(".json")

    # Build JSON structure
    stats: dict = {
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "cube_size": cube_size,
            "combo_count": result.combo_count,
            "optimization_method": "two_phase" if result.is_multi_objective else "single_phase",
            "status": result.status,
            "total_solve_time_seconds": result.solve_time_seconds,
        },
        "phase1": None,
        "phase2": None,
        "improvement": None,
        "top_utilized_cards": [],
        "bottom_utilized_cards": [],
    }

    # Phase 1 stats
    if result.phase1_utilization_stats:
        p1 = result.phase1_utilization_stats
        stats["phase1"] = {
            "solve_time_seconds": result.phase1_solve_time,
            "min_utilization": p1.min_utilization,
            "max_utilization": p1.max_utilization,
            "mean_utilization": p1.mean_utilization,
            "median_utilization": p1.median_utilization,
            "std_deviation": p1.std_deviation,
            "total_absolute_deviation": p1.total_absolute_deviation,
        }

    # Phase 2 stats and improvement (only for multi-objective)
    if result.is_multi_objective and result.phase2_utilization_stats:
        p2 = result.phase2_utilization_stats
        stats["phase2"] = {
            "solve_time_seconds": result.phase2_solve_time,
            "status": result.phase2_status,
            "min_utilization": p2.min_utilization,
            "max_utilization": p2.max_utilization,
            "mean_utilization": p2.mean_utilization,
            "median_utilization": p2.median_utilization,
            "std_deviation": p2.std_deviation,
            "total_absolute_deviation": p2.total_absolute_deviation,
        }

        # Calculate improvement metrics
        if result.phase1_utilization_stats:
            p1 = result.phase1_utilization_stats
            std_improvement = (
                100 * (1 - p2.std_deviation / p1.std_deviation) if p1.std_deviation > 0 else 0.0
            )
            mad_improvement = (
                100 * (1 - p2.total_absolute_deviation / p1.total_absolute_deviation)
                if p1.total_absolute_deviation > 0
                else 0.0
            )
            stats["improvement"] = {
                "std_deviation_reduction_percent": std_improvement,
                "mad_reduction_percent": mad_improvement,
                "range_before": p1.max_utilization - p1.min_utilization,
                "range_after": p2.max_utilization - p2.min_utilization,
            }

    # Top and bottom utilized cards
    if result.utilization_per_card:
        sorted_cards = sorted(result.utilization_per_card.items(), key=lambda x: x[1], reverse=True)
        stats["top_utilized_cards"] = [
            {"card": card, "utilization": util} for card, util in sorted_cards[:10]
        ]
        stats["bottom_utilized_cards"] = [
            {"card": card, "utilization": util} for card, util in sorted_cards[-10:]
        ]

    # Create parent directory if it doesn't exist
    stats_file.parent.mkdir(parents=True, exist_ok=True)

    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    logger.info(f"Utilization statistics written to {stats_file}")


async def build_cube_ilp(
    cube_size: int,
    max_cards_in_combo: int = 4,
    max_variants: int = 10000,
    time_limit_seconds: int = 300,
    use_multi_objective: bool = True,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    combo_tolerance: float = 0.1,
) -> tuple[list[str], int, OptimizationResult]:
    """
    Build cube using ILP optimization with optional API caching.

    Returns:
        - List of card names in cube
        - Number of completable combos
        - Full optimization result with stats
    """
    logger.info(f"Building {cube_size}-card cube using ILP optimization...")

    # Initialize cache
    cache = SpellbookCache(
        enable_write=enable_cache_write,
        enable_read=read_cache,
    )

    # Step 1: Fetch variants with caching
    spellbook = CommanderSpellbook()
    variants = await collect_variants(
        spellbook,
        max_cards_in_combo=max_cards_in_combo,
        max_variants=max_variants,
        cache=cache,
    )

    # Step 2: Preprocess for ILP
    preprocessor = ComboPreprocessor()
    combo_data, all_cards = await preprocessor.preprocess_variants(variants)

    if len(all_cards) < cube_size:
        logger.warning(
            f"Only {len(all_cards)} unique cards available, "
            f"but cube size is {cube_size}. Adjusting cube size."
        )
        cube_size = len(all_cards)

    # Step 3: Run ILP optimization
    optimizer = ILPOptimizer(
        combos=combo_data,
        cube_size=cube_size,
        time_limit_seconds=time_limit_seconds,
        combo_tolerance=combo_tolerance,
    )

    # Run optimization (two-phase by default)
    if use_multi_objective:
        result = optimizer.solve_two_phase()
    else:
        result = optimizer.solve()

    # Log utilization improvements if multi-objective
    if result.is_multi_objective and result.phase2_utilization_stats:
        p1 = result.phase1_utilization_stats
        p2 = result.phase2_utilization_stats
        if p1 and p1.std_deviation > 0:
            logger.info(
                f"Utilization: std_dev {p1.std_deviation:.1f} → {p2.std_deviation:.1f} "
                f"({100 * (1 - p2.std_deviation / p1.std_deviation):.1f}% improvement)"
            )

    logger.info(
        f"ILP complete: {result.combo_count} combos, "
        f"status={result.status}, time={result.solve_time_seconds:.1f}s"
    )

    return result.selected_cards, result.combo_count, result


async def run_ilp(
    cube_size: int,
    output_file: str,
    time_limit_seconds: int = 300,
    max_variants: int = 10000,
    use_multi_objective: bool = True,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    combo_tolerance: float = 0.1,
):
    """Entry point for ILP-based cube building with caching support."""
    cards, combo_count, result = await build_cube_ilp(
        cube_size=cube_size,
        time_limit_seconds=time_limit_seconds,
        max_variants=max_variants,
        use_multi_objective=use_multi_objective,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        combo_tolerance=combo_tolerance,
    )

    logger.info(f"ILP result: {len(cards)} cards, {combo_count} combos ({result.status})")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(cards))

    # Write utilization stats
    write_utilization_stats(result, output_file, cube_size)
