"""CLI entry point for MTG Combo Cube builder."""

import argparse
import asyncio
import logging
import math

from mtg_combo_cube.ilp.ilp_models import CardMixRuleError, CardMixRules
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.outcomes import (
    OutcomeCategoryError,
    outcome_rules_requested,
    resolve_outcome_categories,
)
from mtg_combo_cube.runner import run

if __name__ == "__main__":
    card_mix_defaults = CardMixRules()
    cap_note = "Between 0 and 1, rounded to hundredths; 0 or 1 removes the cap."
    argparser = argparse.ArgumentParser(
        description="Build MTG combo cube with optimal card selection"
    )
    argparser.add_argument("-c", "--cube-size", type=int, default=300)
    argparser.add_argument(
        "-r", "--ratio", type=float, help="Golden ratio for greedy method (default: 1.2)"
    )
    argparser.add_argument("-o", "--output-file", type=str, default="data/cube.txt")
    argparser.add_argument("-d", "--debug", action="store_true")
    argparser.add_argument(
        "-m",
        "--method",
        choices=["greedy", "ilp"],
        default="ilp",
        help="Optimization method: greedy or ilp (default)",
    )
    argparser.add_argument(
        "-t",
        "--time-limit",
        type=int,
        default=300,
        help="Time limit for ILP solver in seconds (default: 300)",
    )
    argparser.add_argument(
        "-n",
        "--max-variants",
        type=int,
        default=20000,
        help="Maximum number of combo variants to fetch (default: 20000)",
    )
    argparser.add_argument(
        "--single-phase",
        action="store_true",
        help="Use single-phase ILP (max combos only). Default: two-phase (balanced utilization)",
    )
    argparser.add_argument(
        "--combo-tolerance",
        type=float,
        default=0.1,
        help="Tolerance of the phase 2 combo window (default: 0.1 = 10%%), measured from the "
        "combo score of the best cube found under the Phase 2 cube rules (coverage, color "
        "balance, archetype support, card mix, outcome rules). "
        "The score is in weighted combos when --variant-weight is below 1 (see the README, "
        "'Combos and variants'). Set to 0 to hold the score exactly.",
    )
    argparser.add_argument(
        "--min-coverage-ratio",
        type=float,
        default=0.1,
        help="Minimum coverage ratio for requirement templates in phase 2 (default: 0.1 = 10%%). "
        "Set to 0 to disable coverage constraints.",
    )
    argparser.add_argument(
        "--skip-api-caching", action="store_true", help="Skip writing API responses to cache files"
    )
    argparser.add_argument(
        "--read-api-cache",
        action="store_true",
        help="Read API responses from cache, fall back to live API if missing",
    )
    argparser.add_argument(
        "--blocklist",
        type=str,
        default=None,
        help="Path to blocklist file (default: data/blocklist.txt)",
    )
    argparser.add_argument(
        "--profile",
        action="store_true",
        help="Enable detailed profiling of ILP optimization (constraint counts, solver stats)",
    )
    argparser.add_argument(
        "--gap-limit",
        type=float,
        default=0.05,
        help="Gap limit for Phase 2 early termination (default: 0.05 = 5%%). "
        "Solver stops when solution is within this %% of optimal ('softcap' and 'tiered': "
        "within this %% of the Phase 1 cube's overage). Set to 0 for exact optimal.",
    )
    argparser.add_argument(
        "--phase2-objective",
        type=str,
        choices=["mad", "minmax", "maxutil", "softcap", "tiered"],
        default="tiered",
        help="Phase 2 objective function: 'tiered' (default; minimize total utilization "
        "above --util-cap, counted again above 2x and 4x the cap), 'softcap' (minimize total "
        "utilization above --util-cap), 'maxutil' (minimize the maximum utilization), "
        "'minmax' (minimize max-min range) or 'mad' (minimize mean absolute deviation).",
    )
    argparser.add_argument(
        "--util-cap",
        type=int,
        default=None,
        help="Utilization cap for the 'softcap' and 'tiered' phase 2 objectives: utilization "
        "above this is penalized (default: 2 x the Phase 1 median utilization).",
    )
    argparser.add_argument(
        "--min-util-floor",
        type=int,
        default=2,
        help="Minimum utilization floor for phase 2, any objective (default: 2). "
        "Cards must participate in at least this many combos. Set to 0 to disable.",
    )
    argparser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel search workers for the ILP solver (default: 8). "
        "Lower this to reduce CPU load.",
    )
    argparser.add_argument(
        "--max-color-ratio",
        type=float,
        default=2.0,
        help="Color balance for phase 2: no color may have more than this many times the "
        "cards of another color (default: 2.0). A card counts once for each color of its "
        "color identity. Must be 0 or at least 1. Set to 0 to disable.",
    )
    argparser.add_argument(
        "--variant-weight",
        type=float,
        default=0.1,
        help="Value of each further completed variant of a combo the cube already completes, "
        "relative to the first, in the Phase 1 objective and the Phase 2 combo window "
        "(default: 0.1; 1 counts every variant as a combo, 0 counts distinct combos only). "
        "Between 0 and 1.",
    )
    argparser.add_argument(
        "--min-pair-combos",
        type=int,
        default=250,
        help="Archetype support for phase 2: every two-color pair must be able to assemble at "
        "least this many distinct combos, counting the pair's combos plus mono-colored and "
        "colorless ones (default: 250). Set to 0 to disable.",
    )
    argparser.add_argument(
        "--min-mono-combos",
        type=int,
        default=150,
        help="Archetype support for phase 2: every mono color must be able to assemble at "
        "least this many distinct combos, counting colorless ones (default: 150). Set to 0 "
        "to disable.",
    )
    argparser.add_argument(
        "--max-wide-combo-share",
        type=float,
        default=0.25,
        help="Archetype support for phase 2: at most this share of the completed combos may "
        "need three or more colors (default: 0.25). Between 0 and 1, in hundredths; 0 or 1 "
        "removes the cap.",
    )
    argparser.add_argument(
        "--max-multicolor-share",
        type=float,
        default=card_mix_defaults.max_multicolor_share,
        help="Card mix for phase 2: at most this share of the cube may be multicolor cards; "
        f"lands are left out of every card mix rule (default: "
        f"{card_mix_defaults.max_multicolor_share:g}). {cap_note}",
    )
    argparser.add_argument(
        "--max-colorless-share",
        type=float,
        default=card_mix_defaults.max_colorless_share,
        help="Card mix for phase 2: at most this share of the cube may be colorless nonland "
        "cards; cards without Scryfall data count as colorless (default: "
        f"{card_mix_defaults.max_colorless_share:g}). {cap_note}",
    )
    argparser.add_argument(
        "--max-expensive-share",
        type=float,
        default=card_mix_defaults.max_expensive_share,
        help="Card mix for phase 2: at most this share of the cube may have a mana value of "
        f"--expensive-mana-value or more (default: {card_mix_defaults.max_expensive_share:g}). "
        f"{cap_note}",
    )
    argparser.add_argument(
        "--expensive-mana-value",
        type=float,
        default=card_mix_defaults.expensive_mana_value,
        help="Mana value from which a card counts as expensive for --max-expensive-share "
        f"(default: {card_mix_defaults.expensive_mana_value:g}). Above 0.",
    )
    argparser.add_argument(
        "--max-creature-share",
        type=float,
        default=card_mix_defaults.max_creature_share,
        help="Card mix for phase 2: at most this share of the cube may be creatures "
        f"(default: {card_mix_defaults.max_creature_share:g}). {cap_note}",
    )
    argparser.add_argument(
        "--min-spell-share",
        type=float,
        default=card_mix_defaults.min_spell_share,
        help="Card mix for phase 2: at least this share of the cube must be instants or "
        f"sorceries (default: {card_mix_defaults.min_spell_share:g}). Between 0 and 1, "
        "rounded to hundredths; 0 disables.",
    )
    argparser.add_argument(
        "--mono-color-ratio",
        type=float,
        default=card_mix_defaults.mono_color_ratio,
        help="Card mix for phase 2: no color may have more than this many times the "
        "mono-colored cards of another color, as --max-color-ratio on mono-colored cards "
        f"only (default: {card_mix_defaults.mono_color_ratio:g}, disabled). 0 or at least 1.",
    )
    argparser.add_argument(
        "--min-outcome-combos",
        type=int,
        default=ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS,
        help="Outcome support for phase 2: the cube must complete at least this many distinct "
        "combos of every outcome category in the table (what the combos do: mana, damage, "
        "tokens, ...), unless the table gives a category its own minimum (default: "
        f"{ILPOptimizer.DEFAULT_MIN_OUTCOME_COMBOS}). Set to 0 to disable.",
    )
    argparser.add_argument(
        "--max-outcome-share",
        type=float,
        default=0,
        help="Outcome support for phase 2: at most this share of the completed combos may be "
        "in any one outcome category (default: 0, disabled). Between 0 and 1, in hundredths; "
        "0 or 1 removes the cap. Costly: every combo in a category is linked exactly in the "
        "warm-start repair models too, so a tight cap may find no cube within the time limit.",
    )
    argparser.add_argument(
        "--outcome-categories",
        type=str,
        default=None,
        help="Path to the outcome category table, a JSON object of category name to feature "
        "name patterns (default: data/outcome_categories.json)",
    )
    argparser.add_argument(
        "--popularity-weight",
        type=float,
        default=0,
        help="How much a combo's Spellbook popularity adds to its value in the Phase 1 "
        "objective and the Phase 2 combo window: a combo is worth 1 + weight x its popularity "
        "on a log scale relative to the most popular combo, so with 1 the most popular combo "
        "is worth two obscure ones (default: 0, popularity is a tiebreak only). 0 or more.",
    )
    args = argparser.parse_args()
    if not math.isfinite(args.max_color_ratio) or 0 < args.max_color_ratio < 1:
        argparser.error("--max-color-ratio must be 0 or at least 1")
    if not 0 <= args.variant_weight <= 1:
        argparser.error("--variant-weight must be between 0 and 1")
    if args.min_pair_combos < 0 or args.min_mono_combos < 0:
        argparser.error("--min-pair-combos and --min-mono-combos must be 0 or more")
    if not 0 <= args.max_wide_combo_share <= 1:
        argparser.error("--max-wide-combo-share must be between 0 and 1")
    if args.min_outcome_combos < 0:
        argparser.error("--min-outcome-combos must be 0 or more")
    if not 0 <= args.max_outcome_share <= 1:
        argparser.error("--max-outcome-share must be between 0 and 1")
    if not math.isfinite(args.popularity_weight) or args.popularity_weight < 0:
        argparser.error("--popularity-weight must be 0 or more")
    # The card mix settings are validated once, by CardMixRules; the error names the field
    try:
        card_mix = CardMixRules(
            max_multicolor_share=args.max_multicolor_share,
            max_colorless_share=args.max_colorless_share,
            max_expensive_share=args.max_expensive_share,
            expensive_mana_value=args.expensive_mana_value,
            max_creature_share=args.max_creature_share,
            min_spell_share=args.min_spell_share,
            mono_color_ratio=args.mono_color_ratio,
        )
    except CardMixRuleError as e:
        argparser.error(str(e).replace(e.field, f"--{e.field.replace('_', '-')}", 1))
    if args.method == "ilp":
        # A missing or invalid outcome table is a usage error, not a traceback
        try:
            resolve_outcome_categories(
                args.outcome_categories,
                required=outcome_rules_requested(args.min_outcome_combos, args.max_outcome_share),
            )
        except (FileNotFoundError, OutcomeCategoryError) as e:
            argparser.error(str(e))
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    asyncio.run(
        run(
            method=args.method,
            cube_size=args.cube_size,
            output_file=args.output_file,
            golden_ratio=args.ratio,
            time_limit_seconds=args.time_limit,
            max_variants=args.max_variants,
            use_multi_objective=not args.single_phase,
            enable_cache_write=not args.skip_api_caching,
            read_cache=args.read_api_cache,
            combo_tolerance=args.combo_tolerance,
            min_coverage_ratio=args.min_coverage_ratio,
            blocklist_path=args.blocklist,
            profile=args.profile,
            gap_limit=args.gap_limit,
            phase2_objective=args.phase2_objective,
            min_utilization_floor=args.min_util_floor,
            num_workers=args.workers,
            util_cap=args.util_cap,
            max_color_ratio=args.max_color_ratio,
            variant_weight=args.variant_weight,
            min_pair_combos=args.min_pair_combos,
            min_mono_combos=args.min_mono_combos,
            max_wide_combo_share=args.max_wide_combo_share,
            card_mix=card_mix,
            outcome_categories_path=args.outcome_categories,
            min_outcome_combos=args.min_outcome_combos,
            max_outcome_share=args.max_outcome_share,
            popularity_weight=args.popularity_weight,
        )
    )
