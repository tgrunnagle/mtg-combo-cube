"""High-level async interface for ILP-based cube building."""

import logging

from mtg_combo_cube.combo_preprocessor import ComboPreprocessor
from mtg_combo_cube.commander_spellbook import CommanderSpellbook
from mtg_combo_cube.ilp_models import OptimizationResult
from mtg_combo_cube.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.models import Variant

logger = logging.getLogger(__name__)


async def collect_variants(
    spellbook: CommanderSpellbook,
    max_cards_in_combo: int = 4,
    max_variants: int = 10000,
) -> list[Variant]:
    """Collect variants from Commander Spellbook API."""
    variants: list[Variant] = []
    async for variant in spellbook.get_variants(
        max_cards_in_combo=max_cards_in_combo,
        max_variants=max_variants,
    ):
        variants.append(variant)
    logger.info(f"Collected {len(variants)} variants from API")
    return variants


async def build_cube_ilp(
    cube_size: int,
    max_cards_in_combo: int = 4,
    max_variants: int = 10000,
    time_limit_seconds: int = 300,
) -> tuple[list[str], int, OptimizationResult]:
    """
    Build cube using ILP optimization.

    Returns:
        - List of card names in cube
        - Number of completable combos
        - Full optimization result with stats
    """
    logger.info(f"Building {cube_size}-card cube using ILP optimization...")

    # Step 1: Fetch variants
    spellbook = CommanderSpellbook()
    variants = await collect_variants(
        spellbook,
        max_cards_in_combo=max_cards_in_combo,
        max_variants=max_variants,
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
    )
    result = optimizer.solve()

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
):
    """Entry point for ILP-based cube building (matches run.run signature)."""
    cards, combo_count, result = await build_cube_ilp(
        cube_size=cube_size,
        time_limit_seconds=time_limit_seconds,
        max_variants=max_variants,
    )

    logger.info(
        f"ILP result: {len(cards)} cards, {combo_count} combos "
        f"({result.status})"
    )

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(cards))
