"""High-level async interface for ILP-based cube building."""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor
from mtg_combo_cube.ilp.cube_evaluation import (
    COLOR_PAIRS,
    COLORLESS,
    MONO_COLORS,
    compute_color_stats,
)
from mtg_combo_cube.ilp.ilp_models import (
    ArchetypeStats,
    CandidateCard,
    ColorStats,
    ComboData,
    OptimizationResult,
)
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.models import Variant
from mtg_combo_cube.scryfall.card_color_fetcher import CardColorFetcher
from mtg_combo_cube.scryfall.scryfall_fetcher import ScryfallFetcher
from mtg_combo_cube.spellbook.api_cache import SpellbookCache
from mtg_combo_cube.spellbook.commander_spellbook import CommanderSpellbook

logger = logging.getLogger(__name__)


async def collect_variants(
    spellbook: CommanderSpellbook,
    max_cards_in_combo: int = 4,
    max_variants: int = 20000,
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


def _optimization_method(result: OptimizationResult) -> str:
    """Name the optimization that produced the result, for the stats file."""
    if result.is_multi_objective:
        return "two_phase"
    if result.phase2_fell_back:
        return "two_phase_fallback_to_phase1"
    return "single_phase"


def _phase2_objective_info(result: OptimizationResult) -> dict:
    """The Phase 2 objective and its utilization cap, when the result records them."""
    info: dict = {}
    if result.phase2_objective is not None:
        info["objective"] = result.phase2_objective
    if result.phase2_util_cap is not None:
        info["util_cap"] = result.phase2_util_cap
    if result.phase2_max_color_ratio is not None:
        info["max_color_ratio"] = result.phase2_max_color_ratio
    if result.phase2_min_pair_combos is not None:
        info["min_pair_combos"] = result.phase2_min_pair_combos
    if result.phase2_min_mono_combos is not None:
        info["min_mono_combos"] = result.phase2_min_mono_combos
    if result.phase2_max_wide_combo_share is not None:
        info["max_wide_combo_share"] = result.phase2_max_wide_combo_share
    if result.phase2_reference_combo_count is not None:
        info["reference_combo_count"] = result.phase2_reference_combo_count
    if result.phase2_reference_distinct_combo_count is not None:
        info["reference_distinct_combo_count"] = result.phase2_reference_distinct_combo_count
    if result.phase2_reference_weighted_combo_count is not None:
        info["reference_weighted_combo_count"] = result.phase2_reference_weighted_combo_count
    if result.phase2_combo_tolerance is not None:
        info["combo_tolerance"] = result.phase2_combo_tolerance
    return info


def _phase1_cards(result: OptimizationResult) -> list[CandidateCard] | None:
    """The Phase 1 cube, when the result records it."""
    if result.phase1_selected_cards is not None:
        return result.phase1_selected_cards
    return None if result.is_multi_objective else result.selected_cards


def _phase1_combo_count(result: OptimizationResult) -> int | None:
    """The number of combos the Phase 1 cube completes, when the result records it."""
    if result.phase1_combo_count is not None:
        return result.phase1_combo_count
    return None if result.is_multi_objective else result.combo_count


def _phase1_distinct_combo_count(result: OptimizationResult) -> int | None:
    """The number of distinct combos (groups) the Phase 1 cube completes, when recorded."""
    if result.phase1_distinct_combo_count is not None:
        return result.phase1_distinct_combo_count
    return None if result.is_multi_objective else result.distinct_combo_count


def _phase1_weighted_combo_count(result: OptimizationResult) -> float | None:
    """The weighted combo count of the Phase 1 cube, when recorded."""
    if result.phase1_weighted_combo_count is not None:
        return result.phase1_weighted_combo_count
    return None if result.is_multi_objective else result.weighted_combo_count


def _grouping_counts(distinct: int | None, weighted: float | None) -> dict:
    """The grouping entries of a phase block; left out when the result has no grouping data."""
    counts: dict = {}
    if distinct is not None:
        counts["distinct_combo_count"] = distinct
    if weighted is not None:
        counts["weighted_combo_count"] = weighted
    return counts


def format_combo_count(variants: int, groups: int | None, weighted: float | None = None) -> str:
    """
    'X variants in G combos (weighted W)': the weighted count is shown when it differs from
    the variant count, i.e. when the variant weight is below 1. Without group data, just
    the variant count.
    """
    if groups is None:
        return f"{variants} variants"
    text = f"{variants} variants in {groups} combos"
    if weighted is not None and weighted != variants:
        text += f" (weighted {weighted:.1f})"
    return text


def _color_stats(
    cards: list[CandidateCard] | None, color_identities: Mapping[str, str] | None
) -> ColorStats | None:
    if cards is None or color_identities is None:
        return None
    return compute_color_stats([card.name for card in cards], color_identities)


def _colors_block(
    cards: list[CandidateCard] | None, color_identities: Mapping[str, str] | None
) -> dict:
    """The "colors" entry of a phase block, empty when there is no color data."""
    stats = _color_stats(cards, color_identities)
    return {"colors": asdict(stats)} if stats is not None else {}


def format_color_stats(stats: ColorStats) -> str:
    """One-line summary of a color distribution."""
    per_color = ", ".join(f"{color}={count}" for color, count in stats.cards_per_color.items())
    unknown = f", unknown={stats.unknown}" if stats.unknown else ""
    return (
        f"{per_color}, colorless={stats.colorless}, multicolor={stats.multicolor}{unknown}; "
        f"across colors: variance={stats.variance:.1f}, std_dev={stats.std_deviation:.1f}"
    )


def _archetypes_block(stats: ArchetypeStats | None) -> dict:
    """The "archetypes" entry of a phase block, empty when the result has no archetype data."""
    if stats is None:
        return {}
    return {
        "archetypes": {
            "combos_per_archetype": stats.combos_per_archetype,
            "combos_by_color_count": stats.combos_by_color_count,
        }
    }


def format_archetype_stats(stats: ArchetypeStats) -> str:
    """One-line summary of the distinct combos per draft archetype."""
    counts = stats.combos_per_archetype
    pairs = ", ".join(f"{pair}={counts[pair]}" for pair in COLOR_PAIRS)
    mono = ", ".join(f"{color}={counts[color]}" for color in MONO_COLORS)
    total = sum(stats.combos_by_color_count.values())
    share = f" ({stats.wide_combo_count / total:.0%})" if total else ""
    return (
        f"pairs {pairs}; mono {mono}; colorless {counts[COLORLESS]}; "
        f"3+ colors {stats.wide_combo_count} of {total}{share}"
    )


def log_phase_summary(
    result: OptimizationResult, color_identities: Mapping[str, str] | None
) -> None:
    """Log the combo count, color distribution and archetype counts of each phase's cube."""
    phase1_count = _phase1_combo_count(result)
    phase1_text = format_combo_count(
        phase1_count or 0,
        _phase1_distinct_combo_count(result),
        _phase1_weighted_combo_count(result),
    )
    if result.is_multi_objective and phase1_count:
        change = 100 * (result.combo_count - phase1_count) / phase1_count
        reference = result.phase2_reference_combo_count
        constrained = ""
        if reference:
            reference_text = format_combo_count(
                reference,
                result.phase2_reference_distinct_combo_count,
                result.phase2_reference_weighted_combo_count,
            )
            constrained = f", best under the cube rules {reference_text}"
        phase2_text = format_combo_count(
            result.combo_count, result.distinct_combo_count, result.weighted_combo_count
        )
        logger.info(
            f"Combos: Phase 1 {phase1_text}{constrained}, "
            f"Phase 2 {phase2_text} ({change:+.1f}% variants from Phase 1)"
        )
    elif phase1_count is not None:
        logger.info(f"Combos: Phase 1 {phase1_text}")

    phase1_colors = _color_stats(_phase1_cards(result), color_identities)
    if phase1_colors is not None:
        logger.info(f"Colors, Phase 1: {format_color_stats(phase1_colors)}")
    if result.is_multi_objective:
        phase2_colors = _color_stats(result.selected_cards, color_identities)
        if phase2_colors is not None:
            logger.info(f"Colors, Phase 2: {format_color_stats(phase2_colors)}")

    if result.phase1_archetype_stats is not None:
        logger.info(f"Archetypes, Phase 1: {format_archetype_stats(result.phase1_archetype_stats)}")
    if result.is_multi_objective and result.phase2_archetype_stats is not None:
        logger.info(f"Archetypes, Phase 2: {format_archetype_stats(result.phase2_archetype_stats)}")


def write_stats(
    result: OptimizationResult,
    output_file: str,
    cube_size: int,
    color_identities: Mapping[str, str] | None = None,
) -> None:
    """
    Write utilization statistics to JSON file.

    color_identities (card name -> WUBRG letters) adds the color distribution of each
    phase's cube when given.
    """
    # Derive stats filename: data/cube.txt -> data/cube_stats.json
    output_path = Path(output_file)
    stats_file = output_path.with_stem(f"{output_path.stem}_stats").with_suffix(".json")

    # Build JSON structure
    stats: dict = {
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "cube_size": cube_size,
            "combo_count": result.combo_count,
            "optimization_method": _optimization_method(result),
            "phase1_status": result.phase1_status,
            "total_solve_time_seconds": result.solve_time_seconds,
        },
        "phase1": None,
        "phase2": None,
        "improvement": None,
        "top_utilized_cards": [],
        "bottom_utilized_cards": [],
    }
    stats["metadata"].update(
        _grouping_counts(result.distinct_combo_count, result.weighted_combo_count)
    )
    if result.variant_weight is not None:
        stats["metadata"]["variant_weight"] = result.variant_weight

    # Phase 1 stats
    if result.phase1_utilization_stats:
        p1 = result.phase1_utilization_stats
        stats["phase1"] = {
            "combo_count": _phase1_combo_count(result),
            **_grouping_counts(
                _phase1_distinct_combo_count(result), _phase1_weighted_combo_count(result)
            ),
            "solve_time_seconds": result.phase1_solve_time,
            "min_utilization": p1.min_utilization,
            "max_utilization": p1.max_utilization,
            "mean_utilization": p1.mean_utilization,
            "median_utilization": p1.median_utilization,
            "std_deviation": p1.std_deviation,
            "total_absolute_deviation": p1.total_absolute_deviation,
            **_colors_block(_phase1_cards(result), color_identities),
            **_archetypes_block(result.phase1_archetype_stats),
        }

    # Phase 2 stats and improvement (only for multi-objective)
    if result.is_multi_objective and result.phase2_utilization_stats:
        p2 = result.phase2_utilization_stats
        stats["phase2"] = {
            "combo_count": result.combo_count,
            **_grouping_counts(result.distinct_combo_count, result.weighted_combo_count),
            "solve_time_seconds": result.phase2_solve_time,
            "status": result.phase2_status,
            "min_utilization": p2.min_utilization,
            "max_utilization": p2.max_utilization,
            "mean_utilization": p2.mean_utilization,
            "median_utilization": p2.median_utilization,
            "std_deviation": p2.std_deviation,
            "total_absolute_deviation": p2.total_absolute_deviation,
            **_phase2_objective_info(result),
            **_colors_block(result.selected_cards, color_identities),
            **_archetypes_block(result.phase2_archetype_stats),
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
            phase1_count = _phase1_combo_count(result)
            if phase1_count is not None:
                stats["improvement"]["combo_count_before"] = phase1_count
                stats["improvement"]["combo_count_after"] = result.combo_count
                stats["improvement"]["combo_count_change_percent"] = (
                    100 * (result.combo_count - phase1_count) / phase1_count
                    if phase1_count > 0
                    else 0.0
                )
            phase1_distinct = _phase1_distinct_combo_count(result)
            if phase1_distinct is not None and result.distinct_combo_count is not None:
                stats["improvement"]["distinct_combo_count_before"] = phase1_distinct
                stats["improvement"]["distinct_combo_count_after"] = result.distinct_combo_count

            # Add card changes between Phase 1 and Phase 2
            if result.phase1_selected_cards is not None:
                phase1_names = {c.name for c in result.phase1_selected_cards}
                phase2_names = {c.name for c in result.selected_cards}

                cards_added = sorted(phase2_names - phase1_names)
                cards_removed = sorted(phase1_names - phase2_names)

                stats["improvement"]["card_changes"] = {
                    "cards_added": cards_added,
                    "cards_removed": cards_removed,
                    "total_changed": len(cards_added) + len(cards_removed),
                }

    # Phase 2 ran but found no solution: the cube is the Phase 1 result
    if result.phase2_fell_back:
        stats["phase2"] = {
            "solve_time_seconds": result.phase2_solve_time,
            "status": result.phase2_status,
            "fell_back_to_phase1": True,
            **_phase2_objective_info(result),
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

    # The combo groups with the most completed variants in the final cube
    if result.largest_combo_groups is not None:
        stats["largest_combo_groups"] = [
            {"group_key": g.group_key, "variant_count": g.variant_count, "cards": g.cards}
            for g in result.largest_combo_groups
        ]

    # Requirement type stats
    if result.requirement_type_stats:
        stats["requirement_types"] = {
            "summary": {
                "mean_coverage_ratio": result.requirement_coverage_stats.mean_coverage_ratio,
                "std_dev_coverage_ratio": result.requirement_coverage_stats.std_dev_coverage_ratio,
            }
            if result.requirement_coverage_stats
            else None,
            "by_type": [
                {
                    "template_name": r.template_name,
                    "combo_count": r.combo_count,
                    "card_count": r.card_count,
                    "coverage_ratio": r.coverage_ratio,
                    "cards": r.cards,
                    **({"aliases": r.aliases} if r.aliases else {}),
                }
                for r in sorted(result.requirement_type_stats, key=lambda x: -x.combo_count)
            ],
        }

    # Cross-template overlap stats
    if result.cross_template_stats:
        cts = result.cross_template_stats
        stats["cross_template_overlap"] = {
            "summary": {
                "multi_template_card_count": cts.multi_template_card_count,
                "max_templates_per_card": cts.max_templates_per_card,
                "mean_templates_per_card": round(cts.mean_templates_per_card, 2),
                "cards_by_template_count": cts.cards_by_template_count,
            },
            "top_versatile_cards": [
                {
                    "card": c.name,
                    "template_count": c.template_count,
                    "requirement_keys": sorted(c.requirement_group_keys),
                }
                for c in cts.top_versatile_cards
            ],
            "template_pair_overlaps": [
                {
                    "template1": p.template1_name,
                    "template2": p.template2_name,
                    "shared_cards": p.shared_cards,
                    "overlap_count": p.overlap_count,
                    "jaccard_similarity": round(p.jaccard_similarity, 3),
                }
                for p in cts.top_overlapping_pairs
            ],
        }

    # Profiling data (when --profile was used)
    if result.profile_data:
        stats["profiling"] = result.profile_data

    # Create parent directory if it doesn't exist
    stats_file.parent.mkdir(parents=True, exist_ok=True)

    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    logger.info(f"Utilization statistics written to {stats_file}")


async def load_instance(
    max_cards_in_combo: int = 4,
    max_variants: int = 20000,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    blocklist: frozenset[str] = frozenset(),
) -> tuple[list[ComboData], dict[str, CandidateCard]]:
    """Fetch the variants (with optional API caching) and preprocess them for the ILP."""
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
    fetcher = ScryfallFetcher(enable_read=read_cache, enable_write=enable_cache_write)
    preprocessor = ComboPreprocessor(blocklist=blocklist, fetcher=fetcher)
    return await preprocessor.preprocess_variants(variants)


async def fetch_color_identities(
    card_names: Iterable[str],
    enable_cache_write: bool = True,
    read_cache: bool = False,
) -> dict[str, str] | None:
    """
    Look up the color identity (WUBRG letters) of each card on Scryfall.

    Returns None when no color data could be fetched, so callers can leave colors out.
    """
    async with ScryfallFetcher() as fetcher:
        color_fetcher = CardColorFetcher(
            fetcher, enable_read=read_cache, enable_write=enable_cache_write
        )
        identities = await color_fetcher.fetch_color_identities(card_names)
    return identities or None


async def build_cube_ilp(
    cube_size: int,
    max_cards_in_combo: int = 4,
    max_variants: int = 20000,
    time_limit_seconds: int = 300,
    use_multi_objective: bool = True,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    combo_tolerance: float = 0.1,
    min_coverage_ratio: float = 0.1,
    min_combo_threshold: int = 10,
    blocklist: frozenset[str] = frozenset(),
    profile: bool = False,
    gap_limit: float = 0.05,
    phase2_objective: str = "tiered",
    min_utilization_floor: int = 2,
    num_workers: int = 8,
    util_cap: int | None = None,
    max_color_ratio: float = 2.0,
    variant_weight: float = 0.1,
    min_pair_combos: int = 250,
    min_mono_combos: int = 150,
    max_wide_combo_share: float = 0.25,
) -> tuple[list[str], int, OptimizationResult, dict[str, str] | None]:
    """
    Build cube using ILP optimization with optional API caching.

    Returns:
        - List of card names in cube
        - Number of completable combos
        - Full optimization result with stats
        - Color identity of every candidate card (None when no color data could be fetched)
    """
    logger.info(f"Building {cube_size}-card cube using ILP optimization...")

    combo_data, candidate_cards = await load_instance(
        max_cards_in_combo=max_cards_in_combo,
        max_variants=max_variants,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        blocklist=blocklist,
    )

    if len(candidate_cards) < cube_size:
        logger.warning(
            f"Only {len(candidate_cards)} unique cards available, "
            f"but cube size is {cube_size}. Adjusting cube size."
        )
        cube_size = len(candidate_cards)

    # Colors feed the Phase 2 color balance constraint and the color statistics
    card_colors = await fetch_color_identities(
        sorted(candidate_cards), enable_cache_write=enable_cache_write, read_cache=read_cache
    )

    # Run ILP optimization
    optimizer = ILPOptimizer(
        combos=combo_data,
        candidate_cards=candidate_cards,
        cube_size=cube_size,
        time_limit_seconds=time_limit_seconds,
        combo_tolerance=combo_tolerance,
        min_coverage_ratio=min_coverage_ratio,
        min_combo_threshold=min_combo_threshold,
        gap_limit=gap_limit,
        phase2_objective=phase2_objective,
        min_utilization_floor=min_utilization_floor,
        num_workers=num_workers,
        util_cap=util_cap,
        card_colors=card_colors,
        max_color_ratio=max_color_ratio,
        variant_weight=variant_weight,
        min_pair_combos=min_pair_combos,
        min_mono_combos=min_mono_combos,
        max_wide_combo_share=max_wide_combo_share,
    )

    # Run optimization (two-phase by default)
    if use_multi_objective:
        result = optimizer.solve_two_phase(profile=profile)
    else:
        result = optimizer.solve(profile=profile)

    # Log utilization improvements if multi-objective
    if result.is_multi_objective and result.phase2_utilization_stats:
        p1 = result.phase1_utilization_stats
        p2 = result.phase2_utilization_stats
        if p1 and p1.std_deviation > 0:
            logger.info(
                f"Utilization: std_dev {p1.std_deviation:.1f} → {p2.std_deviation:.1f} "
                f"({100 * (1 - p2.std_deviation / p1.std_deviation):.1f}% improvement)"
            )

    if result.phase2_fell_back:
        logger.warning(
            f"Phase 2 found no solution ({result.phase2_status}); the cube is the Phase 1 result"
        )

    logger.info(
        f"ILP complete: {format_combo_count(result.combo_count, result.distinct_combo_count)}, "
        f"status={result.phase1_status}, time={result.solve_time_seconds:.1f}s"
    )

    return result.get_selected_card_names(), result.combo_count, result, card_colors


async def run_ilp(
    cube_size: int,
    output_file: str,
    time_limit_seconds: int = 300,
    max_variants: int = 20000,
    use_multi_objective: bool = True,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    combo_tolerance: float = 0.1,
    min_coverage_ratio: float = 0.1,
    min_combo_threshold: int = 10,
    blocklist: frozenset[str] = frozenset(),
    profile: bool = False,
    gap_limit: float = 0.05,
    phase2_objective: str = "tiered",
    min_utilization_floor: int = 2,
    num_workers: int = 8,
    util_cap: int | None = None,
    max_color_ratio: float = 2.0,
    variant_weight: float = 0.1,
    min_pair_combos: int = 250,
    min_mono_combos: int = 150,
    max_wide_combo_share: float = 0.25,
):
    """Entry point for ILP-based cube building with caching support."""
    cards, combo_count, result, color_identities = await build_cube_ilp(
        cube_size=cube_size,
        time_limit_seconds=time_limit_seconds,
        max_variants=max_variants,
        use_multi_objective=use_multi_objective,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        combo_tolerance=combo_tolerance,
        min_coverage_ratio=min_coverage_ratio,
        min_combo_threshold=min_combo_threshold,
        blocklist=blocklist,
        profile=profile,
        gap_limit=gap_limit,
        phase2_objective=phase2_objective,
        min_utilization_floor=min_utilization_floor,
        num_workers=num_workers,
        util_cap=util_cap,
        max_color_ratio=max_color_ratio,
        variant_weight=variant_weight,
        min_pair_combos=min_pair_combos,
        min_mono_combos=min_mono_combos,
        max_wide_combo_share=max_wide_combo_share,
    )

    logger.info(
        f"ILP result: {len(cards)} cards, "
        f"{format_combo_count(combo_count, result.distinct_combo_count)} "
        f"({result.phase1_status})"
    )

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(cards))

    log_phase_summary(result, color_identities)

    # Write utilization stats
    write_stats(result, output_file, cube_size, color_identities)
