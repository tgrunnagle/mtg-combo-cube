"""High-level async interface for ILP-based cube building."""

import json
import logging
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from mtg_combo_cube.blocklist import fetch_blocklist
from mtg_combo_cube.config import ConfigError, CubeConfig, load_config, read_card_list
from mtg_combo_cube.ilp.card_table import (
    CardTable,
    CardTableDefinitions,
    CardTableError,
    CardTableFetchError,
    TableKind,
    check_query_results,
)
from mtg_combo_cube.ilp.combo_preprocessor import ComboPreprocessor
from mtg_combo_cube.ilp.cube_evaluation import (
    COLOR_PAIRS,
    COLORLESS,
    MANA_VALUE_CAP,
    MONO_COLORS,
    compute_card_mix_stats,
    compute_color_stats,
)
from mtg_combo_cube.ilp.enablers import (
    ENABLER_KIND,
    EnablerDefinitions,
    EnablerFetchError,
    EnablerTable,
    resolve_enablers,
)
from mtg_combo_cube.ilp.ilp_models import (
    DEFAULT_CARD_MIX,
    FIXED,
    INCLUSION_REASONS,
    ArchetypeStats,
    CandidateCard,
    CardInclusionStats,
    CardMixRules,
    CardMixStats,
    ColorStats,
    ComboData,
    ConcentrationStats,
    EnablerStats,
    FixedCardStats,
    MeasureStats,
    OneCardAwayReport,
    OptimizationResult,
    OutcomeStats,
    PayoffStats,
    PopularityStats,
    StapleStats,
    UtilizationStats,
)
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.outcomes import OutcomeCategories, outcome_rules_requested
from mtg_combo_cube.ilp.payoffs import (
    DEFAULT_INFERENCE_THRESHOLD,
    PAYOFF_KIND,
    PayoffDefinitions,
    PayoffFetchError,
    PayoffTable,
    infer_payoffs,
    resolve_payoffs,
)
from mtg_combo_cube.ilp.spellbook_links import find_my_combos_url, search_urls
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
    """The Phase 2 objective and its cap, when the result records them."""
    info: dict = {}
    if result.phase2_objective is not None:
        info["objective"] = result.phase2_objective
    if result.phase2_util_cap is not None:
        info["util_cap"] = result.phase2_util_cap
    if result.phase2_util_cap_measure is not None:
        info["util_cap_measure"] = result.phase2_util_cap_measure
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
    if result.phase2_payoff_share is not None:
        info["payoff_share"] = result.phase2_payoff_share
    if result.phase2_payoff_floors is not None:
        info["payoff_floors"] = result.phase2_payoff_floors
    if result.phase2_min_enablers is not None:
        info["min_enablers"] = result.phase2_min_enablers
    if result.phase2_enabler_share is not None:
        info["enabler_share"] = result.phase2_enabler_share
    if result.phase2_enabler_floors is not None:
        info["enabler_floors"] = result.phase2_enabler_floors
    if result.phase2_staple_combos is not None:
        info["staple_combos"] = result.phase2_staple_combos
    if result.phase2_min_staple_combos is not None:
        info["min_staple_combos"] = result.phase2_min_staple_combos
    if result.phase2_staples_completed is not None:
        info["staples_completed"] = result.phase2_staples_completed
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


def _measure_block(stats: MeasureStats | None) -> dict:
    """The five numbers of a per-card measure, for the stats file."""
    if stats is None:
        return {}
    return {
        "min": stats.min,
        "max": stats.max,
        "mean": stats.mean,
        "median": stats.median,
        "std_deviation": stats.std_deviation,
    }


def _combo_measures_block(stats: UtilizationStats) -> dict:
    """
    The utilization statistics' companions for the stats file: the same cards counted in
    distinct combos and in essential combos, and the concentration of the cube's combos
    on its most-used cards.
    """
    block: dict = {}
    if stats.distinct_combos is not None:
        block["distinct_combos_per_card"] = _measure_block(stats.distinct_combos)
    if stats.essential_combos is not None:
        block["essential_combos_per_card"] = _measure_block(stats.essential_combos)
    if stats.concentration is not None:
        c = stats.concentration
        block["concentration"] = {
            "top_card": c.top_card,
            "top_card_share": c.top_card_share,
            "top_five_cards": c.top_five_cards,
            "top_five_share": c.top_five_share,
            "most_essential_card": c.most_essential_card,
            "max_essential_combos": c.max_essential_combos,
        }
    return block


def format_concentration(stats: ConcentrationStats) -> str:
    """One line on how much of a cube's combos rest on a few cards."""
    return (
        f"top card {stats.top_card} in {stats.top_card_share:.0%} of the combos, top five in "
        f"{stats.top_five_share:.0%}, most essential {stats.most_essential_card} "
        f"({stats.max_essential_combos} combos)"
    )


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


# How a table's sources are named in the log ("inferred" stays as it is)
SOURCE_PLURALS = {"card": "cards", "query": "queries"}


def _format_table(table: CardTable) -> str:
    """'mana 31 (inferred 5, cards 0, queries 26); storm 15 (...)' for a resolved table."""
    parts = []
    for name in table.names:
        counts = ", ".join(
            f"{SOURCE_PLURALS.get(source, source)} {count}"
            for source, count in table.source_counts(name).items()
        )
        parts.append(f"{name} {len(table.cards(name))} ({counts})")
    return "; ".join(parts)


def format_payoff_table(payoffs: PayoffTable) -> str:
    """'mana 31 (inferred 5, cards 0, queries 26); storm 15 (...)' for the resolved table."""
    return _format_table(payoffs)


def _enablers_block(stats: EnablerStats | None) -> dict:
    """The "enablers" entry of a phase block, empty when the result has no enabler data."""
    return {"enablers": asdict(stats)} if stats is not None else {}


def _enabler_table_block(enablers: EnablerTable | None) -> dict:
    """
    The top-level "enablers" entry of the stats file: the resolved enabler table (every
    card per category with its sources, and how many each source named).
    """
    if enablers is None:
        return {}
    return {
        "enablers": {
            "cards_per_category": {name: len(enablers.cards(name)) for name in enablers.names},
            "source_counts": {name: enablers.source_counts(name) for name in enablers.names},
            "cards": {
                name: {card: sorted(sources) for card, sources in by_card.items()}
                for name, by_card in enablers.sources.items()
            },
        }
    }


def format_enabler_stats(stats: EnablerStats) -> str:
    """One-line summary of the enabler cards per category."""
    return ", ".join(f"{name}={count}" for name, count in stats.cards_per_category.items())


def format_enabler_table(enablers: EnablerTable) -> str:
    """'fast_mana 18 (cards 0, queries 18); ritual 8 (...)' for the resolved table."""
    return _format_table(enablers)


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


def card_inclusion_summary(cards: list[CardInclusionStats]) -> dict:
    """
    How many of a cube's cards are in it for each reason, and how many are in one combo.
    The entries of fixed cards (reason FIXED) are not the cube's cards and are left out.
    """
    cube_cards = [card for card in cards if card.reason != FIXED]
    reasons = Counter(card.reason for card in cube_cards)
    return {
        "cards_per_reason": {reason: reasons[reason] for reason in INCLUSION_REASONS},
        "cards_in_one_combo": sum(1 for card in cube_cards if card.distinct_combo_count == 1),
    }


def card_inclusion_entries(
    cards: list[CardInclusionStats], phase1_cards: Collection[str] | None = None
) -> list[dict]:
    """
    The per-card entries of the card data file. With `phase1_cards` (the Phase 1 cube, when
    Phase 2 ran) each card also says whether Phase 2 added it: a card Phase 1 left out, so
    there for a Phase 2 rule or the utilization balance (a fixed card, in neither phase's
    cube, does not say). `spellbook_urls` are Spellbook searches listing the card's
    completed combos (one variant each), ten per link.
    """
    phase1 = set(phase1_cards) if phase1_cards is not None else None
    return [
        {
            "card": card.card,
            "reason": card.reason,
            "combo_count": card.combo_count,
            "distinct_combo_count": card.distinct_combo_count,
            "essential_combo_count": card.essential_combo_count,
            "piece_combo_count": card.piece_combo_count,
            "template_combo_count": card.template_combo_count,
            "templates": card.templates,
            "outcomes": card.outcomes,
            "payoff_categories": card.payoff_categories,
            "enabler_categories": card.enabler_categories,
            **(
                {"added_in_phase2": card.card not in phase1}
                if phase1 is not None and card.reason != FIXED
                else {}
            ),
            "essential_combos": card.essential_combos,
            "spellbook_urls": search_urls(card.combo_variants),
        }
        for card in cards
    ]


def card_data_path(output_file: str) -> Path:
    """The card data file of a cube list: data/cube.txt -> data/cube_card_data.json."""
    output_path = Path(output_file)
    return output_path.with_stem(f"{output_path.stem}_card_data").with_suffix(".json")


def reference_cube_path(output_file: str) -> Path:
    """The reference cube file of a cube list: data/cube.txt -> data/cube_reference.txt."""
    output_path = Path(output_file)
    return output_path.with_stem(f"{output_path.stem}_reference")


def read_cube_file(path: str) -> list[str]:
    """
    Read card names from a cube list file (one name per line, blank lines and `#` comments
    ignored, a repeated name read once).
    """
    return read_card_list(path)


def write_reference_cube(cards: list[str], output_file: str) -> Path:
    """
    Write the Phase 2 reference cube (the best cube found under the cube rules, which the
    combo window was measured from) beside the cube list, for a later build's
    --warm-start.
    """
    path = reference_cube_path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(cards), encoding="utf-8")
    logger.info(f"Reference cube written to {path}")
    return path


def write_card_data(
    cards: list[CardInclusionStats],
    output_file: str,
    phase1_cards: Collection[str] | None = None,
    fixed_cards: Collection[str] = (),
) -> Path:
    """
    Write why each card of the cube is in it to the cube's card data file, with a Spellbook
    Find My Combos link for the whole cube (the cube's cards and every fixed card) and
    search links per card. `cards` are the inclusion statistics, the fixed cards among them
    (reason FIXED) after the cube's cards.
    """
    path = card_data_path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    cube_cards = [card.card for card in cards if card.reason != FIXED]
    data = {
        "summary": card_inclusion_summary(cards),
        "spellbook_find_my_combos_url": find_my_combos_url(
            cube_cards + sorted(set(fixed_cards) - set(cube_cards))
        ),
        "cards": card_inclusion_entries(cards, phase1_cards),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Per-card data written to {path}")
    return path


def potential_combos_path(output_file: str) -> Path:
    """The potential combos file of a cube list: cube.txt -> cube_potential_combos.json."""
    output_path = Path(output_file)
    return output_path.with_stem(f"{output_path.stem}_potential_combos").with_suffix(".json")


def one_card_away_entries(report: OneCardAwayReport, links: bool) -> list[dict]:
    """The rows of a one-card-away report, with Spellbook search links when asked."""
    return [
        {
            "piece": piece.piece,
            "kind": piece.kind,
            "variant_count": piece.variant_count,
            "distinct_combo_count": piece.distinct_combo_count,
            "new_combo_count": piece.new_combo_count,
            "popularity": piece.popularity,
            "max_popularity": piece.max_popularity,
            "example_cards": piece.example_cards,
            **({"spellbook_urls": search_urls(piece.variant_ids)} if links else {}),
        }
        for piece in report.pieces
    ]


ONE_CARD_AWAY_TOP = 10  # pieces the stats file and the log show


def _one_card_away_block(report: OneCardAwayReport | None) -> dict:
    """The "one_card_away" entry of the stats file: the totals and the top pieces, no links."""
    if report is None:
        return {}
    return {
        "one_card_away": {
            "variant_count": report.variant_count,
            "piece_count": report.piece_count,
            "top_pieces": one_card_away_entries(
                OneCardAwayReport(
                    report.variant_count, report.piece_count, report.pieces[:ONE_CARD_AWAY_TOP]
                ),
                links=False,
            ),
        }
    }


def write_potential_combos(
    report: OneCardAwayReport,
    output_file: str,
    cube_cards: Collection[str],
    fixed_cards: Collection[str] = (),
) -> Path:
    """
    Write the one-card-away report to the cube's potential combos file: the totals, the
    pieces with Spellbook search links to their variants (ten per link), and a Find My
    Combos link for the cube's cards and the fixed cards together, whose "potential combos"
    list is the same report over all of Spellbook rather than the pool.
    """
    path = potential_combos_path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    cards = list(cube_cards) + sorted(set(fixed_cards) - set(cube_cards))
    data = {
        "variant_count": report.variant_count,
        "piece_count": report.piece_count,
        "fixed_card_count": len(cards) - len(list(cube_cards)),
        "spellbook_find_my_combos_url": find_my_combos_url(cards),
        "pieces": one_card_away_entries(report, links=True),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Potential combos written to {path}")
    return path


def format_one_card_away(report: OneCardAwayReport, limit: int = ONE_CARD_AWAY_TOP) -> str:
    """
    'N variants one piece short, P pieces; top: Sol Ring 10 variants (5 new combos), ...'
    for a one-card-away report.
    """
    top = ", ".join(
        f"{piece.piece} {piece.variant_count} variants ({piece.new_combo_count} new combos)"
        for piece in report.pieces[:limit]
    )
    return f"{report.variant_count} variants one piece short, {report.piece_count} pieces" + (
        f"; top: {top}" if top else ""
    )


def _fixed_cards_block(stats: FixedCardStats | None) -> dict:
    """The "fixed_cards" entry of the stats file, empty without fixed cards."""
    return {"fixed_cards": asdict(stats)} if stats is not None else {}


def format_fixed_card_stats(stats: FixedCardStats) -> str:
    """One-line summary of the fixed cards: how many, candidates, and what they complete."""
    categories = ", ".join(
        f"{name} {len(cards)}"
        for table in (stats.payoff_categories, stats.enabler_categories)
        for name, cards in table.items()
    )
    return (
        f"{len(stats.cards)} cards, {len(stats.candidate_cards)} candidates, in "
        f"{format_combo_count(stats.combo_count, stats.distinct_combo_count)}"
        + (f"; {categories}" if categories else "")
    )


def _staples_block(staples: list[StapleStats] | None) -> dict:
    """The "staples" entry of the stats file, empty without staples."""
    return {"staples": [asdict(staple) for staple in staples]} if staples is not None else {}


def _staples_completed_entry(completed: int | None) -> dict:
    """The "staples_completed" entry of a phase block, empty without staples."""
    return {"staples_completed": completed} if completed is not None else {}


def format_staple_stats(staples: list[StapleStats]) -> str:
    """'7 of 10 staples completed; missing: A + B (C), ...' for the staples of a cube."""
    completed = sum(1 for staple in staples if staple.completed)
    missing = ", ".join(
        f"{' + '.join(staple.cards)} (lacks {', '.join(staple.missing_cards)})"
        for staple in staples
        if not staple.completed
    )
    return f"{completed} of {len(staples)} staples completed" + (
        f"; missing: {missing}" if missing else ""
    )


def _phase1_card_names(result: OptimizationResult) -> list[str] | None:
    """The Phase 1 cube's card names when Phase 2 ran and changed the cube, else None."""
    if not result.is_multi_objective or result.phase1_selected_cards is None:
        return None
    return [card.name for card in result.phase1_selected_cards]


def format_card_inclusion(cards: list[CardInclusionStats]) -> str:
    """One line on why the cube's cards are in it: the cards per reason (fixed cards apart)."""
    reasons = Counter(card.reason for card in cards)
    counts = ", ".join(f"{reason} {reasons[reason]}" for reason in INCLUSION_REASONS)
    one_combo = sum(1 for card in cards if card.distinct_combo_count == 1 and card.reason != FIXED)
    fixed = f"; {reasons[FIXED]} fixed cards in a combo" if reasons[FIXED] else ""
    return f"{counts}; {one_combo} cards in a single combo{fixed}"


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

    if result.phase1_enabler_stats is not None:
        logger.info(f"Enablers, Phase 1: {format_enabler_stats(result.phase1_enabler_stats)}")
    if result.is_multi_objective and result.phase2_enabler_stats is not None:
        logger.info(f"Enablers, Phase 2: {format_enabler_stats(result.phase2_enabler_stats)}")

    phase1_mix = _card_mix_stats(_phase1_cards(result), attributes)
    if phase1_mix is not None:
        logger.info(f"Card mix, Phase 1: {format_card_mix_stats(phase1_mix)}")
    if result.is_multi_objective:
        phase2_mix = _card_mix_stats(result.selected_cards, attributes)
        if phase2_mix is not None:
            logger.info(f"Card mix, Phase 2: {format_card_mix_stats(phase2_mix)}")

    if result.card_inclusion is not None:
        logger.info(f"Cards by reason: {format_card_inclusion(result.card_inclusion)}")

    if result.fixed_card_stats is not None:
        logger.info(f"Fixed cards: {format_fixed_card_stats(result.fixed_card_stats)}")
    if result.staple_stats is not None:
        phases = []
        if result.phase1_staples_completed is not None:
            phases.append(f"Phase 1 {result.phase1_staples_completed}")
        if result.is_multi_objective and result.phase2_staples_completed is not None:
            phases.append(f"Phase 2 {result.phase2_staples_completed}")
        logger.info(
            f"Staples ({', '.join(phases)} of {len(result.staple_stats)}): "
            f"{format_staple_stats(result.staple_stats)}"
        )
    if result.one_card_away is not None:
        logger.info(f"One card away: {format_one_card_away(result.one_card_away)}")


def write_stats(
    result: OptimizationResult,
    output_file: str,
    cube_size: int,
    attributes: Mapping[str, CardAttributes] | None = None,
    payoffs: PayoffTable | None = None,
    enablers: EnablerTable | None = None,
) -> None:
    """
    Write utilization statistics to JSON file.

    attributes (card name -> Scryfall attributes) adds the color distribution and card mix
    of each phase's cube when given; payoffs (the resolved payoff table) adds a top-level
    "payoffs" block with the table and what the inference found, and enablers (the
    resolved enabler table) a top-level "enablers" block with the table.
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
            **_combo_measures_block(p1),
            **_colors_block(_phase1_cards(result), attributes),
            **_archetypes_block(result.phase1_archetype_stats),
            **_card_mix_block(_phase1_cards(result), attributes),
            **_outcomes_block(result.phase1_outcome_stats),
            **_popularity_block(result.phase1_popularity_stats),
            **_payoffs_block(result.phase1_payoff_stats),
            **_enablers_block(result.phase1_enabler_stats),
            **_staples_completed_entry(result.phase1_staples_completed),
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
            **_combo_measures_block(p2),
            **_phase2_objective_info(result),
            **_colors_block(result.selected_cards, attributes),
            **_archetypes_block(result.phase2_archetype_stats),
            **_card_mix_block(result.selected_cards, attributes),
            **_outcomes_block(result.phase2_outcome_stats),
            **_popularity_block(result.phase2_popularity_stats),
            **_payoffs_block(result.phase2_payoff_stats),
            **_enablers_block(result.phase2_enabler_stats),
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

    # Top and bottom utilized cards, with their distinct and essential combos when known
    if result.utilization_per_card:
        sorted_cards = sorted(result.utilization_per_card.items(), key=lambda x: x[1], reverse=True)
        inclusion = {stats.card: stats for stats in result.card_inclusion or []}

        def entry(card: str, util: int) -> dict:
            counts = inclusion.get(card)
            if counts is None:
                return {"card": card, "utilization": util}
            return {
                "card": card,
                "utilization": util,
                "distinct_combos": counts.distinct_combo_count,
                "essential_combos": counts.essential_combo_count,
            }

        stats["top_utilized_cards"] = [entry(card, util) for card, util in sorted_cards[:10]]
        stats["bottom_utilized_cards"] = [entry(card, util) for card, util in sorted_cards[-10:]]

    if result.card_inclusion is not None:
        stats["cards"] = card_inclusion_summary(result.card_inclusion)

    # The fixed cards, the staples and the pieces the final cube is one card short of
    stats.update(_fixed_cards_block(result.fixed_card_stats))
    stats.update(_staples_block(result.staple_stats))
    stats.update(_one_card_away_block(result.one_card_away))

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
    stats.update(_enabler_table_block(enablers))

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


async def fetch_table_queries(
    queries: Sequence[str],
    kind: TableKind,
    fetch_error: type[CardTableFetchError],
    flag: str,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    required: bool = False,
) -> dict[str, list[str]]:
    """
    Resolve a card table's Scryfall queries to card names (query -> ordered names).

    A query Scryfall rejects or only partly understands is a table error (the kind's
    error), whatever the settings. A query that matches no card is one too when the
    results are `required` (the table's floor is on, so a missing query would change a hard
    constraint and make the run incomparable), and so is a query whose request failed
    (`fetch_error`, a transient problem; `flag` names the option that turns the floor off);
    otherwise both are left out with a warning and their cards are missing from the table.
    """
    if not queries:
        return {}
    label = kind.label.capitalize()
    async with ScryfallFetcher() as fetcher:
        query_fetcher = PayoffFetcher(
            fetcher, enable_read=read_cache, enable_write=enable_cache_write, label=label
        )
        results = await query_fetcher.fetch_queries(queries)
    if query_fetcher.rejected:
        raise kind.error(
            f"{kind.label} queries rejected by Scryfall (fix the table): "
            + "; ".join(f"{query!r} ({reason})" for query, reason in query_fetcher.rejected.items())
        )
    if required:
        check_query_results(results, kind)
    elif empty := [query for query, cards in results.items() if not cards]:
        logger.warning(
            f"{label} queries match no card on Scryfall and are skipped: "
            + ", ".join(repr(q) for q in empty)
        )
        results = {query: cards for query, cards in results.items() if cards}
    missing = [query for query in queries if query not in results]
    if missing and required:
        raise fetch_error(
            f"{kind.label} queries could not be fetched from Scryfall (the {kind.label} floor "
            f"needs them; run again, or pass {flag} 0): " + ", ".join(repr(q) for q in missing)
        )
    return results


async def fetch_payoff_queries(
    queries: Sequence[str],
    enable_cache_write: bool = True,
    read_cache: bool = False,
    required: bool = False,
) -> dict[str, list[str]]:
    """
    Resolve the payoff table's Scryfall queries (fetch_table_queries): a rejected query is a
    PayoffTableError, an empty or unfetchable one is an error when `required` (the payoff
    floor is on; PayoffFetchError for the latter).
    """
    return await fetch_table_queries(
        queries,
        PAYOFF_KIND,
        PayoffFetchError,
        "--min-payoffs",
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        required=required,
    )


async def fetch_enabler_queries(
    queries: Sequence[str],
    enable_cache_write: bool = True,
    read_cache: bool = False,
    required: bool = False,
) -> dict[str, list[str]]:
    """
    Resolve the enabler table's Scryfall queries (fetch_table_queries): a rejected query is
    an EnablerTableError, an empty or unfetchable one is an error when `required` (the
    enabler floor is on; EnablerFetchError for the latter).
    """
    return await fetch_table_queries(
        queries,
        ENABLER_KIND,
        EnablerFetchError,
        "--min-enablers",
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        required=required,
    )


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


def build_enabler_table(
    definitions: EnablerDefinitions,
    query_results: Mapping[str, Sequence[str]],
    blocklist: frozenset[str] = frozenset(),
) -> EnablerTable:
    """The resolved enabler table of a run: table cards and queries."""
    enablers = resolve_enablers(definitions, query_results, blocklist)
    logger.info(f"Enabler cards: {format_enabler_table(enablers)}")
    return enablers


def add_table_cards(
    candidate_cards: dict[str, CandidateCard], table: CardTable, label: str
) -> list[str]:
    """
    Add every card of a table the pool lacks to the candidate cards, as a card that
    completes no combo (a payoff-only or enabler-only card, selectable for the table's
    floor). `label` names the table in the log. Returns the names added.
    """
    added = sorted(table.all_cards - set(candidate_cards))
    for name in added:
        candidate_cards[name] = CandidateCard(name, frozenset(), frozenset())
    logger.info(
        f"Added {len(added)} {label}-only cards to the candidate pool; "
        f"{len(table.all_cards) - len(added)} {label} cards were candidates already"
    )
    return added


def add_payoff_cards(candidate_cards: dict[str, CandidateCard], payoffs: PayoffTable) -> list[str]:
    """
    Add every payoff card the pool lacks to the candidate cards, as a card that completes no
    combo (a payoff-only card, selectable for the payoff floor). Returns the names added.
    """
    return add_table_cards(candidate_cards, payoffs, "payoff")


def add_enabler_cards(
    candidate_cards: dict[str, CandidateCard], enablers: EnablerTable
) -> list[str]:
    """
    Add every enabler card the pool lacks to the candidate cards, as a card that completes
    no combo (an enabler-only card, selectable for the enabler floor). Returns the names
    added.
    """
    return add_table_cards(candidate_cards, enablers, "enabler")


def drop_unknown_table_cards[T: CardTable](
    table: T,
    candidate_cards: dict[str, CandidateCard],
    card_attributes: Mapping[str, CardAttributes],
    label: str,
) -> T:
    """
    Remove the added cards of a table Scryfall does not know (a typo in the table's
    `cards`) from the candidate cards and the table, with a warning; a combo piece is known
    to exist. A required card Scryfall does not know is an error, since the cube cannot
    hold it. Returns the table to use.
    """
    unknown = sorted(
        card
        for card in table.all_cards
        if card in candidate_cards
        and not candidate_cards[card].combo_ids
        and not candidate_cards[card].requirement_group_keys
        and card not in card_attributes
    )
    if not unknown:
        return table
    if required := sorted(table.required.intersection(unknown)):
        raise CardTableError(
            f"the {label} table requires cards not known to Scryfall (check the spelling): "
            f"{required}"
        )
    logger.warning(
        f"{label.capitalize()} table: {len(unknown)} cards are not known to Scryfall and are "
        f"dropped (check the spelling): {unknown}"
    )
    for card in unknown:
        del candidate_cards[card]
    return table.without(unknown)


def drop_unknown_payoff_cards(
    payoffs: PayoffTable,
    candidate_cards: dict[str, CandidateCard],
    card_attributes: Mapping[str, CardAttributes],
) -> PayoffTable:
    """
    Remove the payoff-only cards Scryfall does not know (a typo in the table's `cards`) from
    the candidate cards and the table, with a warning; a combo piece is known to exist.
    Returns the table to use.
    """
    return drop_unknown_table_cards(payoffs, candidate_cards, card_attributes, "payoff")


def drop_unknown_enabler_cards(
    enablers: EnablerTable,
    candidate_cards: dict[str, CandidateCard],
    card_attributes: Mapping[str, CardAttributes],
) -> EnablerTable:
    """
    Remove the enabler-only cards Scryfall does not know (a typo in the table's `cards`)
    from the candidate cards and the table, with a warning. Returns the table to use.
    """
    return drop_unknown_table_cards(enablers, candidate_cards, card_attributes, "enabler")


def _log_table(definitions: CardTableDefinitions | None, label: str) -> None:
    """Say which categories a table of the configuration has, or that there is none."""
    if definitions is None:
        logger.info(f"The configuration has no {label} table; {label}s are skipped")
    else:
        logger.info(
            f"Loaded {label} table with {len(definitions)} categories: "
            f"{', '.join(definitions.names)}"
        )


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
    config: CubeConfig | None = None,
    profile: bool = False,
    gap_limit: float = 0.05,
    phase2_objective: str = "essential",
    min_utilization_floor: int = 2,
    num_workers: int = 8,
    util_cap: int | None = None,
    warm_start_file: str | None = None,
    max_color_ratio: float = 2.0,
    variant_weight: float = 0.1,
    min_pair_combos: int = 250,
    min_mono_combos: int = 150,
    max_wide_combo_share: float = 0.25,
    card_mix: CardMixRules = DEFAULT_CARD_MIX,
    min_outcome_combos: int = ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS,
    max_outcome_share: float = 0,
    popularity_weight: float = 0,
    min_payoffs: int = ILPOptimizer.DEFAULT_MIN_PAYOFFS,
    payoff_share: float = ILPOptimizer.DEFAULT_PAYOFF_SHARE,
    payoff_inference_min: int = DEFAULT_INFERENCE_THRESHOLD,
    min_enablers: int = ILPOptimizer.DEFAULT_MIN_ENABLERS,
    enabler_share: float = ILPOptimizer.DEFAULT_ENABLER_SHARE,
    staple_combos: int = ILPOptimizer.DEFAULT_STAPLE_COMBOS,
    min_staple_combos: int = ILPOptimizer.DEFAULT_MIN_STAPLE_COMBOS,
) -> tuple[
    list[str],
    int,
    OptimizationResult,
    dict[str, CardAttributes] | None,
    PayoffTable | None,
    EnablerTable | None,
]:
    """
    Build cube using ILP optimization with optional API caching.

    config holds the fixed cards, the blocklist, the blocklist queries, the outcome category
    table, the payoff table and the enabler table (the default configuration file when None,
    see config.py). The fixed cards (the cube's cards outside the cube_size slots) are in
    every model; the candidates among them count for every rule that counts combos, and a
    fixed card the blocklist blocks is a ConfigError. The cards the blocklist queries match
    are blocked too (see blocklist.py). The staples rule (staple_combos, min_staple_combos)
    holds the most popular combos of the pool in the Phase 2 cube. The
    outcome table is behind the outcome statistics and the Phase 2 outcome rules; without it
    and with no rule on, the build goes on without the outcome statistics. The payoff table
    is behind the payoff statistics and the Phase 2 payoff floor (min_payoffs per category,
    payoff_share of the cube in all); its Scryfall queries are resolved through the payoff
    cache, and the payoff cards the pool lacks are added to the candidate cards. The enabler
    table is behind the enabler statistics and the Phase 2 enabler floor (min_enablers,
    enabler_share) the same way. The configuration is read and checked before the instance
    is loaded, so a bad or incomplete one fails fast. warm_start_file is a cube list from
    an earlier build (its output or reference cube) that hints the Phase 2 reference
    solve; its cards outside the pool are ignored.

    Returns:
        - List of card names in cube
        - Number of completable combos
        - Full optimization result with stats
        - Scryfall attributes of every candidate card (None when none could be fetched)
        - The resolved payoff table (None without one)
        - The resolved enabler table (None without one)
    """
    logger.info(f"Building {cube_size}-card cube using ILP optimization...")

    # Read the configuration first: a bad or incomplete one should fail before the instance
    # is loaded
    if config is None:
        config = load_config()
    config.require(
        outcome_categories=outcome_rules_requested(min_outcome_combos, max_outcome_share)
        or min_payoffs > 0,
        payoffs=min_payoffs > 0,
        enablers=min_enablers > 0,
    )
    blocklist = await fetch_blocklist(
        config.blocklist,
        config.blocklist_queries,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
    )
    if blocked := sorted(config.fixed_cards & blocklist):
        raise ConfigError(
            f"fixed cards cannot be blocked by a blocklist query: {blocked} (fix the blocklist "
            "queries or the fixed cards file)"
        )
    outcome_categories = config.outcome_categories
    payoff_definitions = config.payoffs
    enabler_definitions = config.enablers
    if outcome_categories is None:
        logger.warning(
            "The configuration has no outcome category table; the outcome statistics are skipped"
        )
    else:
        logger.info(
            f"Loaded {len(outcome_categories)} outcome categories: "
            f"{', '.join(outcome_categories.names)}"
        )
    _log_table(payoff_definitions, "payoff")
    _log_table(enabler_definitions, "enabler")
    query_results: dict[str, list[str]] = {}
    if payoff_definitions is not None:
        query_results = await fetch_payoff_queries(
            payoff_definitions.queries,
            enable_cache_write=enable_cache_write,
            read_cache=read_cache,
            required=min_payoffs > 0,
        )
    enabler_query_results: dict[str, list[str]] = {}
    if enabler_definitions is not None:
        enabler_query_results = await fetch_enabler_queries(
            enabler_definitions.queries,
            enable_cache_write=enable_cache_write,
            read_cache=read_cache,
            required=min_enablers > 0,
        )

    combo_data, candidate_cards = await load_instance(
        max_cards_in_combo=max_cards_in_combo,
        max_variants=max_variants,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        blocklist=blocklist,
    )

    # With the payoff floor on, the payoff cards the pool lacks join the candidates, as cards
    # that complete no combo; with it off the table serves the statistics only and the pool
    # is the same as without a table
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
        if min_payoffs > 0:
            add_payoff_cards(candidate_cards, payoffs)
        else:
            logger.info(
                "The payoff floor is off: payoff cards the pool lacks are not added to the "
                "candidate cards"
            )
    # The same for the enabler table and the enabler floor
    enablers: EnablerTable | None = None
    if enabler_definitions is not None:
        enablers = build_enabler_table(enabler_definitions, enabler_query_results, blocklist)
        if min_enablers > 0:
            add_enabler_cards(candidate_cards, enablers)
        else:
            logger.info(
                "The enabler floor is off: enabler cards the pool lacks are not added to the "
                "candidate cards"
            )

    # The fixed cards take no slot of the cube: only the free candidates fill it
    free_candidates = len(set(candidate_cards) - config.fixed_cards)
    if free_candidates < cube_size:
        logger.warning(
            f"Only {free_candidates} unique cards available, "
            f"but cube size is {cube_size}. Adjusting cube size."
        )
        cube_size = free_candidates

    # Card attributes feed the Phase 2 color balance and card mix rules and the statistics;
    # the fixed cards are looked up too, for the record
    card_attributes = await fetch_card_attributes(
        sorted(set(candidate_cards) | config.fixed_cards),
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
    )
    if payoffs is not None and card_attributes is not None:
        payoffs = drop_unknown_payoff_cards(payoffs, candidate_cards, card_attributes)
    if enablers is not None and card_attributes is not None:
        enablers = drop_unknown_enabler_cards(enablers, candidate_cards, card_attributes)

    warm_start_cards: list[str] | None = None
    if warm_start_file is not None:
        warm_start_cards = read_cube_file(warm_start_file)
        logger.info(f"Warm start: {len(warm_start_cards)} cards from {warm_start_file}")

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
        warm_start_cards=warm_start_cards,
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
        payoff_share=payoff_share,
        enablers=enablers,
        min_enablers=min_enablers,
        enabler_share=enabler_share,
        fixed_cards=config.fixed_cards,
        staple_combos=staple_combos,
        min_staple_combos=min_staple_combos,
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

    return (
        result.get_selected_card_names(),
        result.combo_count,
        result,
        card_attributes,
        payoffs,
        enablers,
    )


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
    config: CubeConfig | None = None,
    profile: bool = False,
    gap_limit: float = 0.05,
    phase2_objective: str = "essential",
    min_utilization_floor: int = 2,
    num_workers: int = 8,
    util_cap: int | None = None,
    warm_start_file: str | None = None,
    max_color_ratio: float = 2.0,
    variant_weight: float = 0.1,
    min_pair_combos: int = 250,
    min_mono_combos: int = 150,
    max_wide_combo_share: float = 0.25,
    card_mix: CardMixRules = DEFAULT_CARD_MIX,
    min_outcome_combos: int = ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS,
    max_outcome_share: float = 0,
    popularity_weight: float = 0,
    min_payoffs: int = ILPOptimizer.DEFAULT_MIN_PAYOFFS,
    payoff_share: float = ILPOptimizer.DEFAULT_PAYOFF_SHARE,
    payoff_inference_min: int = DEFAULT_INFERENCE_THRESHOLD,
    min_enablers: int = ILPOptimizer.DEFAULT_MIN_ENABLERS,
    enabler_share: float = ILPOptimizer.DEFAULT_ENABLER_SHARE,
    staple_combos: int = ILPOptimizer.DEFAULT_STAPLE_COMBOS,
    min_staple_combos: int = ILPOptimizer.DEFAULT_MIN_STAPLE_COMBOS,
):
    """Entry point for ILP-based cube building with caching support."""
    cards, combo_count, result, card_attributes, payoffs, enablers = await build_cube_ilp(
        cube_size=cube_size,
        time_limit_seconds=time_limit_seconds,
        max_variants=max_variants,
        use_multi_objective=use_multi_objective,
        enable_cache_write=enable_cache_write,
        read_cache=read_cache,
        combo_tolerance=combo_tolerance,
        min_coverage_ratio=min_coverage_ratio,
        min_combo_threshold=min_combo_threshold,
        config=config,
        profile=profile,
        gap_limit=gap_limit,
        phase2_objective=phase2_objective,
        min_utilization_floor=min_utilization_floor,
        num_workers=num_workers,
        util_cap=util_cap,
        warm_start_file=warm_start_file,
        max_color_ratio=max_color_ratio,
        variant_weight=variant_weight,
        min_pair_combos=min_pair_combos,
        min_mono_combos=min_mono_combos,
        max_wide_combo_share=max_wide_combo_share,
        card_mix=card_mix,
        min_outcome_combos=min_outcome_combos,
        max_outcome_share=max_outcome_share,
        popularity_weight=popularity_weight,
        min_payoffs=min_payoffs,
        payoff_share=payoff_share,
        payoff_inference_min=payoff_inference_min,
        min_enablers=min_enablers,
        enabler_share=enabler_share,
        staple_combos=staple_combos,
        min_staple_combos=min_staple_combos,
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
    write_stats(result, output_file, cube_size, card_attributes, payoffs, enablers)
    fixed_cards = result.fixed_card_stats.cards if result.fixed_card_stats is not None else []
    if result.card_inclusion is not None:
        write_card_data(result.card_inclusion, output_file, _phase1_card_names(result), fixed_cards)
    if result.one_card_away is not None:
        write_potential_combos(result.one_card_away, output_file, cards, fixed_cards)
    if result.phase2_reference_cards is not None:
        write_reference_cube(result.phase2_reference_cards, output_file)
