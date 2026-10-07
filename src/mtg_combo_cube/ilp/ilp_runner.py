"""High-level async interface for ILP-based cube building."""

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor
from mtg_combo_cube.ilp.cube_evaluation import (
    COLOR_PAIRS,
    COLORLESS,
    MANA_VALUE_CAP,
    MONO_COLORS,
    compute_card_mix_stats,
    compute_color_stats,
)
from mtg_combo_cube.ilp.ilp_models import (
    DEFAULT_CARD_MIX,
    ArchetypeStats,
    CandidateCard,
    CardMixRules,
    CardMixStats,
    ColorStats,
    ComboData,
    OptimizationResult,
    OutcomeStats,
    PayoffStats,
    PopularityStats,
)
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.outcomes import (
    DEFAULT_OUTCOME_CATEGORIES_PATH,
    OutcomeCategories,
    outcome_rules_requested,
    resolve_outcome_categories,
)
from mtg_combo_cube.ilp.payoffs import (
    DEFAULT_INFERENCE_THRESHOLD,
    DEFAULT_PAYOFFS_PATH,
    PayoffDefinitions,
    PayoffTable,
    check_query_results,
    infer_payoffs,
    resolve_payoffs,
)
from mtg_combo_cube.ilp.payoffs import resolve_payoff_definitions as _resolve_payoff_definitions
from mtg_combo_cube.models import CardAttributes, Variant
from mtg_combo_cube.scryfall.card_attribute_fetcher import CardAttributeFetcher
from mtg_combo_cube.scryfall.payoff_fetcher import PayoffFetcher
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
    if result.phase2_card_mix is not None and (card_mix_rules := result.phase2_card_mix.enabled()):
        info["card_mix_rules"] = card_mix_rules
    if result.phase2_card_mix_limits:
        info["card_mix_limits"] = result.phase2_card_mix_limits
    if result.phase2_unknown_candidate_cards is not None:
        info["unknown_candidate_cards"] = result.phase2_unknown_candidate_cards
    if result.phase2_outcome_minimums is not None:
        info["outcome_minimums"] = result.phase2_outcome_minimums
    if result.phase2_max_outcome_share is not None:
        info["max_outcome_share"] = result.phase2_max_outcome_share
    if result.phase2_min_payoffs is not None:
        info["min_payoffs"] = result.phase2_min_payoffs
    if result.phase2_payoff_floors is not None:
        info["payoff_floors"] = result.phase2_payoff_floors
    if result.phase2_reference_combo_count is not None:
        info["reference_combo_count"] = result.phase2_reference_combo_count
    if result.phase2_reference_distinct_combo_count is not None:
        info["reference_distinct_combo_count"] = result.phase2_reference_distinct_combo_count
    if result.phase2_reference_weighted_combo_count is not None:
        info["reference_weighted_combo_count"] = result.phase2_reference_weighted_combo_count
    info.update(_score_entry(result, result.phase2_reference_combo_score, "reference_combo_score"))
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


def _score_entry(result: OptimizationResult, score: float | None, key: str = "combo_score") -> dict:
    """
    The combo score entry of a block: only with a popularity weight above 0, when the score
    differs from the weighted combo count.
    """
    if score is None or not result.popularity_weight:
        return {}
    return {key: score}


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
    cards: list[CandidateCard] | None, attributes: Mapping[str, CardAttributes] | None
) -> ColorStats | None:
    if cards is None or attributes is None:
        return None
    return compute_color_stats([card.name for card in cards], attributes)


def _colors_block(
    cards: list[CandidateCard] | None, attributes: Mapping[str, CardAttributes] | None
) -> dict:
    """The "colors" entry of a phase block, empty when there is no card data."""
    stats = _color_stats(cards, attributes)
    return {"colors": asdict(stats)} if stats is not None else {}


def _card_mix_stats(
    cards: list[CandidateCard] | None, attributes: Mapping[str, CardAttributes] | None
) -> CardMixStats | None:
    if cards is None or attributes is None:
        return None
    return compute_card_mix_stats([card.name for card in cards], attributes)


def _card_mix_block(
    cards: list[CandidateCard] | None, attributes: Mapping[str, CardAttributes] | None
) -> dict:
    """The "card_mix" entry of a phase block, empty when there is no card data."""
    stats = _card_mix_stats(cards, attributes)
    return {"card_mix": asdict(stats)} if stats is not None else {}


def format_card_mix_stats(stats: CardMixStats) -> str:
    """One-line summary of a card mix: types, color counts and mana values."""

    def share(count: int) -> str:
        return f" ({count / stats.card_count:.0%})" if stats.card_count else ""

    types = ", ".join(
        f"{card_type}={count}{share(count) if card_type == 'Creature' else ''}"
        for card_type, count in stats.type_counts.items()
    )
    curve = " ".join(
        f"{value}{'+' if value == MANA_VALUE_CAP else ''}:{count}"
        for value, count in stats.mana_value_counts.items()
    )
    per_color = ", ".join(
        f"{color}={mean:.1f}" for color, mean in stats.mean_mana_value_per_color.items()
    )
    unknown = f"; unknown={stats.unknown}" if stats.unknown else ""
    return (
        f"types {types}; multicolor {stats.multicolor}{share(stats.multicolor)}, "
        f"colorless {stats.colorless}{share(stats.colorless)}; mana value (nonland) "
        f"mean {stats.mean_mana_value:.2f}, {curve}, per color {per_color}{unknown}"
    )


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


def _outcomes_block(stats: OutcomeStats | None) -> dict:
    """The "outcomes" entry of a phase block, empty when the result has no outcome data."""
    if stats is None:
        return {}
    return {
        "outcomes": {
            "combos_per_outcome": stats.combos_per_outcome,
            "uncategorized": stats.uncategorized,
            "total": stats.total,
        }
    }


def format_outcome_stats(stats: OutcomeStats) -> str:
    """One-line summary of the distinct combos per outcome category."""
    counts = ", ".join(f"{name}={count}" for name, count in stats.combos_per_outcome.items())
    share = f" ({stats.uncategorized / stats.total:.0%})" if stats.total else ""
    return f"{counts}; no category {stats.uncategorized} of {stats.total}{share}"


def _popularity_block(stats: PopularityStats | None) -> dict:
    """The "popularity" entry of a phase block, empty when the result has none."""
    return {"popularity": asdict(stats)} if stats is not None else {}


def _payoffs_block(stats: PayoffStats | None) -> dict:
    """The "payoffs" entry of a phase block, empty when the result has no payoff data."""
    return {"payoffs": asdict(stats)} if stats is not None else {}


def _payoff_table_block(payoffs: PayoffTable | None) -> dict:
    """
    The top-level "payoffs" entry of the stats file: the resolved payoff table (every card
    per category with its sources, and how many each source named) and everything the
    inference found with its counts, so the table can be tuned from a run.
    """
    if payoffs is None:
        return {}
    return {
        "payoffs": {
            "inference_threshold": payoffs.inference_threshold,
            "cards_per_category": {name: len(payoffs.cards(name)) for name in payoffs.names},
            "source_counts": {name: payoffs.source_counts(name) for name in payoffs.names},
            "cards": {
                name: {card: sorted(sources) for card, sources in by_card.items()}
                for name, by_card in payoffs.sources.items()
            },
            "inferred": payoffs.inferred,
        }
    }


def format_payoff_stats(stats: PayoffStats) -> str:
    """One-line summary of the payoff cards per category."""
    return ", ".join(f"{name}={count}" for name, count in stats.cards_per_category.items())


def format_payoff_table(payoffs: PayoffTable) -> str:
    """'mana 31 (inferred 5, cards 0, queries 26); storm 15 (...)' for the resolved table."""
    parts = []
    for name in payoffs.names:
        counts = payoffs.source_counts(name)
        parts.append(
            f"{name} {len(payoffs.cards(name))} (inferred {counts['inferred']}, cards "
            f"{counts['card']}, queries {counts['query']})"
        )
    return "; ".join(parts)


def format_popularity_stats(stats: PopularityStats) -> str:
    """One-line summary of how popular a cube's distinct combos are."""
    return (
        f"median {stats.median_popularity:g} (pool median {stats.pool_median_popularity:g}), "
        f"mean log {stats.mean_log_popularity:.2f}, "
        f"{stats.below_pool_median_share:.0%} of {stats.combo_count} combos below the pool median"
    )


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
    result: OptimizationResult, attributes: Mapping[str, CardAttributes] | None
) -> None:
    """
    Log the combo count, color distribution, archetype counts and card mix of each phase's
    cube.
    """
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

    phase1_colors = _color_stats(_phase1_cards(result), attributes)
    if phase1_colors is not None:
        logger.info(f"Colors, Phase 1: {format_color_stats(phase1_colors)}")
    if result.is_multi_objective:
        phase2_colors = _color_stats(result.selected_cards, attributes)
        if phase2_colors is not None:
            logger.info(f"Colors, Phase 2: {format_color_stats(phase2_colors)}")

    if result.phase1_archetype_stats is not None:
        logger.info(f"Archetypes, Phase 1: {format_archetype_stats(result.phase1_archetype_stats)}")
    if result.is_multi_objective and result.phase2_archetype_stats is not None:
        logger.info(f"Archetypes, Phase 2: {format_archetype_stats(result.phase2_archetype_stats)}")

    if result.phase1_outcome_stats is not None:
        logger.info(f"Outcomes, Phase 1: {format_outcome_stats(result.phase1_outcome_stats)}")
    if result.is_multi_objective and result.phase2_outcome_stats is not None:
        logger.info(f"Outcomes, Phase 2: {format_outcome_stats(result.phase2_outcome_stats)}")

    if result.phase1_popularity_stats is not None:
        logger.info(
            f"Popularity, Phase 1: {format_popularity_stats(result.phase1_popularity_stats)}"
        )
    if result.is_multi_objective and result.phase2_popularity_stats is not None:
        logger.info(
            f"Popularity, Phase 2: {format_popularity_stats(result.phase2_popularity_stats)}"
        )

    if result.phase1_payoff_stats is not None:
        logger.info(f"Payoffs, Phase 1: {format_payoff_stats(result.phase1_payoff_stats)}")
    if result.is_multi_objective and result.phase2_payoff_stats is not None:
        logger.info(f"Payoffs, Phase 2: {format_payoff_stats(result.phase2_payoff_stats)}")

    phase1_mix = _card_mix_stats(_phase1_cards(result), attributes)
    if phase1_mix is not None:
        logger.info(f"Card mix, Phase 1: {format_card_mix_stats(phase1_mix)}")
    if result.is_multi_objective:
        phase2_mix = _card_mix_stats(result.selected_cards, attributes)
        if phase2_mix is not None:
            logger.info(f"Card mix, Phase 2: {format_card_mix_stats(phase2_mix)}")


def write_stats(
    result: OptimizationResult,
    output_file: str,
    cube_size: int,
    attributes: Mapping[str, CardAttributes] | None = None,
    payoffs: PayoffTable | None = None,
) -> None:
    """
    Write utilization statistics to JSON file.

    attributes (card name -> Scryfall attributes) adds the color distribution and card mix
    of each phase's cube when given; payoffs (the resolved payoff table) adds a top-level
    "payoffs" block with the table and what the inference found.
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
    if result.popularity_weight is not None:
        stats["metadata"]["popularity_weight"] = result.popularity_weight
    stats["metadata"].update(_score_entry(result, result.combo_score))

    # Phase 1 stats
    if result.phase1_utilization_stats:
        p1 = result.phase1_utilization_stats
        stats["phase1"] = {
            "combo_count": _phase1_combo_count(result),
            **_grouping_counts(
                _phase1_distinct_combo_count(result), _phase1_weighted_combo_count(result)
            ),
            **_score_entry(result, result.phase1_combo_score),
            "solve_time_seconds": result.phase1_solve_time,
            "min_utilization": p1.min_utilization,
            "max_utilization": p1.max_utilization,
            "mean_utilization": p1.mean_utilization,
            "median_utilization": p1.median_utilization,
            "std_deviation": p1.std_deviation,
            "total_absolute_deviation": p1.total_absolute_deviation,
            **_colors_block(_phase1_cards(result), attributes),
            **_archetypes_block(result.phase1_archetype_stats),
            **_card_mix_block(_phase1_cards(result), attributes),
            **_outcomes_block(result.phase1_outcome_stats),
            **_popularity_block(result.phase1_popularity_stats),
            **_payoffs_block(result.phase1_payoff_stats),
        }

    # Phase 2 stats and improvement (only for multi-objective)
    if result.is_multi_objective and result.phase2_utilization_stats:
        p2 = result.phase2_utilization_stats
        stats["phase2"] = {
            "combo_count": result.combo_count,
            **_grouping_counts(result.distinct_combo_count, result.weighted_combo_count),
            **_score_entry(result, result.combo_score),
            "solve_time_seconds": result.phase2_solve_time,
            "status": result.phase2_status,
            "min_utilization": p2.min_utilization,
            "max_utilization": p2.max_utilization,
            "mean_utilization": p2.mean_utilization,
            "median_utilization": p2.median_utilization,
            "std_deviation": p2.std_deviation,
            "total_absolute_deviation": p2.total_absolute_deviation,
            **_phase2_objective_info(result),
            **_colors_block(result.selected_cards, attributes),
            **_archetypes_block(result.phase2_archetype_stats),
            **_card_mix_block(result.selected_cards, attributes),
            **_outcomes_block(result.phase2_outcome_stats),
            **_popularity_block(result.phase2_popularity_stats),
            **_payoffs_block(result.phase2_payoff_stats),
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

    stats.update(_payoff_table_block(payoffs))

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


async def fetch_card_attributes(
    card_names: Iterable[str],
    enable_cache_write: bool = True,
    read_cache: bool = False,
) -> dict[str, CardAttributes] | None:
    """
    Look up the attributes (color identity, type line, mana value) of each card on Scryfall.

    Returns None when no card data could be fetched, so callers can leave the rules and
    statistics that need it out.
    """
    async with ScryfallFetcher() as fetcher:
        attribute_fetcher = CardAttributeFetcher(
            fetcher, enable_read=read_cache, enable_write=enable_cache_write
        )
        attributes = await attribute_fetcher.fetch_attributes(card_names)
    return attributes or None


async def fetch_payoff_queries(
    queries: Sequence[str],
    enable_cache_write: bool = True,
    read_cache: bool = False,
) -> dict[str, list[str]]:
    """
    Resolve the payoff table's Scryfall queries to card names (query -> ordered names).

    A query that matches no card is a table error (PayoffTableError); a query whose request
    failed is left out with a warning, so its cards are missing from the payoff set.
    """
    if not queries:
        return {}
    async with ScryfallFetcher() as fetcher:
        payoff_fetcher = PayoffFetcher(
            fetcher, enable_read=read_cache, enable_write=enable_cache_write
        )
        results = await payoff_fetcher.fetch_queries(queries)
    check_query_results(results)
    return results


def resolve_payoff_definitions(
    payoffs_path: str | None,
    outcome_categories: OutcomeCategories | None,
    min_payoffs: int,
) -> PayoffDefinitions | None:
    """
    The payoff table a run uses (payoffs.resolve_payoff_definitions, required when the
    payoff floor is on), logged; None when there is none to use.
    """
    definitions = _resolve_payoff_definitions(
        payoffs_path, outcome_categories, required=min_payoffs > 0
    )
    if definitions is None:
        logger.info(
            f"No payoff table to use (the default is {DEFAULT_PAYOFFS_PATH} in the working "
            "directory; pass --payoffs for another); payoffs are skipped"
        )
        return None
    logger.info(
        f"Loaded payoff table with {len(definitions)} categories: {', '.join(definitions.names)}"
    )
    return definitions


def build_payoff_table(
    definitions: PayoffDefinitions,
    combos: list[ComboData],
    outcome_categories: OutcomeCategories,
    query_results: Mapping[str, Sequence[str]],
    blocklist: frozenset[str] = frozenset(),
    inference_threshold: int = DEFAULT_INFERENCE_THRESHOLD,
) -> PayoffTable:
    """The resolved payoff table of an instance: inferred outlets, table cards and queries."""
    inferred = infer_payoffs(combos, outcome_categories, definitions.names)
    payoffs = resolve_payoffs(
        definitions, inferred, query_results, blocklist, inference_threshold=inference_threshold
    )
    logger.info(f"Payoff cards: {format_payoff_table(payoffs)}")
    return payoffs


def add_payoff_cards(candidate_cards: dict[str, CandidateCard], payoffs: PayoffTable) -> list[str]:
    """
    Add every payoff card the pool lacks to the candidate cards, as a card that completes no
    combo (a payoff-only card, selectable for the payoff floor). Returns the names added.
    """
    added = sorted(payoffs.all_cards - set(candidate_cards))
    for name in added:
        candidate_cards[name] = CandidateCard(name, frozenset(), frozenset())
    logger.info(
        f"Added {len(added)} payoff-only cards to the candidate pool; "
        f"{len(payoffs.all_cards) - len(added)} payoff cards were combo pieces already"
    )
    return added


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
    card_mix: CardMixRules = DEFAULT_CARD_MIX,
    outcome_categories_path: str | None = None,
    min_outcome_combos: int = ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS,
    max_outcome_share: float = 0,
    popularity_weight: float = 0,
    payoffs_path: str | None = None,
    min_payoffs: int = ILPOptimizer.DEFAULT_MIN_PAYOFFS,
    payoff_inference_min: int = DEFAULT_INFERENCE_THRESHOLD,
) -> tuple[
    list[str], int, OptimizationResult, dict[str, CardAttributes] | None, PayoffTable | None
]:
    """
    Build cube using ILP optimization with optional API caching.

    outcome_categories_path is the outcome category table (data/outcome_categories.json by
    default), behind the outcome statistics and the Phase 2 outcome rules. The default
    table is read from the working directory; when it is missing there and no rule is
    on, the build goes on without the outcome statistics. payoffs_path is the payoff table
    (data/payoffs.json by default), behind the payoff statistics and the Phase 2 payoff
    floor (min_payoffs); its Scryfall queries are resolved through the payoff cache, and the
    payoff cards the pool lacks are added to the candidate cards. Both tables are read
    before the instance is loaded, so a bad table fails fast.

    Returns:
        - List of card names in cube
        - Number of completable combos
        - Full optimization result with stats
        - Scryfall attributes of every candidate card (None when none could be fetched)
        - The resolved payoff table (None without one)
    """
    logger.info(f"Building {cube_size}-card cube using ILP optimization...")

    # Read the tables first: a bad path or table should fail before the instance is loaded
    outcome_categories = resolve_outcome_categories(
        outcome_categories_path,
        required=outcome_rules_requested(min_outcome_combos, max_outcome_share) or min_payoffs > 0,
    )
    if outcome_categories is None:
        logger.warning(
            f"No outcome category table at {DEFAULT_OUTCOME_CATEGORIES_PATH} (run from the "
            "repository root or pass --outcome-categories); the outcome statistics are skipped"
        )
    else:
        logger.info(
            f"Loaded {len(outcome_categories)} outcome categories: "
            f"{', '.join(outcome_categories.names)}"
        )
    payoff_definitions = resolve_payoff_definitions(payoffs_path, outcome_categories, min_payoffs)
    query_results: dict[str, list[str]] = {}
    if payoff_definitions is not None:
        query_results = await fetch_payoff_queries(
            payoff_definitions.queries, enable_cache_write=enable_cache_write, read_cache=read_cache
        )

    combo_data, candidate_cards = await load_instance(
        max_cards_in_combo=max_cards_in_combo,
        max_variants=max_variants,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        blocklist=blocklist,
    )

    # The payoff cards the pool lacks join the candidates, as cards that complete no combo
    payoffs: PayoffTable | None = None
    if payoff_definitions is not None and outcome_categories is not None:
        payoffs = build_payoff_table(
            payoff_definitions,
            combo_data,
            outcome_categories,
            query_results,
            blocklist,
            inference_threshold=payoff_inference_min,
        )
        add_payoff_cards(candidate_cards, payoffs)

    if len(candidate_cards) < cube_size:
        logger.warning(
            f"Only {len(candidate_cards)} unique cards available, "
            f"but cube size is {cube_size}. Adjusting cube size."
        )
        cube_size = len(candidate_cards)

    # Card attributes feed the Phase 2 color balance and card mix rules and the statistics
    card_attributes = await fetch_card_attributes(
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
        card_attributes=card_attributes,
        max_color_ratio=max_color_ratio,
        variant_weight=variant_weight,
        min_pair_combos=min_pair_combos,
        min_mono_combos=min_mono_combos,
        max_wide_combo_share=max_wide_combo_share,
        card_mix=card_mix,
        outcome_categories=outcome_categories,
        min_outcome_combos=min_outcome_combos,
        max_outcome_share=max_outcome_share,
        popularity_weight=popularity_weight,
        payoffs=payoffs,
        min_payoffs=min_payoffs,
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

    return result.get_selected_card_names(), result.combo_count, result, card_attributes, payoffs


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
    card_mix: CardMixRules = DEFAULT_CARD_MIX,
    outcome_categories_path: str | None = None,
    min_outcome_combos: int = ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS,
    max_outcome_share: float = 0,
    popularity_weight: float = 0,
    payoffs_path: str | None = None,
    min_payoffs: int = ILPOptimizer.DEFAULT_MIN_PAYOFFS,
    payoff_inference_min: int = DEFAULT_INFERENCE_THRESHOLD,
):
    """Entry point for ILP-based cube building with caching support."""
    cards, combo_count, result, card_attributes, payoffs = await build_cube_ilp(
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
        card_mix=card_mix,
        outcome_categories_path=outcome_categories_path,
        min_outcome_combos=min_outcome_combos,
        max_outcome_share=max_outcome_share,
        popularity_weight=popularity_weight,
        payoffs_path=payoffs_path,
        min_payoffs=min_payoffs,
        payoff_inference_min=payoff_inference_min,
    )

    logger.info(
        f"ILP result: {len(cards)} cards, "
        f"{format_combo_count(combo_count, result.distinct_combo_count)} "
        f"({result.phase1_status})"
    )

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(cards))

    log_phase_summary(result, card_attributes)

    # Write utilization stats
    write_stats(result, output_file, cube_size, card_attributes, payoffs)
