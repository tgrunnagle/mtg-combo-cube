"""Algorithm selection and execution for cube building."""

import logging

from mtg_combo_cube.blocklist import load_blocklist
from mtg_combo_cube.greedy.greedy_runner import run_greedy
from mtg_combo_cube.ilp.ilp_models import DEFAULT_CARD_MIX, CardMixRules
from mtg_combo_cube.ilp.ilp_optimizer import ILPOptimizer
from mtg_combo_cube.ilp.ilp_runner import run_ilp
from mtg_combo_cube.ilp.payoffs import DEFAULT_INFERENCE_THRESHOLD

logger = logging.getLogger(__name__)


async def run(
    method: str,
    cube_size: int,
    output_file: str,
    golden_ratio: float | None = None,
    time_limit_seconds: int = 300,
    max_variants: int = 20000,
    use_multi_objective: bool = True,
    enable_cache_write: bool = True,
    read_cache: bool = False,
    combo_tolerance: float = 0.1,
    min_coverage_ratio: float = 0.1,
    blocklist_path: str | None = None,
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
    payoff_share: float = ILPOptimizer.DEFAULT_PAYOFF_SHARE,
    payoff_inference_min: int = DEFAULT_INFERENCE_THRESHOLD,
):
    """
    Run the cube building algorithm.

    Args:
        method: "greedy" or "ilp"
        cube_size: Target number of cards in the cube
        output_file: Path to write the cube list
        golden_ratio: Ratio for greedy method (default: 1.2)
        time_limit_seconds: Time limit for ILP solver
        max_variants: Maximum combo variants to fetch
        use_multi_objective: Use two-phase ILP optimization
        enable_cache_write: Write API responses to cache
        read_cache: Read API responses from cache only
        combo_tolerance: Tolerance of the phase 2 combo window, on the combo score (weighted
            combos when variant_weight is below 1)
        min_coverage_ratio: Minimum coverage ratio for requirement templates in phase 2
        blocklist_path: Path to blocklist file (default: data/blocklist.txt)
        profile: Enable detailed profiling of ILP optimization
        gap_limit: Relative gap limit for Phase 2 early termination (0.05 = 5%)
        phase2_objective: Phase 2 objective: "minmax", "mad", "maxutil", "softcap" or "tiered"
        min_utilization_floor: Minimum utilization floor for phase 2 (any objective)
        num_workers: Number of parallel search workers for the ILP solver
        util_cap: Utilization cap for "softcap" and "tiered" (None: 2 x Phase 1 median)
        max_color_ratio: Phase 2 limit on how many times larger one color may be than another
            (0 disables)
        variant_weight: Value of each further completed variant of a combo the cube already
            completes, relative to the first (1 counts variants, 0 counts distinct combos)
        min_pair_combos: Phase 2 minimum number of distinct combos every two-color pair can
            assemble (0 disables)
        min_mono_combos: Phase 2 minimum number of distinct combos every mono color can
            assemble (0 disables)
        max_wide_combo_share: Phase 2 cap on the share of completed combos that need three
            or more colors (0 or 1 disables)
        card_mix: Phase 2 limits on the cube's make-up by color count, mana value and card
            type, as shares of the cube size (see CardMixRules)
        outcome_categories_path: The outcome category table (default:
            data/outcome_categories.json)
        min_outcome_combos: Phase 2 minimum number of distinct combos of every outcome
            category, unless the table gives the category its own (0 disables)
        max_outcome_share: Phase 2 cap on the share of completed combos in any one outcome
            category (0 or 1 disables)
        popularity_weight: How much a combo's popularity adds to its value in the Phase 1
            objective and the Phase 2 combo window (0: a tiebreak only)
        payoffs_path: The payoff table (default: data/payoffs.json): per outcome category,
            the Scryfall queries, cards and exclusions that find its outlets
        min_payoffs: Phase 2 minimum number of payoff cards of every category in the payoff
            table (0 disables)
        payoff_share: Share of the cube's cards the Phase 2 payoff floors add up to, split
            among the categories by their combos in the pool (0: the minimum alone)
        payoff_inference_min: Bundled Spellbook variants a card must be the outlet of before
            the inference counts it as a payoff
    """
    blocklist = load_blocklist(blocklist_path)

    if method == "ilp":
        await run_ilp(
            cube_size=cube_size,
            output_file=output_file,
            time_limit_seconds=time_limit_seconds,
            max_variants=max_variants,
            use_multi_objective=use_multi_objective,
            enable_cache_write=enable_cache_write,
            read_cache=read_cache,
            combo_tolerance=combo_tolerance,
            min_coverage_ratio=min_coverage_ratio,
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
            payoff_share=payoff_share,
            payoff_inference_min=payoff_inference_min,
        )
    elif method == "greedy":
        await run_greedy(
            cube_size=cube_size,
            output_file=output_file,
            golden_ratio=golden_ratio,
            blocklist=blocklist,
            enable_cache_write=enable_cache_write,
            read_cache=read_cache,
        )
    else:
        raise ValueError(f"Unknown method: {method}")
