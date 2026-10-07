# MTG Combo Cube

A tool for building Magic: The Gathering combo cubes by optimizing card selection to maximize combo potential.

## Summary

Given a target cube size (e.g., 360 cards), this tool selects cards that maximize the number of completable combos while ensuring balanced card utilization. This problem is a variant of a **weighted maximum coverage** or **set cover**, which is NP-hard. It fetches combo data from the [Commander Spellbook API](https://commanderspellbook.com/) and uses Integer Linear Programming (ILP) to find optimal solutions.

**The core optimization problem:**
- Select exactly N cards for the cube
- Maximize the number of completable combos (a combo is completable if all its required cards are in the cube)
- Balance card utilization so each card contributes to roughly the same number of combos

**Two-phase approach (default):**
1. **Phase 1** - Maximize combo count using weighted optimization (popularity as tiebreaker)
2. **Phase 2** - Balance card utilization while preserving combo count (within configurable tolerance)

This produces cubes where every card pulls its weight: in a two-phase result every card takes part in at least `--min-util-floor` completable combos (default 2), and over-used "hub" cards are pushed down.

## Setup

### Prerequisites
- Python 3.13+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- [Task](https://taskfile.dev/) (optional, for running development commands)

### Installation

```bash
task install

# Or install dependencies with uv directly
uv sync --dev
```

## Usage

### Quick Start

```bash
# Build a 300-card cube using ILP (two-phase optimization, default)
task build:ilp CUBE_SIZE=300
# or
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Build using greedy method
task build:greedy CUBE_SIZE=300
# or
uv run python -m src.mtg_combo_cube -c 300 --method greedy
```

### Command-Line Options

```
-c, --cube-size        Cube size (default: 300)
-m, --method           Optimization method: greedy or ilp (default: ilp)
-o, --output-file      Output file path (default: data/cube.txt)
-r, --ratio            Golden ratio for greedy method (default: 1.2)
-t, --time-limit       ILP solver time limit in seconds (default: 300)
-n, --max-variants     Max combo variants to fetch (default: 20000)
--single-phase         Use single-phase ILP (disables utilization balancing)
--combo-tolerance      Phase 2 combo window, on the combo score (weighted combos when --variant-weight < 1), measured from the best cube under the Phase 2 cube rules (default: 0.1 = 10%)
--gap-limit            Phase 2 early termination gap (default: 0.05 = 5%)
--phase2-objective     Phase 2 objective: tiered (default), softcap, maxutil, minmax or mad
--util-cap             Utilization cap for softcap and tiered (default: 2 x Phase 1 median)
--min-util-floor       Minimum utilization floor for Phase 2, any objective (default: 2)
--max-color-ratio      Phase 2 color balance: largest color at most this many times the smallest (default: 2.0, 0 disables)
--variant-weight       Value of each further variant of a combo the cube already completes (default: 0.1; 1 counts variants, 0 counts distinct combos)
--min-pair-combos      Phase 2 archetype support: distinct combos every two-color pair must be able to assemble (default: 250, 0 disables)
--min-mono-combos      Phase 2 archetype support: distinct combos every mono color must be able to assemble (default: 150, 0 disables)
--max-wide-combo-share Phase 2 cap on the share of completed combos that need three or more colors (default: 0.25, 0 or 1 disables)
--max-multicolor-share Phase 2 card mix: at most this share of the cube may be multicolor cards (default: 0.15, 0 or 1 disables)
--max-colorless-share  Phase 2 card mix: at most this share may be colorless cards (default: 0.25, 0 or 1 disables)
--max-expensive-share  Phase 2 card mix: at most this share may have mana value --expensive-mana-value or more (default: 0.2, 0 or 1 disables)
--expensive-mana-value Mana value from which a card counts as expensive (default: 5)
--max-creature-share   Phase 2 card mix: at most this share may be creatures (default: 0.6, 0 or 1 disables)
--min-spell-share      Phase 2 card mix: at least this share must be instants or sorceries (default: 0.05, 0 disables)
--mono-color-ratio     Phase 2 card mix: largest mono-colored count at most this many times the smallest (default: 0, disabled; otherwise at least 1)
--min-coverage-ratio   Min coverage ratio for requirement templates (default: 0.1)
--workers              Parallel search workers for the ILP solver (default: 8)
--profile              Enable detailed profiling of ILP optimization
--blocklist            Path to card blocklist file (default: data/blocklist.txt)
--skip-api-caching     Skip writing API responses to cache files
--read-api-cache       Read from cache if available, fall back to API if not
-d, --debug            Enable debug logging (includes the CP-SAT search log)
```

`-t`, `-n` and every Phase 2 / solver option apply to the ILP method only; `-r` applies to the greedy method only. `-t` is applied to each phase separately.

The `task build:ilp*` targets pass `--profile --read-api-cache` and accept `CUBE_SIZE`, `OUTPUT`, `TIME_LIMIT`, `MAX_VARIANTS` and `WORKERS` variables, e.g. `task build:ilp CUBE_SIZE=200 TIME_LIMIT=120`. Any other flag goes after `--`, e.g. `task build:ilp CUBE_SIZE=200 MAX_VARIANTS=1000 -- --min-pair-combos 0 --min-mono-combos 0` (the archetype minimums are sized for the default build; see "Phase 2 Options"). Their defaults (300 cards, 20,000 variants, a 360 s time limit, 8 workers) are the top-level `vars` in `Taskfile.yml`, shared with `task precache`.

### Examples

```bash
uv run python -m src.mtg_combo_cube --help

# Two-phase ILP (balanced card utilization)
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Single-phase ILP (max combos only)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --single-phase

# Two-phase with 20% combo tolerance (trades more combos for a flatter cube)
uv run python -m src.mtg_combo_cube -c 300 --method ilp --combo-tolerance 0.2

# Push the single most-used card down instead of the overall spread
uv run python -m src.mtg_combo_cube -c 300 --method ilp --phase2-objective maxutil

# Penalize every card used in more than 40 combos
uv run python -m src.mtg_combo_cube -c 300 --method ilp --util-cap 40

# Small, fast configuration for trying things out (about 1 minute with a warm cache);
# the archetype minimums are sized for a full build, so they are switched off here
uv run python -m src.mtg_combo_cube -c 100 --method ilp -t 30 -n 1000 --read-api-cache --min-pair-combos 0 --min-mono-combos 0

# Greedy with custom ratio
uv run python -m src.mtg_combo_cube -c 360 --method greedy -r 1.5

# Custom output file and time limit
uv run python -m src.mtg_combo_cube -c 450 --method ilp -o my_cube.txt -t 1800
```

### Output Files

- **data/cube.txt**: List of selected cards (one per line)
- **data/cube_stats.json**: Utilization statistics and optimization metrics (ILP only)

The stats file contains:

- `metadata`: cube size, `combo_count` (completed variants), `distinct_combo_count` (completed combos, see "Combos and variants" below), the `variant_weight` used, total solve time, Phase 1 status and `optimization_method`, which is `two_phase`, `single_phase`, or `two_phase_fallback_to_phase1` when Phase 2 ran but found no solution and the cube is the Phase 1 result.
- `phase1` / `phase2`: `combo_count`, `distinct_combo_count`, solve time, utilization statistics (min, max, mean, median, standard deviation), `colors`, the color distribution of that phase's cube, `archetypes`, the distinct combos each draft archetype can assemble, and `card_mix`, its make-up by type, color count and mana value (all three described below). `phase2` also records `status`, the `objective` that ran, the `max_color_ratio` applied, the archetype settings applied (`min_pair_combos`, `min_mono_combos`, `max_wide_combo_share`, each left out when disabled), `card_mix_rules` (the card mix settings in force, left out when all are disabled), the reference cube the tolerance was measured from (`reference_combo_count` variants, `reference_distinct_combo_count` combos and `reference_weighted_combo_count`, the weighted count the window holds) and, for `softcap` / `tiered`, the `util_cap` used. After a fallback it holds only the status, time, objective, cap, archetype and card mix settings, reference and `fell_back_to_phase1: true`.
- `improvement`: Phase 1 to Phase 2 changes, including the variant and distinct combo counts before and after and the cards swapped.
- `largest_combo_groups`: the ten combos with the most completed variants in the final cube, each with its Spellbook combo id, variant count and the cube cards that take part.
- `top_utilized_cards` / `bottom_utilized_cards`, `requirement_types`, `cross_template_overlap`.
- `profiling` (with `--profile`): per-phase timings, variable and constraint counts and solver statistics.

Every combo count and utilization number is computed from the selected cards, not read from solver variables.

`colors` is based on each card's color identity, looked up on Scryfall for every candidate card (with its type line and mana value) and cached in `data/cache/scryfall_card_attributes.json`:

- `cards_per_color`: cards per color (W, U, B, R, G). A multicolor card counts once for each of its colors.
- `mono_colored`, `multicolor`, `colorless`: the cube split into exclusive groups. `unknown` counts cards without color data.
- `variance` / `std_deviation`: spread of the five `cards_per_color` counts. Zero means the colors are evenly represented.

`archetypes` counts distinct combos by the drafter who could assemble them, from each combo's Commander Spellbook color identity:

- `combos_per_archetype`: for each of the ten two-color pairs (`WU` ... `RG`), the five mono colors and `C` (colorless), the completed combos whose color identity fits within those colors. A mono-white combo counts for `W` and for every pair with white; a colorless combo counts everywhere. The identity covers a combo's named cards; a template requirement filled by a colored card can add a color it does not show.
- `combos_by_color_count`: completed combos by the number of colors they need (0 to 5). A combo with variants of different identities needs the fewest colors of its completed variants.

`card_mix` describes the cube's cards, from the same Scryfall data:

- `type_counts`: cards of each card type (Creature, Instant, Sorcery, Artifact, Enchantment, Planeswalker, Battle, Land); a card counts once for every type it has, taken from the front face of a multi-faced card.
- `multicolor`, `colorless`: cards with two or more colors and with none. `unknown` counts cards without Scryfall data, which count as colorless.
- `mana_value_counts`, `mean_mana_value`, `mean_mana_value_per_color`: the nonland cards by mana value (`7` means 7 or more), their mean, and the mean of the nonland cards of each color. A double-faced card has its front face's mana value, a split card the sum.

The log prints the same combo counts ("Combos: Phase 1 4145 variants in 604 combos, ..."), color distribution, archetype counts ("Archetypes, Phase 2: pairs WU=358, WB=335, ...; mono W=219, ...; colorless 128; 3+ colors 304 of 1380 (22%)") and card mix ("Card mix, Phase 2: types Creature=180 (60%), Instant=10, ...; multicolor 43 (14%), colorless 74 (25%); mana value (nonland) mean 3.2, ...") at the end of a run. If Scryfall cannot be reached, the run still completes and `colors` and `card_mix` are left out.

[data/current_best_cube.txt](data/current_best_cube.txt) and its stats file are a tracked example: a 300-card cube from 20,000 variants with the default settings (1,444 variants in 1,060 distinct combos; every two-color pair can assemble at least 280 of them and every mono color at least 186; 60% creatures, 25% colorless cards, 17 instants and sorceries).

### Evaluating a Cube

To score an existing cube list (true variant and distinct combo counts, the largest combo groups, utilization statistics, combos per draft archetype, color distribution and card mix) against the cached data:

```bash
uv run python -m mtg_combo_cube.ilp.evaluate_cube data/cube.txt -n 20000
```

Use the same `-n` (and `--blocklist`) as the build you want to compare with. Cached data is read when present and fetched otherwise (only card attributes are written to the cache); cards that are not part of the instance are reported and count with utilization 0.

### API Caching

Three kinds of API responses are cached in `data/cache/`:

| Data | File | Used by |
|------|------|---------|
| Commander Spellbook combo variants | `variants_cards{max}_max{variants}.json` | ILP |
| Scryfall template searches (the cards that satisfy a requirement such as "Persist Creature") | `scryfall_templates.json` | ILP and greedy |
| Scryfall card attributes of the candidate cards (color identity, type line, mana value) | `scryfall_card_attributes.json` | ILP (color balance, card mix, statistics) |

**Cache behavior:**
- By default, API responses are written to `data/cache/` after fetching
- Use `--read-api-cache` to read from cache when available (falls back to live API on cache miss)
- Use `--skip-api-caching` to disable writing to cache
- Both flags cover both caches. With a warm cache, an ILP run with `--read-api-cache` makes no network requests.
- Scryfall requests are rate-limited (about 10 per second) and retried on HTTP 429 / 5xx and network errors. Failed requests are not cached, so a later run retries them.
- The greedy method always queries Commander Spellbook live; only its Scryfall lookups are cached.

After preprocessing, the ILP method logs how many combos were left out and why, and how the Scryfall data was obtained:

```
Dropped 78 of 10000 combos (blocked_card=5, no_scryfall_api=73, scryfall_failure=0, empty_match=0); Scryfall: 0 network requests, 68 templates read from cache, 0 failed
```

- `blocked_card`: a required card is on the blocklist
- `no_scryfall_api`: a requirement has no Scryfall query
- `empty_match`: the query matches no card that is not blocked
- `scryfall_failure`: the request failed. A warning is logged because the instance is then incomplete; re-running retries it.

```bash
# First run: fetches from API and caches results
uv run python -m src.mtg_combo_cube -c 300 --method ilp

# Subsequent runs: use cached data for faster iteration
uv run python -m src.mtg_combo_cube -c 300 --method ilp --read-api-cache

# Force fresh API fetch without caching
uv run python -m src.mtg_combo_cube -c 300 --method ilp --skip-api-caching
```

#### Precaching

To download everything a build reads ahead of time, so that the build itself makes no network requests:

```bash
task precache                                   # 20,000 variants, as the task build:ilp* targets use by default
task precache MAX_VARIANTS=1000                 # a smaller configuration
task precache -- --keep-existing                # only fetch what is missing

uv run python -m mtg_combo_cube.precache -n 20000 --max-cards-in-combo 4 --blocklist data/blocklist.txt
```

It fills the variants file, the Scryfall template searches and the card attributes. Use the same `-n` (`MAX_VARIANTS`), `--max-cards-in-combo` (`MAX_CARDS_IN_COMBO`) and `--blocklist` (`BLOCKLIST`) as the build: the first two name the variants file, and the blocklist decides which templates and cards the build asks for. `task precache` and the `task build:ilp*` targets both default to 20,000 variants.

- **Existing data is overwritten.** The variants file is replaced, and so is every template and card attribute entry of the configuration. Entries that only other configurations use are left alone. A failed download leaves the existing variants file in place.
- `--keep-existing` keeps the entries already in the cache and fetches only what is missing, which finishes an incomplete run without starting over.
- **Retries:** each client retries single requests with backoff (see above). On top of that, a stage whose requests still failed is run again, up to `--max-passes` times (default 3), waiting `--retry-wait` seconds (default 30) before the second pass and twice as long before each further one.
- The script prints a summary and exits with status 1 when the cache is incomplete. Cards that Scryfall does not know are reported but do not count as a failure.
- `--cache-dir` writes somewhere other than `data/cache/`; a build only reads `data/cache/`.

## Optimization Methods

### Greedy
Fast heuristic approach that iteratively selects high-impact cards. Good for quick iterations.

### ILP (Recommended, default)
Integer Linear Programming using OR-Tools CP-SAT solver. Phase 1 is solved to proven optimality at the tested sizes when every variant counts (`--variant-weight 1`); with the default weight it runs to its time limit at full size and returns the best cube found. Phase 2 returns the best cube found within the gap or time limit. Two operational modes:

**Two-Phase (Default)**
- Phase 1: Maximize combo count
- Phase 2: Balance card utilization while preserving combo count (within tolerance)
- Produces balanced cubes where cards participate more evenly across combos
- Outputs detailed statistics to `{output}_stats.json`

**Combos and variants**

Commander Spellbook lists *variants*: each is one way to assemble a *combo*, and many variants are the same combo with one piece swapped for an equivalent (the cached 20,000 variants belong to about 8,700 combos; the largest combo has 301 variants). Every count in the log and the stats file is reported both ways: `combo_count` is completed variants, `distinct_combo_count` is the combos they belong to (a variant's `of` ids on Spellbook; a variant of two combos combined counts as its own).

`--variant-weight` sets what the optimizer counts. A completed variant is worth 1 if it is the first of its combo and `--variant-weight` for each further one, so `1` counts variants and `0` counts distinct combos, with values in between rewarding extra variants less than new combos. The default is `0.1`: at full size it more than doubles the distinct combos of a variant-counting cube and shrinks the largest combo from 230 variants to 24, at the cost of about 40% of the variants. Any weight below 1 makes Phase 1 run to its time limit at full size instead of proving optimality in seconds. Phase 1 maximizes this weighted count and the Phase 2 combo window (`--combo-tolerance`) holds it, so with a weight below 1 the tolerance and `reference_weighted_combo_count` are in weighted combos, not variants. The table in [docs/plans/combo-grouping-plan.md](docs/plans/combo-grouping-plan.md) compares weights at full size.

**Phase 2 Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--phase2-objective` | `tiered` | Objective function: `tiered`, `softcap`, `maxutil`, `minmax` or `mad` |
| `--util-cap` | 2 x Phase 1 median utilization | Cap `T` for `softcap` and `tiered`: utilization above it is penalized |
| `--combo-tolerance` | `0.1` | How far Phase 2 may move from the reference combo score (10%), in weighted combos when `--variant-weight` is below 1 (see "Combos and variants"). The reference is the best cube found under the Phase 2 cube rules (coverage, color balance, archetype support), which scores lower than the Phase 1 cube |
| `--gap-limit` | `0.05` | Early termination when proven within 5% of optimal (0 = solve to optimality) |
| `--min-util-floor` | `2` | Minimum completable combos each selected card must participate in (all objectives, 0 disables) |
| `--max-color-ratio` | `2.0` | Color balance: no color may have more than this many times the cards of another color (all objectives, 0 disables, otherwise at least 1) |
| `--min-coverage-ratio` | `0.1` | Minimum cards per requirement template (10% of the combos using it, at most the template's card pool; 0 disables) |
| `--variant-weight` | `0.1` | Value of each further completed variant of a combo relative to its first (both phases; `1` counts every variant, `0` counts distinct combos only). The combo window is measured in the same weighted units |
| `--min-pair-combos` | `250` | Archetype support: every two-color pair must be able to assemble at least this many distinct combos (the pair's own plus mono-colored and colorless ones; 0 disables) |
| `--min-mono-combos` | `150` | Archetype support: every mono color must be able to assemble at least this many distinct combos (its own plus colorless ones; 0 disables) |
| `--max-wide-combo-share` | `0.25` | At most this share of the completed combos may need three or more colors (0 or 1 disables) |
| `--max-multicolor-share` | `0.15` | Card mix: at most this share of the cube may be multicolor cards (0 or 1 disables) |
| `--max-colorless-share` | `0.25` | Card mix: at most this share may be colorless cards; cards without Scryfall data count as colorless (0 or 1 disables) |
| `--max-expensive-share` | `0.2` | Card mix: at most this share may have a mana value of `--expensive-mana-value` (default 5) or more (0 or 1 disables) |
| `--max-creature-share` | `0.6` | Card mix: at most this share may be creatures (0 or 1 disables) |
| `--min-spell-share` | `0.05` | Card mix: at least this share must be instants or sorceries (0 disables) |
| `--mono-color-ratio` | `0` | Card mix: no color may have more than this many times the mono-colored cards of another, as `--max-color-ratio` on mono-colored cards only (0 disables, otherwise at least 1) |

**Phase 2 Objectives:**

| Objective | Minimizes | Pick it when |
|-----------|-----------|--------------|
| `tiered` (default) | Total utilization above the cap `T`, counted again above `2T` and `4T` | You want the flattest overall distribution; a single outlier card may remain |
| `softcap` | Total utilization above the cap `T` | As `tiered`, without the extra penalty for cards far above the cap |
| `maxutil` | The highest utilization of any card | The single most-used card matters most |
| `minmax` | `max_utilization - min_utilization` | As `maxutil`; this was the default before `tiered` |
| `mad` | Total absolute deviation from the Phase 1 mean utilization | Comparison with older results |

Measured at 300 cards / 10,000 variants with a 300 s limit (Phase 1 for comparison: maximum 348, standard deviation 41.85, 12 cards above 100):

| Objective | Max utilization | Std deviation | Cards above 100 |
|-----------|-----------------|---------------|-----------------|
| `tiered` (two runs) | 255 | 28.6 / 29.1 | 7 / 8 |
| `softcap` | 261 | 29.8 | 8 |
| `mad` | 240 | 29.5 | 8 |
| `maxutil` | 155 | 32.6 | 18 |
| `minmax` | 155 | 32.2 | 19 |

The objectives fall into two families: `tiered`, `softcap` and `mad` give a lower spread but leave one card far out; `maxutil` and `minmax` hold the worst card at about 155 but end with a plateau of cards just under it. All of them keep 2,223-2,224 of the 2,471 Phase 1 combos (the lower edge of the 10% tolerance), and none finishes before the time limit at this size. These are one or two runs each of a time-limited parallel search; differences inside a family are within run-to-run noise. Details are in the [ILP Improvement Plan](docs/plans/ilp-improvement-plan.md).

Notes:

- The floor (`--min-util-floor`), the coverage rule (`--min-coverage-ratio`), the color balance (`--max-color-ratio`), the archetype rules (`--min-pair-combos`, `--min-mono-combos`, `--max-wide-combo-share`) and the card mix rules (`--max-multicolor-share` and the other share options) are constraints of Phase 2 only and apply to every objective. Phase 1 and `--single-phase` do not enforce them.
- Color balance counts a card once for each color of its color identity, so a white-blue card counts as white and as blue. Colorless cards are not limited by it (see `--max-colorless-share`). The rule needs every color to be present; if it cannot be met within the combo tolerance, Phase 2 fails and the Phase 1 cube is returned. If card data cannot be fetched from Scryfall, a warning is logged and the run continues without the color balance and the card mix rules.
- The card mix rules are shares of the cube size, rounded to hundredths: a cap allows `floor(share x cube size)` cards and the spell floor requires `ceil(share x cube size)`. Card types are those of the front face of a multi-faced card, and a card without Scryfall data counts as colorless, typeless and mana value 0 (a warning gives their number). The defaults were chosen from the measurements in [docs/plans/card-mix-plan.md](docs/plans/card-mix-plan.md): each costs between 1% and 4% of the weighted combo count alone, with the colorless cap, the expensive cap and the spell floor the most binding.
- Archetype support counts distinct combos (not variants) by their Spellbook color identity, as in the `archetypes` statistics. The minimums are absolute counts chosen for the default configuration (300 cards, 20,000 variants, see [docs/plans/archetype-support-plan.md](docs/plans/archetype-support-plan.md)); a much smaller cube or pool cannot reach them, so lower them or pass 0 there. Before solving, a warning names any archetype the whole pool has too few combos for; if the minimums cannot be met, Phase 2 fails, the Phase 1 cube is returned and the log says which archetypes that cube falls short on.
- The tolerance measurements in this section were taken when `--combo-tolerance` was measured from the Phase 1 count. It is now measured from the best cube under the Phase 2 cube rules (coverage, color balance and, since the archetype rules, their minimums and the wide cap), so the same value allows fewer combos than it did then.
- For `softcap` and `tiered` the gap limit is measured against the Phase 1 cube: the solve stops once the total overage is proven within `gap-limit` x (overage of the Phase 1 cube) of optimal.
- If Phase 2 finds no solution (infeasible or out of time), the Phase 1 cube is written, a warning is logged and the stats file says `two_phase_fallback_to_phase1`.

**Single-Phase** (use `--single-phase`)
- Maximizes combo count only
- Faster but may result in unbalanced card utilization

## Design Documentation

- [Architecture](docs/architecture.md) - The system as implemented: data pipeline, the two-phase ILP model and its objectives, outputs and known limitations
- [Plans and design documents](docs/plans/README.md) - Working documents from each round of development, including the [ILP Improvement Plan](docs/plans/ilp-improvement-plan.md) with benchmark results and decisions

Key concepts:
- **Variant and Combo**: A variant is one Spellbook combo listing; a combo (or combo group) is the set of variants that are the same combo with a piece swapped. Reported as `combo_count` (variants) and `distinct_combo_count` (combos). `--variant-weight` sets how much the optimizer values further variants of a combo it already completes.
- **Card Utilization**: Number of completable variants each card participates in. A card counts for a variant when it is one of its required cards or belongs to the card pool of one of its requirement templates, whether or not it is the card that satisfies the template.
- **Utilization Cap**: The `tiered` (default) and `softcap` objectives penalize utilization above a cap, by default twice the Phase 1 median
- **Utilization Floor**: Every card in a Phase 2 cube takes part in at least `--min-util-floor` completable combos
- **Color Balance**: In a Phase 2 cube no color has more than `--max-color-ratio` times the cards of another color
- **Archetype Support**: In a Phase 2 cube every two-color pair can assemble at least `--min-pair-combos` distinct combos and every mono color `--min-mono-combos`, counting the combos whose color identity fits the archetype, and at most `--max-wide-combo-share` of the completed combos need three or more colors
- **Card Mix**: In a Phase 2 cube at most `--max-multicolor-share`, `--max-colorless-share`, `--max-expensive-share` and `--max-creature-share` of the cards are multicolor, colorless, at or above `--expensive-mana-value`, or creatures, and at least `--min-spell-share` are instants or sorceries
- **Fallback Strategy**: Phase 2 failures automatically return Phase 1 results, marked as a fallback in the log and the stats file

## Development

### Using Taskfile

```bash
# Install dependencies
task install

# Run unit tests
task test

# Run tests with coverage
task test:cov

# Run linting and formatting
task lint
task format

# Type checking
task typecheck

# Run all checks (lint, format, typecheck, tests)
task check

# Build a cube (see Quick Start)
task build:ilp
task build:ilp-single
task build:greedy

# Download the API data for a build into data/cache (see API Caching)
task precache
```

### Manual Commands

```bash
# Testing
uv run pytest tests/unit -v
uv run pytest tests/unit --cov=src --cov-report=term-missing

# Linting
uv run ruff check --select I --fix .
uv run ruff check . --fix

# Formatting
uv run ruff format .

# Type checking
uv run ty check .
```

### Test Coverage

The project includes comprehensive unit tests for the ILP optimization logic.

Tests focus on:
- Data model validation and backward compatibility
- Utilization calculation and statistics
- Constraint satisfaction and optimization
- Single-phase and two-phase solver behavior, with every Phase 2 objective checked against brute-force enumeration of a small instance
- Edge cases (empty combos, insufficient cards, etc.)
- Stats file generation and formatting
- Scryfall fetching (cache, retries, rate limiting) against a fake HTTP session
- CLI and runner plumbing

The unit tests make no network requests.

Run `task test:cov` to generate an HTML coverage report in `htmlcov/`.

## How It Works

### Greedy Method
1. Fetch top combos by popularity from Commander Spellbook API
2. Select core cards that appear most frequently across combos
3. Fill remaining slots with cards from "almost included" combos
4. Remove dead cards (cards not in any completable combo)
5. Iterate until cube reaches target size

### ILP Method
1. Fetch and preprocess combo variants
2. Build constraint satisfaction model with:
   - Cube size constraint
   - Combo completion requirements (required cards + optional requirements conditions)
   - Popularity-based tiebreaking
3. **Phase 1**: Maximize weighted combo count
4. **Phase 2** (unless `--single-phase`): Balance card utilization with the combo score held within tolerance of the reference: the best cube found under the Phase 2 cube rules (coverage, color balance, archetype support), from a short extra solve after Phase 1. The Phase 2 model adds exact combo completion (a combo counts if and only if the cube completes it), one utilization variable per card, the utilization floor, the cube rules and the chosen objective. It is warm-started from that reference cube, repaired in a second short solve if it has cards below the floor.
5. Output optimized card list and statistics

### ILP Complexity

The ILP model scales as follows (where **Q** = cube size, **N** = number of combos, **C** = number of unique cards):

| Aspect | Phase 1 | Phase 2 |
|--------|---------|---------|
| Binary variables | C + N | C + N + P |
| Integer variables | 0 | C (utilization) + objective variables |
| Constraints | O(N × R) | O(N × R + C + P × K) |

Where **R** is the average number of optional requirements per combo, **P** the number of distinct requirement card pools (48 at 200 cards / 5,000 variants) and **K** the cards per pool (at most 10). Objective variables: 1 for `maxutil`, 2 for `minmax`, up to C for `softcap`, up to 3C for `tiered`, 2C for `mad`.

**Measured solve times** (8 workers, warm cache, default settings, October 2026; see the [ILP Improvement Plan](docs/plans/ilp-improvement-plan.md)):

| Cube size | Variants | Phase 1 | Phase 2 (`tiered`) |
|-----------|----------|---------|--------------------|
| 100 | 1,000 | 0.2 s, optimal | runs to a 30 s limit |
| 200 | 5,000 | about 3 s, optimal | about 80 s to reach the 5% gap limit (one run) |
| 300 | 10,000 | about 5 s, optimal | runs to the 300 s limit (16-17.5% gap left) |

- Phase 1 is solved to optimality in seconds. Phase 2 is the expensive part: at full size no objective reaches its stop rule within 300 s, so the result is the best cube found when the time limit expires. Most of the improvement happens in the first 3 minutes.
- `--time-limit` (default 300 s) applies to each phase separately; the Phase 2 limit includes the warm-start repair.
- Phase 2 timings vary noticeably between runs (the parallel search is not deterministic).
- **Keep `--workers` at 8.** CP-SAT chooses its set of search strategies by worker count, so fewer workers is not just slower: with 4 workers Phase 1 did not prove optimality within the time limit at any tested size (8 workers: about 5 s at full size), and Phase 2 found no solution at all at 200 and 300 cards. More than 8 workers has not been measured.

## License

MIT
